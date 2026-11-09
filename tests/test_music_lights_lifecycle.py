import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from music_lights import MusicLights


class LedLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.led = MusicLights.__new__(MusicLights)
        self.led.lock = threading.RLock()
        self.led.pipe = None
        self.led.settings = {'enabled': True, 'brightness': 90}
        self.led.native_volume = None
        self.led.native_connection = None
        self.led.yield_until = 0
        self.led.frames_sent = 0
        self.g = SimpleNamespace(ws=object(), state={'aliceState': 'IDLE', 'volume': .5})

    def test_volume_yields_then_recovers_without_changing_preference(self):
        with patch('music_lights.time.monotonic', return_value=10):
            self.assertIsNone(self.led.native_priority(self.g))
            self.g.state['volume'] = .7
            self.assertEqual(self.led.native_priority(self.g), 'volume')
        with patch('music_lights.time.monotonic', return_value=12):
            self.assertIsNone(self.led.native_priority(self.g))
        self.assertTrue(self.led.settings['enabled'])

    def test_reconnect_discards_old_pipe_and_next_frame_opens_one(self):
        self.led.native_priority(self.g)
        old = Mock()
        self.led.pipe = old
        self.g.ws = object()
        self.led.native_priority(self.g)
        old.stdin.close.assert_called_once()
        self.assertIsNone(self.led.pipe)
        pipe = Mock()
        pipe.poll.return_value = None
        self.led.open_pipe = Mock(side_effect=lambda: setattr(self.led, 'pipe', pipe))
        self.led.send_frame([[1, 2, 3]] * 3)
        self.led.send_frame([[3, 2, 1]] * 3)
        self.led.open_pipe.assert_called_once()
        self.assertEqual(pipe.stdin.write.call_count, 2)

    def test_dead_shell_is_replaced_without_checkbox_toggle(self):
        old, new = Mock(), Mock()
        old.poll.return_value = 1
        new.poll.return_value = None
        self.led.pipe = old
        self.led.open_pipe = Mock(side_effect=lambda: setattr(self.led, 'pipe', new))
        self.led.send_frame([[0, 0, 0]] * 3)
        old.stdin.close.assert_called_once()
        new.stdin.write.assert_called_once()

    def test_adb_audio_does_not_require_cloud_connection_for_lights(self):
        self.g.ws = None
        self.g.state['aliceState'] = 'IDLE'
        self.assertIsNone(self.led.native_priority(self.g, adb_playing=True))
        self.g.state['aliceState'] = 'LISTENING'
        self.assertEqual(self.led.native_priority(self.g, adb_playing=True), 'alice')

    def test_voice_and_connection_have_priority(self):
        self.g.state['aliceState'] = 'LISTENING'
        self.assertEqual(self.led.native_priority(self.g), 'alice')
        self.g.ws = None
        self.assertEqual(self.led.native_priority(self.g), 'disconnected')

    def test_frame_command_rechecks_trigger_and_does_not_clear_native_on_exit(self):
        self.led.library = SimpleNamespace(panel=SimpleNamespace(adb=SimpleNamespace(_base=lambda: ['adb'])))
        with patch('music_lights.subprocess.Popen') as popen:
            self.led.open_pipe()
        command = popen.call_args.args[0][-1]
        self.assertLess(command.index('while IFS='), command.index('read -r t'))
        self.assertIn('bits_per_pixel', command)
        self.assertNotIn('trap ', command)


if __name__ == '__main__':
    unittest.main()
