"""Update an existing service.sh-managed YAM without touching boot hooks."""
import hashlib
import json
import re
import shlex
import tempfile
import threading
import time
import uuid
from pathlib import Path
import urllib.request


def install(s):
    lock = threading.Lock()
    base = '/data/local/yam'
    service = base + '/service.sh'

    def start(self):
        self.adb.shell('test -x ' + service + ' && ' + service + ' start', timeout=30)
        return self.yam_status()

    def backups(self):
        # Резервные копии прошлых обновлений из панели: (количество, КБ)
        out = self.adb.shell(f'for d in {base}/backup-web-*; do [ -d "$d" ] && du -sk "$d"; done; true', check=False)[0]
        rows = [line.split() for line in out.splitlines()]
        sizes = [int(r[0]) for r in rows if len(r) == 2 and r[0].isdigit() and r[1].startswith(base + '/backup-web-')]
        return len(sizes), sum(sizes)

    original_status = s.Panel.yam_status

    def status(self):
        result = original_status(self)
        if result.get('installed'):
            count, kb = backups(self)
            result['backups'] = {'count': count, 'kb': kb}
        return result

    def update(self, drop_backups=False):
        # Старые копии удаляются перед новой — только с явного согласия (панель спрашивает перед обновлением).
        if backups(self)[0] and not drop_backups:
            raise s.AdbError('Старые резервные копии yamd будут удалены — подтвердите обновление ещё раз')
        if not lock.acquire(blocking=False):
            raise s.AdbError('Обновление yamd уже выполняется')
        backup = base + '/backup-web-' + time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
        stage = base + '/bin/yamd.web-new'
        acquired = stopped = False
        try:
            # Reject unknown layouts before downloading or modifying anything.
            self.adb.shell(f'test -x {service} && test -x {s.YAMD} && test -d {base}/data')
            before_plugins = self.yam_plugins()
            before_settings = self._yam('GET', '/api/settings')
            req = urllib.request.Request('https://api.github.com/repos/nyakokitsu/yam/releases/latest', headers={'User-Agent': 'webpanel'})
            with urllib.request.urlopen(req, timeout=20) as response:
                release = json.load(response)
            tag = release['tag_name']
            if not re.fullmatch(r'v?\d+\.\d+\.\d+', tag):
                raise s.AdbError('Некорректная версия релиза YAM')
            assets = {a['name']: a for a in release['assets']}
            asset = assets[self.YAM_ASSET]
            with urllib.request.urlopen(assets['SHA256SUMS']['browser_download_url'], timeout=30) as response:
                sums = response.read().decode()
            expected = next((line.split()[0] for line in sums.splitlines() if line.split()[-1:] == [self.YAM_ASSET]), None)
            with urllib.request.urlopen(asset['browser_download_url'], timeout=90) as response:
                blob = response.read()
            if not expected or hashlib.sha256(blob).hexdigest() != expected or (asset.get('digest') and asset['digest'] != 'sha256:' + expected):
                raise s.AdbError('Контрольная сумма yamd не совпала с релизом')
            actual = self.adb.shell(f'sha256sum {s.YAMD}')[0].split()[0]
            if actual == expected:
                return {**self.yam_status(), 'version': tag, 'unchanged': True}
            self.adb.shell(f'mkdir {base}/.web-update-lock')
            acquired = True
            # Старые копии удаляем (на это получено согласие), новая включает данные пакетов и настройки.
            self.adb.shell(f'rm -rf {base}/backup-web-*', check=False)
            self.adb.shell(f'set -e; free=$(df -k {base} | tail -1 | awk \'{{print $4}}\'); '
                           f'need=$(du -sk {base}/data {base}/bin | awk \'{{s+=$1}}END{{print s}}\'); '
                           f'test "$free" -gt "$((need + {len(blob)//1024} + 8192))"; '
                           f'mkdir {backup}; cp -a {base}/bin {base}/data {service} {backup}/; '
                           f'if [ -f {base}/installed-release.json ]; then cp -p {base}/installed-release.json {backup}/; fi', timeout=60)
            with tempfile.TemporaryDirectory() as directory:
                file = Path(directory)/'yamd'; file.write_bytes(blob)
                self.adb.push(file, stage, timeout=90)
            output = self.adb.shell(f'chmod 755 {stage}; sha256sum {stage}; {stage} --help', timeout=20)[0]
            if expected not in output:
                raise s.AdbError('Проверка yamd на колонке не прошла')
            stopped = True
            self.adb.shell(service + ' stop', timeout=30)
            self.adb.shell(f'set -e; mv {stage} {s.YAMD}; {service} start', timeout=30)
            self._yam_port = None
            deadline = time.monotonic() + 30
            def selected(plugins):
                return sorted((p['manifest']['id'], p['manifest']['version'], p['enabled']) for p in plugins)
            while True:
                after_plugins = self.yam_plugins()
                running = all(not p.get('running') or next((q.get('running') for q in after_plugins if q['manifest']['id'] == p['manifest']['id']), False) for p in before_plugins)
                if running: break
                if time.monotonic() >= deadline: raise s.AdbError('Пакеты YAM не восстановились после обновления')
                time.sleep(1)
            if selected(before_plugins) != selected(after_plugins):
                raise s.AdbError('Список или состояние пакетов изменились')
            settings = self._yam('GET', '/api/settings')
            if settings.get('updates', {}).get('enabled') != before_settings.get('updates', {}).get('enabled'):
                raise s.AdbError('Изменилась настройка автообновления YAM')
            if self.adb.shell(f'sha256sum {s.YAMD}')[0].split()[0] != expected:
                raise s.AdbError('Версия установленного бинарника не совпала с релизом')
            record_text = self.adb.shell(f'cat {base}/installed-release.json 2>/dev/null; true')[0]
            record = json.loads(record_text) if record_text.strip() else {}
            record.setdefault('cli_version', record.get('version', 'unknown'))
            if 'commit' in record:
                record.setdefault('cli_commit', record.pop('commit'))
            record.update(version=tag, daemon_version=tag, daemon_sha256=expected, backup=backup)
            with tempfile.TemporaryDirectory() as directory:
                file = Path(directory)/'installed-release.json';file.write_text(json.dumps(record, indent=2))
                self.adb.push(file, base+'/installed-release.json')
            return {**self.yam_status(), 'version': tag, 'backup': backup}
        except Exception as error:
            if stopped:
                try:
                    self.adb.shell(f'set -e; {service} stop; cp -p {backup}/bin/yamd {s.YAMD}; '
                                   f'mv {base}/data {backup}/failed-data; mv {backup}/data {base}/data; '
                                   f'if [ -f {backup}/installed-release.json ]; then cp -p {backup}/installed-release.json {base}/installed-release.json; fi; '
                                   f'{service} start', timeout=60)
                except Exception as rollback:
                    raise s.AdbError(f'Ошибка обновления и отката. Резервная копия: {backup}. {rollback}') from error
            raise s.AdbError('Обновление yamd не выполнено: ' + str(error)) from error
        finally:
            lock.release()
            if acquired:
                self.adb.shell(f'rm -f {stage}; rmdir {base}/.web-update-lock', check=False)

    s.Panel.yam_install_daemon = update
    s.Panel.yam_status = status
    s.Panel.yam_start = start
