import unittest
from unittest.mock import Mock
from test_profiles import fresh_server


class AlarmDefaultSoundTests(unittest.TestCase):
    def test_existing_profile_default_has_priority(self):
        s=fresh_server();p=s.Panel(Mock());p.sounds=Mock(return_value=[s.ALARM_DEFAULT_SOUND,'alarm.wav'])
        self.assertEqual(p._alarm_default_path(),s.ALARM_DEFAULT_SOUND)

    def test_timer_opus_fallback_is_selected_without_changing_profile(self):
        s=fresh_server();p=s.Panel(Mock());p.sounds=Mock(return_value=['common/notification.ogg','common/timer.opus'])
        self.assertEqual(p._alarm_default_path(),'common/timer.opus')
        p.adb.assert_not_called()

    def test_missing_audio_fails_before_read_or_write(self):
        s=fresh_server();p=s.Panel(Mock());p.sounds=Mock(return_value=['not-a-sound.txt'])
        with self.assertRaisesRegex(s.AdbError,'нет системных'):p._alarm_default_path()
        p.adb.assert_not_called()
