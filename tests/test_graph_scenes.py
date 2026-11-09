from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile, threading, unittest, os, shlex, shutil, subprocess, sys, hashlib
from graph_scenes import GraphScenes, legacy_graph, validate_graph, MOSCOW
from native_scenes import NativeScenes
from animation_library import validate_led, parse_led

class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.panel=SimpleNamespace(data_dir=self.tmp.name,conn=SimpleNamespace(online=True),metrics=SimpleNamespace(ringing=None),glagol=SimpleNamespace(state={'aliceState':'IDLE'}),library=SimpleNamespace(state=lambda:{'phase':'playing'},action=Mock(),play=Mock()),say=Mock())
        self.engine=GraphScenes(self.panel,Path(self.tmp.name)/'graph.json',start=False);self.panel.scenes=self.engine
    def migrated(self,steps):
        s=legacy_graph('abc',{'name':'Test','enabled':True,'steps':steps})
        return validate_graph(s,{'abc'})
    def run_scene(self,s,stop=None):
        self.engine.items={'abc':s};self.engine.runs['abc']={'state':'running','step':0,'error':None}
        self.engine.execute('abc',s,stop or threading.Event())
    def test_nested_migration_keeps_both_branches_and_loop_continuation(self):
        step={'type':'if','condition':'phase','value':'playing','steps':[{'type':'say','text':'yes'}],'else':[{'type':'say','text':'no'}]}
        s=self.migrated([{'type':'repeat','count':2,'steps':[step]},{'type':'say','text':'after'}])
        self.run_scene(s)
        self.assertEqual([v.args[0] for v in self.panel.say.call_args_list],['yes','yes','after'])
        self.panel.say.reset_mock();self.panel.library.state=lambda:{'phase':'paused'};self.run_scene(s)
        self.assertEqual([v.args[0] for v in self.panel.say.call_args_list],['no','no','after'])
    def test_cas_and_disk_failure_preserve_existing_graph(self):
        s=self.migrated([{'type':'say','text':'private'}]);self.engine.save({'revision':0,'scenes':[s]});before=self.engine.path.read_bytes()
        with self.assertRaises(ValueError):self.engine.save({'revision':0,'scenes':[]})
        with patch.object(self.engine,'persist',side_effect=OSError()):
            with self.assertRaises(ValueError):self.engine.save({'revision':1,'scenes':[]})
        self.assertEqual(before,self.engine.path.read_bytes());self.assertEqual(self.engine.revision,1)
    def test_migrated_music_options_keep_common_player(self):
        s=self.migrated([{'type':'music','action':'options','repeat_mode':'All','shuffle':True}]);self.run_scene(s)
        self.panel.library.action.assert_called_once_with({'action':'options','repeat_mode':'All','shuffle':True})
    def test_cancel_prevents_audio_and_logs_no_text(self):
        s=self.migrated([{'type':'say','text':'private'}]);stop=threading.Event();stop.set();self.run_scene(s,stop)
        self.panel.say.assert_not_called();self.assertEqual(self.engine.runs['abc']['state'],'cancelled');self.assertNotIn('private',str(self.engine.log))
    def test_once_schedule_survives_reload(self):
        s=self.migrated([{'type':'wait','seconds':0}]);s.update(trigger='time',time='10:00')
        self.engine.save({'revision':0,'scenes':[s]});now=datetime(2026,10,30,10,0,tzinfo=MOSCOW)
        self.engine.fired.clear();self.engine.last_minute=int(now.timestamp()//60)-1;self.engine.start_run=Mock();self.engine.tick(now);self.engine.start_run.assert_called_once_with('abc')
        other=GraphScenes(self.panel,self.engine.path,start=False);other.start_run=Mock();other.last_minute=int(now.timestamp()//60)-1;other.tick(now);other.start_run.assert_not_called()
    def test_native_validation_happens_before_device_writes(self):
        s=self.migrated([{'type':'say','text':'unsafe|text'}]);s['execution']='device';self.engine.items={'abc':s}
        self.panel._ensure_hook=Mock();native=NativeScenes(self.panel,SimpleNamespace(),start=False)
        with self.assertRaises(ValueError):native.deploy()
        self.panel._ensure_hook.assert_not_called()
    def test_native_rejects_pc_only_condition_and_date(self):
        n=NativeScenes(self.panel,SimpleNamespace(),start=False)
        s=self.migrated([{'type':'if','condition':'phase','value':'playing','steps':[{'type':'wait','seconds':0}]}]);s['execution']='device'
        with self.assertRaises(ValueError):n.serialize([s])
    def test_native_failed_restart_requests_rollback_without_replacing_alarm_schedule(self):
        s=self.migrated([{'type':'say','text':'hello'}]);s['execution']='device';self.engine.items={'abc':s}
        p=self.panel;p._ensure_hook=Mock();p._alarm_daemon=Mock(side_effect=[ValueError('failed'),{}]);p.adb=Mock()
        config=SimpleNamespace(ALARM_STATE='/tmp/alarm.state',ALARM_DIR='/data/testalarm',ALARMD='/data/testalarm/alarmd.sh',ALARM_CONF='/data/testalarm/alarms.conf',alarm_script=lambda:b'daemon')
        native=NativeScenes(p,config,start=False);conf=native.serialize([s]).encode()
        def shell(cmd,**kw):
            if cmd.startswith('sha256sum '):return hashlib.sha256(b'daemon' if 'alarmd.sh.new' in cmd else conf).hexdigest()+' file','',0
            return '','',0
        p.adb.shell.side_effect=shell
        with self.assertRaisesRegex(ValueError,'восстановление'):native.deploy()
        self.assertEqual(p._alarm_daemon.call_count,2);self.assertFalse(native.deployed)
        rollback=[c.args[0] for c in p.adb.shell.call_args_list if 'if [ -f' in c.args[0] and '/backups/' in c.args[0]][0]
        self.assertNotIn('alarms.conf',rollback);self.assertNotIn('/etc/',rollback)
        s=self.migrated([{'type':'wait','seconds':0}]);s.update(execution='device',date='2026-10-30')
        with self.assertRaises(ValueError):native.serialize([s])
    def test_led_parser_preserves_three_points_and_native_directives(self):
        p=parse_led(validate_led('loop\nff0000 00ff00 0000ff 40'))
        self.assertTrue(p['simple']);self.assertEqual(p['frames'][0]['c'],[[255,0,0],[0,255,0],[0,0,255]])
        self.assertFalse(parse_led(validate_led('gammacorrection\nffffff 40'))['simple'])
        for text in ('ffffff 40\n$(reboot)','loop','ffffff 40\x00'):
            with self.assertRaises(ValueError):validate_led(text)
    def test_actual_shell_graph_repeat_and_branch_continuation(self):
        shell=shutil.which('sh')
        if not shell and shutil.which('git'):
            candidate=Path(shutil.which('git')).parent.parent/'usr/bin/sh.exe'
            if candidate.exists():shell=str(candidate)
        if not shell:self.skipTest('POSIX shell unavailable')
        s=self.migrated([{'type':'repeat','count':2,'steps':[{'type':'say','text':'inside'}]},{'type':'say','text':'after'}]);s['execution']='device'
        native=NativeScenes(self.panel,SimpleNamespace(),start=False);root=Path(self.tmp.name)
        (root/'scenes.conf').write_text(native.serialize([s]),'utf8')
        prefix=Path('device/alarmd.sh').read_text('utf8').split('\necho idle > $STATE\n')[0]
        script=prefix+'\nD='+shlex.quote(root.as_posix())+'\nSCENES="$D/scenes.conf"\nSRUN="$D/run."\nLOG="$D/log"\n'
        # Only substitute the hardware speech operation; run the real graph and repeat code.
        script+='alice() { printf "%s\\n" "$1" >> "$D/spoken"; }\nslog() { :; }\nrun_scene_worker abc\n'
        env=dict(os.environ);env['PATH']=str(Path(shell).parent)+os.pathsep+env.get('PATH','')
        result=subprocess.run([shell,'-s'],input=script.encode(),capture_output=True,env=env,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertEqual((root/'spoken').read_text('utf8').splitlines(),['Повтори за мной: inside','Повтори за мной: inside','Повтори за мной: after'])
    def test_actual_shell_cancel_interrupts_wait_and_prevents_following_speech(self):
        shell=shutil.which('sh')
        if not shell and shutil.which('git'):
            candidate=Path(shutil.which('git')).parent.parent/'usr/bin/sh.exe'
            if candidate.exists():shell=str(candidate)
        if not shell:self.skipTest('POSIX shell unavailable')
        s=self.migrated([{'type':'wait','seconds':20},{'type':'say','text':'must not play'}]);s['execution']='device'
        root=Path(self.tmp.name);(root/'scenes.conf').write_text(NativeScenes(self.panel,SimpleNamespace(),start=False).serialize([s]),'utf8')
        prefix=Path('device/alarmd.sh').read_text('utf8').split('\necho idle > $STATE\n')[0]
        script=prefix+'\nD='+shlex.quote(root.as_posix())+'\nSCENES="$D/scenes.conf"\nSRUN="$D/run."\n'
        # The bundled Git tools omit sleep.exe. Supply only that external tool;
        # leave the daemon's real signal traps, wait and interpreter unchanged.
        (root/'sleep').write_text('#!/bin/sh\nexec '+shlex.quote(Path(sys.executable).as_posix())+' -c '+shlex.quote('import sys,time; time.sleep(float(sys.argv[1]))')+' "$1"\n','utf8')
        script=script.replace('if [ \"$1\" = --scene ]; then run_scene_worker \"$2\"; exit $?; fi', '')
        script += 'alice() { echo unexpected > "$D/spoken"; }\nslog() { :; }\nif [ "$1" = --scene ]; then run_scene_worker "$2"; exit $?; fi\nrun_scene abc\nsleep 0.2\nkill -- -"$(cat "${SRUN}abc")"\nwait\n'
        env=dict(os.environ);env['PATH']=str(root)+os.pathsep+str(Path(shell).parent)+os.pathsep+env.get('PATH','')
        fixture=root/'scene-daemon.sh';fixture.write_text(script,encoding='utf8')
        result=subprocess.run([shell,str(fixture)],capture_output=True,env=env,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'));self.assertFalse((root/'spoken').exists(),result.stderr.decode(errors='replace'))

if __name__=='__main__':unittest.main()
