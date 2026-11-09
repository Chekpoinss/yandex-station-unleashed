import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from test_voice_events import frame
from test_native_alarms import field
from voice_bridge import VoiceBridge


class VoiceBridgeTests(unittest.TestCase):
    def bridge(self):
        self.state={'active':True,'phase':'playing','track':{'id':'a'},'native':{'matches_track':True},
                    'queue_revision':1,'has_next':True,'has_previous':True}
        lib=SimpleNamespace(data={},lock=threading.RLock(),control=threading.RLock(),
            play_generation=4,offset=12,save=Mock(),state=Mock(side_effect=lambda:self.state),
            action=Mock(),play=Mock(),advance=Mock())
        bridge=VoiceBridge(lib,True,start=False)
        bridge.configure({'enabled':True});bridge.connected=True
        return bridge,lib

    def idle(self, bridge):bridge.receive(field(252,field(1,0))+field(253,3),Mock())

    def test_pause_waits_for_idle_and_duplicate_final_only_dispatches_once(self):
        bridge,lib=self.bridge();send=Mock()
        bridge.receive(frame(),send);bridge.receive(frame(),send)
        self.assertTrue(bridge.jobs.empty());send.assert_not_called()
        self.idle(bridge);self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        lib.action.assert_called_once_with({'action':'pause'})
        self.assertTrue(bridge.jobs.empty());self.assertEqual(bridge.executed,1)

    def test_resume_only_from_paused_owned_source_and_one_cancel(self):
        bridge,lib=self.bridge();send=Mock();self.state['phase']='paused'
        bridge.receive(frame(text='продолжай'),send);bridge.receive(frame(text='продолжай'),send)
        send.assert_called_once();self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        self.assertEqual(lib.play.call_args.args,('a',12))
        self.assertTrue(callable(lib.play.call_args.kwargs['_guard']))

    def test_unknown_phrase_and_yandex_source_never_send_control(self):
        bridge,lib=self.bridge();send=Mock()
        for text in ('сколько времени','поставь будильник','не выключай повтор'):
            bridge.receive(frame(text=text,request_id=text),send)
        self.state['active']=False
        bridge.receive(frame(text='продолжай',request_id='external'),send)
        self.idle(bridge);send.assert_not_called();self.assertTrue(bridge.jobs.empty())

    def test_new_request_invalidates_pending_command_before_idle(self):
        bridge,lib=self.bridge()
        bridge.receive(frame(),Mock())
        bridge.receive(frame(text='какая погода',request_id='new-request',final=False),Mock())
        self.idle(bridge);self.assertTrue(bridge.jobs.empty())

    def test_missing_idle_event_expires_with_visible_error_without_touching_new_command(self):
        bridge,lib=self.bridge()
        timers=[]
        class Timer:
            def __init__(self, delay, callback, args):self.callback,self.args=callback,args
            def start(self):timers.append(self)
        with patch('voice_bridge.threading.Timer',Timer):
            bridge.receive(frame(text='пауза'),Mock())
            first=bridge.pending
            bridge.receive(frame(text='следующий трек',request_id='second'),Mock())
            second=bridge.pending
            timers[0].callback(*timers[0].args)
            self.assertIs(bridge.pending,second)  # old timer must not expire the newer request
            timers[1].callback(*timers[1].args)
        self.assertIsNone(bridge.pending)
        self.assertIn('не завершила',bridge.error)

    def test_disable_or_track_generation_change_rejects_queued_command(self):
        for change in ('disable','generation','source','listening','queue_revision'):
            with self.subTest(change=change):
                bridge,lib=self.bridge();bridge.receive(frame(),Mock());self.idle(bridge)
                job=bridge.jobs.get_nowait()
                if change=='disable':bridge.configure({'enabled':False})
                elif change=='generation':lib.play_generation+=1
                elif change=='source':self.state['active']=False
                elif change=='queue_revision':self.state['queue_revision']+=1
                else:bridge.receive(frame(final=False,status=1,request_id='new'),Mock())
                bridge.dispatch(job);lib.action.assert_not_called()
                self.assertEqual(bridge.cancelled,1)

    def test_stale_request_during_slow_resolution_is_counted_as_cancelled(self):
        bridge,lib=self.bridge();self.state['phase']='paused'
        bridge.receive(frame(text='продолжай'),Mock());self.idle(bridge)
        job=bridge.jobs.get_nowait()
        def resolve(*args,_guard):
            bridge.receive(frame(final=False,request_id='other',status=1),Mock())
            self.assertFalse(_guard())
        lib.play.side_effect=resolve
        bridge.dispatch(job)
        self.assertEqual(bridge.executed,0);self.assertEqual(bridge.cancelled,1)

    def test_next_previous_and_options_route_to_shared_player(self):
        for text,expected in (('следующий трек','next'),('дальше','next'),
                              ('дальше по списку','next'),('предыдущий трек','previous'),
                              ('включи перемешивание','options'),('повторяй этот трек','options')):
            with self.subTest(text=text):
                bridge,lib=self.bridge();send=Mock()
                bridge.receive(frame(text=text),send);self.idle(bridge)
                bridge.dispatch(bridge.jobs.get_nowait());send.assert_called_once()
                if expected=='options':self.assertEqual(lib.action.call_args.args[0]['action'],'options')
                else:self.assertEqual(lib.advance.call_args.kwargs['previous'],expected=='previous')

    def test_natural_language_aliases_cover_all_transport_controls(self):
        expected={
            'обратно':('previous',None),'верни назад':('previous',None),'назад':('previous',None),
            'включи следующий трек':('next',None),'перейди к следующему треку':('next',None),
            'сними с паузы':('resume',None),'возобнови музыку':('resume',None),
            'выруби музыку':('pause',None),'отключи музыку':('pause',None),
            'вруби перемешивание':('options',True),'отключи перемешивание':('options',False),
            'включи повтор':('options','All'),'включи повтор трека':('options','One'),
            'выруби повтор':('options','None'),'отключи повтор':('options','None'),
        }
        for phrase,(action,value) in expected.items():
            with self.subTest(phrase=phrase):
                command=VoiceBridge.command(self,phrase)
                self.assertEqual(command['action'],action)
                if action=='options':
                    if isinstance(value,bool):self.assertEqual(command['shuffle'],value)
                    else:self.assertEqual(command['repeat_mode'],value)

    def test_bad_config_and_failed_save_never_enable_listener(self):
        bridge,lib=self.bridge();bridge.configure({'enabled':False})
        for value in ('true',1,None):
            with self.assertRaises(ValueError):bridge.configure({'enabled':value})
        lib.save.side_effect=OSError('disk full')
        with self.assertRaises(OSError):bridge.configure({'enabled':True})
        self.assertFalse(bridge.enabled);self.assertIs(lib.data['voice_control'],False)
        unsupported=VoiceBridge(lib,False,start=False)
        with self.assertRaises(ValueError):unsupported.configure({'enabled':True})

    def test_status_does_not_expose_recognized_text_or_request(self):
        bridge,lib=self.bridge();bridge.receive(frame(text='пауза',request_id='private-id'),Mock())
        status=str(bridge.status())
        self.assertNotIn('private-id',status);self.assertNotIn('пауза',status)

    def test_voice_next_at_end_keeps_current_audio_and_reports_end(self):
        bridge,lib=self.bridge();self.state['has_next']=False
        bridge.receive(frame(text='следующий трек'),Mock());self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        lib.action.assert_not_called();lib.advance.assert_not_called()
        self.assertEqual(bridge.last_result,'no_next')

    def test_previous_after_stop_is_still_a_panel_command(self):
        bridge,lib=self.bridge();self.state['phase']='stopped'
        send=Mock();bridge.receive(frame(text='предыдущий трек'),send)
        send.assert_called_once();self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        self.assertTrue(lib.advance.call_args.kwargs['previous'])

    def test_resume_stopped_source_starts_at_zero(self):
        bridge,lib=self.bridge();self.state['phase']='stopped'
        bridge.receive(frame(text='продолжай'),Mock());self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        self.assertEqual(lib.play.call_args.args,('a',0))

    def test_resume_playing_or_loading_cancels_cloud_but_never_restarts_audio(self):
        for phase in ('playing','loading'):
            with self.subTest(phase=phase):
                bridge,lib=self.bridge();self.state['phase']=phase
                send=Mock();bridge.receive(frame(text='продолжай'),send);self.idle(bridge)
                bridge.dispatch(bridge.jobs.get_nowait())
                send.assert_called_once();lib.play.assert_not_called()
                self.assertEqual(bridge.last_result,'loading' if phase=='loading' else 'already_playing')

    def test_pause_during_owned_loading_is_delivered_to_shared_transport(self):
        bridge,lib=self.bridge();self.state['phase']='loading'
        bridge.receive(frame(),Mock());self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        lib.action.assert_called_once_with({'action':'pause'})

    def test_local_alarm_priority_prevents_cancel_or_transport_action(self):
        bridge,lib=self.bridge()
        lib.panel=SimpleNamespace(metrics=SimpleNamespace(ringing='local-test'))
        send=Mock();bridge.receive(frame(text='следующий трек'),send);self.idle(bridge)
        send.assert_not_called();self.assertTrue(bridge.jobs.empty())

    def test_close_disconnects_and_removes_only_its_own_forward(self):
        bridge,lib=self.bridge();bridge.stream=Mock();stream=bridge.stream
        bridge.forward_port=32123
        lib.panel=SimpleNamespace(adb=SimpleNamespace(_base=lambda:['adb','-s','test']))
        with patch('voice_bridge.subprocess.run') as run:
            bridge.close()
        stream.shutdown.assert_called_once();stream.close.assert_called_once()
        self.assertEqual(run.call_args.args[0],['adb','-s','test','forward','--remove','tcp:32123'])
        self.assertFalse(bridge.enabled);self.assertTrue(bridge.done.is_set())

    def catalog(self,lib):
        lib.data['tracks']={'a':{'id':'a','title':'First song','artist':'One','source':'local'},
                            'b':{'id':'b','title':'Second song','artist':'One','source':'soundcloud'}}
        lib.public=lambda t:dict(t)

    def test_play_title_uses_shared_transport_and_guard(self):
        bridge,lib=self.bridge();self.catalog(lib);send=Mock()
        bridge.receive(frame(text='включи трек second song'),send);self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        send.assert_called_once();self.assertEqual(lib.play.call_args.args,('b',))
        self.assertTrue(callable(lib.play.call_args.kwargs['_guard']))

    def test_partial_title_found_in_search_can_be_started_by_voice(self):
        bridge,lib=self.bridge();self.catalog(lib);send=Mock()
        bridge.receive(frame(text='запусти second'),send);self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        send.assert_called_once();self.assertEqual(lib.play.call_args.args[0],'b')

    def test_english_title_one_letter_recognition_error_still_routes_to_panel(self):
        bridge,lib=self.bridge();send=Mock()
        lib.data['tracks']={'impostor':{'id':'impostor','title':'Impostor Syndrome','artist':'Sidney Gish'}}
        lib.public=lambda track:dict(track)
        bridge.receive(frame(text='включи imposter syndrome'),send);self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        send.assert_called_once();self.assertEqual(lib.play.call_args.args[0],'impostor')

    def test_worker_surfaces_actionable_stream_error(self):
        bridge,lib=self.bridge();self.catalog(lib)
        lib.play.side_effect=ValueError('Этот поток защищён DRM')
        bridge.receive(frame(text='включи трек second song'),Mock());self.idle(bridge)
        worker=threading.Thread(target=bridge.worker,daemon=True);worker.start()
        bridge.jobs.join()
        bridge.close();worker.join(timeout=1)
        self.assertEqual(bridge.error,'Этот поток защищён DRM')

    def test_explicit_missing_local_track_is_claimed_and_shows_error_instead_of_cloud_fallback(self):
        bridge,lib=self.bridge();self.catalog(lib)
        send=Mock()
        bridge.receive(frame(text='включи трек not in library'),send);self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        send.assert_called_once()
        lib.play.assert_not_called()
        self.assertIn('названия в медиатеке нет',bridge.error)

    def test_play_artist_builds_queue_across_local_and_soundcloud(self):
        bridge,lib=self.bridge();self.catalog(lib)
        bridge.receive(frame(text='включи исполнителя one'),Mock());self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        self.assertEqual(lib.play.call_args.args,('a',))
        self.assertEqual(lib.play.call_args.kwargs['queue'],['a','b'])

    def test_search_publishes_results_without_playing_or_changing_queue(self):
        bridge,lib=self.bridge();self.catalog(lib)
        bridge.receive(frame(text='найди трек song'),Mock());self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        self.assertEqual(len(bridge.status()['search_result']['tracks']),2)
        self.assertEqual(bridge.result_revision,1)
        lib.play.assert_not_called();lib.action.assert_not_called()
        self.assertNotIn('query',str(bridge.status()))

    def test_soundcloud_search_does_not_hold_transport_lock_and_rejects_stale_results(self):
        bridge,lib=self.bridge();self.catalog(lib)
        class ForbiddenLock:
            def __enter__(self):raise AssertionError('Search blocked playback controls')
            def __exit__(self,*args):pass
        lib.control=ForbiddenLock()
        def search(query):
            bridge.receive(frame(text='сколько времени',request_id='new',final=False),Mock())
            return {'tracks':[{'id':'sc-1','title':'Public result'}]}
        lib.soundcloud=Mock(side_effect=search)
        bridge.receive(frame(text='найди в саундклауде музыка'),Mock());self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        self.assertIsNone(bridge.search_result);self.assertEqual(bridge.cancelled,1)
        lib.play.assert_not_called()

    def test_generic_known_artist_claims_source_but_smart_home_phrase_does_not(self):
        bridge,lib=self.bridge();self.catalog(lib);self.state['active']=False
        self.state['native']['matches_track']=False
        send=Mock();bridge.receive(frame(text='включи one'),send);self.idle(bridge)
        bridge.dispatch(bridge.jobs.get_nowait())
        self.assertEqual(lib.play.call_args.args,('a',));send.assert_called_once()
        lib.data['tracks']['light']={'id':'light','title':'свет','artist':'Artist'}
        self.assertIsNone(bridge.command('включи свет'))
        self.assertIsNone(bridge.command('включи неизвестную лампу'))

    def test_direct_text_music_never_needs_a_fabricated_native_request(self):
        bridge,lib=self.bridge();self.catalog(lib);self.state['active']=False
        self.state['native']['matches_track']=False;bridge.latest_request=None
        self.assertTrue(bridge.direct('Включи трек Second song'))
        self.assertEqual(lib.play.call_args.args,('b',))
        self.assertFalse(bridge.direct('Какая погода?'))

    def test_music_alarm_dispatch_uses_guard_and_records_durable_result(self):
        bridge,lib=self.bridge();lib.native_music_cmd=Mock()
        bridge.receive(frame(text='поставь будильник через две минуты с этой песней'),Mock());self.idle(bridge)
        with patch('alarm_music.create',return_value={'id':'test','time':'07:30','created':True,'running':True}) as create:
            bridge.dispatch(bridge.jobs.get_nowait())
        self.assertEqual(create.call_args.args[1],'a')
        self.assertEqual(bridge.alarm_event['time'],'07:30')
        self.assertEqual(bridge.last_result,'alarm_created');lib.native_music_cmd.assert_called_once()

    def test_plain_native_alarm_is_not_intercepted(self):
        bridge,lib=self.bridge();send=Mock()
        bridge.receive(frame(text='поставь будильник на семь'),send);self.idle(bridge)
        send.assert_not_called();self.assertTrue(bridge.jobs.empty())
