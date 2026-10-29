import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
import json
import server
from panel_journal import Journal

class ExtensionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.panel=Mock();self.panel.conn.online=True
        self.panel.scenes.status.return_value={'items':{},'revision':0}
        self.panel.say.return_value={'ok':True}
        self.panel.scenes.save.return_value={'id':'abc','items':{}}
        class Handler(server.Handler):pass
        Handler.panel=self.panel;Handler.access=Mock();Handler.access.session_ok.return_value=True
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.journal=Journal();self.journal.open(Path(self.tmp.name)/'journal')
        patcher=patch.object(server,'JOURNAL',self.journal);patcher.start();self.addCleanup(patcher.stop)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
    def close(self):
        self.http.shutdown();self.http.server_close();self.thread.join(2)
    def request(self,method,path,body=None,origin=None):
        c=http.client.HTTPConnection(*self.http.server_address,timeout=3)
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        c.request(method,path,json.dumps(body) if body is not None else None,headers)
        r=c.getresponse();result=(r.status,json.loads(r.read()));c.close();return result
    def test_private_speech_body_is_not_journaled(self):
        self.assertEqual(self.request('POST','/api/say',{'text':'SECRET signed-url token query'})[0],200)
        self.panel.say.assert_called_once_with('SECRET signed-url token query','alice')
        self.assertEqual(len(self.journal.items),1)
        self.assertNotIn('SECRET',self.journal.path.read_text('utf8'))
        self.assertNotIn('query',self.journal.text())
    def test_foreign_origin_cannot_speak(self):
        self.assertEqual(self.request('POST','/api/say',{'text':'private'},'https://other.invalid')[0],403)
        self.panel.say.assert_not_called()
    def test_local_journal_scenes_and_capabilities_work_offline(self):
        self.panel.conn.online=False
        self.assertEqual(self.request('GET','/api/extensions')[1]['revision'],3)
        self.assertEqual(self.request('GET','/api/scenes')[0],200)
        self.assertEqual(self.request('POST','/api/scenes',{'scene':{}})[0],200)
        self.assertEqual(self.request('GET','/api/logs')[1],['panel'])
        self.assertEqual(self.request('GET','/api/journal')[0],200)
        self.assertEqual(self.request('GET','/api/log?name=panel')[0],200)
        self.assertEqual(self.request('POST','/api/say',{'text':'private'})[0],503)
        self.panel.say.assert_not_called()
    def test_small_rejected_posts_deliver_error_instead_of_tcp_reset(self):
        for _ in range(10):
            self.assertEqual(self.request('POST','/api/say',{'text':'discarded'},'https://other.invalid')[0],403)
        self.panel.say.assert_not_called()
    def test_unauthenticated_read_is_rejected(self):
        self.http.RequestHandlerClass.access.session_ok.return_value=False
        self.assertEqual(self.request('GET','/api/scenes')[0],401)
    def test_support_report_is_offline_and_contains_no_device_data(self):
        self.panel.conn.online=False;self.panel.ffmpeg=True
        status,body=self.request('GET','/api/report')
        self.assertEqual(status,200);self.assertIn(server.UPSTREAM_VERSION,body['text'])
        self.panel.adb.shell.assert_not_called();self.panel.status.assert_not_called()
