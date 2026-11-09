import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class Radio(unittest.TestCase):
    def panel(self, cfg=None):
        panel = server.Panel(Mock())
        panel.stored = cfg or {}
        panel._assist_config = lambda: json.loads(json.dumps(panel.stored))
        panel._assist_write = lambda c: setattr(panel, "stored", c)
        panel._assist_local_md5 = lambda: None
        panel.adb.shell.return_value = ("@installed\n@running\n@status\n{}\n", "", 0)
        return panel

    def test_stations_are_validated_before_the_device(self):
        panel = self.panel()
        for bad in ({"name": "", "url": "http://a.b/x"}, {"name": "x", "url": "ftp://a.b"},
                    {"name": "x", "url": 'http://a.b/"; reboot'}, {"name": "x", "url": "http://a.b/x ! filesink"}):
            with self.assertRaises(server.AdbError):
                panel.radio_set({"stations": [bad]})
            with self.assertRaises(server.AdbError):
                panel.radio_play(bad)
        panel.adb.push.assert_not_called()
        st = panel.radio_set({"stations": [{"name": " Groove Salad ", "url": "http://ice1.somafm.com/groovesalad-128-mp3"}],
                              "voice": False})
        self.assertEqual(st["stations"], [{"name": "Groove Salad", "url": "http://ice1.somafm.com/groovesalad-128-mp3"}])
        self.assertFalse(st["voice"])

    def test_search_keeps_only_playable_streams(self):
        found = [{"name": "Jazz", "url_resolved": "https://jazz.example/stream.mp3", "codec": "MP3", "bitrate": 128},
                 {"name": "HLS", "url_resolved": "https://x.example/a.m3u8", "hls": 1},
                 {"name": "Bad", "url_resolved": "http://x.example/a b"},
                 {"name": "Vorbis", "url_resolved": "http://x.example/rp_192m.ogg", "codec": "OGG"}]
        with patch.object(server.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(found).encode())) as get:
            r = self.panel().radio_search("jazz", "ru")
        self.assertEqual([s["name"] for s in r["stations"]], ["Jazz"])
        self.assertIn("countrycode=RU", get.call_args.args[0].full_url)
        with self.assertRaises(server.AdbError):
            self.panel().radio_search("j")

    def test_alice_station_is_a_text_request(self):
        panel = self.panel()
        panel.glagol = Mock()
        panel.glagol.send.return_value = {"status": "SUCCESS"}
        panel.radio_alice("Европа Плюс")
        panel.glagol.send.assert_called_with({"command": "sendText", "text": "включи радио Европа Плюс"})


if __name__ == "__main__":
    unittest.main()


class AliceLogos(unittest.TestCase):
    def test_logo_is_remembered_from_the_player(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, patch.object(server, "DATA_DIR", Path(d)):
            panel = server.Panel(Mock())
            panel._remember_fm({"id": "europa_plus", "title": "Европа плюс", "type": "FmRadio",
                                "extra": {"coverURI": "avatars.mds.yandex.net/get-music-misc/1/europa_plus-225-full/%%"}})
            panel._remember_fm({"id": "../evil", "title": "x", "extra": {}})
            again = server.Panel(Mock())
            self.assertEqual(list(again.fm_stations()), ["europa_plus"])
            self.assertTrue(again.fm_stations()["europa_plus"]["cover"].endswith("/%%"))


class StationLogos(unittest.TestCase):
    def test_only_public_raster_images(self):
        panel = server.Panel(Mock())
        self.assertFalse(panel._public_url("http://192.168.1.1/favicon.ico"))
        self.assertFalse(panel._public_url("http://127.0.0.1/x.png"))
        self.assertFalse(panel._public_url("file:///etc/passwd"))
        with patch.object(server.Panel, "_public_url", return_value=True):
            for body, ok in ((b"<svg onload=alert(1)>", False), (b"<!DOCTYPE html>", False),
                             (b"\x89PNG\r\n\x1a\n...", True), (b"RIFF\x00\x00\x00\x00WEBPVP8 ", True)):
                opener = Mock()
                opener.open.return_value = io.BytesIO(body)
                with patch.object(server.urllib.request, "build_opener", return_value=opener):
                    self.assertEqual(bool(panel._download_image("https://radio.example/logo")), ok, body)

    def test_regional_stations_are_not_taken_for_the_main_one(self):
        found = [{"name": "Радио Маяк Вологда 102.3 FM", "favicon": "https://a.example/vologda.png"},
                 {"name": "Радио Маяк (Radio Mayak)", "favicon": "https://a.example/mayak.png"}]
        panel = server.Panel(Mock())
        panel.RADIO_LOGO_SITE = {}
        seen = []
        panel._download_image = lambda url: seen.append(url)
        with patch.object(server.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(found).encode())):
            panel._fetch_logo("Маяк", panel._radio_key("Маяк"))
        self.assertEqual(seen, ["https://a.example/mayak.png"])


class StationAliases(unittest.TestCase):
    def test_aliases_are_kept_and_trimmed(self):
        panel = Radio.panel(Radio())
        st = panel.radio_set({"stations": [{"name": "FLUX", "url": "http://a.example/x.mp3",
                                            "aliases": [" флакс ", "", "x" * 60] + ["a"] * 10}]})
        aliases = st["stations"][0]["aliases"]
        self.assertEqual(aliases[:2], ["флакс", "x" * 40])
        self.assertLessEqual(len(aliases), 8)


class StationCheck(unittest.TestCase):
    """Проверка станции на настоящем локальном HTTP-сервере: живой поток, «замирание» после первых КБ, отказ."""

    @classmethod
    def setUpClass(cls):
        import threading, time as _t
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Stream(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                if self.path == "/bad":
                    self.send_error(400); return
                self.send_response(200); self.send_header("Content-Type", "audio/mpeg"); self.send_header("icy-br", "64")
                self.end_headers()
                try:
                    if self.path == "/frozen":
                        self.wfile.write(b"\0" * 17000); self.wfile.flush(); _t.sleep(6); return
                    for _ in range(60):
                        self.wfile.write(b"\0" * 4000); self.wfile.flush(); _t.sleep(0.1)
                except OSError:
                    pass

        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Stream)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def check(self, path):
        import tempfile
        with tempfile.TemporaryDirectory() as d, patch.object(server, "DATA_DIR", Path(d)):
            panel = server.Panel(Mock())
            panel.assistant_status = lambda: {"installed": False}
            return panel.radio_check(self.base + path, seconds=4)

    def test_states(self):
        self.assertEqual(self.check("/live")["state"], "ok")
        frozen = self.check("/frozen")
        self.assertEqual(frozen["state"], "blocked")
        self.assertIn("16 КБ", frozen["detail"])
        self.assertEqual(self.check("/bad")["state"], "down")

    def test_proxy_setting(self):
        panel = Radio.panel(Radio())
        self.assertEqual(panel.radio_set({"proxy": "http://u:secret@10.0.0.1:3128"})["proxy"], "http://u:•••@10.0.0.1:3128")
        panel.radio_set({"proxy": "http://u:•••@10.0.0.1:3128"})
        self.assertEqual(panel.stored["radio"]["proxy"], "http://u:secret@10.0.0.1:3128")
        for bad in ("socks5://a:1", 'http://a"b:1', "http://u:%22x@a:1"):
            with self.assertRaises(server.AdbError):
                panel.radio_set({"proxy": bad})


class LogoRedirects(unittest.TestCase):
    def test_redirect_into_home_network_is_refused(self):
        import urllib.error
        handler = server.Panel._PublicRedirects()
        with patch.object(server.Panel, "_public_url", side_effect=lambda u: not u.startswith("http://192.168.")):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                handler.redirect_request(Mock(), None, 302, "Found", {}, "http://192.168.1.1/router.png")
            if caught.exception.fp is not None:
                caught.exception.close()
