import base64
import pathlib
import sys
import tempfile
import threading
import unittest
import shutil
import subprocess

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import music_library
from mutagen.id3 import ID3, APIC


class LocalImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bundled = pathlib.Path(__file__).parents[1] / 'runtime' / 'ffmpeg.exe'
        ffmpeg = str(bundled) if bundled.is_file() else shutil.which('ffmpeg')
        if not ffmpeg:
            raise unittest.SkipTest('FFmpeg is required to generate the local audio fixture')
        cls.fixture = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.source = pathlib.Path(cls.fixture.name) / 'fixture.mp3'
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                        '-i', 'anullsrc=r=8000:cl=mono', '-t', '12', '-c:a', 'libmp3lame',
                        '-b:a', '32k', '-metadata', 'title=Проверка потока', '-metadata',
                        'artist=Волна', str(cls.source)], capture_output=True, check=True, timeout=15,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = music_library.Library.__new__(music_library.Library)
        self.library.root = pathlib.Path(self.temp.name)
        self.library.index = self.library.root / 'index.json'
        self.library.data = {'tracks': {}, 'playlists': {}}
        self.library.lock = threading.RLock()

    def tearDown(self):
        self.temp.cleanup()

    def test_tags_original_bytes_and_deduplication(self):
        body = self.source.read_bytes()
        first = self.library.import_file('../../outside.mp3', body)
        second = self.library.import_file('another-name.mp3', body)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(self.library.data['tracks']), 1)
        self.assertEqual(first['title'], 'Проверка потока')
        self.assertEqual(first['artist'], 'Волна')
        self.assertGreater(first['duration'], 11)
        saved = self.library.root / self.library.data['tracks'][first['id']]['file']
        self.assertEqual(saved.read_bytes(), body)
        self.assertEqual(saved.parent, self.library.root)
        self.assertNotIn('file', first)

    def test_embedded_cover_is_extracted(self):
        source = self.library.root / 'with-cover.mp3'
        source.write_bytes(self.source.read_bytes())
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==')
        tags = ID3(source)
        tags.add(APIC(mime='image/png', type=3, data=png))
        tags.save(source)
        track = self.library.import_file('with-cover.mp3', source.read_bytes())
        self.assertTrue(track['cover'].startswith('/api/library/cover/'))
        self.assertEqual((self.library.root / (track['id'] + '.cover')).read_bytes(), png)

    def test_invalid_audio_does_not_enter_library(self):
        with self.assertRaises(Exception):
            self.library.import_file('not-a-song.mp3', b'not audio')
        self.assertFalse(self.library.data['tracks'])
        self.assertFalse(list(self.library.root.glob('*.mp3')))

    def test_non_audio_extension_rejected(self):
        with self.assertRaises(ValueError):
            self.library.import_file('bad.html', self.source.read_bytes())


if __name__ == '__main__':
    unittest.main()
