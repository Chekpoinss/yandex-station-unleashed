from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from panel_scenes import Scenes, MOSCOW, validate_scene, validate_steps

class SceneTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.panel=SimpleNamespace(conn=SimpleNamespace(online=True),metrics=SimpleNamespace(ringing=None),glagol=SimpleNamespace(state={'aliceState':'IDLE'}),library=SimpleNamespace(state=lambda:{'phase':'playing'},action=Mock(),play=Mock()),set_volume=Mock(),say=Mock())
        self.engine=Scenes(self.panel,Path(self.tmp.name)/'scenes.json',start=False)
    def save(self,**extra):
        scene={'name':'Test','steps':[{'type':'wait','seconds':0}],**extra}
        return self.engine.save({'revision':self.engine.revision,'scene':scene})['id']
    def test_save_is_passive_and_roundtrips_without_autoplay(self):
        self.save(enabled=True,schedule={'time':'10:00','days':[0,1]})
        self.panel.library.action.assert_not_called()
        other=Scenes(self.panel,self.engine.path,start=False)
        self.assertEqual(self.engine.items,other.items)
    def test_revision_conflict_preserves_schedule(self):
        self.save();before=self.engine.path.read_bytes()
        with self.assertRaises(ValueError):self.engine.save({'revision':0,'scene':{'name':'bad','steps':[{'type':'wait','seconds':1}]}})
        self.assertEqual(before,self.engine.path.read_bytes())
    def test_save_failure_restores_memory_and_revision(self):
        self.save();before=self.engine.status()
        with patch.object(self.engine,'persist',side_effect=OSError()):
            with self.assertRaises(ValueError):self.save()
        self.assertEqual(self.engine.status(),before)
    def test_conditions_repeat_and_music_use_common_transport(self):
        ident=self.save(steps=[{'type':'repeat','count':2,'steps':[{'type':'if','condition':'phase','value':'playing','steps':[{'type':'music','action':'pause'}],'else':[{'type':'say','text':'secret'}]}]},{'type':'music','action':'play','id':'abc'}])
        self.engine.runs[ident]={'state':'running','step':0,'error':None}
        self.engine.execute(ident,self.engine.items[ident],threading.Event())
        self.assertEqual(self.panel.library.action.call_count,2)
        self.panel.library.play.assert_called_once()
        self.assertTrue(self.panel.library.play.call_args.kwargs['_guard']())
        self.panel.say.assert_not_called()
        self.assertEqual(self.engine.runs[ident]['state'],'completed')
    def test_cancel_interrupts_wait_without_following_audio(self):
        ident=self.save(steps=[{'type':'wait','seconds':20},{'type':'say','text':'secret'}])
        self.engine.start_run(ident);self.engine.cancel(ident)
        # Join the test's worker through a deterministic event wait in mocked action is unnecessary;
        # execute an already cancelled run directly to verify the invariant.
        stop=threading.Event();stop.set();self.engine.execute(ident,self.engine.items[ident],stop)
        self.panel.say.assert_not_called()
        self.assertEqual(self.engine.runs[ident]['state'],'cancelled')
    def test_schedule_once_per_minute_and_no_duplicate_on_reload(self):
        now=datetime(2026,10,30,10,0,tzinfo=MOSCOW)
        ident=self.save(enabled=True,schedule={'time':'10:00','date':'2026-10-30','days':[4]})
        self.engine.fired.clear();self.engine.last_minute=int(now.timestamp()//60)-1
        self.engine.start_run=Mock();self.engine.tick(now);self.engine.tick(now)
        self.engine.start_run.assert_called_once_with(ident)
        other=Scenes(self.panel,self.engine.path,start=False);other.start_run=Mock();other.last_minute=int(now.timestamp()//60)-1
        other.tick(now);other.start_run.assert_not_called()
        other.last_minute=int((now+timedelta(days=7)).timestamp()//60)-1
        other.tick(now+timedelta(days=7));other.start_run.assert_not_called()
    def test_busy_run_is_not_overwritten_by_scheduled_attempt(self):
        now=datetime(2026,10,30,10,0,tzinfo=MOSCOW)
        ident=self.save(enabled=True,schedule={'time':'10:00'})
        stop=threading.Event();self.engine.runs[ident]={'state':'running','step':1,'error':None,'stop':stop}
        self.engine.fired.clear();self.engine.last_minute=int(now.timestamp()//60)-1
        self.engine.tick(now);self.assertIs(self.engine.runs[ident]['stop'],stop)
        self.assertEqual(self.engine.runs[ident]['state'],'running')
    def test_corrupt_file_is_not_replaced(self):
        self.engine.path.write_text('{oops',encoding='utf8')
        other=Scenes(self.panel,self.engine.path,start=False)
        self.assertTrue(other.storage_error)
        with self.assertRaises(ValueError):other.save({'revision':0,'scene':{'name':'x','steps':[{'type':'wait','seconds':0}]}})
        self.assertEqual(other.path.read_text('utf8'),'{oops')
    def test_limits_and_arbitrary_commands_rejected(self):
        for step in [{'type':'shell','command':'reboot'},{'type':'wait','seconds':float('nan')},{'type':'repeat','count':10000,'steps':[{'type':'wait','seconds':0}]},{'type':'music','action':'sendText'}]:
            with self.assertRaises(ValueError):validate_steps([step])
    def test_no_catchup_after_missed_minute(self):
        ident=self.save(enabled=True,schedule={'time':'10:00'})
        self.engine.start_run=Mock();now=datetime(2026,10,30,10,5,tzinfo=MOSCOW)
        self.engine.last_minute=int(now.timestamp()//60)-10
        self.engine.tick(now);self.engine.start_run.assert_not_called()
