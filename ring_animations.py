"""Validated three-zone logo animations for the existing single LED worker."""
import copy
import math
import re

NAME = re.compile(r'[\w -]{1,40}', re.UNICODE)

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
