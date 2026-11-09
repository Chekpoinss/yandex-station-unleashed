"""Audio-reactive Micro LEDs from the music stream's low-rate PCM side channel."""
import array
import colorsys
import math
import subprocess
import threading
import time
import copy
import atexit
import socket
import struct
from light_profiles import DEFAULTS, settings as validate_settings, change as change_profile


class AmbientState:
    """Passive Alice state, with no login, speech capture, commands or device files."""
    def __init__(self, adb):
        self.adb, self.port, self.stream = adb, None, None
        self.buffer = b''
        self.state = None
        self.retry_at = 0.

    def close(self):
        if self.stream:
            self.stream.close()
        self.stream = None
        if self.port is not None:
            try:
                subprocess.run(self.adb._base() + ['forward', '--remove', f'tcp:{self.port}'],
                               capture_output=True, timeout=3,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.port, self.state, self.buffer = None, None, b''

    @staticmethod
    def decode(body):
        from native_alarms import fields, one
        try:
            outer = fields(body)
            if outer.get(253) != [3]:
                return None
            state = one(fields(one(outer, 252, bytes)), 1, int)
            return state if state in (0, 1, 2, 3) else None
        except (ValueError, TypeError):
            return None

    def priority(self):
        try:
            if self.stream is None:
                if time.monotonic() < self.retry_at:
                    return 'disconnected'
                self.port = self.adb.forward(9875)
                self.stream = socket.create_connection(('127.0.0.1', self.port), timeout=2)
                self.stream.setblocking(False)
            for _ in range(4):
                try:
                    part = self.stream.recv(65536)
                except BlockingIOError:
                    break
                if not part:
                    raise ConnectionError('Alice state channel closed')
                self.buffer += part
                while len(self.buffer) >= 4:
                    size = struct.unpack('<I', self.buffer[:4])[0]
                    if not 0 < size <= 256 * 1024:
                        raise ValueError('Invalid Alice state frame')
                    if len(self.buffer) < size + 4:
                        break
                    value = self.decode(self.buffer[4:size + 4])
                    self.buffer = self.buffer[size + 4:]
                    if value is not None:
                        self.state = value
                if len(self.buffer) > 256 * 1024 + 4:
                    raise ValueError('Alice state buffer exceeds limit')
            return 'disconnected' if self.state is None else 'alice' if self.state else None
        except Exception:
            self.close()
            self.retry_at = time.monotonic() + 2
            return 'disconnected'


def band_levels(raw):
    samples = array.array('h', raw[:len(raw) // 2 * 2])
    if not samples:
        return [0., 0., 0.]
    # Three broad bands at 8 kHz, with first-order lowpass separation.
    a = (2 * math.pi * 220) / (8000 + 2 * math.pi * 220)
    b = (2 * math.pi * 1600) / (8000 + 2 * math.pi * 1600)
    low = midlow = 0.
    power = [0., 0., 0.]
    for v in samples:
        x = v / 32768
        low += a * (x - low)
        midlow += b * (x - midlow)
        for i, value in enumerate((low, midlow - low, x - midlow)):
            power[i] += value * value
    return [math.sqrt(v / len(samples)) for v in power]


class BassPulse:
    """Fast kick attack against a slowly moving bass floor, at 10 frames/s."""
    def __init__(self):
        self.floor = 0.
        self.pulse = 0.

    def update(self, bass):
        bass = max(0., bass)
        onset = max(0., bass - self.floor * 1.3 - .008)
        attack = min(1., onset * 7)
        self.pulse = max(attack, self.pulse * .62)
        self.floor += .12 * (bass - self.floor)
        if bass < .002:
            self.pulse *= .5
        return self.pulse


def frame_for(levels, cap, position=0, bass_pulse=0, palette=None):
    energy = [min(1., max(0., x - .0015) * 3) ** .65 for x in levels]
    total = sum(energy) or 1
    # Slow hue travel plus spectral balance avoids a permanently saturated mix.
    hue = (position / 13 + (energy[2] - energy[0]) / total * .22) % 1
    out = []
    for i in range(3):
        pulse = max(0., min(1., bass_pulse))
        colour = colorsys.hsv_to_rgb((hue + i * .13) % 1, .95 - .45 * pulse, 1.)
        if palette:
            point = ((position / 13 + i * .13) % 1) * len(palette)
            start, fraction = int(point), point % 1
            first, second = palette[start], palette[(start + 1) % len(palette)]
            rgb = [(int(first[n:n+2], 16) * (1 - fraction) + int(second[n:n+2], 16) * fraction) / 255
                   for n in (1, 3, 5)]
            colour = [value + (1 - value) * .3 * pulse for value in rgb]
        # Reserve headroom so kicks remain visible even in a loud master.
        level = min(1., (energy[i] * .75 + max(energy) * .25) * .72 + .65 * pulse)
        out.append([round(cap * level * c) for c in colour])
    return out


class MusicLights:
    def __init__(self, library, supported):
        self.library = library
        self.supported = supported
        try:
            self.settings = validate_settings(DEFAULTS, library.data.get('music_lights', {}))
        except (ValueError, TypeError):
            self.settings = copy.deepcopy(DEFAULTS)
        self.profiles = copy.deepcopy(library.data.get('light_profiles', {}))
        if not isinstance(self.profiles, dict):
            self.profiles = {}
        self.profiles = {name: value for name, value in self.profiles.items() if isinstance(name, str) and isinstance(value, dict)}
        self.selected_profile = library.data.get('light_profile_selected')
        if not isinstance(self.selected_profile, str) or self.selected_profile not in self.profiles:
            self.selected_profile = None
        from ring_animations import validate_name, validate_frames
        self.animations = {}
        saved_animations = library.data.get('ring_animations')
        for name, frames in (saved_animations if isinstance(saved_animations, dict) else {}).items():
            try:
                self.animations[validate_name(name)] = validate_frames(frames)
            except (ValueError, TypeError):
                pass
        self.animation = None
        self.effect_monitor = None
        atexit.register(self.close_effect_monitor)
        self.alarm_checked = 0.
        self.alarm_reason = None
        self.config_lock = threading.Lock()
        self.revision = 0
        self.lock = threading.RLock()
        self.file = None
        self.token = None
        self.offset = 0
        self.pipe = None
        self.error = None
        self.levels = [0., 0., 0.]
        self.bass = BassPulse()
        self.last_frame = [[0, 0, 0]] * 3
        self.frames_sent = 0
        self.yield_until = 0.
        self.native_volume = None
        self.native_connection = None
        self.reason = 'waiting'
        threading.Thread(target=self.run, daemon=True, name='music-lights').start()

    def status(self):
        with self.lock:
            return {**self.settings, 'supported': self.supported,
                    'profiles': copy.deepcopy(self.profiles), 'profile': self.selected_profile,
                    'profile_modified': bool(self.selected_profile and self.profiles[self.selected_profile] != {k: v for k, v in self.settings.items() if k != 'enabled'}),
                    'active': self.pipe is not None and self.pipe.poll() is None, 'error': self.error,
                    'reason': self.reason,
                    'frames_sent': self.frames_sent, 'frame': self.last_frame,
                    'bass_pulse': round(self.bass.pulse, 3)}

    def close_effect_monitor(self):
        monitor, self.effect_monitor = self.effect_monitor, None
        if monitor is not None:
            monitor.close()

    def start_native_animation(self, text, seconds):
        import tempfile
        from pathlib import Path
        self.animation_action({'action':'stop'})
        path = '/tmp/webpanel_led_editor_' + __import__('secrets').token_hex(8) + '.led'
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'animation.led'; f.write_text(text, encoding='utf8')
            self.library.panel.adb.push(f, path)
        with self.lock:
            self.release()
            self.animation = {'name': 'editor', 'native': path, 'until': time.monotonic()+seconds, 'started':False}

    def native_animation_tick(self, anim, priority):
        import shlex
        panel = self.library.panel
        pid = '/tmp/webpanel_led_editor.pid'
        if priority or time.monotonic() >= anim['until']:
            if anim.get('started'):
                panel.adb.shell(f'p=$(cat {pid} 2>/dev/null); case "$p" in ""|*[!0-9]*) ;; *) '
                                f'tr "\\000" " " < /proc/$p/cmdline | grep -Fq -- {shlex.quote(anim["native"])} && kill "$p" 2>/dev/null;; esac; '
                                f'rm -f {pid}; true', check=False)
                anim['started'] = False
            if time.monotonic() >= anim['until']:
                panel.adb.shell('rm -f '+shlex.quote(anim['native']),check=False)
                self.animation = None
            self.release();self.reason=priority or 'waiting'
            return
        if not anim.get('started'):
            command = (f'LD_LIBRARY_PATH=/system/vendor/quasar/libs /system/vendor/quasar/led_util {shlex.quote(anim["native"])} '
                       f'>/dev/null 2>&1 & p=$!; echo "$p" > {pid}; wait "$p"')
            panel.detach('led_editor',command);anim['started']=True
        self.reason='animation'

    def animation_status(self):
        with self.lock:
            anim = self.animation
            effect = copy.deepcopy(anim.get('effect')) if anim else None
            if effect is not None:
                effect['remaining'] = max(0, anim['until'] - time.monotonic()) if anim['until'] is not None else None
            return {'supported': self.supported, 'animations': copy.deepcopy(self.animations),
                    'playing': anim['name'] if anim else None, 'reason': self.reason, 'effect': effect}

    def effect_tick(self, anim, now, priority=None):
        """Return to the native writer on expiry; keep desired mode while Alice speaks."""
        from ring_animations import effect_frame
        if anim['until'] is not None and now >= anim['until']:
            if not priority:
                self.send_frame([[0, 0, 0] for _ in range(3)], animation=True)
            self.animation = None
            self.release()
        elif priority:
            self.reason = priority
            self.release()
        else:
            self.send_frame(effect_frame(anim['effect'], now - anim['start']), animation=True)

    def animation_action(self, data):
        from ring_animations import validate_name, validate_frames
        if not self.supported:
            raise ValueError('Редактор логотипа поддерживается на Лайт 1')
        action = data.get('action')
        if action == 'effect':
            from ring_animations import effect_settings
            with self.lock:
                current = self.animation.get('effect') if self.animation else None
                selected = effect_settings(data, current)
            # Continuous effects and audio-reactive lighting share one worker.
            # Selecting an effect switches off FFT; enabling FFT ends the effect.
            if self.settings['enabled']:
                self.configure({'enabled': False})
            with self.lock:
                if self.animation and self.animation.get('native'):
                    self.animation['until'] = 0
                    self.native_animation_tick(self.animation, 'cancelled')
                now = time.monotonic()
                self.animation = {'name': selected['mode'], 'effect': selected, 'start': now,
                                  'until': now + selected['seconds'] if selected['seconds'] else None}
            return self.animation_status()
        if action == 'stop':
            with self.lock:
                if self.animation and self.animation.get('native'):
                    self.animation['until']=0
                    self.native_animation_tick(self.animation, 'cancelled')
                self.animation = None
                self.release()
            return self.animation_status()
        name = validate_name(data.get('name'))
        with self.config_lock:
            with self.lock:
                updated = copy.deepcopy(self.animations)
                if action == 'save':
                    if name not in updated and len(updated) >= 30:
                        raise ValueError('Можно сохранить до 30 анимаций')
                    updated[name] = validate_frames(data.get('frames'))
                elif action == 'delete':
                    updated.pop(name, None)
                elif action == 'play':
                    loops = data.get('loops', 1)
                    if name not in updated or type(loops) is not int or not 1 <= loops <= 10:
                        raise ValueError('Выберите сохранённую анимацию и 1–10 повторов')
                    self.animation = {'name': name, 'frames': copy.deepcopy(updated[name]),
                                      'start': time.monotonic(), 'loops': loops}
                    return self.animation_status()
                else:
                    raise ValueError('Неизвестное действие анимации')
            # Keep the normal library -> lights lock order used by playback.
            with self.library.lock:
                before = self.library.data.get('ring_animations')
                self.library.data['ring_animations'] = updated
                try:
                    self.library.save()
                except Exception:
                    if before is None:
                        self.library.data.pop('ring_animations', None)
                    else:
                        self.library.data['ring_animations'] = before
                    raise
            with self.lock:
                self.animations = updated
                if self.animation and self.animation['name'] == name:
                    self.animation = None
                    self.release()
        return self.animation_status()

    def configure(self, data):
        if not self.supported:
            raise ValueError('Светомузыка поддерживается на профиле Лайт 1')
        with self.config_lock:
            with self.lock:
                updated, profiles, selected = change_profile(self.settings, self.profiles, self.selected_profile, data)
                if updated['enabled'] and self.animation and self.animation.get('effect'):
                    self.animation = None
                    self.release()
                if updated['analysis'] != self.settings['analysis']:
                    self.bass = BassPulse()
                    self.levels = [0., 0., 0.]
                self.settings, self.profiles, self.selected_profile = updated, profiles, selected
                self.revision += 1
                self.error = None
            with self.library.lock:
                self.library.data['music_lights'] = copy.deepcopy(updated)
                self.library.data['light_profiles'] = profiles
                self.library.data['light_profile_selected'] = selected
                self.library.save()
        return self.status()

    def attach(self, path, token, offset):
        with self.lock:
            old = self.file
            self.file, self.token, self.offset = path, token, offset
            self.levels = [0., 0., 0.]
            self.bass = BassPulse()
        if old and old != path:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass

    def cleanup(self, path):
        with self.lock:
            current = self.file == path
        if not current:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def open_pipe(self):
        adb = self.library.panel.adb
        command = (
            'test "$(cat /sys/class/graphics/fb0/name)" = "YNDX LED ring" || exit 1; '
            'test "$(cat /sys/class/graphics/fb0/virtual_size)" = "3,1" || exit 1; '
            'test "$(cat /sys/class/graphics/fb0/bits_per_pixel)" = 24 || exit 1; '
            'while IFS= read -r f; do '
            # Quasar can reset triggers without closing our ADB shell. Reclaim
            # only while sending music frames, after yielding system priority.
            'for n in red green blue; do for i in 1 2 3; do '
            'p=/sys/class/leds/micro:$n:ring$i/trigger; read -r t < "$p"; '
            'case "$t" in *"[led_ring_fb]"*) ;; *) echo led_ring_fb > "$p" || exit 1;; esac; '
            'done; done; echo -e -n "$f" > /dev/fb0 || break; done')
        self.pipe = subprocess.Popen(adb._base() + ['shell', command], stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def release(self):
        with self.lock:
            pipe, self.pipe = self.pipe, None
        if pipe:
            try:
                pipe.stdin.close()
                pipe.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pipe.kill()
                pipe.wait()

    def system_indication(self):
        # A bounded quiet interval for the transient volume indicator, extended
        # by each new volume event. This does not change the desired LED mode.
        with self.lock:
            self.yield_until = time.monotonic() + 1.

    def native_priority(self, glagol, adb_playing=False):
        """Yield voice/system events and invalidate pipes after native reconnect."""
        connection = getattr(glagol, 'ws', None)
        state = (getattr(glagol, 'state', None) or {})
        volume = state.get('volume')
        if self.native_volume is not None and volume != self.native_volume:
            self.system_indication()
        self.native_volume = volume
        if connection is not self.native_connection:
            self.release()
            self.native_connection = connection
        alice = str(state.get('aliceState', 'IDLE')).upper()
        if not connection and not adb_playing:
            return 'disconnected'
        if alice not in ('IDLE', 'NONE', 'STANDBY', ''):
            return 'alice'
        if time.monotonic() < self.yield_until:
            return 'volume'
        return None

    def alarm_priority(self):
        if time.monotonic() - self.alarm_checked >= .5:
            self.alarm_checked = time.monotonic()
            try:
                out, _, rc = self.library.panel.adb.shell(
                    'if [ -f /tmp/webpanel_alarm.state ]; then cat /tmp/webpanel_alarm.state; else echo idle; fi; '
                    'for f in /tmp/webpanel_scene.*.active; do [ ! -f \"$f\" ] || echo scene-active; done; '
                    'echo @assistant; head -c 65536 /tmp/webpanel_assistant.json 2>/dev/null; true',
                    check=False, timeout=2)
                meta,_,assistant=out.partition('@assistant')
                self.alarm_reason = ('alarm' if meta.strip().startswith('ringing') else 'scene' if 'scene-active' in meta else None) if rc == 0 else 'alarm-state-unavailable'
                if assistant.strip():
                    try:
                        info=__import__('json').loads(assistant)
                        if info.get('state') in ('thinking','speaking'):self.alarm_reason=self.alarm_reason or 'assistant'
                    except ValueError:pass
            except Exception:
                self.alarm_reason = 'alarm-state-unavailable'
        return self.alarm_reason

    def send_frame(self, frame, animation=False):
        wire = ''.join(f'\\x{v:02x}' for r, g, b in frame for v in (b, g, r)) + '\n'
        with self.lock:
            if not self.settings['enabled'] and not animation:
                return
            if self.pipe and self.pipe.poll() is not None:
                self.release()
            if not self.pipe:
                self.open_pipe()
            self.pipe.stdin.write(wire.encode('ascii'))
            self.pipe.stdin.flush()
            self.last_frame = frame
            self.frames_sent += 1
            self.reason = 'animation' if animation else 'reactive'
            self.error = None

    def run(self):
        while True:
            time.sleep(.05 if self.animation and self.animation.get('effect') else .02 if self.animation else .1)
            try:
                with self.library.lock:
                    phase = self.library.phase
                    playing = self.library.active and phase == 'playing'
                    token = getattr(self.library, 'play_token', None)
                    position = self.library.position()
                panel = self.library.panel
                glagol = getattr(panel, 'glagol', None)
                priority = self.native_priority(glagol, bool(getattr(self.library, 'adb_audio', None)) and bool(getattr(getattr(panel, 'conn', None), 'online', False)))
                if self.animation and self.animation.get('effect') and priority == 'disconnected':
                    if self.effect_monitor is None:
                        self.effect_monitor = AmbientState(panel.adb)
                    priority = self.effect_monitor.priority()
                    if not priority and time.monotonic() < self.yield_until:
                        priority = 'volume'
                elif self.effect_monitor is not None:
                    self.effect_monitor.close()
                    self.effect_monitor = None
                alarm = getattr(getattr(panel, 'metrics', None), 'ringing', None)
                with self.lock:
                    path, offset = self.file, self.offset
                    enabled = self.supported and self.settings['enabled']
                    cap = self.settings['brightness']
                    analysis = self.settings['analysis']
                    sensitivity = self.settings['sensitivity']
                    smoothing = self.settings['smoothing']
                    weights = list(self.settings['weights'])
                    palette = list(self.settings['palette'])
                    revision = self.revision
                    ready = token == self.token
                if self.animation or (enabled and playing):
                    priority = priority or self.alarm_priority()
                # One LED writer for FFT and custom animation. System/Alice/alarm always wins.
                with self.lock:
                    anim = self.animation
                    if anim and anim.get('effect'):
                        self.effect_tick(anim, time.monotonic(), priority or ('alarm' if alarm else None))
                        continue
                    if anim and anim.get('native'):
                        self.native_animation_tick(anim, priority or ('alarm' if alarm else None))
                        continue
                    if anim:
                        from ring_animations import frame_at
                        frame = frame_at(anim['frames'], time.monotonic() - anim['start'], anim['loops'])
                        if frame is None or time.monotonic() >= anim.get('until', float('inf')):
                            self.animation = None
                            self.release()
                        elif not priority and not alarm:
                            self.send_frame(frame, animation=True)
                            continue
                if not (enabled and playing and ready and path and not priority and not alarm):
                    self.reason = priority or ('alarm' if alarm else 'waiting')
                    self.release()
                    continue
                # The side-channel is decoded in the same FFmpeg process as the
                # audio stream, so SoundCloud is fetched only once.
                index = max(0, round((position - offset) * 8000))
                try:
                    with path.open('rb') as f:
                        f.seek(index * 2)
                        raw = f.read(1600)
                except OSError:
                    self.reason = 'buffering'
                    self.release()
                    continue
                if len(raw) < 160:
                    self.reason = 'buffering'
                    self.release()
                    continue
                if analysis == 'fft':
                    from music_spectrum import fft_bands
                    measured = fft_bands(raw)
                else:
                    measured = band_levels(raw)
                current = [value * sensitivity * weight for value, weight in zip(measured, weights)]
                with self.lock:
                    if revision != self.revision:
                        continue
                    self.levels = [old + (.7 if value > old else 1 - smoothing) * (value - old)
                                   for old, value in zip(self.levels, current)]
                    pulse = self.bass.update(current[0])
                    frame = frame_for(self.levels, cap, position, pulse, palette)
                    self.send_frame(frame)
            except Exception as exc:
                self.release()
                self.error = 'Не удалось управлять подсветкой: ' + str(exc)[:180]
                time.sleep(1)
