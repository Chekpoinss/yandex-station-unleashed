import base64
import json
import pathlib
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from music_library import Library, native_audio_payload


class NativeAudioTests(unittest.TestCase):
    def test_directive_preserves_utf8_metadata_and_mp3_url(self):
        url = 'http://192.168.1.2:8767/' + 'a' * 100 + '.mp3'
        result = native_audio_payload(url, {'id': 'abc', 'title': 'Песня', 'artist': 'Исполнитель'})
        self.assertEqual(result['command'], 'externalCommandBypass')
        raw = base64.b64decode(result['data'])
        fields = {}
        i = 0
        while i < len(raw):
            tag = raw[i] >> 3
            i += 1
            length, shift = 0, 0
            while True:
                byte = raw[i]
                i += 1
                length |= (byte & 127) << shift
                if not byte & 128:
                    break
                shift += 7
            fields[tag] = raw[i:i+length]
            i += length
        self.assertEqual(fields[1], b'audio_play')
        payload = json.loads(fields[2])
        self.assertEqual(payload['stream']['url'], url)
        self.assertEqual(payload['stream']['format'], 'MP3')
        self.assertEqual(payload['metadata']['title'], 'Песня')

    def test_head_accepts_station_query_parameter_but_rejects_wrong_token(self):
        library = Library.__new__(Library)
        library.lock = threading.RLock()
        library.token = 'secret'
        library.play_token = 'track'
        server = ThreadingHTTPServer(('127.0.0.1', 0), library.stream_handler())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            request = urllib.request.Request(base + '/secret/track.mp3?vsid=station', method='HEAD')
            with op.open(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Content-Type'], 'audio/mpeg')
            with self.assertRaises(urllib.error.HTTPError) as error:
                op.open(urllib.request.Request(base + '/wrong/track.mp3', method='HEAD'), timeout=3)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
