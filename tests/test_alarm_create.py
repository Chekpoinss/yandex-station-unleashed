from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from zoneinfo import ZoneInfo

from alarm_music import create
from test_native_alarms import alarm


class AlarmCreateTests(unittest.TestCase):
    def setUp(self):
        epoch=datetime(2026,10,29,10,0,tzinfo=ZoneInfo('Europe/Moscow')).timestamp()
        self.snapshot={'alarms':[],'installed':True,'revision':'a'*64,
                       'device_time':{'epoch':epoch},'tz':'Europe/Moscow'}
        self.panel=SimpleNamespace(alarms=Mock(side_effect=lambda:dict(self.snapshot)),
            adb=SimpleNamespace(read_bytes=Mock(return_value=b'')),alarms_save=Mock())
        self.lib=SimpleNamespace(panel=self.panel)
        self.prepare=patch('alarm_music.prepare',return_value={'sound':'u_1234567890.wav','sound_name':'Test song'})
        self.prepare.start();self.addCleanup(self.prepare.stop)

    def test_native_collision_never_writes_a_second_alarm(self):
        self.panel.adb.read_bytes.return_value=alarm(hour=12,minute=34)
        with self.assertRaisesRegex(ValueError,'Алисы'):create(self.lib,'track',{'time':'12:34'},lambda:True)
        self.panel.alarms_save.assert_not_called()

    def test_existing_local_alarm_never_duplicated(self):
        self.snapshot['alarms']=[{'id':'existing','on':True,'time':'12:34','days':''}]
        with self.assertRaisesRegex(ValueError,'панели'):create(self.lib,'track',{'time':'12:34'},lambda:True)
        self.panel.alarms_save.assert_not_called()

    def test_unknown_native_record_fails_closed(self):
        self.panel.adb.read_bytes.return_value=b'\x1a\x00'
        with self.assertRaisesRegex(ValueError,'все штатные'):create(self.lib,'track',{'time':'12:34'},lambda:True)
        self.panel.alarms_save.assert_not_called()

    def test_save_keeps_revision_guard_and_requires_durable_receipt(self):
        self.panel.alarms_save.side_effect=lambda data:{'alarms':data['alarms'],'running':True}
        guard=lambda:True
        result=create(self.lib,'track',{'time':'12:34'},guard)
        self.assertTrue(result['created']);self.assertTrue(result['running'])
        saved=self.panel.alarms_save.call_args.args[0]
        self.assertEqual(saved['revision'],'a'*64);self.assertIs(saved['_guard'],guard)
        self.assertEqual(saved['alarms'][0]['ramp'],0)
