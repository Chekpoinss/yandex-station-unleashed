import pathlib
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from music_library import Library, bind_panel_music


class PanelMusicAdapterTests(unittest.TestCase):
    def setUp(self):
        self.track = {'id': 'abc', 'title': 'Song', 'artist': 'Artist', 'album': 'Album',
                      'duration': 120, 'cover': '/api/library/cover/abc',
                      'source': 'local', 'favorite': False}
        self.library = SimpleNamespace(
            data={'tracks': {'abc': self.track}}, remote={}, lock=threading.RLock(),
            active=True, current='abc', repeat_mode='None', save=Mock(), action=Mock(), play=Mock())
        self.library.state = Mock(return_value={'track': self.track, 'phase': 'playing',
            'position': 15, 'index': 0, 'queue': ['abc'], 'queue_tracks': [self.track],
            'repeat': False, 'repeat_mode': 'None', 'shuffle': False, 'error': None})
        self.original_music = Mock(return_value={'auth': {'logged_in': False}, 'glagol': {'state': None}})
        self.original_cmd = Mock(return_value={'status': 'SUCCESS'})
        self.panel = SimpleNamespace(music=self.original_music, music_cmd=self.original_cmd,
            music_info=Mock(), music_queue=Mock(), music_like=Mock(), music_dislike=Mock(),
            music_play=Mock(), music_lyrics=Mock(), status=Mock(return_value={'vol_level': 70}),
            set_volume=Mock(return_value={'ok': True}), conn=SimpleNamespace(online=True),
            auth=SimpleNamespace(info=lambda: {'logged_in': False}),
            ymusic=SimpleNamespace(lyrics_by_name=Mock(return_value={'text': 'Lyrics'})))
        bind_panel_music(self.panel, self.library)

    def test_stock_music_contract_without_faking_yandex_login(self):
        result = self.panel.music()
        self.assertFalse(result['auth']['logged_in'])
        self.assertEqual(result['source'], 'library')
        p = result['glagol']['state']['playerState']
        self.assertEqual(p['title'], 'Song')
        self.assertEqual(p['progress'], 15)
        self.assertEqual(p['extra']['coverURI'], self.track['cover'])
        self.assertTrue(result['glagol']['state']['playing'])

    def test_weather_text_does_not_stop_or_reassign_our_source(self):
        self.panel.music_cmd({'command':'sendText','text':'Какая погода?'})
        self.library.action.assert_not_called()
        self.assertTrue(self.library.active)
        self.original_cmd.assert_called_once()

    def test_native_pause_seek_and_repeat_dispatch_to_library(self):
        self.panel.music_cmd({'command': 'stop'})
        self.library.action.assert_called_with({'action': 'pause'})
        self.panel.music_cmd({'command': 'rewind', 'position': 45})
        self.library.action.assert_called_with({'action': 'seek', 'position': 45})
        self.panel.music_cmd({'command': 'repeat', 'mode': 'One'})
        self.library.action.assert_called_with({'action': 'options', 'repeat_mode': 'One'})

    def test_stock_queue_and_favorite_use_same_track(self):
        q = self.panel.music_queue()
        self.assertEqual(q['current'], 0)
        self.assertEqual(q['tracks'][0]['durationMs'], 120000)
        self.assertEqual(self.panel.music_info('abc')['coverBig'], self.track['cover'])
        self.assertEqual(self.panel.music_like('abc', True), {'liked': True})
        self.assertTrue(self.library.data['tracks']['abc']['favorite'])
        self.library.save.assert_called_once()

    def test_volume_is_shared_with_stock_controls(self):
        self.panel.status()
        self.assertEqual(self.panel.music()['glagol']['state']['volume'], .7)
        self.panel.set_volume({'level': 35})
        self.assertEqual(self.panel.music()['glagol']['state']['volume'], .35)

    def test_yandex_source_keeps_original_behavior(self):
        self.panel.music_cmd({'command': 'source', 'source': 'yandex'})
        self.assertFalse(self.library.active)
        self.assertEqual(self.panel.music()['source'], 'yandex')
        self.panel.music_cmd({'command': 'play'})
        self.original_cmd.assert_called_with({'command': 'play'})

    def test_local_lyrics_do_not_need_yandex_account(self):
        self.assertEqual(self.panel.music_lyrics('abc'), {'text': 'Lyrics'})
        self.panel.ymusic.lyrics_by_name.assert_called_once_with('Song', 'Artist', 'Album', 120)

    def test_resume_during_seek_loading_does_not_restart_at_zero(self):
        for phase in ('loading','playing'):
            with self.subTest(phase=phase):
                value=self.library.state.return_value
                self.library.state.return_value={**value,'phase':phase,'position':90}
                self.panel.music_cmd({'command':'play'})
                self.library.play.assert_not_called()
                self.library.action.assert_not_called()


class PauseAudioReleaseTests(unittest.TestCase):
    def setUp(self):
        self.library = Library.__new__(Library)
        self.library.control = threading.RLock()
        self.library.lock = threading.RLock()
        self.library.current = 'abc'
        self.library.phase = 'playing'
        self.library.offset = 0
        self.library.position = lambda: 42.5
        self.library.kill = Mock()
        self.library.play = Mock(return_value={'phase': 'loading'})
        self.library.state = lambda: {'phase': self.library.phase, 'position': self.library.offset}

    def test_pause_releases_sink_and_resume_reopens_at_saved_position(self):
        self.library.action({'action': 'pause'})
        self.library.kill.assert_called_once()
        self.assertEqual(self.library.phase, 'paused')
        self.assertEqual(self.library.offset, 42.5)
        self.library.action({'action': 'resume'})
        self.library.play.assert_called_once_with('abc', 42.5)

    def test_duplicate_resume_does_not_stop_playing_audio(self):
        self.library.action({'action': 'resume'})
        self.library.kill.assert_not_called()
        self.library.play.assert_not_called()


if __name__ == '__main__':
    unittest.main()
