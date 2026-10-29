import unittest
from types import SimpleNamespace
from music_metadata import lyrics_text, embedded_lyrics


class LyricsTests(unittest.TestCase):
    def test_lrc_multiple_stamps_offset_and_order(self):
        result = lyrics_text('[offset:-500]\n[00:20.00]later\n[00:02.50][00:05]repeat')
        self.assertEqual(result['sync'], [[2, 'repeat'], [4.5, 'repeat'], [19.5, 'later']])

    def test_plain_text_has_no_fake_timing(self):
        self.assertEqual(lyrics_text('first\nsecond')['sync'], None)

    def test_enhanced_lrc_keeps_line_timing_without_fake_word_timing(self):
        self.assertEqual(lyrics_text('[00:01]<00:01>hello <00:02>world')['sync'], [[1, 'hello world']])

    def test_invalid_seconds_are_not_timestamps(self):
        self.assertIsNone(lyrics_text('[00:99]invalid')['sync'])

    def test_mp4_and_vorbis_lyrics(self):
        for key in ('lyrics', '\xa9lyr'):
            with self.subTest(key=key):
                result = embedded_lyrics(SimpleNamespace(tags={key: ['[00:01]one']}))
                self.assertEqual(result['sync'], [[1, 'one']])

    def test_id3_sylt_uses_only_milliseconds(self):
        from mutagen.id3 import ID3, SYLT
        tags = ID3()
        tags.add(SYLT(encoding=3, lang='eng', format=2, type=1, text=[('first', 1000), ('second', 2000)]))
        self.assertEqual(embedded_lyrics(SimpleNamespace(tags=tags))['sync'], [[1, 'first'], [2, 'second']])
        tags = ID3()
        tags.add(SYLT(encoding=3, lang='eng', format=1, type=1, text=[('frames are not milliseconds', 1000)]))
        self.assertIsNone(embedded_lyrics(SimpleNamespace(tags=tags)))

    def test_id3_unsynced_text(self):
        from mutagen.id3 import ID3, USLT
        tags = ID3()
        tags.add(USLT(encoding=3, lang='eng', text='plain lyrics'))
        self.assertEqual(embedded_lyrics(SimpleNamespace(tags=tags))['text'], 'plain lyrics')


if __name__ == '__main__':
    unittest.main()
