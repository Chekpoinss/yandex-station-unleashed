"""Reversible sound overlays; no root remount, boot hooks, service changes or playback."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile
import threading

class SoundOverrides:
    LIMIT = 50 * 1024 * 1024

    def __init__(self, panel, server, folder="sound-backups", remote_folder="sound-overrides"):
        self.panel, self.server = panel, server
        # Bind records to a specific ADB target even outside multi-speaker mode.
        identity = hashlib.sha256((panel.adb.serial or 'unselected').encode()).hexdigest()[:16]
        self.root = Path(panel.data_dir) / folder / identity
        self.root.mkdir(parents=True, exist_ok=True)
        self.remote = server.WEB_DIR + '/' + remote_folder
        self.manifest = self.root / 'manifest.json'
        self.lock = threading.RLock()
        self.records = json.loads(self.manifest.read_text('utf8')) if self.manifest.exists() else {}

    def paths(self, name):
        if not self.panel.adb.serial:
            raise ValueError('Для замены звуков нужна явно выбранная колонка')
        if (not isinstance(name, str) or name.startswith('/') or any(c in name for c in '\\ \t\r\n')
                or any(p in ('', '.', '..') for p in name.split('/')) or name not in self.panel.sounds()):
            raise ValueError('Выберите существующий системный звук')
        if name == 'common/boot.wav':
            raise ValueError('Звук загрузки требует отдельной проверки раннего хука; здесь недоступен')
        key = hashlib.sha256(name.encode()).hexdigest()
        return self.server.SOUNDS_DIR + '/' + name, self.remote + '/' + key, key

    def mounted(self, target):
        q = shlex.quote
        out, _, _ = self.panel.adb.shell(f"awk -v p={q(target)} '$2==p {{print $2}}' /proc/mounts", check=False)
        return bool(out.strip())

    def owned(self, target, custom):
        q = shlex.quote
        out, _, rc = self.panel.adb.shell(f"stat -c '%d:%i' {q(target)} {q(custom)}", check=False)
        values = out.strip().splitlines()
        return rc == 0 and len(values) == 2 and values[0] == values[1]

    def save(self):
        tmp = self.manifest.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.records, ensure_ascii=False), encoding='utf8')
        os.replace(tmp, self.manifest)

    def status(self):
        with self.lock:
            items = []
            for name, record in self.records.items():
                target, base, _ = self.paths(name)
                mounted = self.mounted(target)
                items.append({'name': name, 'mounted': mounted, 'owned': mounted and self.owned(target, base + '.custom')})
            return {'items': items, 'persistent': False, 'ffmpeg': bool(self.panel.ffmpeg),
                    'note': 'Подмена действует до перезагрузки колонки. Оригиналы сохраняются; автозапуск не изменяется.'}

    def _convert(self, original, body):
        return self.panel._audio_like(original, body)

    def play_original(self, name):
        with self.lock:
            _, _, key = self.paths(name)
            record = self.records.get(name)
            if not record:raise ValueError('Панель не заменяла этот звук')
            raw = (self.root / (key + '.original')).read_bytes()
            if hashlib.sha256(raw).hexdigest() != record['original_sha256']:
                raise ValueError('Резервная копия повреждена; воспроизведение отменено')
        return self.panel._transcode_and_play(raw)

    def replace(self, name, body):
        if not body or len(body) > self.LIMIT:
            raise ValueError('Аудиофайл: от 1 байта до 50 МБ')
        with self.lock:
            target, base, key = self.paths(name)
            mounted = self.mounted(target)
            if mounted and (name not in self.records or not self.owned(target, base + '.custom')):
                raise ValueError('Звук подменён другой программой; её монтирование не изменено')
            original = self.root / (key + '.original')
            if not original.exists():
                if mounted:
                    raise ValueError('Нет резервной копии оригинала; подмена не изменена')
                raw = self.panel.adb.read_bytes(target)
                if not raw or len(raw) > self.LIMIT:
                    raise ValueError('Не удалось сохранить оригинал')
                original.write_bytes(raw)
            if not mounted and hashlib.sha256(self.panel.adb.read_bytes(target)).digest() != hashlib.sha256(original.read_bytes()).digest():
                raise ValueError('Системный оригинал изменился. Прежняя резервная копия сохранена; подмена отменена')
            encoded = self._convert(original.read_bytes(), body)
            if len(encoded) > self.LIMIT:
                raise ValueError('Перекодированный звук больше 50 МБ')
            q = shlex.quote
            self.panel.adb.shell(f'mkdir -p {q(self.remote)}')
            # Remote immutable backup plus the local original; never write into /system.
            with tempfile.TemporaryDirectory() as td:
                f = Path(td) / 'sound'; f.write_bytes(encoded)
                self.panel.adb.push(original, base + '.original.new')
                self.panel.adb.push(f, base + '.new')
            expected = hashlib.sha256(encoded).hexdigest()
            out, _, _ = self.panel.adb.shell(f'sha256sum {q(base + ".new")}')
            if out.split()[:1] != [expected]:
                raise ValueError('Проверка переданного звука не прошла')
            self.panel.adb.shell(f'test -f {q(base + ".original")} || mv {q(base + ".original.new")} {q(base + ".original")}')
            # Keep old inode for rollback until new mount succeeds.
            command = (f'cp {q(base + ".new")} {q(base + ".next")} && '
                       + (f'umount {q(target)} && ' if mounted else '')
                       + f'mount --bind {q(base + ".next")} {q(target)}')
            _, _, rc = self.panel.adb.shell(command, check=False)
            if rc:
                if mounted:
                    self.panel.adb.shell(f'mount --bind {q(base + ".custom")} {q(target)}', check=False)
                raise ValueError('Не удалось подменить звук; восстановление предыдущей подмены запрошено')
            self.panel.adb.shell(f'mv {q(base + ".next")} {q(base + ".custom")}')
            self.records[name] = {'original_sha256': hashlib.sha256(original.read_bytes()).hexdigest()}
            self.save()
            return self.status()

    def reset(self, name):
        with self.lock:
            target, base, key = self.paths(name)
            if name not in self.records:
                raise ValueError('Панель не заменяла этот звук')
            if self.mounted(target):
                if not self.owned(target, base + '.custom'):
                    raise ValueError('Чужое монтирование не изменено')
                self.panel.adb.shell('umount ' + shlex.quote(target))
            # The original is exposed by unmounting, not overwritten from a backup.
            digest = hashlib.sha256(self.panel.adb.read_bytes(target)).hexdigest()
            if digest != self.records[name]['original_sha256']:
                raise ValueError('Оригинал после снятия подмены отличается; резервные копии сохранены')
            self.records.pop(name)
            self.save()
            return self.status()
