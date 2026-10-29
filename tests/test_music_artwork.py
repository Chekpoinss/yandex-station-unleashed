import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from music_artwork import ArtworkWorker, ArtworkFailure, matching_releases, deezer_match, album_base
import json

RID = '00000000-0000-0000-0000-000000000001'


class ArtworkTests(unittest.TestCase):
    def setUp(self):
        self.track = {'id': 'local', 'source': 'local', 'title': 'My Song', 'artist': 'My Artist',
                      'album': 'My Album', 'duration': 120}
        self.record = {'title': 'My Song', 'length': 120000,
                       'artist-credit': [{'name': 'My Artist'}],
                       'releases': [{'id': RID, 'title': 'My Album'}]}

    def test_exact_metadata_and_duration_match(self):
        self.assertEqual(matching_releases(self.track, [self.record]), [RID])

    def test_different_artist_title_version_album_or_duration_is_rejected(self):
        variants = [{'artist-credit': [{'name': 'Other'}]}, {'title': 'My Song (Sped Up)'},
                    {'length': 170000}, {'releases': [{'id': RID, 'title': 'Other Album'}]}]
        for change in variants:
            with self.subTest(change=change):
                self.assertEqual(matching_releases(self.track, [{**self.record, **change}]), [])

    def test_missing_album_with_ambiguous_releases_is_rejected(self):
        self.track['album'] = ''
        self.record['releases'].append({'id': RID[:-1] + '2', 'title': 'Other Album'})
        self.assertEqual(matching_releases(self.track, [self.record]), [])

    def test_collaboration_credit_order_is_not_a_different_artist(self):
        self.track['artist'] = 'First Artist, Second Artist'
        self.record['artist-credit'] = [{'name': 'Second Artist'}, {'name': 'First Artist'}]
        self.assertEqual(matching_releases(self.track, [self.record]), [RID])
        self.record['artist-credit'].pop()
        self.assertEqual(matching_releases(self.track, [self.record]), [])

    def test_unknown_artist_and_unknown_duration_are_rejected(self):
        self.assertEqual(matching_releases({**self.track, 'artist': 'Неизвестный исполнитель'}, [self.record]), [])
        self.assertEqual(matching_releases({**self.track, 'duration': 0}, [self.record]), [])

    def worker(self, directory):
        worker = ArtworkWorker.__new__(ArtworkWorker)
        worker.library = SimpleNamespace(lock=threading.RLock(), root=Path(directory),
                                         data={'tracks': {'local': self.track}}, save=Mock())
        worker.lookup = Mock(return_value=(b'\xff\xd8\xffcover', 'https://musicbrainz.org/release/' + RID))
        return worker

    def test_download_is_cached_and_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = self.worker(directory)
            worker.process('local')
            worker.process('local')
            worker.lookup.assert_called_once()
            self.assertEqual((Path(directory) / 'local.cover').read_bytes(), b'\xff\xd8\xffcover')
            self.assertIn('Cover Art Archive', self.track['cover_provider'])

    def test_manual_cover_selected_during_lookup_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = self.worker(directory)
            def manual(_):
                self.track['cover'] = '/manual'
                return b'\xff\xd8\xffauto', 'https://musicbrainz.org/'
            worker.lookup.side_effect = manual
            worker.process('local')
            self.assertEqual(self.track['cover'], '/manual')
            self.assertFalse(list(Path(directory).glob('*.cover')))

    def test_not_found_is_negative_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = self.worker(directory)
            worker.lookup.return_value = None
            worker.process('local')
            worker.process('local')
            worker.lookup.assert_called_once()
            self.assertEqual(self.track['cover_lookup'], 'not_found')
            self.assertNotIn('cover', self.track)

    def test_metadata_change_during_lookup_discards_result_and_requests_requeue(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = self.worker(directory)
            def change(_):
                self.track['album'] = 'Corrected album'
                return b'\xff\xd8\xffold', 'https://musicbrainz.org/'
            worker.lookup.side_effect = change
            self.assertIs(worker.process('local'), True)
            self.assertNotIn('cover_checked_at', self.track)
            self.assertFalse(list(Path(directory).glob('*.cover')))

    def test_cache_write_failure_remains_retryable_without_installing_cover(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = self.worker(directory)
            worker.library.root = Path(directory) / 'missing-directory'
            worker.process('local')
            self.assertEqual(self.track['cover_lookup'], 'unavailable')
            self.assertEqual(self.track['cover_lookup_details'][-1]['result'], 'write_error')
            self.assertNotIn('cover', self.track)

    def test_deezer_fallback_after_tls_error_records_the_real_reason(self):
        candidate={'id':12,'title':'My Song','duration':120,'artist':{'name':'My Artist'},
                   'album':{'id':5,'title':'My Album','cover_big':'https://cdn-images.dzcdn.net/images/cover/test.jpg'}}
        with tempfile.TemporaryDirectory() as directory:
            worker=self.worker(directory)
            worker.lookup=ArtworkWorker.lookup.__get__(worker)
            def fetch(url,limit):
                if 'musicbrainz.org' in url:raise ArtworkFailure('tls_error')
                if 'api.deezer.com/search?' in url:return json.dumps({'data':[candidate]}).encode()
                if 'dzcdn.net' in url:return b'\xff\xd8\xffimage'
                self.fail('Unexpected URL '+url)
            worker.fetch=Mock(side_effect=fetch)
            worker.process('local')
            self.assertEqual(self.track['cover_provider'],'Deezer')
            self.assertEqual(self.track['cover_lookup'],'found')
            self.assertEqual(self.track['cover_lookup_details'][0]['result'],'tls_error')

    def test_album_fallback_requires_the_actual_track_in_the_named_album(self):
        album={'id':5,'title':'My Album (Remastered)','artist':{'name':'My Artist'},
               'cover_big':'https://cdn-images.dzcdn.net/images/cover/test.jpg',
               'tracks':{'data':[{'title':'My Song','duration':120,'artist':{'name':'My Artist'}}]}}
        with tempfile.TemporaryDirectory() as directory:
            worker=self.worker(directory);worker.lookup=ArtworkWorker.lookup.__get__(worker)
            def fetch(url,limit):
                if 'musicbrainz.org' in url:return b'{"recordings":[]}'
                if 'search/album?' in url:return json.dumps({'data':[album]}).encode()
                if '/album/5' in url:return json.dumps(album).encode()
                if '/search?' in url:return b'{"data":[]}'
                if 'dzcdn.net' in url:return b'\xff\xd8\xffimage'
                self.fail('Unexpected URL '+url)
            worker.fetch=Mock(side_effect=fetch)
            worker.process('local')
            self.assertEqual(self.track['cover_lookup'],'found')
            self.assertEqual(self.track['cover_lookup_details'][-1]['result'],'matched_album_track')

    def test_incomplete_catalog_search_is_not_cached_as_not_found(self):
        with tempfile.TemporaryDirectory() as directory:
            worker=self.worker(directory);worker.lookup=ArtworkWorker.lookup.__get__(worker)
            worker.fetch=Mock(side_effect=ArtworkFailure('tls_error'))
            worker.process('local')
            self.assertEqual(self.track['cover_lookup'],'unavailable')
            self.assertTrue(self.track['cover_lookup_details'])

    def test_deezer_rejects_karaoke_remix_wrong_album_or_artist(self):
        candidate={'title':'My Song','duration':120,'artist':{'name':'My Artist'},'album':{'title':'My Album'}}
        self.assertTrue(deezer_match(self.track,candidate))
        for changes in ({'title':'My Song (Karaoke)'},{'title':'My Song (Sped Up)'},
                        {'artist':{'name':'Someone else'}},{'album':{'title':'Best Of'}},{'duration':150}):
            self.assertFalse(deezer_match(self.track,{**candidate,**changes}))
        self.assertEqual(album_base('My Album (2024 Remaster)'),album_base('My Album'))
        self.assertNotEqual(album_base('My Album (Deluxe)'),album_base('My Album'))


if __name__ == '__main__':
    unittest.main()
