import unittest
from unittest.mock import Mock, patch
import server


class LegacyAppDetection(unittest.TestCase):
    def panel(self, output):
        p = server.Panel(Mock())
        p.adb.serial = 'test-device'
        p.adb.shell.return_value = (output, '', 0)
        return p

    def test_running_install_wins_over_profile_default(self):
        p = self.panel('/data/local/yam\n/data/quazar/yam\n@running\n'
                       '/bkupgrade/data/quazar/yam/bin/yamd\n')
        with patch.object(server, 'YAM_DIR', '/data/local/yam'):
            self.assertEqual(p._yam_root(), '/data/quazar/yam')
        p.adb.push.assert_not_called()

    def test_stopped_install_in_alternate_layout_is_found(self):
        p = self.panel('/data/local/yam\n@running\n')
        with patch.object(server, 'YAM_DIR', '/data/quazar/yam'):
            self.assertEqual(p._yam_root(), '/data/local/yam')

    def test_device_output_cannot_supply_arbitrary_shell_path(self):
        p = self.panel('/tmp/evil;reboot\n@running\n')
        self.assertEqual(p._yam_root(), server.YAM_DIR)

    def test_status_uses_detected_binary_and_accepts_running_manager(self):
        p = self.panel('running\nlistening\n')
        p._yam_root = Mock(return_value='/data/quazar/yam')
        status = p.yam_status()
        self.assertTrue(status['installed'])
        self.assertTrue(status['running'])
        self.assertIn('[ -x /data/quazar/yam/bin/yamd ]', p.adb.shell.call_args.args[0])

    def test_version_reads_detected_install_without_modifying_device(self):
        p = self.panel('abc123 /data/quazar/yam/bin/yamd\n')
        p._yam_root = Mock(return_value='/data/quazar/yam')
        p._yam_release_list = Mock(return_value=[])
        p.yam_version()
        self.assertIn('sha256sum /data/quazar/yam/bin/yamd', p.adb.shell.call_args.args[0])
        p.adb.push.assert_not_called()


class AssistantInstallResources(unittest.TestCase):
    def test_low_space_fails_before_download_or_device_write(self):
        p = server.Panel(Mock())
        p.adb.shell.return_value = ('aarch64\n', '', 0)
        p.yam2_preflight = Mock(return_value={
            'compatible': False, 'available_kib': 24684, 'free_kib': 2048,
            'issues': ['нужно не менее 32 МБ свободного /data']})
        p._assist_binary = Mock()
        with self.assertRaisesRegex(server.AdbError, 'свободно /data: 2.0 МБ'):
            p.assistant_install()
        p._assist_binary.assert_not_called()
        p.adb.push.assert_not_called()
        self.assertEqual(p.adb.shell.call_args_list[0].args[0], 'uname -m')
        self.assertEqual(p.adb.shell.call_count, 1)

    def test_uninstalled_status_explains_blocked_install(self):
        p = server.Panel(Mock())
        p.adb.shell.return_value = ('@status\n{}\n', '', 0)
        p._assist_config = Mock(return_value={})
        p.yam2_preflight = Mock(return_value={'compatible': False, 'free_kib': 2048})
        result = p.assistant_status()
        self.assertFalse(result['installed'])
        self.assertEqual(result['preflight']['free_kib'], 2048)

    def test_arm64_alias_passes_resource_check(self):
        p = server.Panel(Mock())
        p.adb.shell.return_value = ('arm64\n65536\n65536\n', '', 0)
        self.assertTrue(p.yam2_preflight()['compatible'])
