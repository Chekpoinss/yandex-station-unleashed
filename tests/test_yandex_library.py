import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,MagicMock,patch
from yandex_library import YandexLibrary


class YandexCollectionTests(unittest.TestCase):
    def adapter(self):
        api=SimpleNamespace(auth=SimpleNamespace(music_token='test-token'),uid=Mock(return_value=7),
            cover=lambda uri:'https://images.yandex.net/'+uri if uri else None)
        def request(path,form=None):
            if path.endswith('/likes/tracks'):return {'library':{'tracks':[{'id':2},{'id':1}]}}
            if path.endswith('/likes/albums'):return [{'id':10,'timestamp':'test'}]
            if path=='/tracks':return [{'id':i,'title':str(i),'artists':[{'name':'Artist'}],
                'albums':[{'id':10,'title':'Album'}],'durationMs':120000} for i in (1,2)]
            if path=='/albums':return [{'id':10,'title':'Album','artists':[{'name':'Artist'}],'trackCount':2}]
            if path.endswith('/download-info'):return [{'codec':'mp3','preview':False,'bitrateInKbps':192,
                'downloadInfoUrl':'https://storage.yandex.net/download.xml'}]
            raise AssertionError(path)
        api._req=Mock(side_effect=request)
        return YandexLibrary(api),api

    def test_favorites_preserve_order_and_have_no_streams_or_credentials(self):
        adapter,api=self.adapter();result=adapter.favorites()
        self.assertEqual([t['id'] for t in result['tracks']],['ym-2','ym-1'])
        self.assertEqual(len(result['albums']),1)
        self.assertTrue(all(t['favorite'] for t in result['tracks']))
        self.assertNotIn('test-token',str(result));self.assertNotIn('downloadInfoUrl',str(result))
        count=api._req.call_count;self.assertEqual(adapter.favorites(),result)
        self.assertEqual(api._req.call_count,count)

    def test_logout_never_returns_cached_account_collection(self):
        adapter,api=self.adapter();adapter.favorites();api.auth.music_token=None
        with self.assertRaises(ValueError):adapter.favorites()
        self.assertIsNone(adapter.cached)

    def test_account_switch_invalidates_metadata_cache(self):
        adapter,api=self.adapter();adapter.favorites();count=api._req.call_count
        api.auth.music_token='other-account';adapter.favorites()
        self.assertGreater(api._req.call_count,count)

    def test_preview_or_unknown_permission_is_not_a_full_song(self):
        for preview in (True,None):
            adapter,api=self.adapter()
            api._req.return_value=[{'codec':'mp3','preview':preview}];api._req.side_effect=None
            with self.assertRaises(ValueError):adapter.resolve({'yandex_id':'1'})

    def test_xml_signature_accepts_real_hex_stamp_and_https_port(self):
        adapter,api=self.adapter()
        response=MagicMock();response.__enter__.return_value.read.return_value=(
            b'<download-info><host>storage.yandex.net:443</host><path>/file.mp3</path>'
            b'<ts>00005abc123</ts><s>salt</s></download-info>')
        with patch('yandex_library.urlopen',return_value=response):url=adapter.resolve({'yandex_id':'1'})
        signature=hashlib.md5(b'XGRlBW9FXlekgbPrRHuSiAfile.mp3salt').hexdigest()
        self.assertEqual(url,'https://storage.yandex.net:443/get-mp3/'+signature+'/00005abc123/file.mp3')

    def test_foreign_stream_metadata_host_is_rejected_before_request(self):
        adapter,api=self.adapter();api._req.side_effect=None
        api._req.return_value=[{'codec':'mp3','preview':False,'downloadInfoUrl':'https://untrusted.example/x'}]
        with patch('yandex_library.urlopen') as request:
            with self.assertRaises(ValueError):adapter.resolve({'yandex_id':'1'})
        request.assert_not_called()

    def test_unlike_requires_server_ack_and_invalidates_collection(self):
        adapter,api=self.adapter();adapter.favorites()
        api._req.side_effect=None;api._req.return_value={'revision':4}
        self.assertEqual(adapter.favorite('1',False),{'liked':False})
        self.assertIsNone(adapter.cached)
        self.assertEqual(api._req.call_args.kwargs['form'],{'track-ids':'1'})
        api._req.return_value=False
        with self.assertRaises(ValueError):adapter.favorite('10',False,'album')
