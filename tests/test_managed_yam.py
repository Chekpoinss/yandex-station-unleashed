import hashlib
import io
import json
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
from unittest.mock import Mock, patch
import managed_yam


class UpdateValidation(unittest.TestCase):
    def panel(self):
        class Panel:
            YAM_ASSET = 'yamd-aarch64-linux-musl'
            def yam_plugins(self): return []
            def _yam(self, *args): return {'updates': {'enabled': False}}
            def yam_status(self): return {'installed': True, 'running': True}
        context = SimpleNamespace(Panel=Panel, AdbError=RuntimeError, YAMD='/data/local/yam/bin/yamd')
        managed_yam.install(context)
        panel = Panel();panel.adb = Mock();panel.adb.shell.return_value = ('','',0)
        return panel

    def downloads(self, blob, expected):
        release = {'tag_name': 'v0.1.17', 'assets': [
            {'name':'SHA256SUMS','browser_download_url':'https://example/sums'},
            {'name':'yamd-aarch64-linux-musl','browser_download_url':'https://example/binary','digest':'sha256:'+expected}]}
        return [io.BytesIO(json.dumps(release).encode()),
                io.BytesIO((expected+'  yamd-aarch64-linux-musl').encode()), io.BytesIO(blob)]

    def test_bad_sha_never_stops_or_pushes(self):
        panel = self.panel()
        with patch.object(managed_yam.urllib.request,'urlopen',side_effect=self.downloads(b'wrong','0'*64)):
            with self.assertRaisesRegex(RuntimeError,'Контрольная сумма'):
                panel.yam_install_daemon()
        panel.adb.push.assert_not_called()
        self.assertFalse(any(' stop' in call.args[0] for call in panel.adb.shell.call_args_list))

    def test_same_release_does_not_restart_packages(self):
        panel = self.panel();digest = hashlib.sha256(b'valid').hexdigest()
        panel.adb.shell.return_value = (digest+'  yamd','',0)
        with patch.object(managed_yam.urllib.request,'urlopen',side_effect=self.downloads(b'valid',digest)):
            result = panel.yam_install_daemon()
        self.assertTrue(result['unchanged'])
        panel.adb.push.assert_not_called()
        self.assertFalse(any(' stop' in call.args[0] for call in panel.adb.shell.call_args_list))

    def test_changed_auto_update_setting_rolls_back(self):
        panel = self.panel();digest = hashlib.sha256(b'valid').hexdigest()
        def shell(command, **kwargs):
            return ((digest if 'chmod 755' in command else '0'*64)+'  yamd','',0)
        panel.adb.shell.side_effect = shell
        panel._yam = Mock(side_effect=[{'updates':{'enabled':False}}, {'updates':{'enabled':True}}])
        with patch.object(managed_yam.urllib.request,'urlopen',side_effect=self.downloads(b'valid',digest)):
            with self.assertRaisesRegex(RuntimeError,'автообновления'):
                panel.yam_install_daemon()
        commands = [call.args[0] for call in panel.adb.shell.call_args_list]
        self.assertTrue(any('failed-data' in command and 'cp -p' in command for command in commands))
        self.assertTrue(any('rmdir /data/local/yam/.web-update-lock' in command for command in commands))

    def test_old_backups_need_consent_and_are_removed(self):
        panel = self.panel();digest = hashlib.sha256(b'valid').hexdigest()
        old = '2048\t/data/local/yam/backup-web-20261020T000000Z-abcdef12\n'
        def shell(command, **kwargs):
            if 'backup-web-*; do' in command: return (old,'',0)
            return ((digest if 'chmod 755' in command else '0'*64)+'  yamd','',0)
        panel.adb.shell.side_effect = shell
        self.assertEqual(panel.yam_status()['backups'], {'count': 1, 'kb': 2048})
        with self.assertRaisesRegex(RuntimeError,'резервные копии'):
            panel.yam_install_daemon()
        panel.adb.push.assert_not_called()
        self.assertFalse(any('rm -rf' in call.args[0] for call in panel.adb.shell.call_args_list))
        panel._yam = Mock(side_effect=[{'updates':{'enabled':False}}, {'updates':{'enabled':True}}])
        with patch.object(managed_yam.urllib.request,'urlopen',side_effect=self.downloads(b'valid',digest)):
            with self.assertRaises(RuntimeError):
                panel.yam_install_daemon(drop_backups=True)
        commands = [call.args[0] for call in panel.adb.shell.call_args_list]
        removed = next(i for i,c in enumerate(commands) if c == 'rm -rf /data/local/yam/backup-web-*')
        created = next(i for i,c in enumerate(commands) if 'mkdir /data/local/yam/backup-web-' in c)
        self.assertLess(removed, created, 'old copies go before the new one is made')

if __name__ == '__main__': unittest.main()
