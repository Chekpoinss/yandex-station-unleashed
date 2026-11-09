import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from test_profiles import fresh_server
import profiles
import server
import multi


class Station2Colors(unittest.TestCase):
    def test_primaries_and_mixed_colors_use_rgbx_and_preview_reads_same_channels(self):
        s = fresh_server(); profiles.install(s, 'station2')
        panel = s.Panel(Mock())
        panel.adb.shell.return_value = ('', '', 0)
        frames = []
        panel.adb.push.side_effect = lambda path, dest: frames.append(Path(path).read_bytes())
        # This device's LED controller consumes RGBX despite reporting BGRX fb offsets.
        for rgb in ((255,0,0),(0,255,0),(0,0,255),(255,255,255),(108,71,255),(255,102,0),(11,37,83)):
            panel.ring(dict(zip(('red','green','blue'),rgb)))
            self.assertEqual(frames[-1],bytes((*rgb,0))*89)
            panel.adb.read_bytes.return_value = frames[-1]
            self.assertEqual(panel.clock_state()['ring_all'],[list(rgb)]*89)


class UpdateLifecycle(unittest.TestCase):
    def test_multi_stops_children_before_replacing_process(self):
        events = Mock(); workers = Mock()
        events.attach_mock(workers.stop, 'stop')
        with patch.object(multi.server,'restart_self') as restart:
            events.attach_mock(restart,'restart')
            multi.restart_workers(workers)
        self.assertEqual([call[0] for call in events.mock_calls],['stop','restart'])
    def test_fork_update_cannot_replace_sources_or_restart(self):
        updater = server.PanelUpdater(restart=Mock())
        updater.check = Mock(return_value={'update': True, 'latest': '99', 'install': 'zip'})
        updater._from_zip = Mock()
        with patch.object(server.threading, 'Timer') as timer, self.assertRaisesRegex(server.AdbError, 'отключено'):
            updater.update()
        updater._from_zip.assert_not_called()
        updater.check.assert_not_called()
        timer.assert_not_called()
        self.assertFalse(updater.lock.locked())
    def test_missing_module_repair_does_not_download_upstream(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td, patch.object(server, 'ROOT', Path(td)), patch('builtins.print'):
            updater = server.PanelUpdater()
            updater._from_zip = Mock()
            self.assertIn('server.py', updater.repair_missing()['missing'])
            updater._from_zip.assert_not_called()



class YamServices(unittest.TestCase):
    def test_service_fallback_and_unrelated_errors(self):
        panel = server.Panel(Mock())
        panel._yam_root = Mock(return_value=server.YAM_DIR)
        panel._yam = Mock(side_effect=[server.AdbError('package launch mode does not support this command'), {}])
        panel.yam_action('run','example')
        self.assertEqual(panel._yam.call_args.args,('POST','/api/plugins/example/service/start'))
        panel._yam = Mock(side_effect=server.AdbError('permission denied'))
        with self.assertRaisesRegex(server.AdbError,'permission denied'):
            panel.yam_action('run','example')
        self.assertEqual(panel._yam.call_count,1)

class LocalYamProxy(unittest.TestCase):
    def test_adb_forward_never_uses_system_proxy(self):
        import os, threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(b'{"ok":true}')
        http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            p=server.Panel(Mock());p._yam_port=http.server_port
            with patch.dict(os.environ,{'http_proxy':'http://127.0.0.1:1','HTTP_PROXY':'http://127.0.0.1:1','no_proxy':'','NO_PROXY':''}):
                self.assertEqual(p._yam('GET','/api/plugins'),{'ok':True})
        finally:
            http.shutdown();http.server_close();thread.join()
