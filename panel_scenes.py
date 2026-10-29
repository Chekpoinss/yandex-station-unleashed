"""Panel-hosted scenarios. No arbitrary shell, extra service or autonomous device daemon."""
import copy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

MOSCOW = timezone(timedelta(hours=3), 'Europe/Moscow')


def validate_steps(steps, depth=0, budget=None):
    budget = [0] if budget is None else budget
    if depth > 5 or not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError('Шаги: 1–100, вложенность до 5')
    out = []
    for raw in steps:
        if not isinstance(raw, dict): raise ValueError('Шаг должен быть объектом')
        budget[0] += 1
        if budget[0] > 200: raise ValueError('Не более 200 узлов сценария')
        kind = raw.get('type')
        step = {'type': kind}
        if kind == 'wait':
            seconds = raw.get('seconds')
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 3600:
                raise ValueError('Ожидание: 0–3600 секунд')
            step['seconds'] = seconds
        elif kind == 'music':
            action = raw.get('action')
            if action not in ('play', 'pause', 'resume', 'stop', 'next', 'previous', 'options'):
                raise ValueError('Неизвестное действие общего плеера')
            step['action'] = action
            if action == 'play':
                if not isinstance(raw.get('id'), str) or not re.fullmatch(r'[a-z0-9-]{1,64}', raw['id']):
                    raise ValueError('Выберите трек медиатеки')
                step['id'] = raw['id']
            if action == 'options':
                if raw.get('repeat_mode', 'None') not in ('None', 'One', 'All') or type(raw.get('shuffle', False)) is not bool:
                    raise ValueError('Проверьте режим повтора и shuffle')
                step.update(repeat_mode=raw.get('repeat_mode', 'None'), shuffle=raw.get('shuffle', False))
        elif kind == 'volume':
            v = raw.get('level')
            if type(v) is not int or not 0 <= v <= 100: raise ValueError('Громкость: 0–100')
            step['level'] = v
        elif kind == 'say':
            text = raw.get('text')
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 300: raise ValueError('Текст: 1–300 символов')
            step['text'] = text.strip()
        elif kind == 'animation':
            from ring_animations import validate_name
            step['name'] = validate_name(raw.get('name'))
        elif kind == 'repeat':
            count = raw.get('count')
            if type(count) is not int or not 1 <= count <= 20: raise ValueError('Цикл: 1–20 повторов')
            step.update(count=count, steps=validate_steps(raw.get('steps'), depth+1, budget))
        elif kind == 'if':
            field = raw.get('condition')
            value = raw.get('value')
            if field == 'phase' and value not in ('playing', 'paused', 'stopped', 'loading'):
                raise ValueError('Условие состояния плеера некорректно')
            if field == 'online' and type(value) is not bool: raise ValueError('Условие связи: true или false')
            if field not in ('phase', 'online'): raise ValueError('Условия: phase или online')
            step.update(condition=field, value=value, steps=validate_steps(raw.get('steps'), depth+1, budget))
            if raw.get('else'):
                step['else'] = validate_steps(raw['else'], depth+1, budget)
        else:
            raise ValueError('Неизвестный тип шага')
        out.append(step)
    return out


def validate_scene(raw):
    if not isinstance(raw, dict): raise ValueError('Сценарий должен быть объектом')
    name = raw.get('name')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80: raise ValueError('Название: 1–80 символов')
    enabled = raw.get('enabled', False)
    if type(enabled) is not bool: raise ValueError('enabled: true или false')
    schedule = raw.get('schedule')
    if schedule is not None:
        if not isinstance(schedule, dict) or not isinstance(schedule.get('time'), str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', schedule.get('time', '')):
            raise ValueError('Время: ЧЧ:ММ, Europe/Moscow')
        days = schedule.get('days', list(range(7)))
        if not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
            raise ValueError('Дни: 0 (понедельник) — 6 (воскресенье)')
        schedule = {'time': schedule['time'], 'days': sorted(set(days))}
        if raw['schedule'].get('date'):
            date = raw['schedule']['date']
            try:
                parsed = datetime.strptime(date, '%Y-%m-%d').date().isoformat()
            except (TypeError, ValueError):
                raise ValueError('Дата разового запуска: ГГГГ-ММ-ДД') from None
            schedule['date'] = parsed
    return {'name': name.strip(), 'enabled': enabled, 'schedule': schedule, 'steps': validate_steps(raw.get('steps'))}


class Scenes:
    def __init__(self, panel, path, journal=None, start=True):
        self.panel, self.path, self.journal = panel, Path(path), journal
        self.lock = threading.RLock()
        self.items, self.fired, self.runs = {}, {}, {}
        self.revision = 0
        self.storage_error = False
        self.last_minute = int(time.time() // 60)
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text('utf8'))
                self.items = {k: validate_scene(v) for k, v in raw['items'].items() if re.fullmatch(r'[a-f0-9]{12}', k)}
                self.revision = int(raw.get('revision', 0))
                self.fired = {k: str(v) for k, v in raw.get('fired', {}).items() if k in self.items}
            except (ValueError, KeyError, TypeError, AttributeError):
                # Never overwrite a corrupt schedule as if it were empty.
                self.storage_error = True
        if start:
            threading.Thread(target=self.scheduler, daemon=True, name='panel-scenes').start()

    def persist(self):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'revision': self.revision, 'items': self.items, 'fired': self.fired}, ensure_ascii=False), encoding='utf8')
        os.replace(tmp, self.path)

    def status(self):
        with self.lock:
            return {'items': copy.deepcopy(self.items), 'revision': self.revision, 'timezone': 'Europe/Moscow',
                    'hosted_on': 'pc', 'storage_error': self.storage_error,
                    'runs': {k: {x: v[x] for x in ('state', 'step', 'error')} for k, v in self.runs.items()}}

    def save(self, data):
        if data.get("action", "save") not in ("save", "delete"):
            raise ValueError("Неизвестное действие")
        scene = validate_scene(data.get('scene')) if data.get('action') != 'delete' else None
        with self.lock:
            if self.storage_error: raise ValueError('Файл сценариев повреждён. Исходный файл сохранён; исправьте его перед записью.')
            if data.get('revision') != self.revision: raise ValueError('Сценарии изменились. Обновите список.')
            ident = data.get('id') or uuid.uuid4().hex[:12]
            if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{12}', ident): raise ValueError('Некорректный ID')
            if ident in self.runs and self.runs[ident]['state'] in ('running', 'cancelling'):
                raise ValueError('Сначала остановите сценарий')
            before, old_fired = copy.deepcopy(self.items), dict(self.fired)
            if data.get('action') == 'delete':
                self.items.pop(ident, None); self.fired.pop(ident, None)
            else:
                if ident not in self.items and len(self.items) >= 50: raise ValueError('До 50 сценариев')
                self.items[ident] = scene
                # Editing never fires a new schedule in the current minute.
                self.fired[ident] = datetime.now(MOSCOW).strftime('%Y-%m-%d %H:%M')
            self.revision += 1
            try: self.persist()
            except OSError:
                self.items, self.fired = before, old_fired; self.revision -= 1
                raise ValueError('Не удалось сохранить сценарии; прежнее расписание сохранено') from None
            if data.get('action') == 'delete': self.runs.pop(ident, None)
            return {'id': ident, **self.status()}

    def start_run(self, ident):
        with self.lock:
            if ident not in self.items: raise ValueError('Сценарий не найден')
            if not self.panel.conn.online: raise ValueError('Колонка не подключена')
            if any(r['state'] in ('running', 'cancelling') for r in self.runs.values()):
                raise ValueError('Уже выполняется сценарий; дождитесь завершения или остановите его')
            scene = copy.deepcopy(self.items[ident])
            stop = threading.Event()
            self.runs[ident] = {'state': 'running', 'step': 0, 'error': None, 'stop': stop}
            threading.Thread(target=self.execute, args=(ident, scene, stop), daemon=True, name='panel-scene-run').start()
            return self.status()

    def cancel(self, ident):
        with self.lock:
            run = self.runs.get(ident)
            if run and run['state'] == 'running': run['state'] = 'cancelling'; run['stop'].set()
            return self.status()

    def tick(self, now):
        minute = int(now.timestamp() // 60)
        with self.lock:
            if minute <= self.last_minute: return
            self.last_minute = minute
            key = now.strftime('%Y-%m-%d %H:%M')
            for ident, scene in self.items.items():
                sc = scene['schedule']
                if (not scene['enabled'] or not sc or sc['time'] != now.strftime('%H:%M')
                        or now.weekday() not in sc['days'] or sc.get('date', now.date().isoformat()) != now.date().isoformat()
                        or self.fired.get(ident) == key): continue
                before = self.fired.get(ident)
                self.fired[ident] = key
                try: self.persist()  # At most once, including a restart in this minute.
                except OSError:
                    if before is None: self.fired.pop(ident, None)
                    else: self.fired[ident] = before
                    self.storage_error = True
                    return
                try: self.start_run(ident)
                except ValueError:
                    if self.runs.get(ident, {}).get('state') in ('running', 'cancelling'):
                        continue
                    self.runs[ident] = {'state': 'skipped', 'step': 0, 'error': 'Пропущен: колонка недоступна или занят исполнитель'}

    def scheduler(self):
        while True:
            time.sleep(.5)
            try:
                if not self.storage_error: self.tick(datetime.now(MOSCOW))
            except Exception:
                self.storage_error = True

    def execute(self, ident, scene, stop):
        deadline = time.monotonic() + 3600
        count = [0]
        def check():
            if stop.is_set(): raise InterruptedError()
            if time.monotonic() > deadline: raise ValueError('Лимит времени')
            if not self.panel.conn.online: raise ValueError('Связь потеряна')
        def steps(nodes):
            for node in nodes:
                check(); count[0] += 1
                if count[0] > 1000: raise ValueError('Лимит шагов')
                with self.lock: self.runs[ident]['step'] = count[0]
                kind = node['type']
                if kind == 'repeat':
                    for _ in range(node['count']): steps(node['steps'])
                elif kind == 'if':
                    value = self.panel.conn.online if node['condition'] == 'online' else self.panel.library.state().get('phase')
                    branch = node['steps'] if value == node['value'] else node.get('else', [])
                    steps(branch)
                elif kind == 'wait':
                    until = min(deadline, time.monotonic() + node['seconds'])
                    while time.monotonic() < until:
                        if stop.wait(min(.2, max(0, until-time.monotonic()))): raise InterruptedError()
                        check()
                else:
                    # Avoid stealing audio/LED from Alice and the alarm. Timeout/cancel still apply.
                    while (getattr(getattr(self.panel, 'metrics', None), 'ringing', None)
                           or str((self.panel.glagol.state or {}).get('aliceState', 'IDLE')).upper() not in ('IDLE', 'NONE', 'STANDBY', '')):
                        check()
                        if stop.wait(.2): raise InterruptedError()
                    check()
                    if kind == 'music':
                        if node['action'] == 'play':
                            self.panel.library.play(node['id'], _guard=lambda: not stop.is_set() and time.monotonic() < deadline)
                        else: self.panel.library.action(node)
                    elif kind == 'volume': self.panel.set_volume({'level': node['level']})
                    elif kind == 'say': self.panel.say(node['text'])
                    elif kind == 'animation': self.panel.library.lights.animation_action({'action': 'play', 'name': node['name']})
                    check()
        state, error = 'completed', None
        try: steps(scene['steps'])
        except InterruptedError: state = 'cancelled'
        except Exception:
            state, error = 'failed', 'Выполнение прервано: проверьте подключение, трек и настройки; тексты и адреса ошибок не журналируются'
        with self.lock:
            self.runs[ident].update(state=state, error=error)
        if self.journal:
            self.journal.record('/api/scenes/run', 200 if state == 'completed' else 409)
