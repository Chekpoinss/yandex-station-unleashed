"""Explicit hardware profiles. Lite 2 keeps its original implementation by default."""
import os
import json
import shlex
import tempfile
import threading
import time
from pathlib import Path

PROFILES = {
    'lite1': dict(name='Станция Лайт', platform='micro', clock=False, boot=False, alarms=True, light=True),
    'lite2': dict(name='Станция Лайт 2', platform='plum', clock=True, boot=True, alarms=True, light=True),
    'station2': dict(name='Станция 2', platform='yandexmidi', clock=False, boot=False, alarms=False, light=True),
    'mini3pro': dict(name='Станция Мини 3 Про', platform='pickle', clock=True, boot=False, alarms=False, light=True),
    # Common ADB features and a distinct 3D model; LED layout has not been verified on hardware.
    'mini3': dict(name='Станция Мини 3', platform='bergamot', clock=False, boot=False, alarms=False, light=False, experimental=True),
}


def mini3pro_ring_script(rgb=None, leds='/sys/class/leds', state='/tmp/webpanel_ring.state'):
    """Snapshot brightness AND triggers; reset repairs the old detached `none` state.

    Pickle RGB outputs use ledfb-ring. Separate pickle:ring:sync[01] LEDs use
    ledfb-ring-sync and are deliberately never written here.
    """
    names = [f'pickle:{color}:ring{i}' for i in range(16) for color in ('red','green','blue')]
    lines = ['set -e', 'D='+shlex.quote(leds), 'S='+shlex.quote(state), 'N='+shlex.quote(' '.join(names)),
             'for n in $N; do test -r "$D/$n/brightness"; '
             'case " $(cat "$D/$n/trigger") " in *" ledfb-ring "*|*" [ledfb-ring] "*) ;; *) exit 1;; esac; done']
    if rgb is not None:
        lines += ['if [ ! -s "$S" ]; then', 'T="$S.$$"; trap \'rm -f "$T"\' 0',
                  'for n in $N; do v=$(cat "$D/$n/brightness"); '
                  't=$(sed -n \'s/.*\\[\\([^]]*\\)\\].*/\\1/p\' "$D/$n/trigger"); '
                  'test -n "$t"; printf "%s %s %s\\n" "$n" "$v" "$t"; done > "$T"',
                  '[ "$(wc -l < "$T")" -eq 48 ]; mv "$T" "$S"', 'fi']
        for i in range(16):
            for color,value in zip(('red','green','blue'),rgb):
                lines.append(f'echo {max(0,min(255,int(value)))} > "$D/pickle:{color}:ring{i}/brightness"')
    else:
        lines += ['if [ -s "$S" ]; then [ "$(wc -l < "$S")" -eq 48 ];',
                  '  for n in $N; do row=$(awk -v name="$n" \'$1 == name {print $2, $3}\' "$S"); '
                  'set -- $row; [ "$#" -eq 2 ]; case "$1" in \'\'|*[!0-9]*) exit 1;; esac; [ "$1" -le 255 ]; '
                  't=$2; [ "$t" != none ] || t=ledfb-ring; '
                  'case " $(cat "$D/$n/trigger") " in *" $t "*|*" [$t] "*) ;; *) exit 1;; esac; done; fi',
                  'for n in $N; do',
                  '  t=ledfb-ring',
                  '  if [ -s "$S" ]; then',
                  '    row=$(awk -v name="$n" \'$1 == name {print $2, $3}\' "$S"); set -- $row; [ "$#" -eq 2 ]',
                  '    v=$1; t=$2; case "$v" in \'\'|*[!0-9]*) exit 1;; esac; [ "$v" -le 255 ]',
                  '    echo "$v" > "$D/$n/brightness"',
                  '    case "$t" in none) t=ledfb-ring;; esac',
                  '  fi',
                  '  echo "$t" > "$D/$n/trigger"',
                  'done', 'rm -f "$S"']
    return '\n'.join(lines)


def install(s, model='lite2', auto=False):
    s.Panel.btout_supported = model == 'lite2'
    profile = dict(PROFILES[model], id=model, auto=auto)
    profile['light_note'] = ('Подсветка этой модели ещё не проверена.' if not profile['light'] else
                             'Анимация Алисы может заменить выбранный цвет.')
    s.GLAGOL_PLATFORM = profile['platform']
    s.LED_PREFIX = profile['platform']
    original_get, original_post = s.Handler.do_GET, s.Handler.do_POST

    def do_get(self):
        if model != 'lite2' and self.path.split('?')[0] == '/manifest.webmanifest':
            if not self._guard('/manifest.webmanifest'):
                manifest = json.loads((s.STATIC/'manifest.webmanifest').read_text(encoding='utf8'))
                manifest.update(name=profile['name'], short_name=profile['name'], description='Панель управления: '+profile['name'])
                self._send(200, json.dumps(manifest,ensure_ascii=False).encode('utf8'), s.MIME['.webmanifest'])
            return
        if self.path.split('?')[0] == '/api/profile':
            if not self._guard('/api/profile'):
                self._send(200, profile)
            return
        original_get(self)

    def do_post(self):
        path = self.path.split('?')[0]
        disabled = (not profile['boot'] and (path.startswith('/api/clock/boot') or path.startswith('/api/boot/')) or
                    not profile['alarms'] and path.startswith('/api/alarms') or
                    model != 'lite2' and path in ('/api/footprint/reset', '/api/backup/restore'))
        if disabled:
            if not self._guard(path):
                self._send(400, {'error': 'Эта операция загрузки/восстановления не поддерживается профилем ' + model})
            return
        original_post(self)

    s.Handler.do_GET, s.Handler.do_POST = do_get, do_post
    if model == 'lite2':
        return  # No changes to the original Lite 2 hardware, boot hook, YAM or alarm code.

    P = s.Panel
    original_shell = s.Adb.shell

    def shell(self, command, timeout=15, check=True):
        # Older adbd can print a root-shell banner and lose the remote exit status.
        marker = '__PANEL_' + os.urandom(8).hex() + '__'
        wrapped = (f"printf '{marker}\\n'; sh -c {shlex.quote(command)}; rc=$?; "
                   f"printf '\\n{marker}RC=%s\\n' \"$rc\"")
        out, err, _ = original_shell(self, wrapped, timeout, False)
        out = out.replace('\r', '')
        if marker + '\n' not in out:
            raise s.AdbError('Нет ответа shell колонки')
        body = out.split(marker + '\n', 1)[1]
        body, separator, result = body.rpartition('\n' + marker + 'RC=')
        if not separator or not result.strip().isdigit():
            raise s.AdbError('Команда колонки не завершилась')
        code = int(result.strip())
        if check and code:
            raise s.AdbError(s.strip_ansi(err or body).strip()[-500:] or f'Код завершения: {code}')
        return body, err, code

    s.Adb.shell = shell

    def alive(self):
        try:
            return self.adb.shell('echo ok', timeout=4)[0].strip() == 'ok'
        except (s.AdbError, s.subprocess.TimeoutExpired):
            return False
    s.Connection._alive = alive

    def version(self):
        if self._version is None:
            out, _, _ = self.adb.shell('cat /system/vendor/quasar/version_full')
            device, _, _ = self.adb.shell(f'{s.QRUN}/maind --device-id 2>/dev/null | tail -1', timeout=20)
            did = device.replace('Device ID:', '').strip()
            self._version = {'quasar_version': out.strip(), 'device_id': did, 'model': model}
            self._configure_glagol(did)
        return self._version
    P.version = version

    def unsupported(self=None, *args, **kwargs):
        raise s.AdbError('Функция не проверена для профиля ' + model)

    if not profile['clock']:
        for method in ('clock', 'display_set', 'play_clock', 'clock_frames'):
            setattr(P, method, unsupported)
    # Never connect the Light 2 rootkeeper hook to a different model.
    P._ensure_hook = unsupported
    if model == 'lite1':
        from native_alarms import NativeAlarms
        original_alarms = P.alarms
        native_reader_lock = threading.Lock()

        def alarms(self):
            result = original_alarms(self)
            with native_reader_lock:
                if not hasattr(self, '_native_alarm_reader'):
                    self._native_alarm_reader = NativeAlarms(self.adb)
            return {**result, 'native': self._native_alarm_reader.read()}
        P.alarms = alarms
        # Micro has no rootkeeper.sh. Its own init entry starts only alarmd;
        # Lite 2 display/boot hooks remain disabled.
        s.ALARM_DEFAULT_SOUND = 'common/timer.opus'
        original_alarm_script = s.alarm_script

        def alarm_script():
            return original_alarm_script().replace(b'/sys/class/leds/plum:', b'/sys/class/leds/micro:')
        s.alarm_script = alarm_script

        def pc_tz():
            import importlib.resources
            import sys
            packages = str(Path(s.__file__).parent / 'runtime' / 'packages')
            if packages not in sys.path:
                sys.path.insert(0, packages)
            return 'Europe/Moscow', importlib.resources.files('tzdata').joinpath('zoneinfo/Europe/Moscow').read_bytes()
        P._pc_tz = staticmethod(pc_tz)

        init_file = '/etc/init.d/S99zpanelalarm'
        marker = '# webpanel: Micro alarm service'
        service = ('#!/bin/sh\n' + marker + '\n'
                   'case "$1" in\nstart)\n'
                   f'  [ -f {s.ALARM_CONF} ] && [ -x {s.ALARMD} ] || exit 0\n'
                   f'  p=$(cat {s.ALARM_SD} 2>/dev/null)\n'
                   '  [ -n "$p" ] && kill -0 "$p" 2>/dev/null && exit 0\n'
                   f'  start-stop-daemon -S -b -m -p {s.ALARM_SD} -x /bin/sh -- {s.ALARMD}\n'
                   '  ;;\nstop)\n'
                   f'  [ -s {s.ALARM_SD} ] && kill "$(cat {s.ALARM_SD})" 2>/dev/null\n'
                   '  ;;\nesac\nexit 0\n')

        def ensure_alarm_hook(self):
            _, _, exists = self.adb.shell(f'test -e {init_file}', check=False)
            if exists == 0:
                out, _, _ = self.adb.shell(f'cat {init_file}')
                if out.replace('\r', '') == service:
                    return
                if marker not in out:
                    raise s.AdbError('Файл запуска будильника уже занят другой программой')
            raise s.AdbError('Автозапуск будильника отсутствует или отличается. '
                             'Автоматическая запись в системный раздел отключена.')

        def drop_alarm_hook(self):
            # The existing service exits when /data alarm files are absent.
            # Uninstalling user data must never rewrite the read-only root.
            return
        P._ensure_hook = ensure_alarm_hook
        P._drop_hook_if_unused = drop_alarm_hook
    for method in ('yam_install_daemon', 'yam_update_daemon', 'yam_autostart'):
        if hasattr(P, method):
            setattr(P, method, unsupported)
    P.boot_status = lambda self: dict(supported=False, custom=False, mounted=False, sound_supported=False,
                                    sound_custom=False, sound_mounted=False, hook=False, no_boot_sound=True, always=False)

    # Existing community YAM installations on the new profiles use this layout.
    old = s.YAM_DIR
    for name, value in list(vars(s).items()):
        if name.startswith('YAM') and isinstance(value, str):
            setattr(s, name, value.replace(old, '/data/local/yam'))

    def patterns(self):
        if model != 'mini3pro':
            return {'ring': [], 'clock': []}
        out, _, _ = self.adb.shell(f'find {s.QUASAR}/ledpatterns/clock -maxdepth 1 -type f 2>/dev/null; true')
        return {'ring': [], 'clock': [Path(line).name for line in out.splitlines()]}
    P.patterns = patterns

    def play_pattern(self, kind, name, seconds):
        if kind != 'clock' or model != 'mini3pro':
            return unsupported()
        if name not in self.patterns()['clock']:
            raise s.AdbError('Нет такой анимации')
        return self.play_clock(s.decode_clock_pattern(name, self.adb.read_bytes(f'{s.QUASAR}/ledpatterns/clock/{name}')), seconds)
    P.play_pattern = play_pattern

    def leds(self):
        if model == 'lite1':
            names = [f'micro:{c}:ring{i}/brightness' for i in (1, 2, 3) for c in ('red', 'green', 'blue')]
            out, _, _ = self.adb.shell('cd /sys/class/leds && cat ' + ' '.join(names))
            values = [int(v) for v in out.split()]
            if len(values) != 9:
                raise s.AdbError('Не удалось прочитать подсветку Лайт')
            return {'v': [0] * 36, 'ring': [values[i:i+3] for i in (0, 3, 6)]}
        if model == 'mini3':
            return {'v': [0] * 36, 'ring': [[0, 0, 0]] * 3}
        if model == 'station2':
            temporary = '/tmp/webpanel_fb_' + os.urandom(8).hex()
            try:
                self.adb.shell(f'head -c 356 /dev/fb0 > {temporary}')
                blob = self.adb.read_bytes(temporary)
            finally:
                self.adb.shell(f'rm -f {temporary}', check=False)
            if len(blob) != 356:
                raise s.AdbError('Неожиданный размер световой панели')
            # gowin_led_device consumes RGBX despite its advertised BGRX offsets.
            pixels = [[blob[i], blob[i + 1], blob[i + 2]] for i in range(0, 356, 4)]
            representative = [[round(sum(pixel[k] for pixel in pixels[a:b]) / (b-a)) for k in range(3)]
                              for a, b in ((0, 30), (30, 60), (60, 89))]
            return {'v': [0] * 36, 'ring': representative, 'ring_all': pixels}
        names = [f'pickle:white:clock{name}' for name in s.CLOCK_LEDS]
        names += [f'pickle:{color}:ring{i}' for i in range(16) for color in ('red', 'green', 'blue')]
        out, _, _ = self.adb.shell('cd /sys/class/leds; ' + '; '.join(f'cat {name}/brightness' for name in names))
        values = [int(value) for value in out.split() if value.isdigit()]
        if len(values) != 84:
            raise s.AdbError('Неполное состояние светодиодов')
        ring = [values[36+i*3:39+i*3] for i in range(16)]
        return {'v': values[:36], 'ring': [ring[i] for i in (0, 5, 10)], 'ring_all': ring}
    P.clock_state = leds

    class LedStream:
        def __init__(self, adb):
            self.adb, self.at, self.cached, self.lock = adb, 0, {'v': None}, threading.Lock()
        def get(self):
            with self.lock:
                if time.monotonic() - self.at > .4:
                    self.cached, self.at = leds(self), time.monotonic()
                return self.cached
    s.LedStream = LedStream

    def ring(self, data):
        if not profile['light']:
            return unsupported()
        rgb = [max(0, min(255, int(data.get(color, 0)))) for color in ('red', 'green', 'blue')]
        if model == 'lite1':
            names = [f'micro:{c}:ring{i}' for i in (1, 2, 3) for c in ('red', 'green', 'blue')]
            cmd = 'test "$(cat /sys/class/graphics/fb0/virtual_size)" = "3,1" && test "$(cat /sys/class/graphics/fb0/bits_per_pixel)" = 24 || exit 1; '
            cmd += '; '.join(f'echo led_ring_fb > /sys/class/leds/{n}/trigger' for n in names)
            self.adb.shell(cmd + '; ' + s.ring_fb_cmd([rgb] * 3))
        elif model == 'mini3pro':
            self.adb.shell(mini3pro_ring_script(rgb))
        else:
            self.adb.shell('test "$(cat /sys/class/graphics/fb0/virtual_size)" = "89,1" && '
                           'test "$(cat /sys/class/graphics/fb0/bits_per_pixel)" = 32 || exit 1; '
                           '[ -f /tmp/webpanel_ring.before ] || head -c 356 /dev/fb0 > /tmp/webpanel_ring.before')
            with tempfile.TemporaryDirectory() as directory:
                frame = Path(directory) / 'frame'
                frame.write_bytes(bytes((rgb[0], rgb[1], rgb[2], 0)) * 89)
                self.adb.push(frame, '/tmp/webpanel_ring.frame')
            self.adb.shell('cat /tmp/webpanel_ring.frame > /dev/fb0')
        return {'ok': True}
    P.ring = ring

    def reset_ring(self):
        if model == 'lite1':
            return self.ring({'red': 0, 'green': 0, 'blue': 0})
        if model == 'mini3':
            return {'ok': True}
        if model == 'mini3pro':
            command = mini3pro_ring_script()
        else:
            command = 'if [ -s /tmp/webpanel_ring.before ]; then cat /tmp/webpanel_ring.before > /dev/fb0; rm -f /tmp/webpanel_ring.before; fi'
        self.adb.shell(command)
        return {'ok': True, 'leds': self.clock_state()['ring']}
    P.ring_reset = reset_ring
    original_stop = P.stop_pattern
    def stop(self, kind=None):
        if kind in (None, 'ring'):
            self.ring_reset()
        if kind in (None, 'clock') and profile['clock']:
            original_stop(self, 'clock')
        return {'ok': True}
    P.stop_pattern = stop

    if model in ("station2", "mini3pro"):
        try:
            import managed_yam
        except ImportError:     # архив обновлён кодом 0.1.3, а он новые модули не копирует
            print('Нет managed_yam.py — обновление yamd из панели для этой модели недоступно')
        else:
            managed_yam.install(s)
