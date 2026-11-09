import os
from pathlib import Path
import subprocess
import sys
import unittest


class ConsoleEncoding(unittest.TestCase):
    def test_help_is_safe_with_legacy_redirected_encoding(self):
        root = Path(__file__).resolve().parents[1]
        for script in ('server.py', 'multi.py'):
            with self.subTest(script=script):
                env = dict(os.environ, PYTHONIOENCODING='cp1252:strict')
                result = subprocess.run([sys.executable, str(root/script), '--help'],
                                        capture_output=True, env=env, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr.decode('utf8', 'replace'))
                self.assertIn('usage:', result.stdout.decode('utf8'))
