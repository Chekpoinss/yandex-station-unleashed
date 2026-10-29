import copy
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch
from ring_animations import frame_at,validate_frames
from music_lights import MusicLights

class AnimationsTests(unittest.TestCase):
    def setUp(self):
        self.frames=[{'ms':100,'rgb':[[1,2,3]]*3},{'ms':200,'rgb':[[3,2,1]]*3}]
        self.lights=MusicLights.__new__(MusicLights)
        self.lights.library=SimpleNamespace(data={},lock=threading.RLock(),save=Mock())
        self.lights.lock=threading.RLock();self.lights.config_lock=threading.Lock()
        self.lights.supported=True;self.lights.animations={};self.lights.animation=None;self.lights.reason='waiting';self.lights.release=Mock()
    def test_frame_timing_loop_and_finite_end(self):
        frames=validate_frames(self.frames)
        self.assertEqual(frame_at(frames,0),self.frames[0]['rgb'])
        self.assertEqual(frame_at(frames,.1),self.frames[1]['rgb'])
        self.assertEqual(frame_at(frames,.3,2),self.frames[0]['rgb'])
        self.assertIsNone(frame_at(frames,.6,2))
    def test_invalid_frames_and_duration_are_rejected(self):
        for frames in [[],[{'ms':50,'rgb':[[1,2,3]]*3}],[{'ms':100,'rgb':[[256,2,3]]*3}], [{'ms':10000,'rgb':[[1,2,3]]*3}]*7]:
            with self.assertRaises(ValueError):validate_frames(frames)
    def test_save_does_not_touch_hardware_and_play_does_not_change_fft_preference(self):
        self.lights.settings={'enabled':True}
        self.lights.animation_action({'action':'save','name':'Test','frames':self.frames})
        self.lights.release.assert_not_called()
        self.assertEqual(self.lights.library.data['ring_animations']['Test'],self.frames)
        self.lights.animation_action({'action':'play','name':'Test','loops':2})
        self.assertTrue(self.lights.settings['enabled'])
        self.lights.animation_action({'action':'stop'})
        self.assertIsNone(self.lights.animation);self.lights.release.assert_called_once()
    def test_save_failure_does_not_commit_animation(self):
        self.lights.library.save.side_effect=OSError()
        with self.assertRaises(OSError):self.lights.animation_action({'action':'save','name':'Test','frames':self.frames})
        self.assertEqual(self.lights.animations,{})
        self.assertNotIn('ring_animations',self.lights.library.data)
    def test_alarm_priority_fails_closed_and_cache_is_bounded(self):
        self.lights.alarm_checked=0;self.lights.alarm_reason=None
        adb=Mock();self.lights.library.panel=SimpleNamespace(adb=adb)
        adb.shell.return_value=('ringing|id', '',0)
        with patch('music_lights.time.monotonic',return_value=10):
            self.assertEqual(self.lights.alarm_priority(),'alarm');self.lights.alarm_priority()
        self.assertEqual(adb.shell.call_count,1)
        adb.shell.side_effect=OSError()
        with patch('music_lights.time.monotonic',return_value=11):self.assertEqual(self.lights.alarm_priority(),'alarm-state-unavailable')
