import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import server


class AddressChange(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / '.panel_settings.json'
        self.before = {'device': '192.168.1.50:5555', 'model': 'lite1', 'other_setting': True}
        self.file.write_text(json.dumps(self.before))
        self.panel = SimpleNamespace(adb=SimpleNamespace(adb='adb'), library=SimpleNamespace(phase='stopped'), scenes=None)
        patcher = patch.object(server, 'SETTINGS_FILE', self.file); patcher.start(); self.addCleanup(patcher.stop)

    def test_invalid_addresses_never_start_adb(self):
        for value in ('', None, '192.168.1.999', '192.168.1.2;reboot', 'http://192.168.1.2', '127.0.0.1', '0.0.0.0', '192.168.1.2:0', '192.168.1.2:65536'):
            with patch.object(server.subprocess, 'run') as run:
                with self.assertRaises(ValueError): server.change_connection_address(self.panel, value, Mock())
                run.assert_not_called()
        self.assertEqual(json.loads(self.file.read_text()), self.before)

    def test_success_probes_then_saves_all_settings_and_schedules_panel_restart(self):
        with patch.object(server.subprocess, 'run') as run, patch.object(server, 'Adb') as adb, patch.object(server.threading, 'Timer') as timer:
            adb.return_value.shell.return_value = ('__panel_address_check__', '', 0)
            restart = Mock()
            result = server.change_connection_address(self.panel, '192.168.1.51', restart)
            self.assertTrue(result['restarting'])
            self.assertEqual(run.call_args.args[0], ['adb', 'connect', '192.168.1.51:5555'])
            adb.return_value.shell.assert_called_once_with('echo __panel_address_check__', timeout=5, check=False)
            timer.assert_called_once_with(.8, restart); timer.return_value.start.assert_called_once()
        saved = json.loads(self.file.read_text())
        self.assertEqual(saved['model'], 'lite1'); self.assertTrue(saved['other_setting'])
        self.assertEqual(saved['device'], '192.168.1.51:5555'); self.assertTrue(saved['device_from_panel'])

    def test_failed_probe_preserves_previous_address_and_never_restarts(self):
        with patch.object(server.subprocess, 'run'), patch.object(server, 'Adb') as adb, patch.object(server.threading, 'Timer') as timer:
            adb.return_value.shell.side_effect = server.AdbError('unreachable')
            with self.assertRaisesRegex(ValueError, 'прежний адрес сохранён'):
                server.change_connection_address(self.panel, '192.168.1.51', Mock())
            timer.assert_not_called()
        self.assertEqual(json.loads(self.file.read_text()), self.before)

    def test_playing_audio_blocks_restart_before_any_probe(self):
        self.panel.library.phase = 'playing'
        with patch.object(server.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'остановите воспроизведение'):
                server.change_connection_address(self.panel, '192.168.1.51', Mock())
            run.assert_not_called()

    def test_new_address_survives_old_launcher_arguments(self):
        settings = {'device': '192.168.1.51:5555', 'device_from_panel': True}
        self.assertEqual(server.initial_device('192.168.1.50:5555', settings), '192.168.1.51:5555')
        self.assertEqual(server.initial_device('192.168.1.50', {'device': '192.168.1.51:5555'}), '192.168.1.50:5555')
        self.assertEqual(server.initial_device(None, settings), '192.168.1.51:5555')
