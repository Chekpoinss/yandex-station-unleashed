"""Validated LED settings and profile CRUD, independent of hardware."""
import copy
import math
import re

DEFAULTS = {'enabled': False, 'brightness': 90, 'analysis': 'rc',
            'sensitivity': 1., 'smoothing': .78, 'weights': [1., 1., 1.],
            'palette': []}


def settings(current, updates):
    result = copy.deepcopy(current)
    if 'enabled' in updates:
        if not isinstance(updates['enabled'], bool):
            raise ValueError('Некорректное состояние светомузыки')
        result['enabled'] = updates['enabled']
    for key, low, high in (('brightness', 10, 160), ('sensitivity', .2, 4), ('smoothing', .1, .95)):
        if key in updates:
            value = float(updates[key])
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError('Некорректный параметр светомузыки: ' + key)
            result[key] = round(value) if key == 'brightness' else value
    if 'analysis' in updates:
        if updates['analysis'] not in ('rc', 'fft'):
            raise ValueError('Неизвестный способ анализа')
        result['analysis'] = updates['analysis']
    if 'weights' in updates:
        values = updates['weights']
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError('Нужно три значения частотных диапазонов')
        values = [float(v) for v in values]
        if not all(math.isfinite(v) and 0 <= v <= 3 for v in values):
            raise ValueError('Реакция диапазона должна быть от 0 до 3')
        result['weights'] = values
    if 'palette' in updates:
        values = updates['palette']
        if not isinstance(values, list) or len(values) not in (0, 1, 2, 3, 4, 5, 6):
            raise ValueError('Палитра должна содержать до шести цветов')
        if not all(isinstance(v, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', v) for v in values):
            raise ValueError('Некорректный цвет палитры')
        result['palette'] = [v.lower() for v in values]
    return result


def change(current, profiles, selected, data):
    """Return a new transaction; bad input never mutates the supplied objects."""
    updated, saved = settings(current, data), copy.deepcopy(profiles)
    action = data.get('profile_action')
    if not action:
        return updated, saved, selected
    name = str(data.get('name', '')).strip()
    if not name or len(name) > 40:
        raise ValueError('Название профиля: от 1 до 40 символов')
    if action == 'save':
        if name not in saved and len(saved) >= 32:
            raise ValueError('Можно сохранить до 32 профилей')
        saved[name] = {k: v for k, v in updated.items() if k != 'enabled'}
        selected = name
    elif action == 'apply':
        if name not in saved:
            raise ValueError('Профиль не найден')
        updated = settings(updated, {k: v for k, v in saved[name].items() if k != 'enabled'})
        selected = name
    elif action == 'rename':
        old = data.get('old')
        if old not in saved or (name in saved and name != old):
            raise ValueError('Профиль не найден или новое имя уже занято')
        saved[name] = saved.pop(old)
        if selected == old:
            selected = name
    elif action == 'delete':
        if name not in saved:
            raise ValueError('Профиль не найден')
        del saved[name]
        if selected == name:
            selected = None
    else:
        raise ValueError('Неизвестное действие с профилем')
    return updated, saved, selected
