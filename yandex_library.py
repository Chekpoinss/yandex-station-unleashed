"""Yandex account collections and permitted MP3 streams for the shared player.

Uses the existing account/API client. No credentials or signed stream URLs are
returned with collection metadata or written to the library index.
"""
import hashlib
import re
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from urllib.request import urlopen, Request


class YandexLibrary:
    def __init__(self, api):
        self.api=api
        self.lock=threading.RLock()
        self.cached=None
        self.checked=0
        self.liked=set()
        self.identity=None

    def track(self, raw):
        ident=str(raw.get('id',''))
        if not ident.isdigit():return None
        album=(raw.get('albums') or [{}])[0]
        title=raw.get('title') or 'Без названия'
        if raw.get('version'):title+=' ('+raw['version']+')'
        return {'id':'ym-'+ident,'yandex_id':ident,'source':'yandex','title':title,
                'artist':', '.join(a.get('name','') for a in raw.get('artists',[])),
                'album':album.get('title',''),'album_id':str(album.get('id','')),
                'duration':(raw.get('durationMs') or 0)/1000,
                'cover':self.api.cover(raw.get('coverUri') or album.get('coverUri')),
                'url':'https://music.yandex.ru/track/'+ident,
                'favorite':ident in self.liked,'available':raw.get('available',True)}

    def favorites(self, refresh=False):
        with self.lock:
            token=getattr(getattr(self.api,'auth',None),'music_token',None)
            if not token:
                self.cached=None
                self.liked.clear()
                raise ValueError('Войди в Яндекс в панели, чтобы открыть избранное')
            identity=hashlib.sha256(token.encode()).digest()
            if identity!=self.identity:
                self.identity=identity
                self.api._uid=None
                self.cached=None
                self.liked.clear()
            if self.cached is not None and not refresh and time.monotonic()-self.checked<60:
                return self.cached
            uid=self.api.uid()
            if not uid:raise ValueError('Войди в Яндекс в панели, чтобы открыть избранное')
            likes=(self.api._req(f'/users/{uid}/likes/tracks') or {}).get('library',{}).get('tracks',[])
            ids=list(dict.fromkeys(str(t.get('id','')) for t in likes if str(t.get('id','')).isdigit()))
            self.liked=set(ids)
            found={}
            for offset in range(0,len(ids),100):
                for raw in self.api._req('/tracks',form={'track-ids':','.join(ids[offset:offset+100])}) or []:
                    track=self.track(raw)
                    if track:found[track['yandex_id']]=track
            album_likes=self.api._req(f'/users/{uid}/likes/albums') or []
            album_ids=[str(a.get('id','')) for a in album_likes if str(a.get('id','')).isdigit()]
            albums=[]
            for offset in range(0,len(album_ids),100):
                for raw in self.api._req('/albums',form={'album-ids':','.join(album_ids[offset:offset+100])}) or []:
                    ident=str(raw.get('id',''))
                    if not ident.isdigit():continue
                    albums.append({'id':ident,'source':'yandex','title':raw.get('title',''),
                        'artist':', '.join(a.get('name','') for a in raw.get('artists',[])),
                        'cover':self.api.cover(raw.get('coverUri')),'favorite':True,
                        'track_count':raw.get('trackCount'), 'url':'https://music.yandex.ru/album/'+ident})
            self.cached={'tracks':[found[i] for i in ids if i in found],'albums':albums,
                         'source':'yandex','missing_tracks':len(ids)-len(found)}
            self.checked=time.monotonic()
            return self.cached

    def album(self, ident):
        if not str(ident).isdigit():raise ValueError('Некорректный альбом')
        raw=self.api._req(f'/albums/{ident}/with-tracks') or {}
        tracks=[]
        for volume in raw.get('volumes',[]):
            for item in volume:
                if not item.get('albums'):item={**item,'albums':[raw]}
                track=self.track(item)
                if track:tracks.append(track)
        return {'id':str(ident),'title':raw.get('title',''),'tracks':tracks}

    def favorite(self, ident, on, kind='track'):
        if kind not in ('track','album') or not str(ident).isdigit():raise ValueError('Некорректный объект избранного')
        uid=self.api.uid()
        result=self.api._req(f'/users/{uid}/likes/{kind}s/'+('add-multiple' if on else 'remove'),
                             form={kind+'-ids':str(ident)})
        if not ((kind=='track' and isinstance(result,dict) and 'revision' in result) or
                (kind=='album' and result=='ok')):
            raise ValueError('Яндекс Музыка не подтвердила изменение избранного')
        with self.lock:
            self.checked=0
            self.cached=None
            if kind=='track':
                if on:self.liked.add(str(ident))
                else:self.liked.discard(str(ident))
                self.api._likes=(0.0,set())
        return {'liked':bool(on)}

    @staticmethod
    def trusted_url(url):
        parsed=urlparse(url)
        return (parsed.scheme=='https' and not parsed.username and not parsed.password
                and bool(parsed.hostname) and parsed.hostname.endswith(('.yandex.net','.yandex.ru','.yandex.com')))

    def resolve(self, track):
        ident=track['yandex_id']
        if not str(ident).isdigit():raise ValueError('Некорректный трек Яндекс Музыки')
        options=self.api._req(f'/tracks/{ident}/download-info') or []
        permitted=[o for o in options if o.get('codec')=='mp3' and o.get('preview') is False]
        if not permitted:raise ValueError('Яндекс не предоставил полный MP3 для этого аккаунта. Предпрослушивание вместо песни не включается.')
        option=max(permitted,key=lambda o:o.get('bitrateInKbps',0))
        url=option.get('downloadInfoUrl','')
        if not self.trusted_url(url):raise ValueError('Яндекс вернул неподдерживаемый адрес аудио')
        with urlopen(Request(url,headers={'User-Agent':'Yandex-Music-API'}),timeout=15) as response:
            data=response.read(65537)
        if len(data)>65536:raise ValueError('Слишком большой ответ сервера аудио')
        try:
            root=ET.fromstring(data)
            host,path,stamp,salt=(root.findtext(k) or '' for k in ('host','path','ts','s'))
        except ET.ParseError:raise ValueError('Яндекс вернул некорректные параметры аудио') from None
        if (not path.startswith('/') or not re.fullmatch(r'[a-zA-Z0-9_-]+',stamp) or not salt or
            not re.fullmatch(r'[a-zA-Z0-9.-]+(?::443)?',host) or not self.trusted_url('https://'+host)):
            raise ValueError('Не удалось проверить параметры аудио Яндекса')
        signature=hashlib.md5(('XGRlBW9FXlekgbPrRHuSiA'+path[1:]+salt).encode()).hexdigest()
        return f'https://{host}/get-mp3/{signature}/{stamp}{path}'
