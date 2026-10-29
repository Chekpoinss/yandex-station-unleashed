import io
import json
import unittest
from unittest.mock import Mock,patch
import server

class Yam2Tests(unittest.TestCase):
    def test_resource_preflight_is_read_only_and_insufficient_resources_block_install(self):
        p=server.Panel(Mock());p.adb.shell.return_value=('aarch64\n10000\n9000\n','','0')
        check=p.yam2_preflight()
        self.assertFalse(check['compatible'])
        with self.assertRaises(server.AdbError):p.yam2_install()
        self.assertFalse(any('mkdir' in c.args[0] or 'remount' in c.args[0] for c in p.adb.shell.call_args_list))
        p.adb.push.assert_not_called()
    def test_micro_detect_does_not_fall_back_to_mini(self):
        self.assertEqual(server.Panel.yam2_detect('yandex,station-micro amlogic,axg'),'yandexmicro')
    def test_autostart_requires_verified_existing_hook_before_writes(self):
        p=server.Panel(Mock());p._ensure_hook=Mock(side_effect=server.AdbError('Нет проверенного hook'))
        with self.assertRaises(server.AdbError):p.yam2_service('yam~luna~main','boot')
        p.adb.shell.assert_not_called();p.adb.push.assert_not_called()
    def test_invalid_package_and_service_paths_are_rejected(self):
        p=server.Panel(Mock())
        for name in ('../../etc','yam/pkg;reboot','yam/pkg@1\nboom'):
            with self.assertRaises(server.AdbError):p._yam2_pkg(name,True)
        for sid in ('x;reboot','yam~luna~main/../x'):
            with self.assertRaises(server.AdbError):p.yam2_service(sid,'stop')
        p.adb.shell.assert_not_called()
    def test_old_release_selection_skips_yam2_and_keeps_newest_yamd(self):
        p=server.Panel(Mock());p._yam_releases=(0,[])
        releases=[{'tag_name':'yam-v0.2','assets':[{'name':'yam-aarch64'}],'published_at':'2026-10-29'}, {'tag_name':'old','assets':[{'name':p.YAM_ASSET}],'published_at':'2026-10-20'}, {'tag_name':'new','assets':[{'name':p.YAM_ASSET}],'published_at':'2026-10-28'}]
        response=io.BytesIO(json.dumps(releases).encode())
        with patch.object(server.urllib.request,'urlopen',return_value=response):result=p._yam_release_list(True)
        self.assertEqual([r['tag_name'] for r in result],['new','old'])
    def test_micro_requires_static_aarch64_elf_not_32bit_glibc(self):
        blob=bytearray(120);blob[:6]=b'\x7fELF\x02\x01';blob[18:20]=(183).to_bytes(2,'little')
        blob[32:40]=(64).to_bytes(8,'little');blob[54:56]=(56).to_bytes(2,'little');blob[56:58]=(1).to_bytes(2,'little')
        blob[64:68]=(1).to_bytes(4,'little');server.Panel._yam2_validate_binary(blob)
        blob[64:68]=(3).to_bytes(4,'little')
        with self.assertRaises(server.AdbError):server.Panel._yam2_validate_binary(blob)
        blob[4]=1
        with self.assertRaises(server.AdbError):server.Panel._yam2_validate_binary(blob)
    def test_sufficient_preflight_is_compatible_without_running_any_binary(self):
        p=server.Panel(Mock());p.adb.shell.return_value=('aarch64\n131360\n79788\n','',0)
        self.assertTrue(p.yam2_preflight()['compatible'])
        p.adb.push.assert_not_called()
    def test_full_manager_remove_refuses_foreign_installation(self):
        p=server.Panel(Mock());p.adb.shell.side_effect=server.AdbError('foreign marker')
        with self.assertRaises(server.AdbError):p.yam2_uninstall()
        self.assertEqual(p.adb.shell.call_count,1)
        self.assertNotIn('mv ',p.adb.shell.call_args.args[0])
