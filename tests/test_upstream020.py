import unittest
from unittest.mock import Mock
import server,glagol

BLUEZ='''object path "/org/bluez/hci0"
array [
string "org.bluez.Adapter1"
array [
string "Powered"
variant boolean true
object path "/org/bluez/hci0/dev_A/player0"
array [
string "org.bluez.MediaPlayer1"
array [
string "Position"
variant uint32 12345
'''

class Upstream020Tests(unittest.TestCase):
    def test_dbus_parser_preserves_types_and_player_position(self):
        objects=server.parse_bluez(BLUEZ)
        self.assertIs(objects['/org/bluez/hci0']['org.bluez.Adapter1']['Powered'],True)
        self.assertEqual(objects['/org/bluez/hci0/dev_A/player0']['org.bluez.MediaPlayer1']['Position'],12345)
    def test_bluetooth_poll_does_not_launch_bluetoothctl(self):
        p=server.Panel(Mock());p.adb.shell.return_value=(BLUEZ+'\n@@PAIR\n@@OUT\n@@VOL\n@@LAST\n@@NAME\n','',0)
        self.assertTrue(p.bt_state()['powered'])
        self.assertNotIn('bluetoothctl',p.adb.shell.call_args.args[0])
    def test_external_bluetooth_changes_are_guarded_before_device_access(self):
        p=server.Panel(Mock());p.btout_supported=False
        for fn,args in ((p.btout_scan,()),(p.btout_use,('AA:BB:CC:DD:EE:FF',)),(p.btout_off,()),(p.btout_volume,(50,))):
            with self.assertRaises(server.AdbError):fn(*args)
        p.adb.shell.assert_not_called();p.adb.push.assert_not_called()
    def test_missing_yandex_title_no_longer_breaks_short_track(self):
        ym=glagol.YandexMusic(Mock())
        self.assertEqual(ym._short({'id':1,'title':None})['title'],'')
    def test_wave_queue_omits_neighbors_without_metadata(self):
        ym=glagol.YandexMusic(Mock());ym._raw=Mock(side_effect=lambda ident:{'id':ident,**({'title':'Known'} if ident=='good' else {})})
        result=ym.queue({'id':'good','playlistType':'Radio','playlistId':'test','entityInfo':{'prev':{'id':'empty'}}})
        self.assertEqual([t['id'] for t in result['tracks']],['good'])

if __name__=='__main__':unittest.main()
