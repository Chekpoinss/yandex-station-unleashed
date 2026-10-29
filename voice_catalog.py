"""Explicit music intents and conservative catalog matching; no audio or I/O."""
from voice_events import normalized_phrase
from music_artwork import artist_parts


class CatalogError(ValueError):
    pass


def parse_intent(phrase):
    from alarm_intent import parse
    alarm=parse(phrase)
    if alarm:return alarm
    prefixes=(('найди в саундклауде ', 'search_soundcloud'),
              ('найди в soundcloud ', 'search_soundcloud'),
              ('найди трек ', 'search_catalog'),('найди песню ', 'search_catalog'),
              ('включи исполнителя ', 'play_artist'),
              ('включи трек ', 'play_catalog'),('включи песню ', 'play_catalog'))
    for prefix,action in prefixes:
        if phrase.startswith(prefix):
            query=phrase[len(prefix):].strip()
            if query and len(query)<=200:return {'action':action,'query':query}
    return None


def artist_names(track):
    return {normalized_phrase(s) for s in
            [track.get('artist',''),track.get('voice_artist',''),*artist_parts(track.get('artist',''))] if s}


def select(command, tracks):
    query=normalized_phrase(command['query'])
    action=command['action']
    if action=='search_catalog':
        return [t for t in tracks if query in normalized_phrase(' '.join(str(t.get(k,'')) for k in
                    ('title','artist','album','voice_title','voice_artist')))][:50]
    if action=='play_artist':
        matches=[t for t in tracks if query in artist_names(t)]
        if not matches:raise CatalogError('Исполнитель не найден в нашей медиатеке. Можно задать имя для Алисы в данных трека.')
        return matches
    title,sep,artist=query.partition(' исполнителя ')
    matches=[t for t in tracks if title in {normalized_phrase(t.get('title','')),normalized_phrase(t.get('voice_title',''))}
             and (not sep or artist in artist_names(t))]
    if not matches:raise CatalogError('Точного названия в медиатеке нет. Можно задать название для Алисы в данных трека.')
    if len(matches)!=1:raise CatalogError('Несколько треков с таким названием. Уточни исполнителя или задай название для Алисы.')
    return matches
