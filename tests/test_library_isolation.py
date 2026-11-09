import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import music_library

class LibraryIsolationTests(unittest.TestCase):
    def test_two_workers_keep_library_queue_and_stream_capabilities_separate(self):
        with tempfile.TemporaryDirectory() as td:
            def panel(name):return SimpleNamespace(data_dir=Path(td)/name,glagol=SimpleNamespace(state_callbacks=[]),ymusic=Mock(),adb=SimpleNamespace(serial=name+':5555'))
            http1=Mock(server_address=('127.0.0.1',40001));http2=Mock(server_address=('127.0.0.1',40002));http3=Mock(server_address=('127.0.0.1',40003))
            with patch.dict(os.environ,{'PANEL_GATEWAY_SECRET':'fixture-only'}),patch.object(music_library.socket,'socket') as sock,patch.object(music_library,'ThreadingHTTPServer',side_effect=[http1,http2,http3]) as http,patch.object(music_library.threading,'Thread'),patch('music_artwork.ArtworkWorker'),patch('yandex_library.YandexLibrary'):
                sock.return_value.getsockname.return_value=('127.0.0.1',1234)
                first=music_library.Library(panel('room-a'));second=music_library.Library(panel('room-b'))
                first.data['tracks']['abc']={'id':'abc','title':'Private A','cover':'/api/library/cover/abc'}
                first.data['last_queue']=['abc'];first.save()
                second.data['music_lights']={'enabled':False};second.save()
                restored=music_library.Library(panel('room-a'))
                self.assertIn('abc',restored.data['tracks']);self.assertNotIn('abc',second.data['tracks'])
                self.assertNotEqual(first.root,second.root)
                self.assertNotEqual(first.token,second.token)
                self.assertNotEqual(first.port,second.port)
                self.assertTrue(all(call.args[0][1]==0 for call in http.call_args_list))
                self.assertEqual(json.loads(second.index.read_text('utf8'))['tracks'],{})

    def test_first_launch_without_speaker_or_network(self):
        for serial in (None, '', 'usb-serial'):
            with self.subTest(serial=serial), tempfile.TemporaryDirectory() as td:
                panel = SimpleNamespace(data_dir=Path(td),
                    glagol=SimpleNamespace(state_callbacks=[]), ymusic=Mock(),
                    adb=SimpleNamespace(serial=serial))
                with patch.object(music_library.socket, 'socket') as sock, \
                     patch.object(music_library, 'ThreadingHTTPServer') as http, \
                     patch.object(music_library.threading, 'Thread'), \
                     patch('music_artwork.ArtworkWorker'):
                    sock.return_value.connect.side_effect = OSError('network unavailable')
                    http.return_value.server_address = ('127.0.0.1', 8767)
                    library = music_library.Library(panel)
                    self.assertEqual(library.host, '127.0.0.1')
                    http.assert_called_once()
                    sock.return_value.close.assert_called_once()
