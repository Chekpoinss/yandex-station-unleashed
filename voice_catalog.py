"""Explicit music intents and conservative catalog matching; no audio or I/O."""
from difflib import SequenceMatcher
from voice_events import normalized_phrase
from music_artwork import artist_parts


class CatalogError(ValueError):
    pass


def parse_intent(phrase):
    from alarm_intent import parse
    alarm=parse(phrase)
    if alarm:return alarm
    prefixes=(('найди в саундклауде ', 'search_soundcloud'),
              ('поищи в саундклауде ', 'search_soundcloud'),
              ('найди в soundcloud ', 'search_soundcloud'),
              ('поищи в soundcloud ', 'search_soundcloud'),
              ('найди трек ', 'search_catalog'),('найди песню ', 'search_catalog'),
              ('поищи трек ', 'search_catalog'),('поищи песню ', 'search_catalog'),
              ('найди музыку ', 'search_catalog'),('поищи музыку ', 'search_catalog'),
              ('включи исполнителя ', 'play_artist'),('запусти исполнителя ', 'play_artist'),
              ('поставь исполнителя ', 'play_artist'),
              ('включи трек ', 'play_catalog'),('включи песню ', 'play_catalog'),
              ('запусти трек ', 'play_catalog'),('запусти песню ', 'play_catalog'),
              ('поставь трек ', 'play_catalog'),('поставь песню ', 'play_catalog'),
              ('проиграй трек ', 'play_catalog'),('проиграй песню ', 'play_catalog'),
              ('воспроизведи трек ', 'play_catalog'),('воспроизведи песню ', 'play_catalog'))
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
    def title_match(track, exact):
        names={normalized_phrase(track.get('title','')),normalized_phrase(track.get('voice_title',''))}
        names.discard('')
        title_ok=title in names if exact else any(title in name for name in names)
        return title_ok and (not sep or artist in artist_names(track))
    matches=[t for t in tracks if title_match(t, True)]
    # Search intentionally supports partial titles. Playback may use the same
    # unique partial match, while still refusing ambiguous titles.
    if not matches:
        matches=[t for t in tracks if title_match(t, False)]
    if not matches and not sep:
        # Speech recognition commonly changes one character in English song
        # names (for example "imposter" / "impostor"). Accept only when every
        # query word has a strong match to a distinct title word.
        query_words=query.split()
        ranked=[]
        for track in tracks:
            names={normalized_phrase(track.get('title','')),normalized_phrase(track.get('voice_title',''))}
            best=0.0
            for name in names:
                title_words=name.split()
                if not query_words or len(query_words)>len(title_words):
                    continue
                scores=[]
                used=set()
                for word in query_words:
                    choices=[(SequenceMatcher(None,word,other).ratio(),i)
                             for i,other in enumerate(title_words) if i not in used]
                    if not choices:
                        scores=[];break
                    score,index=max(choices)
                    if score < (1.0 if len(word)<4 else .82):
                        scores=[];break
                    used.add(index);scores.append(score)
                if scores:best=max(best,sum(scores)/len(scores))
            if best:ranked.append((best,track))
        if ranked:
            ranked.sort(key=lambda item:item[0],reverse=True)
            top=ranked[0][0]
            matches=[track for score,track in ranked if top-score < .015]
    if not matches:raise CatalogError('Точного названия в медиатеке нет. Можно задать название для Алисы в данных трека.')
    if len(matches)!=1:raise CatalogError('Несколько треков с таким названием. Уточни исполнителя или задай название для Алисы.')
    return matches
