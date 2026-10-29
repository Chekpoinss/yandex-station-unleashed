import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import multi
import profiles

ROOT = Path(__file__).resolve().parents[1]


def fresh_server():
    spec = importlib.util.spec_from_file_location('isolated_panel', ROOT/'server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProfileTests(unittest.TestCase):
    def test_default_preserves_lite2_hardware_methods(self):
        s = fresh_server()
        previous = [s.Panel.clock, s.Panel.ring, s.Panel._ensure_hook, s.Adb.shell, s.LedStream]
        profiles.install(s)
        self.assertEqual(previous, [s.Panel.clock, s.Panel.ring, s.Panel._ensure_hook, s.Adb.shell, s.LedStream])
        self.assertEqual(s.GLAGOL_PLATFORM, 'plum')

    def test_pro_clock_scripts_use_pickle_without_altering_ring_count(self):
        s = fresh_server()
        profiles.install(s, 'mini3pro')
        self.assertIn('pickle:white:clock', s.display_script('date'))
        self.assertNotIn('plum:white:clock', s.display_script('date'))
        self.assertEqual(len(s.CLOCK_LEDS), 36)

    def test_experimental_model_rejects_led_writes_before_adb(self):
        s = fresh_server()
        profiles.install(s, 'mini3')
        p = s.Panel(Mock())
        for action in (lambda: p.clock({'text':'12:34'}), lambda: p.ring({'red':128}), lambda: p._ensure_hook()):
            with self.assertRaises(s.AdbError): action()
        p.adb.assert_not_called()
        self.assertEqual(p.adb.mock_calls, [])

    def test_other_profiles_block_boot_restore_alarm_posts(self):
        for model in ('station2', 'mini3pro', 'mini3'):
            s = fresh_server()
            s.Handler.do_POST = Mock()
            original = s.Handler.do_POST
            profiles.install(s, model)
            for path in ('/api/clock/boot', '/api/boot/install', '/api/alarms/set', '/api/backup/restore', '/api/footprint/reset'):
                request = SimpleNamespace(path=path, _guard=lambda _:False, _send=Mock())
                s.Handler.do_POST(request)
                self.assertEqual(request._send.call_args.args[0], 400)
            original.assert_not_called()


class ConfigTests(unittest.TestCase):
    def load(self, speakers):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'speakers.json'
            path.write_text(json.dumps({'speakers':speakers}), encoding='utf8')
            return multi.load_config(path)

    def test_multiple_instances_of_one_model_have_separate_ids(self):
        value = self.load([dict(id=name, model='lite2', device='example.local') for name in ('room-a','room-b')])
        self.assertEqual(list(value), ['room-a','room-b'])

    def test_invalid_config_is_rejected_before_launch(self):
        valid = dict(id='room', model='lite2', device='example.local')
        for speakers in ([], [valid,valid], [dict(valid,id='../room')], [dict(valid,model='unknown')],
                         [dict(valid,device='two addresses')], [dict(valid,name='')]):
            with self.subTest(speakers=speakers), self.assertRaises(ValueError): self.load(speakers)


if __name__ == '__main__': unittest.main()
