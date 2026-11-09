"""Start the new server after a legacy ZIP updater omitted model_colors.py."""
import shutil
import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ColorZipUpgrade(unittest.TestCase):
    def test_missing_module_is_repaired_before_loading_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for file in ROOT.glob('*.py'):
                if file.name != 'model_colors.py':
                    shutil.copy(file, root / file.name)
            shutil.copy(ROOT / 'VERSION', root / 'VERSION')
            # A local release archive supplies the missing module and palette.
            with zipfile.ZipFile(root / 'release.zip', 'w') as archive:
                for name in ('server.py', 'model_colors.py', 'static/model-colors.json'):
                    archive.write(ROOT / name, 'yandex-station-unleashed-' + (ROOT/'VERSION').read_text().strip() + '/' + name)
            code = '''
import io, sys
from pathlib import Path
from unittest.mock import patch
blob = Path('release.zip').read_bytes()
def fake_download(request, **kwargs):
    assert '/Chekpoinss/yandex-station-unleashed/' in request.full_url
    if request.full_url.endswith('SHA256SUMS'):
        import hashlib
        return io.BytesIO((hashlib.sha256(blob).hexdigest() + '  yandex-station-unleashed-' + Path('VERSION').read_text().strip() + '.zip\\n').encode())
    return io.BytesIO(blob)
import urllib.request
urllib.request.urlopen = fake_download
import server, profiles
assert server.model_colors is not None
def profiles_ready(*args, **kwargs):
    assert server.model_colors.palette('mini3pro')['green']
    raise StopIteration('profiles ready')
sys.argv = ['server.py', '--device', '192.0.2.1', '--model', 'mini3pro', '--data-dir', 'data']
with patch.object(server.PanelUpdater, 'kind', return_value='zip'), \\
     patch.object(server.urllib.request, 'urlopen', return_value=io.BytesIO(blob)), \\
     patch.object(profiles, 'install', side_effect=profiles_ready):
    try:
        server.main()
    except StopIteration:
        pass
    else:
        raise AssertionError('profiles were not reached')
assert Path('model_colors.py').exists()
'''
            result = subprocess.run([sys.executable, '-c', code], cwd=root,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
