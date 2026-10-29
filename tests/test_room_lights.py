import copy
import math
import struct
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from music_lights import AmbientState, MusicLights
from ring_animations import EFFECTS, effect_frame, effect_settings


def varint(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    return bytes(out) + bytes([value])


def field(number, value):
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    return varint(number << 3 | 2) + varint(len(value)) + value


def alice(state):
    return field(252, field(1, state)) + field(253, 3)


class RoomFrames(unittest.TestCase):
    def test_all_effects_have_three_bounded_rgb_zones(self):
        for mode in EFFECTS:
            for t in (0, .5, 2, 100000):
                frame = effect_frame(effect_settings({'mode': mode}), t)
                self.assertEqual(len(frame), 3)
                self.assertTrue(all(len(c) == 3 and all(type(v) is int and 0 <= v <= 89 for v in c) for c in frame))

    def test_rainbow_separates_zones_rgb_moves_together_and_breathing_changes_level(self):
        rainbow = effect_frame(effect_settings({'mode': 'rainbow'}), 1)
        self.assertEqual(len({tuple(v) for v in rainbow}), 3)
        rgb = effect_frame(effect_settings({'mode': 'rgb'}), 1)
        self.assertEqual(rgb[0], rgb[1])
        settings = effect_settings({'mode': 'breathe'})
        self.assertNotEqual(effect_frame(settings, 0), effect_frame(settings, 1))

    def test_speed_changes_phase_and_static_uses_selected_color(self):
        first = effect_settings({'mode': 'rainbow', 'speed': 2})
        self.assertEqual(effect_frame(first, 1), effect_frame(effect_settings({'mode': 'rainbow'}), 2))
        self.assertEqual(effect_frame(effect_settings({'mode': 'static', 'color': [100, 50, 0], 'brightness': 50}), 1), [[50, 25, 0]] * 3)

    def test_invalid_input_is_rejected_before_settings_mutate(self):
        current = effect_settings({})
        for data in ({'speed': math.nan}, {'brightness': 0}, {'seconds': math.inf},
                     {'seconds': -1}, {'seconds': 601}, {'color': [300, 0, 0]},
                     {'color': [True, 0, 0]}, {'mode': 'shell'}, {'speed': True}):
            before = copy.deepcopy(current)
            with self.assertRaises(ValueError):
                effect_settings(data, current)
            self.assertEqual(current, before)


class RoomLifecycle(unittest.TestCase):
    def setUp(self):
        library = SimpleNamespace(data={}, lock=threading.RLock(), save=Mock())
        with patch('music_lights.threading.Thread'):
            self.lights = MusicLights(library, True)
        self.lights.release = Mock()
        self.lights.send_frame = Mock()

    def start(self, **data):
        with patch('music_lights.time.monotonic', return_value=10):
            return self.lights.animation_action({'action': 'effect', 'mode': 'rainbow', **data})

    def test_until_stopped_survives_many_days_and_status_has_no_infinity(self):
        result = self.start(seconds=0)
        self.assertIsNone(result['effect']['remaining'])
        anim = self.lights.animation
        self.lights.effect_tick(anim, 10 + 86400 * 7)
        self.assertIs(self.lights.animation, anim)
        self.lights.send_frame.assert_called_once()
        self.lights.animation_action({'action': 'stop'})
        self.assertIsNone(self.lights.animation)

    def test_timer_expires_and_does_not_overwrite_alice_on_expiry(self):
        self.start(seconds=5)
        self.lights.effect_tick(self.lights.animation, 14)
        self.assertIsNotNone(self.lights.animation)
        self.lights.send_frame.reset_mock()
        self.lights.effect_tick(self.lights.animation, 15, 'alice')
        self.assertIsNone(self.lights.animation)
        self.lights.send_frame.assert_not_called()

    def test_expiry_clears_last_frame_in_idle_state(self):
        self.start(seconds=1)
        self.lights.effect_tick(self.lights.animation, 11)
        self.lights.send_frame.assert_called_once_with([[0, 0, 0]] * 3, animation=True)
        self.assertIsNone(self.lights.animation)

    def test_system_priority_pauses_effect_then_resumes(self):
        self.start()
        anim = self.lights.animation
        for priority in ('alice', 'alarm', 'volume', 'disconnected'):
            self.lights.effect_tick(anim, 12, priority)
            self.lights.send_frame.assert_not_called()
            self.assertIs(self.lights.animation, anim)
        self.lights.effect_tick(anim, 15)
        self.lights.send_frame.assert_called_once()

    def test_music_and_ambient_effects_share_one_writer(self):
        self.lights.settings['enabled'] = True
        self.start()
        self.assertFalse(self.lights.settings['enabled'])
        self.lights.configure({'enabled': True})
        self.assertIsNone(self.lights.animation)

    def test_invalid_effect_does_not_stop_existing_effect(self):
        self.start()
        anim = self.lights.animation
        with self.assertRaises(ValueError):
            self.start(speed=math.nan)
        self.assertIs(self.lights.animation, anim)


class PassiveState(unittest.TestCase):
    def test_only_native_state_is_used_not_recognition_or_commands(self):
        for state in range(4):
            self.assertEqual(AmbientState.decode(alice(state)), state)
        for data in (b'', b'\xff', field(253, 210) + field(252, field(1, 0)), alice(99)):
            self.assertIsNone(AmbientState.decode(data))

    def test_fragmented_read_has_no_writes_and_unknown_state_fails_closed(self):
        monitor = AmbientState(Mock())
        monitor.stream = Mock()
        monitor.stream.recv.side_effect = [BlockingIOError()]
        self.assertEqual(monitor.priority(), 'disconnected')
        body = alice(0)
        wire = struct.pack('<I', len(body)) + body
        monitor.stream.recv.side_effect = [wire[:3], BlockingIOError()]
        self.assertEqual(monitor.priority(), 'disconnected')
        monitor.stream.recv.side_effect = [wire[3:], BlockingIOError()]
        self.assertIsNone(monitor.priority())
        monitor.stream.sendall.assert_not_called()
        monitor.adb.shell.assert_not_called()
        monitor.adb.push.assert_not_called()
