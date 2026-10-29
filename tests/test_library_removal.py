from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import test_playback_integration as playback_fixtures


class LibraryRemovalTests(unittest.TestCase):
    def library(self):
        lib=playback_fixtures.PlaybackIntegrationTests().library('local')
        lib.data['playlists']={'one':['b','a','c'],'two':['b']}
        lib.data['tracks']['b']['favorite']=True
        lib.order.select('b');lib.order.select('c');lib.current='c';lib._sync_order(False)
        return lib

    def test_removes_from_all_lists_and_history_but_keeps_original_file(self):
        lib=self.library()
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'original.mp3';file.write_bytes(b'original audio')
            lib.data['tracks']['b']['file']=str(file)
            lib.remove_track('b')
            self.assertNotIn('b',lib.data['tracks'])
            self.assertNotIn('b',lib.order.history)
            self.assertNotIn('b',lib.queue)
            self.assertEqual(lib.data['playlists'],{'one':['a','c'],'two':[]})
            self.assertEqual(file.read_bytes(),b'original audio')
            self.assertNotIn('file',lib.tracks()['trash'][0])

    def test_restore_recovers_favorites_and_playlist_position_without_playing(self):
        lib=self.library();lib.remove_track('b');lib.play=Mock()
        lib.restore_track('b')
        self.assertTrue(lib.data['tracks']['b']['favorite'])
        self.assertEqual(lib.data['playlists'],{'one':['b','a','c'],'two':['b']})
        self.assertNotIn('b',lib.data['trash']);lib.play.assert_not_called()

    def test_removing_current_stops_only_owned_transport(self):
        for active in (False,True):
            lib=self.library();lib.active=active;lib.action=Mock()
            lib.remove_track('c')
            self.assertEqual(lib.action.call_count,int(active))
            self.assertIsNone(lib.current)

    def test_unlike_failure_does_not_remove_local_track(self):
        lib=self.library();lib.data['tracks']['b'].update(source='yandex',yandex_id='12')
        lib.yandex=Mock();lib.yandex.favorite.side_effect=ValueError('provider failure')
        with self.assertRaises(ValueError):lib.remove_track('b',service=True)
        self.assertIn('b',lib.data['tracks']);self.assertNotIn('trash',lib.data)

    def test_explicit_service_removal_is_undone_with_restore(self):
        lib=self.library();lib.data['tracks']['b'].update(source='yandex',yandex_id='12')
        lib.yandex=Mock();lib.remove_track('b',service=True);lib.restore_track('b')
        self.assertEqual([c.args for c in lib.yandex.favorite.call_args_list],[('12',False),('12',True)])

    def test_disk_failure_keeps_original_membership_and_order(self):
        lib=self.library();before=lib.order.dump();lib.save.side_effect=OSError('disk full')
        with self.assertRaises(OSError):lib.remove_track('b')
        self.assertIn('b',lib.data['tracks']);self.assertEqual(lib.order.dump(),before)
        self.assertEqual(lib.data['playlists']['two'],['b'])
