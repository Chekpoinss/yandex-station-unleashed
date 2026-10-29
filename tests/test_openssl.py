import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import access


class OpenSSLTests(unittest.TestCase):
    def test_vpn_and_public_sans_do_not_invalidate_lan_certificate(self):
        try:
            access.find_openssl()
        except access.AccessError:
            self.skipTest('OpenSSL executable is required for real certificate verification')
        with tempfile.TemporaryDirectory() as directory:
            tls = Path(directory)
            with patch.object(access.socket,'gethostname',return_value='long-runner-hostname.' * 5), patch.object(access, 'local_names', return_value=(['localhost'], ['127.0.0.1','10.11.0.209','100.127.115.50','203.0.113.1','169.254.1.2'])):
                crt, key = access.ensure_certs(tls)
                access._openssl('verify', '-CAfile', str(tls/'ca.crt'), '-verify_ip', '10.11.0.209', str(crt), cwd=tls)
                self.assertEqual((tls/'server.sans').read_text(), 'DNS:localhost,IP:127.0.0.1,IP:10.11.0.209')
                previous = crt.read_bytes()
                self.assertEqual(access.ensure_certs(tls), (crt, key))
                self.assertEqual(crt.read_bytes(), previous)

    def test_vpn_san_filter_and_subject_limit_without_external_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            tls=Path(directory)
            def fake_openssl(*args,**kwargs):
                for flag in ('-out','-keyout'):
                    if flag in args:Path(args[args.index(flag)+1]).write_text('test fixture')
            with patch.object(access.socket,'gethostname',return_value='long-host.'*12), \
                 patch.object(access,'local_names',return_value=(['localhost'],['127.0.0.1','192.168.1.2','198.18.0.1','203.0.113.4'])), \
                 patch.object(access,'_openssl',side_effect=fake_openssl) as openssl:
                access.ensure_certs(tls)
            self.assertEqual((tls/'server.sans').read_text(),'DNS:localhost,IP:127.0.0.1,IP:192.168.1.2')
            subject=next(call.args for call in openssl.call_args_list if '-subj' in call.args)
            self.assertLessEqual(len(subject[subject.index('-subj')+1].removeprefix('/CN=')),64)

    def test_explicit_path_can_contain_spaces(self):
        with tempfile.TemporaryDirectory(prefix='openssl test ') as directory:
            executable=Path(directory)/'openssl.exe';executable.touch()
            with patch.dict(os.environ, {'PANEL_OPENSSL':str(executable)}):
                self.assertEqual(access.find_openssl(),str(executable))

    def test_missing_dependency_has_actionable_message(self):
        with patch.dict(os.environ,{'PANEL_OPENSSL':''}), patch.object(access.shutil,'which',return_value=None), patch.object(Path,'is_file',return_value=False):
            with self.assertRaisesRegex(access.AccessError,'PANEL_OPENSSL.*--https-port 0'):
                access.find_openssl()

    def test_missing_program_never_escapes_as_winerror(self):
        with patch.object(access,'find_openssl',return_value='openssl'), patch.object(access.subprocess,'run',side_effect=FileNotFoundError()):
            with self.assertRaisesRegex(access.AccessError,'Не удалось запустить OpenSSL'):
                access._openssl('version',cwd='.')

    @unittest.skipUnless(os.name=='nt','Windows Git layout')
    def test_git_openssl_is_found_without_path_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            program=Path(directory)/'Git/usr/bin/openssl.exe';program.parent.mkdir(parents=True);program.touch()
            with patch.dict(os.environ,{'PANEL_OPENSSL':'','ProgramFiles':directory}), patch.object(access.shutil,'which',return_value=None):
                self.assertEqual(access.find_openssl(),str(program))
