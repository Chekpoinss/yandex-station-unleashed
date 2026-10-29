import json
from pathlib import Path
import tempfile
import threading
import unittest
from panel_journal import Journal, ACTIONS

class JournalTests(unittest.TestCase):
    def test_only_allowlisted_route_and_status_survive_disk_and_reload(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'journal.jsonl'; j=Journal(); j.open(path)
            j.record('/api/say?text=secret&token=y0_private',200)
            j.record('/api/custom-secret',500)
            j.record('/api/say',200)
            j.record('/api/library/soundcloud',400)
            raw=path.read_text('utf8')
            self.assertNotIn('secret',raw); self.assertNotIn('token',raw)
            self.assertEqual(len(j.items),2)
            k=Journal();k.open(path);self.assertEqual(list(j.items),list(k.items))
            self.assertIn('Сказать',k.text())

    def test_preexisting_arbitrary_fields_are_discarded(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'j';p.write_text(json.dumps({'t':1,'action':'/api/say','status':200,'msg':'speech URL secret'})+'\n'+json.dumps({'t':2,'action':'speech','status':400}),encoding='utf8')
            j=Journal();j.open(p);self.assertEqual(len(j.items),1);self.assertNotIn('secret',j.text())
            j.record('/api/say',400);self.assertNotIn('secret',p.read_text('utf8'))

    def test_concurrent_writes_bounded_and_valid(self):
        with tempfile.TemporaryDirectory() as td:
            j=Journal();j.LIMIT=20
            import collections
            j.open(Path(td)/'j');j.items=collections.deque(maxlen=20)
            ts=[threading.Thread(target=lambda:[j.record('/api/volume',200) for _ in range(20)]) for _ in range(4)]
            for t in ts:t.start()
            for t in ts:t.join()
            self.assertEqual(len(j.items),20)
            self.assertEqual(len([json.loads(row) for row in j.path.read_text('utf8').splitlines()]),20)

    def test_invalid_status_cannot_enter_storage(self):
        j=Journal()
        for status in ('token',True,0,600):j.record('/api/say',status)
        self.assertEqual(len(j.items),0)
