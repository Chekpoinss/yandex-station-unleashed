import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class ClientGone(unittest.TestCase):
    """Вкладку закрыли или перезагрузили, пока колонка отвечала: без трейсбеков в журнале."""

    def handler(self, error):
        h = server.Handler.__new__(server.Handler)
        h.request_version, h.requestline, h.command = "HTTP/1.1", "GET /api/status HTTP/1.1", "GET"
        h.client_address, h._headers_buffer, h.log_request = ("127.0.0.1", 1), [], lambda *a: None

        class Gone:
            def write(self, data):
                raise error

        h.wfile = Gone()
        return h

    def test_send_to_closed_connection_is_quiet(self):
        for error in (BrokenPipeError(32, "Broken pipe"), ConnectionResetError(104, "Connection reset")):
            h = self.handler(error)
            h._send(200, {"ok": True})
            self.assertTrue(h.close_connection)

    def test_handle_does_not_retry_with_500(self):
        h = self.handler(BrokenPipeError(32, "Broken pipe"))
        h._handle(lambda: {"ok": True})   # раньше второй BrokenPipeError вылетал из попытки отправить 500
        self.assertTrue(h.close_connection)


if __name__ == "__main__":
    unittest.main()
