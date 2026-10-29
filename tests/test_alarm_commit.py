import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from test_profiles import fresh_server


class AlarmCommitTests(unittest.TestCase):
    def setUp(self):
        shell=shutil.which('sh')
        if not shell and shutil.which('git'):
            candidate=Path(shutil.which('git')).parent.parent/'usr/bin/sh.exe'
            if candidate.exists():shell=str(candidate)
        if not shell:self.skipTest('POSIX shell required')
        self.shell=shell
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.s=fresh_server();self.panel=self.s.Panel(Mock())
        self.panel._ensure_hook=Mock();self.panel._alarm_daemon=Mock()
        self.panel._pc_tz=Mock(return_value=(None,None));self.panel.alarms=Mock(return_value={})
        self.old='one|0|07:30||sound\n'
        (self.root/'alarms.conf').write_bytes(self.old.encode())
        (self.root/'snooze').write_bytes(b'2000000000|one\n')
        self.changed=False
        def push(source,target):
            name=target.rsplit('/',1)[-1]
            (self.root/name).write_bytes(Path(source).read_bytes())
            if self.changed and name.startswith('alarms.conf.new.'):
                (self.root/'alarms.conf').write_text('concurrent|1|08:00||sound\n')
        def shell(command,**kwargs):
            if command.startswith('mkdir -p '):return ('default.wav\nu_1234567890.wav','',0)
            if command.startswith('exec 9>'):
                # Git's Windows tool bundle lacks sha256sum; use the same
                # SHA-256 through Python, while running the real commit shell.
                hash_code='import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest()+"  "+sys.argv[1])'
                script=('flock() { return 0; }\nsha256sum() { '+shlex.quote(Path(sys.executable).as_posix())
                        +' -c '+shlex.quote(hash_code)+' "$1"; }\n'
                        +command.replace(self.s.ALARM_DIR,shlex.quote(self.root.as_posix())))
                env=dict(os.environ);env['PATH']=str(Path(self.shell).parent)+os.pathsep+env.get('PATH','')
                result=subprocess.run([self.shell,'-s'],input=script.encode(),capture_output=True,env=env,timeout=10)
                return result.stdout.decode(),result.stderr.decode(),result.returncode
            raise AssertionError(command)
        self.panel.adb.push.side_effect=push;self.panel.adb.shell.side_effect=shell

    def save(self,items):
        return self.panel.alarms_save({'alarms':items,'revision':hashlib.sha256(self.old.encode()).hexdigest()})

    def test_concurrent_change_is_preserved_and_daemon_not_restarted(self):
        self.changed=True
        with self.assertRaisesRegex(self.s.AdbError,'изменился'):self.save([])
        self.assertEqual((self.root/'alarms.conf').read_text(),'concurrent|1|08:00||sound\n')
        self.panel._alarm_daemon.assert_not_called()
        self.assertFalse(list(self.root.glob('alarms.conf.new.*')))

    def test_snooze_and_prepared_song_survive_unrelated_save(self):
        song=self.root/'u_1234567890.wav';song.write_bytes(b'prepared song')
        self.save([{'id':'one','time':'07:30','on':False,'ramp':25}])
        self.assertTrue((self.root/'snooze').exists());self.assertTrue(song.exists())
        self.assertEqual((self.root/'alarms.conf').read_text().split('|')[9],'0')

    def test_removing_snoozed_alarm_clears_only_its_pending_snooze(self):
        self.save([])
        self.assertFalse((self.root/'snooze').exists())

    def test_missing_hook_fails_before_any_upload(self):
        self.panel._ensure_hook.side_effect=self.s.AdbError('missing hook')
        with self.assertRaisesRegex(self.s.AdbError,'missing hook'):self.save([])
        self.panel.adb.push.assert_not_called();self.panel.adb.shell.assert_not_called()
