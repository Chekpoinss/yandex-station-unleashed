import io
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import server


class UpdateCompleteness(unittest.TestCase):
    def test_update_installs_new_module_and_preserves_user_data(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, 'w') as z:
            z.writestr('yandex-station-unleashed-0.3.0/server.py', '# server')
            z.writestr('yandex-station-unleashed-0.3.0/new_feature.py', 'value = 1')
            z.writestr('yandex-station-unleashed-0.3.0/.yandex_auth.json', 'untrusted')

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'panel'; root.mkdir()
            (root / '.yandex_auth.json').write_text('keep')
            sums = hashlib.sha256(blob.getvalue()).hexdigest() + '  yandex-station-unleashed-0.3.0.zip\n'
            with patch.object(server, 'ROOT', root), patch.object(server.urllib.request, 'urlopen', side_effect=[io.BytesIO(blob.getvalue()), io.BytesIO(sums.encode())]):
                server.PanelUpdater()._from_zip('0.3.0')
            self.assertEqual((root / 'new_feature.py').read_text(), 'value = 1')
            self.assertEqual((root / '.yandex_auth.json').read_text(), 'keep')
            self.assertFalse((root.parent / 'outside.py').exists())

    def verified_archive(self, entries):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, 'w') as archive:
            for name, body in entries.items():
                archive.writestr('yandex-station-unleashed-0.3.0/' + name, body)
        data = blob.getvalue()
        sums = hashlib.sha256(data).hexdigest() + '  yandex-station-unleashed-0.3.0.zip\n'
        return [io.BytesIO(data), io.BytesIO(sums.encode())]

    def test_unsafe_archive_is_rejected_before_any_source_change(self):
        responses = self.verified_archive({'server.py': 'replacement', '../outside.py': 'bad'})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'server.py').write_text('original')
            with patch.object(server, 'ROOT', root), patch.object(server.urllib.request, 'urlopen', side_effect=responses):
                with self.assertRaisesRegex(ValueError, 'путь'):
                    server.PanelUpdater()._from_zip('0.3.0')
            self.assertEqual((root/'server.py').read_text(), 'original')

    def test_partial_write_restores_original_content_and_permissions(self):
        responses = self.verified_archive({'server.py': 'replacement', 'new_feature.py': 'new'})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); original = root/'server.py'
            original.write_text('original'); original.chmod(0o640)
            expected_mode = original.stat().st_mode & 0o777
            replace = os.replace
            def fail_second(source, target):
                if Path(target).name == 'new_feature.py':
                    raise OSError('synthetic write failure')
                return replace(source, target)
            with patch.object(server, 'ROOT', root), patch.object(server.urllib.request, 'urlopen', side_effect=responses), patch.object(server.os, 'replace', side_effect=fail_second):
                with self.assertRaisesRegex(OSError, 'synthetic'):
                    server.PanelUpdater()._from_zip('0.3.0')
            self.assertEqual(original.read_text(), 'original')
            self.assertEqual(original.stat().st_mode & 0o777, expected_mode)
            self.assertFalse((root/'new_feature.py').exists())
            self.assertFalse(list(root.glob('*.upd')))

    def test_legacy_update_rejects_bad_hash(self):
        source = Path(server.__file__).parent
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for f in source.glob('*.py'):
                if f.name not in ('zvuk.py', 'zvuk_player.py', 'zvuk_queries.py'):
                    shutil.copy2(f, root / f.name)
            (root / 'VERSION').write_text('0.3.0')
            with zipfile.ZipFile(root / 'release.zip', 'w') as z:
                for name in ('server.py', 'zvuk.py', 'zvuk_player.py', 'zvuk_queries.py'):
                    z.write(source / name, 'yandex-station-unleashed-0.3.0/' + name)
            code = '''
import io, urllib.request
blob = open("release.zip", "rb").read()
sums = b"0" * 64 + b"  yandex-station-unleashed-0.3.0.zip\\n"
calls = 0
def fake(*a, **kw):
    global calls
    calls += 1
    return io.BytesIO(blob if calls == 1 else sums)
urllib.request.urlopen = fake
try:
    import server
except RuntimeError as e:
    print("rejected", e)
else:
    raise SystemExit("recovery accepted a bad hash")
'''
            env = os.environ.copy(); env['PYTHONPATH'] = str(root)
            p = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('rejected', p.stdout)
            self.assertFalse((root / 'zvuk_queries.py').exists())

    def test_legacy_update_recovers_modules_before_import(self):
        source = Path(server.__file__).parent
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for f in source.glob('*.py'):
                if f.name not in ('zvuk.py', 'zvuk_player.py', 'zvuk_queries.py'):
                    shutil.copy2(f, root / f.name)
            (root / 'VERSION').write_text('0.3.0')
            (root / 'static').mkdir()
            shutil.copy2(source / 'static/model-colors.json', root / 'static/model-colors.json')
            archive = root / 'release.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for name in ('server.py', 'zvuk.py', 'zvuk_player.py', 'zvuk_queries.py', 'model_colors.py'):
                    z.write(source / name, 'yandex-station-unleashed-0.3.0/' + name)
            code = '''
import io, hashlib, urllib.request
blob = open("release.zip", "rb").read()
sums = hashlib.sha256(blob).hexdigest() + "  yandex-station-unleashed-0.3.0.zip\\n"
calls = 0
def fake(*a, **kw):
    global calls
    calls += 1
    return io.BytesIO(blob if calls == 1 else sums.encode())
urllib.request.urlopen = fake
import server
print("started")
'''
            env = os.environ.copy(); env['PYTHONPATH'] = str(root)
            p = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('started', p.stdout)
            self.assertTrue((root / 'zvuk_queries.py').exists())
