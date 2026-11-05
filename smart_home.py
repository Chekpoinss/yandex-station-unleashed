"""Account-scoped Yandex smart-home client. Cloud data stays in memory.

QR sessions follow AlexxIT/YandexStation's x-token session flow. An optional
official OAuth token uses the documented api.iot.yandex.net user API instead.
No speaker files, discovery broadcasts, background commands or device deletion.
"""
import copy
import hashlib
import http.cookiejar
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class HomeError(ValueError):
    pass


class HomeAuthError(HomeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward OAuth headers to a redirected URL.


class SmartHome:
    TYPES = {'devices.capabilities.' + name for name in ('on_off', 'toggle', 'range', 'mode', 'color_setting')}
    def __init__(self, auth, path):
        self.auth, self.path = auth, Path(path)
        self.lock = threading.RLock()
        self.token = ''
        try:
            self.token = json.loads(self.path.read_text('utf8')).get('token', '')
        except (OSError, ValueError, AttributeError):
            pass
        self.identity = None
        self.opener = None
        self.csrf = None
        self.cache = None
        self.cached_at = 0

    def _credential(self):
        if self.token:
            return 'official', self.token
        session = getattr(self.auth, 'qr_session', None)
        if session:
            return 'qr_session', session[0]
        token = self.auth.data.get('x_token')
        if token:
            return 'qr', token
        raise HomeAuthError('Войдите в Яндекс по QR-коду, чтобы открыть устройства умного дома.')

    def _account(self):
        kind, token = self._credential()
        identity = kind + ':' + hashlib.sha256(token.encode()).hexdigest()
        if identity != self.identity:
            self.identity = identity
            self.cache, self.cached_at, self.csrf, self.opener = None, 0, None, None
        return kind, token, identity

    def _same_account(self, identity):
        if self._account()[2] != identity:
            raise HomeAuthError('Аккаунт изменился. Обновите список устройств.')

    def _http(self, url, method='GET', data=None, headers=None, form=False, redirect_session=False):
        headers = {'User-Agent': 'Yandex-Station-Unleashed', **(headers or {})}
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        request = urllib.request.Request(url, body, headers, method=method)
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise HomeError('Слишком большой ответ умного дома')
            return raw.decode('utf8')
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if redirect_session and code in (301, 302, 303):
                return ''  # Cookie processor already accepted the session cookie.
            if code in (401, 403):
                raise HomeAuthError('Сессия Яндекса истекла или не хватает прав. Войдите по QR-коду заново либо обновите токен с правами iot:view и iot:control.') from None
            if method != 'GET':
                raise HomeError(f'Нет подтверждения команды (HTTP {code}). Обновите состояние перед повтором.') from None
            raise HomeError(f'Умный дом Яндекса ответил HTTP {code}. Попробуйте обновить список.') from None
        except (OSError, TimeoutError):
            if method != 'GET':
                raise HomeError('Нет подтверждения команды. Проверьте состояние устройства перед повтором.') from None
            raise HomeError('Не удалось связаться с умным домом Яндекса. Проверьте интернет.') from None

    def _json(self, *args, **kwargs):
        try:
            result = json.loads(self._http(*args, **kwargs))
        except (ValueError, UnicodeError) as error:
            if isinstance(error, HomeError):
                raise
            raise HomeError('Яндекс вернул непонятный ответ. Попробуйте войти заново.') from None
        if not isinstance(result, dict):
            raise HomeError('Неожиданный формат ответа умного дома')
        if result.get('status') != 'ok':
            raise HomeError('Яндекс не выполнил запрос умного дома. Обновите список устройств.')
        return result

    def _session(self, kind, token):
        if self.opener is not None:
            return
        jar = self.auth.qr_session[1] if kind == 'qr_session' else http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect())
        if kind in ('official', 'qr_session'):
            return
        try:
            result = self._json('https://mobileproxy.passport.yandex.net/1/bundle/auth/x_token/', 'POST',
                                {'type': 'x-token', 'retpath': 'https://www.yandex.ru'},
                                {'Ya-Consumer-Authorization': 'OAuth ' + token}, form=True)
            host = result.get('passport_host')
            if host not in ('https://passport.yandex.ru', 'https://passport.yandex.com') or not isinstance(result.get('track_id'), str):
                raise HomeAuthError('Не удалось создать сессию умного дома. Войдите по QR-коду заново.')
            self._http(host + '/auth/session/?' + urllib.parse.urlencode({'track_id': result['track_id']}),
                       redirect_session=True)
        except Exception:
            self.opener = None
            raise

    def _request(self, method, path, data=None):
        try:
            return self._request_impl(method, path, data)
        except HomeAuthError:
            self.opener, self.csrf, self.cache = None, None, None
            raise

    def _request_impl(self, method, path, data=None):
        kind, token, identity = self._account()
        self._session(kind, token)
        self._same_account(identity)
        if kind == 'official':
            return self._json('https://api.iot.yandex.net/v1.0/' + path, method, data,
                              {'Authorization': 'Bearer ' + token})
        if method == 'GET':
            url = 'https://iot.quasar.yandex.ru/m/v3/user/devices' if path == 'user/info' else 'https://iot.quasar.yandex.ru/m/user/' + path
            return self._json(url)
        if self.csrf is None:
            page = self._http('https://yandex.ru/iot')
            match = re.search(r'"csrfToken2"\s*:\s*("(?:[^"\\]|\\.)*")', page)
            if not match:
                raise HomeAuthError('Яндекс не выдал разрешение на управление. Войдите по QR-коду заново.')
            self.csrf = json.loads(match[1])
        self._same_account(identity)
        device = data['devices'][0]
        return self._json('https://iot.quasar.yandex.ru/m/user/devices/' +
                          urllib.parse.quote(device['id'], safe='') + '/actions', 'POST',
                          {'actions': device['actions']}, {'x-csrf-token': self.csrf})

    @staticmethod
    def _safe_device(device, rooms=None, house=''):
        rooms = rooms or {}
        room = device.get('room') or ''
        room_name = room.get('name', '') if isinstance(room, dict) else rooms.get(room, device.get('room_name', ''))
        def public_items(items):
            return [{k: copy.deepcopy(item[k]) for k in ('type', 'state', 'parameters', 'retrievable') if k in item}
                    for item in items if isinstance(item, dict)]
        # Return only UI fields, never vendor custom_data, URLs or account IDs.
        return {key: copy.deepcopy(value) for key, value in {
            'id': device.get('id'), 'name': device.get('name') or 'Устройство',
            'type': device.get('type', ''), 'state': device.get('state', ''),
            'room': room_name or 'Без комнаты', 'household': house or 'Мой дом',
            'capabilities': public_items(device.get('capabilities') or []), 'properties': public_items(device.get('properties') or [])
        }.items()}

    def devices(self, refresh=False):
        with self.lock:
            try:
                kind, _, identity = self._account()
            except HomeAuthError as error:
                self.cache, self.opener, self.identity = None, None, None
                return {'connected': False, 'auth_required': True, 'message': str(error), 'devices': [], 'source': None}
            if self.cache is not None and not refresh and time.monotonic() - self.cached_at < 10:
                return copy.deepcopy(self.cache)
            try:
                data = self._request('GET', 'user/info')
                self._same_account(identity)
                web_session = kind in ('qr', 'qr_session')
                if not isinstance(data.get('households' if web_session else 'devices'), list):
                    raise HomeError('Яндекс вернул другой формат списка устройств. Обновите страницу или войдите заново.')
                rooms = {r['id']: r.get('name', '') for r in data.get('rooms', []) if isinstance(r, dict) and 'id' in r}
                devices = []
                if web_session:
                    for home in data.get('households', []):
                        for room in home.get('rooms', []):
                            rooms[room['id']] = room.get('name', '')
                        devices.extend(self._safe_device(d, rooms, home.get('name', '')) for d in home.get('all', [])
                                       if d.get('item_type', 'device') == 'device' and d.get('id'))
                else:
                    houses = {h['id']: h.get('name', '') for h in data.get('households', []) if 'id' in h}
                    devices = [self._safe_device(d, rooms, houses.get(d.get('household_id'), '')) for d in data.get('devices', []) if d.get('id')]
                self.cache = {'connected': True, 'auth_required': False, 'source': kind, 'devices': devices}
                self.cached_at = time.monotonic()
                return copy.deepcopy(self.cache)
            except HomeAuthError as error:
                self.opener, self.csrf, self.cache = None, None, None
                return {'connected': False, 'auth_required': True, 'message': str(error), 'devices': [], 'source': kind}

    def device(self, ident):
        with self.lock:
            if not isinstance(ident, str) or not 1 <= len(ident) <= 256:
                raise HomeError('Неверный идентификатор устройства')
            if not any(d['id'] == ident for d in self.devices()['devices']):
                raise HomeError('Устройства нет в текущем аккаунте. Обновите список.')
            identity = self._account()[2]
            result = self._request('GET', 'devices/' + urllib.parse.quote(ident, safe=''))
            self._same_account(identity)
            result = result.get('device', result)
            if result.get('id') != ident:
                raise HomeError('Яндекс вернул другое устройство')
            summary = next(d for d in self.cache['devices'] if d['id'] == ident)
            device = self._safe_device(result, house=summary['household'])
            device['room'] = summary['room']
            return device

    @classmethod
    def validate_action(cls, device, data):
        kind, instance, value = data.get('type'), data.get('instance'), data.get('value')
        if not isinstance(kind, str) or kind not in cls.TYPES or not isinstance(instance, str):
            raise HomeError('Это умение пока не поддерживается панелью')
        cap = next((c for c in device['capabilities'] if c.get('type') == kind and
                    ((c.get('parameters') or {}).get('instance') or (c.get('state') or {}).get('instance') or
                     ('on' if kind == 'devices.capabilities.on_off' else None)) == instance), None)
        if kind == 'devices.capabilities.color_setting':
            cap = next((c for c in device['capabilities'] if c.get('type') == kind), None)
        if cap is None:
            raise HomeError('Устройство не поддерживает выбранное действие')
        params, relative = cap.get('parameters') or {}, data.get('relative', False)
        if type(relative) is not bool:
            raise HomeError('Неверный тип относительного изменения')
        if kind.endswith(('.on_off', '.toggle')):
            if type(value) is not bool or relative:
                raise HomeError('Переключатель принимает только включено или выключено')
        elif kind.endswith('.mode'):
            if relative or value not in [m.get('value') for m in params.get('modes', [])]:
                raise HomeError('Режим не поддерживается устройством')
        elif kind.endswith('.range'):
            limits = params.get('range') or {}
            low, high, step = limits.get('min'), limits.get('max'), limits.get('precision', 1)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise HomeError('Нужно конечное числовое значение')
            if relative:
                if type(step) not in (int, float) or not math.isfinite(step) or step <= 0 or not math.isclose(abs(value), step):
                    raise HomeError('Относительное изменение: один шаг устройства')
            else:
                if params.get('random_access') is False or type(low) not in (int, float) or type(high) not in (int, float) or not low <= value <= high:
                    raise HomeError('Значение вне диапазона устройства')
                if type(step) not in (int, float) or not math.isfinite(step) or step <= 0 or not math.isclose((value - low) / step, round((value - low) / step), abs_tol=1e-6):
                    raise HomeError('Значение не соответствует шагу устройства')
        elif instance == 'rgb':
            if params.get('color_model') != 'rgb' or type(value) is not int or not 0 <= value <= 0xFFFFFF or relative:
                raise HomeError('Цвет RGB не поддерживается или задан неверно')
        elif instance == 'temperature_k':
            limits = params.get('temperature_k') or {}
            if type(value) not in (int, float) or not math.isfinite(value) or not limits.get('min', 1) <= value <= limits.get('max', 0) or relative:
                raise HomeError('Цветовая температура вне диапазона устройства')
        elif instance == 'scene':
            if relative or value not in [s.get('id') for s in (params.get('color_scene') or {}).get('scenes', [])]:
                raise HomeError('Цветовая сцена не поддерживается')
        else:
            raise HomeError('Этот режим цвета пока не поддерживается')
        state = {'instance': instance, 'value': value}
        if relative:
            state['relative'] = True
        return {'type': kind, 'state': state}

    def action(self, data):
        with self.lock:
            if not isinstance(data, dict):
                raise HomeError('Нужна одна команда устройства')
            identity = self._account()[2]
            device = self.device(data.get('id'))  # Validate against current cloud metadata.
            if device['state'] == 'offline':
                raise HomeError('Устройство не в сети')
            action = self.validate_action(device, data)
            self._same_account(identity)
            result = self._request('POST', 'devices/actions', {'devices': [{'id': device['id'], 'actions': [action]}]})
            for item in result.get('devices', []):
                if (item.get('action_result') or {}).get('status') == 'ERROR':
                    raise HomeError('Устройство отклонило команду. Обновите его состояние.')
                for cap in item.get('capabilities', []):
                    outcome = (cap.get('state') or {}).get('action_result') or {}
                    if outcome.get('status') == 'ERROR':
                        raise HomeError('Устройство отклонило команду. Обновите состояние и проверьте его доступность.')
            self.cache = None
            return {'ok': True, 'message': 'Команда отправлена. Обновляем состояние устройства.'}

    def configure(self, data):
        with self.lock:
            if not isinstance(data, dict):
                raise HomeError('Неверные настройки подключения')
            if data.get('disconnect') is True:
                self.path.unlink(missing_ok=True)
                self.token, self.identity, self.opener, self.cache = '', None, None, None
                return {'ok': True}
            token = str(data.get('token', '')).strip()
            if not re.fullmatch(r'[A-Za-z0-9_.-]{20,2048}', token):
                raise HomeError('Вставьте OAuth-токен с правами iot:view и iot:control')
            previous = self.token
            self.token, self.identity = token, None
            try:
                result = self.devices(refresh=True)
                if not result['connected']:
                    raise HomeAuthError(result['message'])
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix('.tmp')
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w') as f:
                    json.dump({'token': token}, f)
                os.replace(tmp, self.path)
            except Exception:
                self.token, self.identity, self.opener, self.cache = previous, None, None, None
                raise
            return {'ok': True}
