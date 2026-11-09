import hashlib
import subprocess
import shlex
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from sound_overrides import SoundOverrides
import server

class SoundOverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.panel=SimpleNamespace(data_dir=Path(self.tmp.name),adb=Mock(serial='test-device'),ffmpeg=True,sounds=lambda:['common/test.opus','common/boot.wav'],_audio_like=Mock(return_value=b'encoded'))
        self.panel.adb.read_bytes.return_value=b'original'
        self.panel.adb.shell.side_effect=lambda cmd,**kw:((hashlib.sha256(b'encoded').hexdigest()+' filename') if 'sha256sum' in cmd else '', '',0)
        self.over=SoundOverrides(self.panel,server)
    def test_foreign_mount_is_never_unmounted_or_written(self):
        with patch.object(self.over,'mounted',return_value=True),patch.object(self.over,'owned',return_value=False):
            with self.assertRaisesRegex(ValueError,'другой'):self.over.replace('common/test.opus',b'audio')
        self.panel.adb.push.assert_not_called();self.panel.adb.shell.assert_not_called()
    def test_backup_before_mount_and_no_system_write_or_autostart(self):
        with patch.object(self.over,'mounted',return_value=False):self.over.replace('common/test.opus',b'audio')
        self.assertEqual(next(self.over.root.glob('*.original')).read_bytes(),b'original')
        commands=[c.args[0] for c in self.panel.adb.shell.call_args_list]
        self.assertTrue(any('mount --bind' in c for c in commands))
        self.assertFalse(any('remount' in c or 'rootkeeper' in c or '/etc/init' in c for c in commands))
        for call in self.panel.adb.push.call_args_list:self.assertTrue(call.args[1].startswith('/data/'))
    def test_restore_exposes_original_without_overwriting_system(self):
        self.over.records['common/test.opus']={'original_sha256':hashlib.sha256(b'original').hexdigest()}
        with patch.object(self.over,'mounted',side_effect=[True]),patch.object(self.over,'owned',return_value=True):self.over.reset('common/test.opus')
        self.panel.adb.push.assert_not_called()
        self.assertTrue(any(c.args[0].startswith('umount ') for c in self.panel.adb.shell.call_args_list))
        self.assertEqual(self.over.records,{})
    def test_changed_firmware_original_aborts_before_any_upload(self):
        _,_,key=self.over.paths('common/test.opus');(self.over.root/(key+'.original')).write_bytes(b'old-firmware')
        with patch.object(self.over,'mounted',return_value=False):
            with self.assertRaisesRegex(ValueError,'изменился'):self.over.replace('common/test.opus',b'audio')
        self.panel.adb.push.assert_not_called()
    def test_paths_and_boot_are_rejected(self):
        for name in ('../test','/system/test','common/boot.wav','bad\nname',None):
            with self.assertRaises(ValueError):self.over.paths(name)
    def test_original_playback_checks_backup_without_changing_mount(self):
        _,_,key=self.over.paths('common/test.opus');(self.over.root/(key+'.original')).write_bytes(b'original')
        self.over.records['common/test.opus']={'original_sha256':hashlib.sha256(b'original').hexdigest()}
        self.panel._transcode_and_play=Mock(return_value={'ok':True})
        self.over.play_original('common/test.opus');self.panel._transcode_and_play.assert_called_once_with(b'original')
        self.panel.adb.shell.assert_not_called();self.panel.adb.push.assert_not_called()
        (self.over.root/(key+'.original')).write_bytes(b'changed')
        with self.assertRaises(ValueError):self.over.play_original('common/test.opus')
    def test_failed_new_bind_restores_previous_mount(self):
        target,base,key=self.over.paths('common/test.opus')
        self.over.records['common/test.opus']={'original_sha256':hashlib.sha256(b'original').hexdigest()}
        (self.over.root/(key+'.original')).write_bytes(b'original')
        def shell(cmd,**kw):
            if 'sha256sum' in cmd:return hashlib.sha256(b'encoded').hexdigest()+' file','',0
            return ('','',1 if '.next' in cmd and 'mount --bind' in cmd else 0)
        self.panel.adb.shell.side_effect=shell
        with patch.object(self.over,'mounted',return_value=True),patch.object(self.over,'owned',return_value=True):
            with self.assertRaises(ValueError):self.over.replace('common/test.opus',b'audio')
        self.assertTrue(any(c.args[0]==f'mount --bind {base}.custom {target}' for c in self.panel.adb.shell.call_args_list))

class SayTests(unittest.TestCase):
    def test_text_is_file_not_shell_and_only_alice_is_supported(self):
        panel=server.Panel(Mock());panel.detach=Mock()
        pushed=[];panel.adb.push.side_effect=lambda f,path:pushed.append(Path(f).read_text('utf8'))
        text='hello $(reboot); secret'
        panel.say(text)
        self.assertEqual(pushed,['Повтори за мной: '+text+'\n'])
        self.assertNotIn(text,panel.detach.call_args.args[1])
        panel.assistant_command = Mock(return_value={'ok': True})
        panel.say('hello',voice='assistant')
        panel.assistant_command.assert_called_once_with({'say':'hello'})
    def test_empty_and_long_text_never_touch_device(self):
        panel=server.Panel(Mock())
        for text in ('',' '*3,'a'*301):
            with self.assertRaises(server.AdbError):panel.say(text)
        panel.adb.push.assert_not_called()

class BootOverrideTests(unittest.TestCase):
    def test_record_restores_only_matching_original_and_allowed_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            web, quasar = root/'web', root/'quasar'
            folder = web/'animation-overrides';folder.mkdir(parents=True)
            target = quasar/'ledpatterns'/'answer.led';target.parent.mkdir(parents=True)
            base = folder/'test'
            target.write_bytes(b'original')
            base.with_suffix('.custom').write_bytes(b'custom')
            base.with_suffix('.target').write_text(str(target)+'\n')
            base.with_suffix('.sha256').write_text(hashlib.sha256(b'original').hexdigest()+'\n')
            line = next(x for x in server.BOOT_HOOK_SCRIPT.splitlines() if x.startswith('for d in '))
            line = line.replace(server.WEB_DIR, str(web)).replace(server.QUASAR, str(quasar))
            script = 'bind() { cp "$1" "$2"; }; '+line+'; true'
            subprocess.run(['sh','-c',script],check=True)
            self.assertEqual(target.read_bytes(),b'custom')
            target.write_bytes(b'new firmware')
            subprocess.run(['sh','-c',script],check=True)
            self.assertEqual(target.read_bytes(),b'new firmware')
            outside = root/'foreign';outside.write_bytes(b'original')
            base.with_suffix('.target').write_text(str(outside)+'\n')
            subprocess.run(['sh','-c',script],check=True)
            self.assertEqual(outside.read_bytes(),b'original')

    def test_boot_record_publishes_target_after_checksum(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);remote=root/'remote';remote.mkdir()
            panel=SimpleNamespace(data_dir=root,adb=Mock(serial='test'),sounds=lambda:['common/test.opus'])
            panel.adb.shell.side_effect=lambda cmd,**kw: (subprocess.run(['sh','-c',cmd],capture_output=True,text=True).stdout,'',0)
            overrides=SoundOverrides(panel,server);overrides.remote=str(remote)
            record={'original_sha256':hashlib.sha256(b'original').hexdigest()}
            overrides.boot_record('common/test.opus',record)
            target,base,_=overrides.paths('common/test.opus')
            self.assertEqual(Path(base+'.target').read_text(),target+'\n')
            self.assertEqual(Path(base+'.sha256').read_text(),record['original_sha256']+'\n')
