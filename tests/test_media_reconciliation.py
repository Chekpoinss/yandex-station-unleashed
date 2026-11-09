import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from music_library import Library


class NativeReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.lib = Library.__new__(Library)
        self.lib.lock = threading.RLock()
        self.lib.active = self.lib.native_audio = True
        self.lib.current = 'local-track'
        self.lib.phase = 'playing'
        self.lib.offset, self.lib.stream_offset = 40, 40
        self.lib.started = time.monotonic()
        self.lib.native_updated = 0
        self.lib.remote = {}
        self.lib.data = {'tracks': {'local-track': {'duration': 180}}}
        self.lib.save = Mock()
        self.g = SimpleNamespace(ws=object(), updated=time.time(), state={
            'playing': False, 'playerState': {'id': 'local-track', 'progress': 12}})
        self.lib.panel = SimpleNamespace(glagol=self.g)

    def event(self, **state):
        self.g.updated += .1
        self.g.state.update(state)
        self.lib.reconcile_native()

    def test_voice_pause_freezes_real_progress_and_matching_resume_recovers(self):
        self.lib.reconcile_native()
        self.assertEqual(self.lib.phase, 'paused')
        self.assertEqual(self.lib.position(), 52)
        self.event(playing=True)
        self.assertEqual(self.lib.phase, 'playing')
        self.assertAlmostEqual(self.lib.position(), 52, delta=.1)

    def test_other_music_never_resumes_library_or_its_lights(self):
        self.event(playing=True, playerState={'id': 'yandex-track', 'progress': 0})
        self.assertTrue(self.lib.active)  # one stale/mid-transition snapshot is not ownership transfer
        self.event(playing=True, playerState={'id': 'yandex-track', 'progress': 1})
        self.assertFalse(self.lib.active)
        self.assertEqual(self.lib.phase, 'paused')
        self.assertEqual(self.lib.data['music_source'], 'yandex')
        self.lib.save.assert_called_once()

    def test_changing_foreign_session_resets_handoff_confirmation(self):
        self.event(playing=True, playerState={'id': 'yandex-track', 'progress': 0})
        self.event(playing=True, playerState={'id': 'another-track', 'progress': 0})
        self.assertTrue(self.lib.active)

    def test_voice_activity_alone_does_not_resume(self):
        self.lib.phase = 'paused'
        self.event(aliceState='SPEAKING')
        self.assertEqual(self.lib.phase, 'paused')

    def test_disconnect_freezes_clock_then_matching_session_recovers(self):
        self.g.ws = None
        self.lib.reconcile_native()
        self.assertEqual(self.lib.phase, 'paused')
        self.assertEqual(self.lib.media_reason, 'disconnected')
        self.g.ws = object()
        self.event(playing=True)
        self.assertEqual(self.lib.phase, 'playing')

    def test_loading_does_not_start_on_ack_or_cached_state(self):
        self.lib.phase = 'loading'
        self.event(playing=True)
        self.assertEqual(self.lib.phase, 'loading')

    def test_stale_event_does_not_override_new_play_request(self):
        self.lib.play_requested_at = self.g.updated + 1
        self.lib.reconcile_native()
        self.assertEqual(self.lib.phase, 'playing')

    def test_unchanged_event_does_not_keep_resetting_clock(self):
        self.lib.reconcile_native()
        self.lib.started = 123
        self.lib.reconcile_native()
        self.assertEqual(self.lib.started, 123)

    def test_native_eof_allows_queue_advance(self):
        self.event(playerState={'id': 'local-track', 'progress': 140})
        self.assertTrue(self.lib.native_ended)

    def test_invalid_progress_does_not_poison_position(self):
        self.event(playerState={'id': 'local-track', 'progress': float('nan')})
        self.assertAlmostEqual(self.lib.position(), 40, delta=.1)

    def test_panel_pause_position_survives_native_stop_ack(self):
        self.lib.phase = 'paused'
        self.lib.pause_requested = True
        self.lib.offset = 73
        self.event(playerState={'id': 'local-track', 'progress': 0})
        self.assertEqual(self.lib.position(), 73)


if __name__ == '__main__':
    unittest.main()
