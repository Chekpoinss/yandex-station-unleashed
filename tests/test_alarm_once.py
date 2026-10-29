import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


class OneShotCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shell=shutil.which('sh')
        if not shell and shutil.which('git'):
            candidate=Path(shutil.which('git')).parent.parent/'usr/bin/sh.exe'
            if candidate.exists():shell=str(candidate)
        if not shell:raise unittest.SkipTest('POSIX shell is required for alarm script fixtures')
        cls.shell=shell
        cls.prefix=Path('device/alarmd.sh').read_text('utf8').split('\necho idle > $STATE\n')[0]

    def run_cleanup(self,lines,extra=''):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);conf=root/'alarms.conf';conf.write_text(lines,'utf8')
            script=self.prefix+'\nD='+shlex.quote(root.as_posix())+'\nCONF="$D/alarms.conf"\nCONF_LOCK="$D/config.lock"\nSNOOZE="$D/snooze"\n'
            # Exercise the actual cleanup/filter code without needing a Windows
            # implementation of kernel flock. Native Micro flock was probed read-only.
            script+='flock() { return 0; }\n'+extra+'\nremove_completed_once one\n'
            env=dict(os.environ);env['PATH']=str(Path(self.shell).parent)+os.pathsep+env.get('PATH','')
            process=subprocess.run([self.shell,'-s'],input=script.encode(),capture_output=True,env=env,timeout=10)
            self.assertIn(process.returncode,(0,1),process.stderr.decode(errors='replace'))
            return conf.read_text('utf8'),process.returncode

    def test_completed_one_shot_removed_other_rows_unchanged(self):
        keep='repeat|1|07:30|12345|sound\nother|0|08:00||sound\n'
        result,code=self.run_cleanup('one|0|07:30||sound\n'+keep)
        self.assertEqual(code,0);self.assertEqual(result,keep)

    def test_enabled_manual_test_or_reenabled_alarm_is_preserved(self):
        line='one|1|07:30||sound\n'
        self.assertEqual(self.run_cleanup(line)[0],line)

    def test_recurring_alarm_is_preserved_even_when_disabled(self):
        line='one|0|07:30|12345|sound\n'
        self.assertEqual(self.run_cleanup(line)[0],line)

    def test_pending_snooze_keeps_record_for_next_ring(self):
        line='one|0|07:30||sound\n'
        result,code=self.run_cleanup(line,'printf "2000000000|one\\n" > "$SNOOZE"')
        self.assertEqual(code,1);self.assertEqual(result,line)

    def test_lock_contention_keeps_record_for_retry(self):
        line='one|0|07:30||sound\n'
        result,code=self.run_cleanup(line,'conf_lock() { return 1; }')
        self.assertEqual(code,1);self.assertEqual(result,line)
