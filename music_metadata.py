"""Local lyrics parsing. No invented timings and no network calls."""
import re


def lyrics_text(text, source='Локальный файл'):
    text = str(text).lstrip('\ufeff')[:1024 * 1024]
    offset = re.search(r'\[offset:([+-]?\d+)\]', text, re.I)
    offset = int(offset[1]) / 1000 if offset else 0
    sync = []
    for line in text.splitlines():
        stamps = list(re.finditer(r'\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]', line))
        if not stamps:
            continue
        words = line[stamps[-1].end():].strip()
        # Enhanced LRC word timings are not rendered as real word alignment yet.
        words = re.sub(r'<\d{1,3}:\d{2}(?:\.\d{1,3})?>', '', words)
        for stamp in stamps:
            if float(stamp[2]) < 60:
                sync.append([max(0, int(stamp[1]) * 60 + float(stamp[2]) + offset), words])
    sync.sort(key=lambda row: row[0])
    return {'sync': sync or None, 'text': '\n'.join(row[1] for row in sync) if sync else text.strip(),
            'source': source}


def embedded_lyrics(audio):
    tags = getattr(audio, 'tags', None)
    if not tags:
        return None
    if hasattr(tags, 'getall'):
        for tag in tags.getall('SYLT'):
            if tag.format == 2 and tag.type == 1:
                sync = [[ms / 1000, str(text)] for text, ms in tag.text
                        if isinstance(ms, (int, float)) and 0 <= ms <= 24 * 3600 * 1000]
                if sync:
                    sync.sort(key=lambda row: row[0])
                    return {'sync': sync, 'text': '\n'.join(row[1] for row in sync), 'source': 'Встроенный текст'}
        for tag in tags.getall('USLT'):
            if tag.text.strip():
                return lyrics_text(tag.text, 'Встроенный текст')
    for key in ('lyrics', 'unsyncedlyrics', 'syncedlyrics', '\xa9lyr'):
        values = tags.get(key) or []
        if isinstance(values, str):
            values = [values]
        if values and str(values[0]).strip():
            return lyrics_text(values[0], 'Встроенный текст')
    return None
