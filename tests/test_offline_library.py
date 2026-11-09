import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import unittest
import test_playback_integration as fixtures


class OfflineLibraryTests(unittest.TestCase):
    def offline(self, folder):
        lib = fixtures.PlaybackIntegrationTests().library('local')
        lib.panel.glagol.ws = None
        lib.panel.glagol.state = {}
        lib.panel.ffmpeg = 'ffmpeg'
        lib.root = Path(folder)
        return lib

    def test_local_play_pause_seek_and_resume_without_cloud_connection(self):
        with tempfile.TemporaryDirectory() as td, patch('audio_stream.AdbPcmPlayer') as transport:
            lib = self.offline(td)
            lib.kill = lib.__class__.kill.__get__(lib)
            result = lib.play('b')
            self.assertEqual(result['phase'], 'playing')
            self.assertFalse(lib.native_audio)
            self.assertEqual(lib.current, 'b')
            lib.panel.glagol.send.assert_not_called()
            lib.action({'action': 'seek', 'position': 4})
            self.assertEqual(transport.call_args.args[3], 4)
            lib.action({'action': 'pause'})
            self.assertEqual(lib.phase, 'paused')
            transport.return_value.close.assert_called()
            position = lib.offset
            lib.action({'action': 'resume'})
            self.assertEqual(lib.phase, 'playing')
            self.assertEqual(transport.call_args.args[3], position)
            lib.panel.glagol.send.assert_not_called()

    def test_actual_decoder_error_is_not_reported_as_account_requirement(self):
        with tempfile.TemporaryDirectory() as td, patch('audio_stream.AdbPcmPlayer', side_effect=ValueError('FFmpeg: corrupt file')):
            lib = self.offline(td)
            with self.assertRaisesRegex(ValueError, 'corrupt file'):
                lib.play('b')
            self.assertEqual(lib.phase, 'stopped')
            lib.panel.glagol.send.assert_not_called()

    def test_natural_completion_advances_once_and_closes_old_audio(self):
        with tempfile.TemporaryDirectory() as td, patch('audio_stream.AdbPcmPlayer') as transport:
            old, new = Mock(), Mock()
            old.poll.return_value = 0
            new.poll.return_value = None
            transport.side_effect = [old, new]
            lib = self.offline(td)
            lib.play('a')
            lib.playback_tick()
            self.assertEqual(lib.current, 'b')
            old.close.assert_called_once()
            lib.playback_tick()
            self.assertEqual(lib.current, 'b')
            self.assertEqual(transport.call_count, 2)

    def test_cloud_tracks_still_require_their_transport(self):
        with tempfile.TemporaryDirectory() as td:
            lib = self.offline(td)
            lib.data['tracks']['b']['source'] = 'soundcloud'
            with self.assertRaisesRegex(ValueError, 'штатному плееру'):
                lib.play('b')
            lib.resolve.assert_not_called()
