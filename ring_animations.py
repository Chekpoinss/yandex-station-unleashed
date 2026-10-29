"""Validated three-zone logo animations for the existing single LED worker."""
import copy
import colorsys
import math
import re

NAME = re.compile(r'[\w -]{1,40}', re.UNICODE)

EFFECTS = ('static', 'rgb', 'rainbow', 'chase', 'breathe')


def effect_settings(data, current=None):
    """Transient room-light settings. Zero seconds means no expiry."""
    result = dict(current or dict(mode='rainbow', brightness=35, speed=1., seconds=0,
                                 color=[108, 71, 255]))
    for key in ('mode', 'brightness', 'speed', 'seconds', 'color'):
        if key in data:
            result[key] = copy.deepcopy(data[key])
    if result['mode'] not in EFFECTS:
        raise ValueError('Неизвестный эффект подсветки')
    for key, low, high in (('brightness', 1, 100), ('speed', .2, 3), ('seconds', 0, 600)):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('Некорректный параметр подсветки: ' + key)
    color = result['color']
    if not isinstance(color, list) or len(color) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in color):
        raise ValueError('Цвет: три целых значения от 0 до 255')
    return result


def effect_frame(settings, elapsed):
    """Three-zone effects adapted from the owner's Aura lighting controller."""
    phase = elapsed * settings['speed']
    scale = settings['brightness'] / 100
    mode, color = settings['mode'], settings['color']
    def hue(h):
        return [round(c * 255 * scale) for c in colorsys.hsv_to_rgb(h % 1, .9, 1)]
    if mode == 'rgb':
        return [hue(phase / 7) for _ in range(3)]
    if mode == 'rainbow':
        return [hue(phase / 7 + i / 3) for i in range(3)]
    if mode == 'chase':
        head = phase * 1.5 % 3
        return [[round(c * scale * max(.02, 1 - ((head - i) % 3) / 1.8)) for c in color] for i in range(3)]
    value = (.5 - .5 * math.cos(phase * 2)) ** 1.5 if mode == 'breathe' else 1
    return [[round(c * scale * value) for c in color] for _ in range(3)]

def validate_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name) or not name.strip():
        raise ValueError('Имя: 1–40 букв, цифр, пробелов, дефисов или подчёркиваний')
    return name.strip()

def validate_frames(frames):
    if not isinstance(frames, list) or not 1 <= len(frames) <= 300:
        raise ValueError('Анимация: 1–300 кадров')
    out = []
    for f in frames:
        if not isinstance(f, dict) or type(f.get('ms')) is not int or not 100 <= f['ms'] <= 10000:
            raise ValueError('Длительность кадра: 100–10000 мс')
        rgb = f.get('rgb')
        if (not isinstance(rgb, list) or len(rgb) != 3 or any(not isinstance(zone, list) or len(zone) != 3
                or any(type(c) is not int or not 0 <= c <= 255 for c in zone) for zone in rgb)):
            raise ValueError('Кадр: три RGB-зоны, компоненты 0–255')
        out.append({'ms': f['ms'], 'rgb': copy.deepcopy(rgb)})
    if sum(f['ms'] for f in out) > 60000:
        raise ValueError('Один проход анимации: до 60 секунд')
    return out

def frame_at(frames, elapsed, loops=1):
    duration = sum(f['ms'] for f in frames)
    if not math.isfinite(elapsed) or elapsed < 0 or elapsed * 1000 >= duration * loops:
        return None
    cursor = elapsed * 1000 % duration
    for f in frames:
        if cursor < f['ms']:
            return f['rgb']
        cursor -= f['ms']
    return None
