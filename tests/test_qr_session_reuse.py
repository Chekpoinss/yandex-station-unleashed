import threading
import unittest
from unittest.mock import Mock, patch
from glagol import YandexAuth

class QrSessionReuse(unittest.TestCase):
    def test_second_tab_preserves_pending_confirmation(self):
        auth = YandexAuth.__new__(YandexAuth)
        auth.lock = threading.Lock()
        session = {'started': 100, 'link': 'https://example.invalid/qr', 'submit': {'track_id': 'fixture'}}
        auth.qr = session
        auth._req = Mock(side_effect=AssertionError('must not replace pending QR'))
        with patch('glagol.time.time', return_value=110):
            self.assertEqual(auth.qr_start(), {'link': session['link']})
            self.assertIs(auth.qr, session)
            auth._req.assert_not_called()
