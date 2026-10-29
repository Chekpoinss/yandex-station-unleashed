import pathlib
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from collections import deque

from music_library import Library
from playback_order import PlaybackOrder


class PlaybackIntegrationTests(unittest.TestCase):
    def library(self, source):
        lib=Library.__new__(Library)
        lib.lock=threading.RLock();lib.control=threading.RLock()
        lib.data={'tracks':{ident:{'id':ident,'title':ident,'artist':'Test','source':source,'duration':10}
                           for ident in ('a','b','c')},'playlists':{}}
        lib.remote={};lib.current='a';lib.order=PlaybackOrder(['a','b','c'],'a',shuffler=lambda v:v.reverse())
        lib.save=Mock();lib._sync_order(False)
        lib.active=True;lib.phase='playing';lib.native_audio=False;lib.offset=0;lib.stream_offset=0;lib.started=time.monotonic()
        lib.native_ended=False;lib.media_reason='test';lib.native_updated=0;lib.error=None
        lib.panel=SimpleNamespace(glagol=SimpleNamespace(ws=object(),state={'playing':True},send=Mock(return_value={'status':'SUCCESS'}),updated=0,error=None),adb=Mock())
        lib.resolve=Mock(return_value=('test.mp3',False));lib.kill=Mock();lib.host='127.0.0.1';lib.port=8767;lib.token='secret'
        return lib

    def test_repeat_modes_for_both_providers(self):
        for source in ('local','soundcloud'):
            for mode,initial,expected in (('One','a','a'),('All','c','a'),('None','c',None)):
                with self.subTest(source=source,mode=mode):
                    lib=self.library(source);lib.order.select(initial);lib.current=initial;lib._sync_order(False)
                    lib.action({'action':'options','repeat_mode':mode})
                    lib.offset=10;lib.phase='paused';lib.native_ended=True
                    lib.playback_tick()
                    if expected:
                        self.assertEqual(lib.current,expected);self.assertEqual(lib.phase,'loading');self.assertEqual(lib.offset,0)
                    else:
                        self.assertEqual(lib.phase,'stopped');self.assertEqual(lib.position(),10)
                    lib.playback_tick() # old end event must not advance again
                    self.assertEqual(lib.current,expected or initial)

    def test_shuffle_and_history_for_both_providers(self):
        for source in ('local','soundcloud'):
            with self.subTest(source=source):
                lib=self.library(source)
                lib.action({'action':'options','shuffle':True,'repeat_mode':'One'})
                self.assertEqual(lib.repeat_mode,'One')
                lib.action({'action':'next'});self.assertEqual(lib.current,'c')
                lib.action({'action':'previous'});self.assertEqual(lib.current,'a')
                lib.action({'action':'next'});self.assertEqual(lib.current,'c')
                lib.action({'action':'options','shuffle':False})
                self.assertEqual(lib.repeat_mode,'One')
                self.assertEqual(lib.data['playback_order']['history'],['a','c'])

    def test_seek_paused_keeps_audio_paused_and_position_is_clamped(self):
        lib=self.library('local');lib.phase='paused'
        lib.action({'action':'seek','position':40})
        self.assertEqual(lib.phase,'paused');self.assertEqual(lib.position(),10)
        lib.resolve.assert_not_called();lib.kill.assert_not_called()

    def test_obsolete_voice_resolution_does_not_stop_audio_or_commit_order(self):
        lib=self.library('soundcloud')
        original=lib.order.dump()
        lib.play('b',_guard=lambda:False)
        lib.resolve.assert_called_once()
        lib.kill.assert_not_called();lib.panel.glagol.send.assert_not_called()
        self.assertEqual(lib.current,'a');self.assertEqual(lib.order.dump(),original)

    def test_local_alarm_pauses_owned_music_without_advancing_queue(self):
        lib=self.library('local');lib.panel.metrics=SimpleNamespace(ringing='test-alarm')
        lib.playback_tick()
        self.assertEqual(lib.phase,'paused');self.assertEqual(lib.current,'a')
        self.assertEqual(lib.media_reason,'alarm-priority');lib.resolve.assert_not_called()
        with self.assertRaises(ValueError):lib.action({'action':'seek','position':float('nan')})

    def test_stream_eof_is_duration_evidence_not_playback_end(self):
        lib=self.library('soundcloud');lib.play_token='new'
        with tempfile.TemporaryDirectory() as folder:
            pcm=pathlib.Path(folder)/'decoded.pcm';pcm.write_bytes(bytes(16000*12))
            lib.stream_completed('old',pcm,0)
            self.assertEqual(lib.data['tracks']['a']['duration'],10)
            lib.stream_completed('new',pcm,3)
        self.assertEqual(lib.data['tracks']['a']['duration'],15)
        self.assertFalse(lib.native_ended);self.assertEqual(lib.phase,'playing')

    def test_end_reset_preserves_final_position_and_advances(self):
        lib=self.library('local');lib.native_audio=True;lib.play_token='new';lib.source_complete_token='new'
        lib.offset=9.8;lib.started=time.monotonic()
        lib.panel.glagol.state={'playing':False,'playerState':{'id':'a','progress':0}}
        lib.panel.glagol.updated=time.time()
        lib.playback_tick()
        self.assertEqual(lib.current,'b');self.assertEqual(lib.phase,'loading')

    def test_soundcloud_queue_metadata_survives_restart(self):
        lib=self.library('local')
        lib.remote['sc-123']={'id':'sc-123','title':'Remote','artist':'Artist','source':'soundcloud','duration':30,'url':'https://soundcloud.com/test/remote'}
        lib.play('sc-123',queue=['a','sc-123'])
        self.assertNotIn('sc-123',lib.data['tracks'])
        self.assertIn('sc-123',lib.data['queue_tracks'])
        restored=PlaybackOrder.restore(lib.data['playback_order'],set(lib.data['tracks'])|set(lib.data['queue_tracks']))
        self.assertEqual(restored.current,'sc-123');self.assertEqual(restored.base,['a','sc-123'])

    def test_playing_search_results_does_not_save_all_results_to_library(self):
        lib=self.library('local')
        before=set(lib.data['tracks'])
        for i in range(12):
            ident=f'sc-{i}'
            lib.remote[ident]={'id':ident,'title':ident,'artist':'Artist','source':'soundcloud',
                               'duration':10,'url':f'https://soundcloud.com/artist/track-{i}'}
        lib.play('sc-0',queue=list(lib.remote))
        self.assertEqual(set(lib.data['tracks']),before)
        self.assertEqual(len(lib.data['queue_tracks']),12)
        self.assertNotIn('stream',str(lib.data['queue_tracks']))

    def test_terminal_native_progress_rollback_does_not_look_like_pause(self):
        lib=self.library('local');lib.native_audio=True;lib.play_token='new';lib.source_complete_token='new'
        lib.offset=9.99;lib.started=time.monotonic()
        lib.panel.glagol.state={'playing':False,'playerState':{'id':'a','progress':9.4}}
        lib.panel.glagol.updated=time.time()
        lib.playback_tick()
        self.assertEqual(lib.current,'b');self.assertEqual(lib.phase,'loading')

    def test_late_source_completion_joins_pending_end_without_waiting_for_another_event(self):
        lib=self.library('local');lib.native_audio=True;lib.play_token='new'
        lib.offset=9.99;lib.started=time.monotonic()
        lib.panel.glagol.state={'playing':False,'playerState':{'id':'a','progress':9.4}}
        lib.panel.glagol.updated=time.time()
        lib.reconcile_native();self.assertFalse(lib.native_ended)
        with tempfile.TemporaryDirectory() as folder:
            pcm=pathlib.Path(folder)/'decoded.pcm';pcm.write_bytes(bytes(16000*10))
            lib.stream_completed('new',pcm,0)
        lib.playback_tick()
        self.assertEqual(lib.current,'b')

    def test_previous_generation_cannot_pause_or_take_over_new_session(self):
        lib=self.library('local');lib.native_audio=True;lib.native_session_id='panel-new'
        lib.owned_sessions=deque(['panel-old','panel-new'])
        lib.panel.glagol.state={'playing':True,'playerState':{'id':'panel-old','progress':8}}
        lib.panel.glagol.updated=time.time()
        lib.reconcile_native()
        self.assertTrue(lib.active);self.assertEqual(lib.phase,'playing')

    def test_short_track_end_and_echo_are_not_lost_between_monitor_ticks(self):
        lib=self.library('local');lib.native_audio=True;lib.native_session_id='panel-new'
        lib.data['tracks']['a']['duration']=.2
        lib.play_token='new';lib.source_complete_token='new';lib.stream_ready=True
        lib.native_confirmed=False;lib.phase='loading'
        now=time.time()
        lib.native_pending=deque([
            ({'playing':True,'playerState':{'id':'panel-new','progress':.05}},now-.3),
            ({'playing':False,'playerState':{'id':'panel-new','progress':.19}},now-.1),
            ({'playing':False,'playerState':{'id':'panel-new','progress':.19}},now-.05)])
        lib.playback_tick()
        self.assertEqual(lib.current,'b');self.assertEqual(lib.phase,'loading')


if __name__=='__main__':unittest.main()
