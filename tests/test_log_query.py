import unittest
from unittest.mock import Mock, patch
import server

class LogQueryTests(unittest.TestCase):
    def test_panel_log_accepts_numeric_http_query(self):
        journal = server.Journal()
        for number in range(30):
            journal.items.append({'t': 0, 'level': 'info', 'msg': str(number)})
        handler = server.Handler.__new__(server.Handler)
        handler.path = '/api/log?name=panel&lines=10'
        handler._guard = Mock(return_value=False)
        handler._handle = lambda fn: fn()
        with patch.object(server, 'JOURNAL', journal):
            result = handler.do_GET()
        self.assertEqual(len(result['text'].splitlines()), 10)
        self.assertTrue(result['text'].endswith('29'))
