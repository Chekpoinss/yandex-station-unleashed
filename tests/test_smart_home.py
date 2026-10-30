import copy
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import server
from smart_home import SmartHome, HomeError, HomeAuthError, NoRedirect


def lamp(ident='lamp'):
    return {'id': ident, 'name': 'Свет', 'type': 'devices.types.light', 'state': 'online', 'room': 'room',
            'capabilities': [
                {'type': 'devices.capabilities.on_off', 'state': {'instance': 'on', 'value': False}},
                {'type': 'devices.capabilities.range', 'parameters': {'instance': 'brightness', 'range': {'min': 0, 'max': 100, 'precision': 5}}, 'state': {'instance': 'brightness', 'value': 50}},
                {'type': 'devices.capabilities.mode', 'parameters': {'instance': 'thermostat', 'modes': [{'value': 'auto'}, {'value': 'heat'}]}},
                {'type': 'devices.capabilities.color_setting', 'parameters': {'color_model': 'rgb', 'temperature_k': {'min': 2000, 'max': 6500}}}],
            'properties': [{'type': 'devices.properties.float', 'parameters': {'instance': 'temperature'}, 'state': {'instance': 'temperature', 'value': 23}}]}


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.auth = SimpleNamespace(data={'x_token': 'x' * 32})
        self.home = SmartHome(self.auth, Path(self.tmp.name) / '.smart_home_auth.json')
        self.info = {'status': 'ok', 'households': [{'name': 'Дом', 'rooms': [{'id': 'room', 'name': 'Комната'}], 'all': [lamp()]}]}
        self.home._request = Mock(return_value=self.info)

    def command(self, kind='on_off', instance='on', value=True, **extra):
        return {'id': 'lamp', 'type': 'devices.capabilities.' + kind, 'instance': instance, 'value': value, **extra}

    def test_discovery_never_controls_or_persists_devices(self):
        data = self.home.devices()
        self.assertEqual(data['devices'][0]['room'], 'Комната')
        self.assertEqual(data['devices'][0]['household'], 'Дом')
        self.home._request.assert_called_once_with('GET', 'user/info')
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.assertNotIn('x' * 32, json.dumps(data))

    def test_cache_is_bound_to_account_and_refresh_bypasses_it(self):
        self.home.devices(); self.home.devices()
        self.assertEqual(self.home._request.call_count, 1)
        self.home.devices(True)
        self.assertEqual(self.home._request.call_count, 2)
        self.auth.data = {'x_token': 'new-account-token' * 3}
        self.home._request.return_value = {'status': 'ok', 'households': []}
        self.assertEqual(self.home.devices()['devices'], [])
        self.assertEqual(self.home._request.call_count, 3)

    def test_logout_hides_all_cached_devices(self):
        self.home.devices(); self.auth.data = {}
        data = self.home.devices()
        self.assertTrue(data['auth_required']); self.assertEqual(data['devices'], [])

    def test_account_change_during_discovery_drops_result(self):
        def request(*_):
            self.auth.data = {'x_token': 'other' * 8}
            return self.info
        self.home._request.side_effect = request
        self.assertEqual(self.home.devices()['devices'], [])

    def test_official_schema_rooms_and_private_vendor_fields(self):
        self.home.token = 'official' * 5
        device = lamp(); device['custom_data'] = {'password': 'secret'}
        device['capabilities'][0]['secret'] = 'secret'
        self.home._request.return_value = {'status': 'ok', 'devices': [device], 'rooms': [{'id': 'room', 'name': 'Зал'}]}
        result = self.home.devices()
        self.assertEqual(result['devices'][0]['room'], 'Зал')
        self.assertNotIn('secret', json.dumps(result))

    def test_unknown_device_is_rejected_before_device_request(self):
        with self.assertRaises(HomeError): self.home.device('../not-owned')
        self.home._request.assert_called_once_with('GET', 'user/info')

    def test_commands_are_checked_against_fresh_cloud_capabilities(self):
        self.home._request.side_effect = [self.info, {'status': 'ok', **lamp()}, {'status': 'ok'}]
        self.assertTrue(self.home.action(self.command())['ok'])
        args = self.home._request.call_args.args
        self.assertEqual(args[:2], ('POST', 'devices/actions'))
        self.assertEqual(args[2]['devices'][0]['actions'][0]['state'], {'instance': 'on', 'value': True})

    def test_offline_device_never_receives_post(self):
        device = lamp(); device['state'] = 'offline'
        self.home._request.side_effect = [self.info, {'status': 'ok', **device}]
        with self.assertRaisesRegex(HomeError, 'не в сети'): self.home.action(self.command())
        self.assertTrue(all(call.args[0] == 'GET' for call in self.home._request.call_args_list))

    def test_device_command_error_is_not_reported_as_success(self):
        failure = {'status': 'ok', 'devices': [{'capabilities': [{'state': {'action_result': {'status': 'ERROR'}}}]}]}
        self.home._request.side_effect = [self.info, {'status': 'ok', **lamp()}, failure]
        with self.assertRaisesRegex(HomeError, 'отклонило'): self.home.action(self.command())

    def test_account_switch_before_action_never_sends_post(self):
        self.home.devices()
        def request(method, path, *args):
            self.auth.data = {'x_token': 'different' * 6}
            return {'status': 'ok', **lamp()}
        self.home._request.reset_mock(); self.home._request.side_effect = request
        with self.assertRaises(HomeAuthError): self.home.action(self.command())
        self.assertTrue(all(c.args[0] == 'GET' for c in self.home._request.call_args_list))

    def test_bounded_types_ranges_precision_modes_and_color(self):
        for cmd in (self.command(), self.command('range', 'brightness', 55), self.command('mode', 'thermostat', 'heat'), self.command('color_setting', 'rgb', 0xff0011), self.command('color_setting', 'temperature_k', 3000)):
            SmartHome.validate_action(lamp(), cmd)
        for cmd in (self.command(value='true'), self.command('range', 'brightness', True), self.command('range', 'brightness', 101), self.command('range', 'brightness', 51), self.command('range', 'brightness', float('nan')), self.command('mode', 'thermostat', 'bogus'), self.command('color_setting', 'rgb', -1), self.command('color_setting', 'temperature_k', 10000), self.command('lock', 'lock', True)):
            with self.assertRaises(HomeError): SmartHome.validate_action(lamp(), cmd)

    def test_relative_control_is_exactly_one_device_step(self):
        SmartHome.validate_action(lamp(), self.command('range', 'brightness', -5, relative=True))
        with self.assertRaises(HomeError): SmartHome.validate_action(lamp(), self.command('range', 'brightness', 1000, relative=True))

    def test_property_only_sensor_cannot_receive_commands(self):
        device = lamp(); device['capabilities'] = []
        with self.assertRaises(HomeError): SmartHome.validate_action(device, self.command())

    def test_token_is_validated_before_saving_and_not_returned(self):
        self.home._request.return_value = {'status': 'ok', 'devices': []}
        token = 'official-token-' * 4
        result = self.home.configure({'token': token})
        self.assertNotIn(token, json.dumps(result))
        self.assertEqual(json.loads(self.home.path.read_text())['token'], token)
        self.home.configure({'disconnect': True}); self.assertFalse(self.home.path.exists())

    def test_bad_token_preserves_previous_token_file(self):
        self.home.token = 'old-token' * 5
        self.home.path.write_text(json.dumps({'token': self.home.token}))
        self.home._request.side_effect = HomeAuthError('denied')
        with self.assertRaises(HomeAuthError): self.home.configure({'token': 'new-token' * 5})
        self.assertEqual(self.home.token, 'old-token' * 5)
        self.assertEqual(json.loads(self.home.path.read_text())['token'], 'old-token' * 5)

    def test_no_redirect_and_no_automatic_command_retry(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid'))
        self.home.opener = Mock(); self.home.opener.open.side_effect = OSError('secret-proxy-password')
        with self.assertRaises(HomeError) as error:
            self.home._http('https://api.iot.yandex.net/v1.0/devices/actions', 'POST', {})
        self.assertNotIn('secret-proxy-password', str(error.exception))
        self.home.opener.open.assert_called_once()


class HomeHTTP(unittest.TestCase):
    def setUp(self):
        class Handler(server.Handler):
            def log_message(self, *_): pass
        Handler.panel = Mock(); Handler.panel.conn.online = False
        Handler.panel.smart_home.devices.return_value = {'connected': False, 'devices': [], 'auth_required': True}
        Handler.panel.smart_home.action.return_value = {'ok': True}
        Handler.access = Mock(); Handler.access.session_ok.return_value = True
        self.handler = Handler
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join(2)

    def request(self, method='GET', path='/api/smart-home', origin=None):
        connection = http.client.HTTPConnection(*self.http.server_address, timeout=3)
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        connection.request(method, path, '{}' if method == 'POST' else None, headers)
        response = connection.getresponse(); code, data = response.status, json.loads(response.read())
        connection.close(); return code, data

    def test_home_works_while_speaker_is_offline(self):
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.request('POST', '/api/smart-home/action')[0], 200)

    def test_foreign_origin_cannot_control_home(self):
        self.assertEqual(self.request('POST', '/api/smart-home/action', 'https://evil.invalid')[0], 403)
        self.handler.panel.smart_home.action.assert_not_called()

    def test_panel_password_still_protects_cloud_devices(self):
        self.handler.access.session_ok.return_value = False
        self.assertEqual(self.request()[0], 401)
        self.handler.panel.smart_home.devices.assert_not_called()
