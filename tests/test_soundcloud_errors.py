import unittest
from unittest.mock import patch, MagicMock
import music_library
import yt_dlp


class SoundCloudErrorsTests(unittest.TestCase):
    def extractor_error(self, message):
        extractor=MagicMock()
        extractor.__enter__.return_value.extract_info.side_effect=yt_dlp.utils.DownloadError(message)
        with patch('yt_dlp.YoutubeDL',return_value=extractor):
            with self.assertRaises(ValueError) as caught:
                music_library.soundcloud_extract({},'https://soundcloud.com/test/test')
        return str(caught.exception)

    def test_drm_is_not_reported_as_a_vpn_failure_or_leaked_raw_trace(self):
        result=self.extractor_error('\x1b[0;31mERROR:\x1b[0m [soundcloud] 947928331: This video is DRM protected')
        self.assertIn('DRM',result);self.assertIn('Открой оригинал',result)
        self.assertNotIn('\x1b',result);self.assertNotIn('947928331',result)
        self.assertNotIn('DownloadError',result)

    def test_network_failure_has_actionable_message_without_private_url(self):
        result=self.extractor_error('Unable to download: proxy connection timed out https://host/?token=private')
        self.assertIn('VPN',result);self.assertNotIn('private',result)

    def test_other_failure_does_not_guess_drm(self):
        result=self.extractor_error('track unavailable')
        self.assertIn('доступный поток',result);self.assertNotIn('DRM',result)

    def test_success_preserves_extractor_result(self):
        extractor=MagicMock();extractor.__enter__.return_value.extract_info.return_value={'url':'https://audio.example/test'}
        with patch('yt_dlp.YoutubeDL',return_value=extractor):
            self.assertEqual(music_library.soundcloud_extract({},'test'),{'url':'https://audio.example/test'})
