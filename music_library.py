"""Local music library and LAN streaming for the owner's speaker.

The control API stays behind the panel's access guard. Only a random-token audio
URL is reachable on LAN; no files or control commands are exposed there.
"""
import hashlib
import copy
import base64
import json
import mimetypes
import math
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from playback_order import PlaybackOrder, unique
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import ProxyHandler, build_opener, getproxies

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'runtime' / 'packages'))
from mutagen import File as AudioFile


def soundcloud_extract(options, target):
    """Keep extractor diagnostics out of user-facing errors and stream URLs."""
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(target, download=False)
    except yt_dlp.utils.DownloadError as exc:
        detail=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(exc)).lower()
        if 'drm' in detail:
            message=('Этот трек SoundCloud недоступен нашему плееру: загрузчик сообщил о защите DRM. '
                     'Он может играть на сайте SoundCloud. Открой оригинал через меню трека '
                     'или выбери другую доступную запись. Переключение VPN не снимает защиту.')
        elif any(s in detail for s in ('timed out','timeout','connection','proxy','unable to download')):
            message='Не удалось получить трек из SoundCloud. Проверь VPN на ПК и повтори попытку.'
        else:
            message='SoundCloud не предоставил доступный поток. Попробуй другую запись или открой оригинал через меню трека.'
        raise ValueError(message) from None


def native_audio_payload(url, track, session_id=None):
    """audio_play directive, as used by AlexxIT/YandexStation's Glagol player.

    https://github.com/AlexxIT/YandexStation/blob/master/custom_components/yandex_station/core/utils.py
    """
    payload = {'stream': {'url': url, 'format': 'MP3', 'type': 'Track', 'id': track['id']},
               'metadata': {'title': track['title'], 'subtitle': track['artist']}}
    if session_id:
        payload['stream']['id'] = session_id
    def field(number, value):
        raw = value.encode('utf8')
        size = len(raw)
        length = bytearray()
        while size > 127:
            length.append((size & 127) | 128)
            size >>= 7
        length.append(size)
        return bytes([number << 3 | 2]) + length + raw
    encoded = field(1, 'audio_play') + field(2, json.dumps(payload, ensure_ascii=False))
    return {'command': 'externalCommandBypass', 'data': base64.b64encode(encoded).decode('ascii')}


class Library:
    def __init__(self, panel):
        self.panel = panel
        self.root = Path(getattr(panel, 'data_dir', ROOT)) / 'music-data'
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = self.root / 'library.json'
        self.data = json.loads(self.index.read_text('utf8')) if self.index.exists() else {'tracks': {}, 'playlists': {}}
        self.lock = threading.RLock()
        self.control = threading.RLock()
        # Queue persistence is independent from explicit library membership.
        cached=self.data.get('queue_tracks',{})
        self.remote={i:dict(t) for i,t in cached.items()
                     if isinstance(t,dict) and t.get('source') in ('soundcloud','yandex') and t.get('id')==i} if isinstance(cached,dict) else {}
        known=set(self.data['tracks']) | set(self.remote)
        self.queue = [i for i in self.data.get('last_queue', []) if i in known]
        self.current = self.data.get('last_track') if self.data.get('last_track') in known else None
        self.cursor = self.queue.index(self.current) if self.current in self.queue else -1
        self.phase = 'stopped'
        self.offset = 0
        self.started = 0
        self.error = None
        self.order = PlaybackOrder.restore(self.data.get('playback_order'), known, self.queue, self.current)
        self.current = self.order.current
        self._sync_order(persist=False)
        self.native_events = deque(maxlen=128)
        self.native_pending = deque(maxlen=256)
        self.native_session_id = None
        self.owned_sessions = deque(maxlen=16)
        self.native_confirmed = False
        self.stream_ready = False
        self.play_generation = 0
        if hasattr(panel.glagol, 'state_callbacks'):
            panel.glagol.state_callbacks.append(lambda state, updated: self.native_pending.append((state, updated)))
        self.active = self.data.get('music_source', 'library') == 'library'
        self.native_audio = False
        self.native_updated = 0
        self.stream_offset = 0
        self.media_reason = 'stopped'
        self.token = secrets.token_urlsafe(32)
        proxies = getproxies()
        self.proxy = os.environ.get('MUSIC_PROXY') or proxies.get('https') or proxies.get('http') or ''
        from yandex_library import YandexLibrary
        self.yandex = YandexLibrary(panel.ymusic)
        speaker = panel.adb.serial.split(':')[0]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((speaker, 5555))
            self.host = sock.getsockname()[0]
        finally:
            sock.close()
        self.port = 0 if os.environ.get('PANEL_GATEWAY_SECRET') else 8767
        self.http = ThreadingHTTPServer((self.host, self.port), self.stream_handler())
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True, name='music-stream').start()
        threading.Thread(target=self.monitor, daemon=True, name='music-queue').start()
        from music_artwork import ArtworkWorker
        self.artwork = ArtworkWorker(self)
        for ident, track in list(self.data['tracks'].items()):
            if not track.get('cover'):
                self.artwork.enqueue(ident)
        def warm_yandex():
            try:self.yandex_collection()
            except Exception:pass  # explicit UI refresh reports login/network errors
        threading.Thread(target=warm_yandex,daemon=True,name='yandex-collection').start()

    def yandex_collection(self, refresh=False, album=None):
        result=self.yandex.album(album) if album is not None else self.yandex.favorites(refresh)
        with self.lock:
            changed=False
            if album is None:
                for t in self.data['tracks'].values():
                    if t.get('source')=='yandex':
                        liked=t.get('yandex_id') in self.yandex.liked
                        if bool(t.get('favorite'))!=liked:t['favorite']=liked;changed=True
                for t in self.remote.values():
                    if t.get('source')=='yandex':t['favorite']=t.get('yandex_id') in self.yandex.liked
            tracks=[]
            for t in result.get('tracks',[]):
                owned=self.data['tracks'].get(t['id'])
                value=self.public(owned) if owned else dict(t)
                self.remote[t['id']]=value
                tracks.append(value)
            if changed:self.save()
        return {**result,'tracks':tracks}

    def voice_catalog(self):
        with self.lock:
            tracks={i:self.public(t) for i,t in self.data['tracks'].items()}
            for ident,t in self.remote.items():
                if t.get('source')=='yandex' and t.get('favorite'):tracks.setdefault(ident,self.public(t))
            return list(tracks.values())

    def save(self):
        with self.lock:
            temp = self.index.with_suffix('.tmp')
            temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf8')
            temp.replace(self.index)

    def _sync_order(self, persist=True):
        self.queue, self.cursor = list(self.order.order), self.order.index
        self.repeat_mode, self.shuffle = self.order.repeat, self.order.shuffle
        self.repeat = self.repeat_mode != 'None'
        if persist:
            self.data['queue_tracks']={ident:self.public(self.remote[ident]) for ident in self.order.base
                                      if ident not in self.data['tracks'] and ident in self.remote}
            self.data['last_track'] = self.current
            self.data['last_queue'] = list(self.order.base)
            self.data['playback_order'] = self.order.dump()
            self.save()

    def public(self, track):
        return {k: v for k, v in track.items() if k not in ('file', 'stream', '_lyrics', '_lyrics_checked')}

    def local_lyrics(self, track):
        if '_lyrics' in track:
            return track['_lyrics']
        if track.get('source') != 'local' or track.get('_lyrics_checked'):
            return None
        from music_metadata import embedded_lyrics
        value = embedded_lyrics(AudioFile(self.root / track['file']))
        with self.lock:
            if track.get('_lyrics'):
                return track['_lyrics']
            track['_lyrics_checked'] = True
            if value:
                track['_lyrics'] = value
            self.save()
        return value

    def tracks(self):
        with self.lock:
            return {'tracks': [self.public(t) for t in self.data['tracks'].values()],
                    'playlists': self.data['playlists'],
                    'trash': [self.public(r['track']) for r in self.data.get('trash',{}).values()],
                    'features': {'local_lyrics': True, 'auto_artwork': True,
                                 'artwork_fallback': True, 'metadata_edit': True, 'voice_catalog': True, 'trash':True,
                                 'alarm_music':getattr(getattr(self,'lights',None),'supported',False)}}

    def remove_track(self, ident, service=False):
        with self.control:
            with self.lock:
                track=self.data['tracks'].get(ident) or self.remote.get(ident)
                if not track:raise ValueError('Трек не найден')
                track=dict(track)
                owned=ident in self.data['tracks']
            service_removed=False
            if service and track.get('source')=='yandex' and track.get('favorite'):
                self.yandex.favorite(track['yandex_id'],False)
                service_removed=True
            if self.current==ident and self.active:
                self.action({'action':'stop'})
            with self.lock:
                original=copy.deepcopy(self.data)
                previous_order=self.order.copy()
                previous_remote=dict(self.remote)
                playlists={name:ids.index(ident) for name,ids in self.data['playlists'].items() if ident in ids}
                self.data.setdefault('trash',{})[ident]={'track':track,'saved':owned,
                    'playlists':playlists,'service_removed':service_removed,'removed_at':time.time()}
                self.data['tracks'].pop(ident,None)
                self.remote.pop(ident,None)
                for name,ids in self.data['playlists'].items():
                    self.data['playlists'][name]=[i for i in ids if i!=ident]
                self.order.discard(ident)
                self.current=self.order.current
                try:self._sync_order()
                except Exception:
                    self.data=original;self.remote=previous_remote;self.order=previous_order
                    self.current=self.order.current;self._sync_order(False)
                    if service_removed:
                        raise ValueError('Лайк в Яндексе снят, но локальное удаление не удалось. Трек сохранён в панели.') from None
                    raise
            return {'ok':True,'id':ident,'service_removed':service_removed}

    def restore_track(self, ident):
        with self.control:
            with self.lock:
                record=self.data.get('trash',{}).get(ident)
                if not record:raise ValueError('Трек не найден в корзине')
                record=copy.deepcopy(record)
            if record.get('service_removed'):
                self.yandex.favorite(record['track']['yandex_id'],True)
            with self.lock:
                original=copy.deepcopy(self.data)
                old_remote=dict(self.remote)
                if record['saved']:self.data['tracks'].setdefault(ident,record['track'])
                else:self.remote.setdefault(ident,record['track'])
                for name,index in record['playlists'].items():
                    if name in self.data['playlists'] and ident not in self.data['playlists'][name]:
                        self.data['playlists'][name].insert(min(index,len(self.data['playlists'][name])),ident)
                self.data['trash'].pop(ident,None)
                try:self.save()
                except Exception:
                    self.data=original;self.remote=old_remote
                    raise
            return self.public(record['track'])

    def import_file(self, name, body):
        if not body or len(body) > 150 * 1024 * 1024:
            raise ValueError('Допустимый размер файла: до 150 МБ')
        suffix = Path(name).suffix.lower()
        if suffix not in ('.mp3', '.flac', '.wav', '.ogg', '.opus', '.m4a', '.aac'):
            raise ValueError('Выберите аудиофайл MP3, FLAC, WAV, OGG, OPUS или M4A')
        ident = hashlib.sha256(body).hexdigest()[:24]
        with self.lock:
            if ident in self.data['tracks']:
                return self.public(self.data['tracks'][ident])
        path = self.root / (ident + suffix)
        path.write_bytes(body)
        try:
            easy = AudioFile(path, easy=True)
            raw = AudioFile(path)
            if not easy or not hasattr(easy, 'info'):
                raise ValueError('Не удалось прочитать аудиофайл')
            def tag(key, default=''):
                values = easy.get(key, [])
                return str(values[0]) if values else default
            title = tag('title', Path(name).stem)
            artist = tag('artist', 'Неизвестный исполнитель')
            cover = None
            tags = getattr(raw, 'tags', None)
            pictures = getattr(raw, 'pictures', [])
            if pictures:
                cover = pictures[0].data
            elif tags:
                pics = tags.getall('APIC') if hasattr(tags, 'getall') else []
                if pics:
                    cover = pics[0].data
                elif tags.get('covr'):
                    cover = bytes(tags['covr'][0])
                elif tags.get('metadata_block_picture'):
                    import base64
                    from mutagen.flac import Picture
                    cover = Picture(base64.b64decode(tags['metadata_block_picture'][0])).data
            cover_url = None
            if cover and len(cover) <= 12 * 1024 * 1024:
                cp = self.root / (ident + '.cover')
                cp.write_bytes(cover)
                cover_url = '/api/library/cover/' + ident
            track = {'id': ident, 'title': title, 'artist': artist, 'album': tag('album'),
                     'duration': round(easy.info.length, 2), 'cover': cover_url,
                     'file': path.name, 'filename': name, 'size': len(body),
                     'source': 'local', 'added': time.time(), 'favorite': False}
            from music_metadata import embedded_lyrics
            track['_lyrics_checked'] = True
            lyrics = embedded_lyrics(raw)
            if lyrics:
                track['_lyrics'] = lyrics
            with self.lock:
                self.data['tracks'][ident] = track
                self.save()
            if not cover_url and getattr(self, 'artwork', None):
                self.artwork.enqueue(ident)
            return self.public(track)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def soundcloud(self, query, search=True):
        import yt_dlp
        options = {'quiet': True, 'no_warnings': True, 'socket_timeout': 15,
                   'retries': 1, 'proxy': self.proxy, 'noplaylist': True,
                   'extract_flat': 'in_playlist' if search else False,
                   'format': 'bestaudio/best', 'cachedir': str(self.root / 'sc-cache')}
        query = query.strip()
        if not query:
            return {'tracks': []}
        is_url = query.startswith(('https://', 'http://'))
        if is_url:
            host = urlparse(query).hostname or ''
            if host not in ('soundcloud.com', 'www.soundcloud.com', 'on.soundcloud.com', 'm.soundcloud.com'):
                raise ValueError('Нужна ссылка SoundCloud')
        target = query if is_url else 'scsearch12:' + query[:200]
        result = soundcloud_extract(options, target)
        entries = list(result.get('entries') or [result])
        tracks = []
        with self.lock:
            for e in entries:
                if not e or not e.get('id'):
                    continue
                ident = 'sc-' + str(e['id'])
                url = e.get('webpage_url') or e.get('url')
                if not url or not url.startswith('http'):
                    continue
                thumbs = e.get('thumbnails') or []
                cover = e.get('thumbnail') or next((x['url'] for x in thumbs if x.get('id') == 't500x500'), None)
                if not cover and thumbs:
                    cover = thumbs[-1].get('url')
                t = {'id': ident, 'title': e.get('title') or 'SoundCloud',
                     'artist': e.get('uploader') or e.get('artist') or 'SoundCloud',
                     'duration': e.get('duration') or 0, 'cover': cover,
                     'url': url, 'source': 'soundcloud', 'album': '', 'added': time.time(), 'favorite': False}
                if ident in self.data['tracks']:
                    t['favorite'] = self.data['tracks'][ident].get('favorite', False)
                self.remote[ident] = t
                if not search:
                    self.data['tracks'][ident] = t
                tracks.append(self.public(t))
            if not search:
                self.save()
        return {'tracks': tracks}

    def resolve(self, track):
        if track['source'] == 'local':
            return str(self.root / track['file']), False
        if track['source'] == 'yandex':
            return self.yandex.resolve(track), True
        import yt_dlp
        info = soundcloud_extract({'quiet': True, 'no_warnings': True, 'proxy': self.proxy,
                              'socket_timeout': 15, 'retries': 1, 'noplaylist': True,
                              'format': 'bestaudio/best', 'cachedir': str(self.root / 'sc-cache')}, track['url'])
        if not info.get('url'):
            raise ValueError('SoundCloud не разрешил воспроизведение этого трека')
        return info['url'], True

    def raw_position(self):
        return max(0, self.offset + (max(0, time.monotonic() - self.started) if self.phase == 'playing' else 0))

    def position(self):
        value = self.raw_position()
        track = self.data['tracks'].get(self.current) or self.remote.get(self.current) or {}
        duration = track.get('duration', 0)
        return min(value, duration) if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0 else value

    def state(self):
        self.reconcile_native()
        with self.lock:
            t = self.data['tracks'].get(self.current) or self.remote.get(self.current)
            g = getattr(self.panel, 'glagol', None)
            native = (g.state or {}) if g else {}
            return {'track': self.public(t) if t else None, 'phase': self.phase,
                    'position': round(self.position(), 2), 'queue': list(self.queue),
                    'queue_tracks': [self.public(self.data['tracks'].get(i) or self.remote[i])
                                     for i in self.queue if i in self.data['tracks'] or i in self.remote],
                    'index': self.cursor, 'error': self.error, 'repeat': self.repeat,
                    'shuffle': self.shuffle, 'repeat_mode': self.repeat_mode, 'active': self.active,
                    'media_reason': getattr(self, 'media_reason', None),
                    'has_next': self.order.has_next if hasattr(self, 'order') else False,
                    'has_previous': self.order.has_previous if hasattr(self, 'order') else False,
                    'queue_revision': self.order.revision if hasattr(self, 'order') else 0,
                    'native': {'connected': bool(g and g.ws), 'playing': bool(native.get('playing')),
                               'matches_track': self.matches_native(native.get('playerState') or {}),
                               'player_type': (native.get('playerState') or {}).get('playerType')}}

    def matches_native(self, player):
        return str(player.get('id', '')) == (getattr(self, 'native_session_id', None) or self.current)

    def reconcile_native(self, snapshot=None):
        """Observe the identified native session; never infer a resume from voice activity.

        audio_play preserves our stream id in Glagol playerState on Micro. An
        unrelated playing session belongs to Alice, even if our queue is selected.
        No voice interception or automatic command replay is performed here.
        """
        if not getattr(self, 'native_audio', False) or not self.active:
            if hasattr(self, 'native_pending'):
                self.native_pending.clear()
            return
        if snapshot is None and hasattr(self, 'native_pending'):
            for _ in range(256):
                try:
                    event = self.native_pending.popleft()
                except IndexError:
                    break
                self.reconcile_native(event)
        g = getattr(self.panel, 'glagol', None)
        with self.lock:
            if not g or not g.ws:
                if self.phase == 'playing':
                    self.offset = self.position()
                    self.phase = 'paused'
                    self.media_reason = 'disconnected'
                return
            updated = snapshot[1] if snapshot else g.updated
            if updated <= self.native_updated or updated < getattr(self, 'play_requested_at', 0):
                return
            self.native_updated = updated
            s = snapshot[0] if snapshot else (g.state or {})
            p = s.get('playerState') or {}
            before, previous_phase = self.position(), self.phase
            if hasattr(self, 'native_events'):
                self.native_events.append({'at': round(updated, 3), 'playing': bool(s.get('playing')),
                    'matched': self.matches_native(p), 'progress': p.get('progress'),
                    'duration': p.get('duration'), 'before': round(before, 3), 'phase': previous_phase,
                    'alice': s.get('aliceState'), 'repeat': self.repeat_mode,
                    'index': self.cursor, 'generation': getattr(self, 'play_generation', 0)})
            if not self.matches_native(p):
                if self.phase == 'loading' or str(p.get('id', '')) in getattr(self, 'owned_sessions', ()):
                    self.foreign_candidate = None
                    return
                if s.get('playing'):
                    # Quasar can briefly publish a stale/transition playerState
                    # while Alice changes tracks. Require the same external
                    # session twice before relinquishing the panel queue.
                    ident = str(p.get('id', ''))
                    if not ident:
                        return
                    candidate = getattr(self, 'foreign_candidate', None)
                    if not candidate or candidate[0] != ident:
                        self.foreign_candidate = (ident, 1)
                        return
                    if candidate[1] < 1:
                        self.foreign_candidate = (ident, candidate[1] + 1)
                        return
                    self.offset = self.position()
                    self.phase = 'paused'
                    self.active = False
                    self.media_reason = 'native-source'
                    self.data['music_source'] = 'yandex'
                    self.save()
                else:
                    self.foreign_candidate = None
                return
            self.foreign_candidate = None
            if self.phase == 'stopped':
                return
            if self.phase == 'loading':
                if s.get('playing'):
                    self.native_confirmed = True
                if not (getattr(self, 'stream_ready', False) and getattr(self, 'native_confirmed', False)):
                    return
                if not s.get('playing'):
                    return
            elif getattr(self, 'native_ended', False) and not s.get('playing'):
                return  # end is latched until the queue controller consumes it
            if self.phase == 'paused' and getattr(self, 'pause_requested', False) and not s.get('playing'):
                # Our stop acknowledgement can report progress=0. It must not
                # erase the saved seek position used by the panel's resume.
                return
            progress = p.get('progress')
            if isinstance(progress, (int, float)) and math.isfinite(progress) and progress >= 0:
                self.offset = self.stream_offset + progress
            else:
                self.offset = self.position()
            self.started = time.monotonic() - max(0, time.time() - updated)
            self.phase = 'playing' if s.get('playing') else 'paused'
            self.media_reason = 'native-playing' if s.get('playing') else 'native-paused'
            t = self.data['tracks'].get(self.current) or self.remote.get(self.current) or {}
            duration = t.get('duration', 0)
            reported_end = duration > 0 and self.offset >= duration - .3
            reset_at_end = (duration > 0 and before >= duration - .75
                            and getattr(self, 'source_complete_token', None) == getattr(self, 'play_token', ''))
            self.native_ended = (getattr(self, 'native_ended', False) or
                                (previous_phase == 'playing' and not s.get('playing')
                                 and not getattr(self, 'pause_requested', False) and (reported_end or reset_at_end)))
            if previous_phase == 'playing' and not s.get('playing') and not getattr(self, 'pause_requested', False):
                self.end_candidate = (getattr(self, 'play_token', None), before)
            if self.native_ended:
                self.offset = duration
                self.media_reason = 'native-ended'

    def kill(self):
        if self.native_audio:
            self.panel.glagol.send({'command': 'stop'})
        self.panel.adb.shell("p=/tmp/lane_music.pid; if [ -s $p ]; then n=$(cat $p); "
                             "if grep -q -- --gst-launch /proc/$n/cmdline 2>/dev/null; then "
                             "kill -CONT $n 2>/dev/null; kill -INT $n 2>/dev/null; "
                             "i=0; while kill -0 $n 2>/dev/null && [ $i -lt 20 ]; do sleep 0.05; i=$((i+1)); done; "
                             "if grep -q -- --gst-launch /proc/$n/cmdline 2>/dev/null; then kill $n 2>/dev/null; fi; "
                             "fi; rm -f $p; fi; true")

    def play(self, ident, offset=0, queue=None, _order=None, _guard=None):
        with self.control:
            with self.lock:
                t = self.data['tracks'].get(ident) or self.remote.get(ident)
                if not t:
                    raise ValueError('Трек не найден')
                t = dict(t)
                position = float(offset)
                if not math.isfinite(position):
                    raise ValueError('Некорректная позиция трека')
                candidate = _order or self.order.copy()
                if queue is not None or ident not in candidate.base:
                    valid = set(self.data['tracks']) | set(self.remote)
                    ids = [i for i in unique(queue if queue is not None else [ident]) if i in valid]
                    if ident not in ids:
                        ids = [ident]
                    old = candidate
                    candidate = PlaybackOrder(ids, ident, shuffler=old.shuffler)
                    candidate.repeat = old.repeat
                    candidate.set_shuffle(old.shuffle)
                    candidate.revision = old.revision + 1
                else:
                    candidate.select(ident)
            if not getattr(self.panel, 'glagol', None) or not self.panel.glagol.ws:
                raise ValueError('Нет подключения к штатному плееру. Дождитесь соединения с Яндексом в панели.')
            source, proxied = self.resolve(t)
            # Voice resolution can outlive its request (especially SoundCloud).
            # Do not stop native audio or commit an obsolete source afterwards.
            if _guard is not None and not _guard():
                return self.state()
            glagol = getattr(self.panel, 'glagol', None)
            if glagol and glagol.ws and (glagol.state or {}).get('playing'):
                glagol.send({'command': 'stop'})
            self.kill()
            with self.lock:
                self.stream_source = (source, proxied)
                # A separate token for each playback prevents stale streams using a new source.
                self.play_token = secrets.token_urlsafe(24)
                self.native_session_id = 'panel-' + secrets.token_hex(12)
                if not hasattr(self, 'owned_sessions'):
                    self.owned_sessions = deque(maxlen=16)
                self.owned_sessions.append(self.native_session_id)
                self.native_confirmed = False
                self.stream_ready = False
                self.play_generation = getattr(self, 'play_generation', 0) + 1
                self.source_complete_token = None
                self.end_candidate = None
                self.current = ident
                self.active = True
                self.data['music_source'] = 'library'
                self.phase = 'loading'
                self.pause_requested = False
                self.native_ended = False
                self.media_reason = 'opening-stream'
                self.requested = time.monotonic()
                self.play_requested_at = time.time()
                duration = t.get('duration', 0)
                self.offset = max(0, min(position, max(0, duration - .01))) if duration > 0 else max(0, position)
                self.stream_offset = self.offset
                self.error = None
                self.order = candidate
                self._sync_order()
                url = f'http://{self.host}:{self.port}/{self.token}/{self.play_token}.mp3'
            if not self.panel.glagol.ws:
                self.phase = 'stopped'
                raise ValueError('Нет подключения к штатному плееру. Дождитесь соединения с Яндексом в панели.')
            self.native_audio = True
            result = self.panel.glagol.send(native_audio_payload(url, t, self.native_session_id))
            if result.get('status') not in ('SUCCESS', 'timeout'):
                self.phase = 'stopped'
                raise ValueError('Штатный плеер не принял поток: ' + str(result.get('status')))
            return self.state()

    def action(self, data):
        action = data.get('action')
        with self.control:
            if action == 'play':
                return self.play(data['id'], data.get('position', 0), data.get('queue'))
            if action == 'seek' and self.current:
                if self.phase in ('paused', 'stopped'):
                    position = float(data.get('position', 0))
                    if not math.isfinite(position):
                        raise ValueError('Некорректная позиция трека')
                    with self.lock:
                        t = self.data['tracks'].get(self.current) or self.remote.get(self.current) or {}
                        duration = t.get('duration', 0)
                        self.offset = max(0, min(position, duration)) if duration > 0 else max(0, position)
                        self.phase, self.pause_requested, self.native_ended = 'paused', True, False
                    return self.state()
                return self.play(self.current, data.get('position', 0))
            if action in ('pause', 'resume'):
                # Suspending the ALSA dmix owner with SIGSTOP freezes audio for
                # other clients. Pause releases the sink; resume seeks afresh.
                if action == 'resume':
                    if self.phase == 'paused':
                        return self.play(self.current, self.offset)
                    return self.state()
                if self.phase not in ('loading', 'playing'):
                    return self.state()
                position = self.position()
                with self.lock:
                    self.pause_requested = True
                    self.native_ended = False
                self.kill()
                with self.lock:
                    if action == 'pause' and self.phase in ('loading', 'playing'):
                        self.offset = position
                        self.phase = 'paused'
                return self.state()
            if action == 'stop':
                self.kill()
                with self.lock:
                    self.offset = 0
                    self.phase = 'stopped'
                    self.native_ended = False
                return self.state()
            if action in ('next', 'previous'):
                return self.advance(previous=action == 'previous')
            if action == 'options':
                with self.lock:
                    candidate = self.order.copy()
                    mode = data.get('repeat_mode', ('All' if data['repeat'] else 'None') if 'repeat' in data else candidate.repeat)
                    if mode not in ('None', 'All', 'One'):
                        raise ValueError('Неизвестный режим повтора')
                    if mode != candidate.repeat:
                        candidate.repeat, candidate.revision = mode, candidate.revision + 1
                    if 'shuffle' in data:
                        candidate.set_shuffle(bool(data['shuffle']))
                    self.order = candidate
                    self._sync_order()
                return self.state()
            if action == 'enqueue':
                ident = data.get('id')
                with self.lock:
                    if ident not in self.data['tracks'] and ident not in self.remote:
                        raise ValueError('Трек не найден')
                    self.order.append(ident)
                    self._sync_order()
                return self.state()
            raise ValueError('Неизвестная команда')

    def advance(self, automatic=False, previous=False, _guard=None):
        with self.control:
            with self.lock:
                candidate = self.order.copy()
                ident = candidate.previous() if previous else candidate.next(automatic=automatic)
            if ident:
                return self.play(ident, _order=candidate, _guard=_guard)
            if previous:
                return self.state()
            if not automatic:
                return self.action({'action': 'stop'})
            with self.lock:
                self.offset = self.position()
                self.phase, self.native_ended = 'stopped', False
                self.media_reason = 'queue-ended'
            return self.state()

    def playback_tick(self):
        """Serialize end-of-track handling with UI control; consume one event once."""
        with self.control:
            self.reconcile_native()
            alarm=bool(getattr(getattr(self.panel,'metrics',None),'ringing',None))
            if alarm and self.active and self.phase in ('playing','loading'):
                self.action({'action':'pause'})
                self.media_reason='alarm-priority'
                return
            with self.lock:
                ended = self.active and getattr(self, 'native_ended', False)
                if ended:
                    self.native_ended = False
                if self.phase == 'loading' and time.monotonic() - self.requested > 30:
                    self.error = 'Колонка не открыла поток. Проверь доступ к локальной сети в VPN.'
                    self.phase = 'stopped'
            if ended:
                self.advance(automatic=True)

    def stream_completed(self, token, pcm, offset):
        remaining = pcm.stat().st_size / 16000
        if not 0 < remaining < 7 * 86400:
            return
        with self.lock:
            if token != self.play_token:
                return
            t = self.data['tracks'].get(self.current) or self.remote.get(self.current)
            self.source_complete_token = token
            if t and abs(t.get('duration', 0) - (offset + remaining)) > .05:
                t['duration'] = round(offset + remaining, 2)
                self.save()
            candidate = getattr(self, 'end_candidate', None)
            if (t and candidate and candidate[0] == token and self.phase == 'paused'
                    and not getattr(self, 'pause_requested', False) and candidate[1] >= t['duration'] - .75):
                self.native_ended = True
                self.offset = t['duration']
                self.media_reason = 'native-ended'

    def monitor(self):
        while True:
            time.sleep(.5)
            try:
                artwork = getattr(self, 'artwork', None)
                if artwork and time.monotonic() >= getattr(self, 'next_artwork_check', 0):
                    self.next_artwork_check = time.monotonic() + 300
                    with self.lock:
                        missing = [ident for ident, t in self.data['tracks'].items() if not t.get('cover')]
                    for ident in missing:
                        artwork.enqueue(ident)
                self.playback_tick()
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)
                    self.phase = 'stopped'

    def stream_handler(self):
        library = self
        class Stream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                with library.lock:
                    expected = '/' + library.token + '/' + getattr(library, 'play_token', '') + '.mp3'
                    if urlparse(self.path).path != expected:
                        self.send_error(404)
                        return
                    source, proxied = library.stream_source
                    offset = library.stream_offset
                    playback_token = library.play_token
                from audio_stream import command as stream_command
                pcm = library.root / ('.lights-' + playback_token + '.pcm')
                library.lights.attach(pcm, playback_token, offset)
                command = stream_command(library.panel.ffmpeg, source, proxied, library.proxy, offset, pcm)
                proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    first = proc.stdout.read(4096)
                    empty = not first
                    if first and len(first) < 4096:
                        proc.wait(timeout=3)
                        empty = not pcm.exists() or pcm.stat().st_size == 0
                    if empty:
                        self.send_error(502, 'Audio source failed')
                        with library.lock:
                            if library.play_token == playback_token:
                                library.error = 'Источник не отдал аудиоданные после открытия или перемотки'
                                library.phase = 'stopped'
                        return
                    with library.lock:
                        if library.play_token == playback_token and library.phase == 'loading':
                            library.stream_ready = True
                            if library.native_confirmed:
                                library.phase = 'playing'
                                library.started = time.monotonic()
                    self.send_response(200)
                    self.send_header('Content-Type', 'audio/mpeg')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(first)
                    while chunk := proc.stdout.read(32768):
                        with library.lock:
                            if library.play_token != playback_token or library.phase == 'stopped':
                                break
                        self.wfile.write(chunk)
                    else:
                        if proc.wait(timeout=3) == 0:
                            library.stream_completed(playback_token, pcm, offset)
                except (BrokenPipeError, ConnectionResetError, OSError, subprocess.TimeoutExpired):
                    pass
                finally:
                    # FFmpeg can have finished before the consumer closes its
                    # socket. Preserve decode duration even if write() got reset.
                    if pcm.exists():
                        try:
                            with library.lock:
                                current = library.data['tracks'].get(library.current) or library.remote.get(library.current) or {}
                                duration = current.get('duration', 0)
                            decoded = offset + pcm.stat().st_size / 16000
                            if proc.poll() == 0 or (duration > 0 and decoded >= duration - .1):
                                library.stream_completed(playback_token, pcm, offset)
                        except OSError:
                            pass
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
                    proc.stdout.close()
                    library.lights.cleanup(pcm)

            def do_HEAD(self):
                with library.lock:
                    expected = '/' + library.token + '/' + getattr(library, 'play_token', '') + '.mp3'
                if urlparse(self.path).path != expected:
                    return self.send_error(404)
                self.send_response(200)
                self.send_header('Content-Type', 'audio/mpeg')
                self.end_headers()
        return Stream


def bind_panel_music(panel, library):
    """Use the panel's existing music contract for every playback surface."""
    original = {name: getattr(panel, name) for name in
                ('music', 'music_cmd', 'music_info', 'music_queue', 'music_like',
                 'music_dislike', 'music_play', 'music_lyrics', 'status', 'set_volume')}
    library.native_music_cmd=original['music_cmd']
    volume = [0.55]

    def track(ident):
        return library.data['tracks'].get(str(ident)) or library.remote.get(str(ident))

    def info(t):
        return {'id': t['id'], 'title': t['title'], 'artists': t['artist'],
                'artistList': [], 'album': {'title': t.get('album') or ''},
                'cover': t.get('cover'), 'coverBig': t.get('cover'),
                'durationMs': t.get('duration', 0) * 1000,
                'liked': bool(t.get('favorite')), 'local': True,
                'source': t['source'], 'available': True}

    def status():
        value = original['status']()
        if value.get('vol_level') is not None:
            volume[0] = value['vol_level'] / 100
        return value

    def set_volume(data):
        lights = getattr(library, 'lights', None)
        if lights:
            lights.system_indication()
        result = original['set_volume'](data)
        if 'level' in data:
            volume[0] = max(0, min(100, round(float(data['level']) / 5) * 5)) / 100
        return result

    def music():
        if not library.active:
            return {**original['music'](), 'source': 'yandex', 'library_available': True, 'server_time': time.time()}
        state = library.state()
        if not library.active:
            return {**original['music'](), 'source': 'yandex', 'library_available': True, 'server_time': time.time()}
        t = state['track']
        player = None
        if t:
            player = {'id': t['id'], 'title': t['title'], 'subtitle': t['artist'],
                      'duration': t.get('duration', 0), 'progress': state['position'],
                      'playerType': 'library', 'type': 'Track', 'playlistType': 'Playlist',
                      'playlistDescription': 'SoundCloud' if t['source'] == 'soundcloud' else 'Мои треки',
                      'hasPrev': state.get('has_previous', state['index'] > 0 or state['repeat']),
                      'hasNext': state.get('has_next', state['index'] + 1 < len(state['queue']) or state['repeat']),
                      'extra': {'coverURI': t.get('cover'), 'album': t.get('album')},
                      'entityInfo': {'repeatMode': state['repeat_mode'], 'shuffled': state['shuffle'],
                                     'queueRevision': state.get('queue_revision', 0)}}
        now = time.time()
        g = getattr(panel, 'glagol', None)
        connected = panel.conn.online and (bool(g.ws) if g else True)
        native_volume = (g.state or {}).get('volume') if g else None
        if isinstance(native_volume, (int, float)):
            volume[0] = native_volume
        return {'auth': panel.auth.info(), 'source': 'library', 'library_available': True, 'server_time': now,
                'library': state, 'glagol': {'connected': connected, 'error': state['error'] or (g.error if g else None),
                    'updated': now, 'progress_at': now,
                    'state': {'playing': connected and state['phase'] == 'playing', 'volume': volume[0],
                              'playerState': player}}}

    original_provider = getattr(panel, 'music_provider', None)
    if original_provider:
        def music_provider(provider=None):
            # Provider preference (Yandex/Zvuk) is independent from transport
            # ownership. Changing the catalogue must not stop a local queue.
            return original_provider(provider, preserve_transport=bool(library.active))
        panel.music_provider = music_provider

    def music_cmd(data):
        command = data.get('command')
        if command == 'source':
            source = data.get('source')
            if source not in ('library', 'yandex'):
                raise ValueError('Неизвестный источник')
            if source == 'yandex' and library.state()['phase'] in ('loading', 'playing', 'paused'):
                library.action({'action': 'stop'})
            if source == 'library' and not library.active:
                glagol = getattr(panel, 'glagol', None)
                if glagol and glagol.ws and (glagol.state or {}).get('playing'):
                    glagol.send({'command': 'stop'})
            with library.lock:
                library.active = source == 'library'
                library.data['music_source'] = source
                library.save()
            return {'status': 'SUCCESS'}
        if command == 'sendText':
            voice=getattr(library,'voice',None)
            if voice and voice.direct(str(data.get('text',''))):return {'status':'SUCCESS'}
            # A weather/smart-home/text request is not a source switch. Observe
            # actual native playback before handing ownership to another source.
            return original['music_cmd'](data)
        if not library.active:
            return original['music_cmd'](data)
        state = library.state()
        if command == 'play':
            if state['phase'] in ('loading', 'playing'):
                return {'status': 'SUCCESS'}
            if state['phase'] == 'paused':
                library.action({'action': 'resume'})
            else:
                ident = library.current or next(iter(library.data['tracks']), None)
                if not ident:
                    raise ValueError('Сначала добавьте аудиофайл или выберите трек SoundCloud')
                library.play(ident)
        elif command in ('stop', 'pause'):
            if state['phase'] in ('playing', 'loading'):
                library.action({'action': 'pause'})
        elif command in ('prev', 'next'):
            library.action({'action': 'previous' if command == 'prev' else 'next'})
        elif command == 'rewind':
            library.action({'action': 'seek', 'position': data.get('position', 0)})
        elif command == 'shuffle':
            library.action({'action': 'options', 'shuffle': bool(data.get('enable')),
                            'repeat_mode': library.repeat_mode})
        elif command == 'repeat':
            library.action({'action': 'options', 'repeat_mode': data.get('mode', 'None')})
        else:
            raise ValueError('Неизвестная команда плеера')
        return {'status': 'SUCCESS'}

    def music_info(ident):
        t = track(ident)
        return info(t) if t else original['music_info'](ident)

    def music_queue():
        if not library.active:
            return original['music_queue']()
        state = library.state()
        return {'title': 'Медиатека', 'current': state['index'],
                'tracks': [info(t) for t in state['queue_tracks']]}

    def music_like(ident, on):
        t = track(ident)
        if not t:
            return original['music_like'](ident, on)
        if t.get('source')=='yandex':
            library.yandex.favorite(t['yandex_id'],bool(on))
            with library.lock:
                t['favorite']=bool(on)
                if t['id'] in library.remote:library.remote[t['id']]['favorite']=bool(on)
                if t['id'] in library.data['tracks']:
                    library.data['tracks'][t['id']]['favorite']=bool(on)
                    library.save()
            return {'liked':bool(on)}
        with library.lock:
            t = dict(t, favorite=bool(on))
            library.data['tracks'][t['id']] = t
            if t['id'] in library.remote:
                library.remote[t['id']]['favorite'] = bool(on)
            library.save()
        return {'liked': bool(on)}

    def music_dislike(ident):
        if not track(ident):
            return original['music_dislike'](ident)
        music_like(ident, False)
        library.action({'action': 'next'})
        return {'status': 'SUCCESS'}

    def music_play(ident, kind):
        if track(ident):
            library.play(ident)
            return {'status': 'SUCCESS'}
        if library.active:
            # Never pass a stale Yandex id to the cloud player while the
            # library owns transport; it can replace local audio asynchronously.
            raise ValueError('Трек не принадлежит выбранной медиатеке. Обновите список и повторите выбор.')
        if library.state()['phase'] in ('loading', 'playing', 'paused'):
            library.action({'action': 'stop'})
        library.active = False
        return original['music_play'](ident, kind)

    def music_lyrics(ident, *metadata):
        t = track(ident)
        if t:
            local = t.get('_lyrics')
            if not local and t.get('file'):
                local = library.local_lyrics(t)
            if local:
                return local
            return panel.ymusic.lyrics_by_name(t['title'], t['artist'], t.get('album'), t.get('duration', 0))
        return original['music_lyrics'](ident, *metadata)

    for name, method in (('music', music), ('music_cmd', music_cmd), ('music_info', music_info),
                         ('music_queue', music_queue), ('music_like', music_like),
                         ('music_dislike', music_dislike), ('music_play', music_play),
                         ('music_lyrics', music_lyrics), ('status', status), ('set_volume', set_volume)):
        setattr(panel, name, method)


def install(server, panel):
    library = Library(panel)
    from music_lights import MusicLights
    library.lights = MusicLights(library, server.LED_PREFIX == 'micro')
    from voice_bridge import VoiceBridge
    library.voice = VoiceBridge(library, server.LED_PREFIX == 'micro')
    bind_panel_music(panel, library)
    original_ring, original_reset = panel.ring, panel.ring_reset

    def manual_ring(data):
        library.lights.animation_action({"action": "stop"}) if library.lights.supported else None
        if library.lights.supported and library.lights.settings['enabled']:
            library.lights.configure({'enabled': False})
            library.lights.release()
        return original_ring(data)

    def manual_reset():
        library.lights.animation_action({"action": "stop"}) if library.lights.supported else None
        if library.lights.supported and library.lights.settings['enabled']:
            library.lights.configure({'enabled': False})
            library.lights.release()
        return original_reset()

    panel.ring, panel.ring_reset = manual_ring, manual_reset
    original_get, original_post = server.Handler.do_GET, server.Handler.do_POST

    def get(handler):
        path = urlparse(handler.path)
        if not path.path.startswith('/api/library'):
            return original_get(handler)
        if handler._guard(path.path):
            return
        if path.path == '/api/library':
            return handler._send(200, library.tracks())
        if path.path == '/api/library/state':
            return handler._send(200, library.state())
        if path.path == '/api/library/animations':
            return handler._send(200, library.lights.animation_status())
        if path.path == '/api/library/lights':
            return handler._send(200, library.lights.status())
        if path.path == '/api/library/voice':
            return handler._send(200, library.voice.status())
        if path.path == '/api/library/yandex':
            def yandex_collection():
                query=parse_qs(path.query)
                return library.yandex_collection(refresh=query.get('refresh')==['1'],album=query['album'][0] if query.get('album') else None)
            return handler._handle(yandex_collection)
        if path.path == '/api/library/diagnostics':
            with library.lock:
                return handler._send(200, {'native_events': list(library.native_events),
                    'order': library.order.dump(), 'media_reason': library.media_reason,
                    'source_complete': getattr(library, 'source_complete_token', None) == getattr(library, 'play_token', ''),
                    'stream_ready': library.stream_ready, 'native_confirmed': library.native_confirmed,
                    'generation': library.play_generation})
        if path.path == '/api/library/artwork/status':
            return handler._send(200,{'pending':library.artwork.pending.qsize(),'error':library.artwork.last_error})
        if path.path.startswith('/api/library/cover/'):
            ident = path.path.rsplit('/', 1)[-1]
            if not re.fullmatch('(?:[a-f0-9]{24}|(?:sc|ym)-[0-9]+)', ident):
                return handler._send(404, {'error': 'Обложка не найдена'})
            file = library.root / (ident + '.cover')
            if not file.exists():
                return handler._send(404, {'error': 'Обложка не найдена'})
            blob = file.read_bytes()
            mime = 'image/png' if blob.startswith(b'\x89PNG') else 'image/jpeg'
            return handler._send(200, blob, mime)
        return handler._send(404, {'error': 'Not found'})

    def post(handler):
        path = urlparse(handler.path).path
        if not path.startswith('/api/library'):
            return original_post(handler)
        if handler._guard(path):
            return
        if path.startswith('/api/library/cover/'):
            ident = path.rsplit('/', 1)[-1]
            if ident not in library.data['tracks'] or not re.fullmatch('[a-z0-9-]{1,40}', ident):
                return handler._send(404, {'error': 'Трек не найден'})
            if int(handler.headers.get('Content-Length', 0)) > 8 * 1024 * 1024:
                return handler._send(413, {'error': 'Обложка больше 8 МБ'})
            blob = handler._body()
            if not (blob.startswith(b'\x89PNG\r\n\x1a\n') or blob.startswith(b'\xff\xd8\xff')):
                return handler._send(400, {'error': 'Выберите PNG или JPEG'})
            with library.lock:
                (library.root / (ident + '.cover')).write_bytes(blob)
                library.data['tracks'][ident]['cover'] = '/api/library/cover/' + ident + '?v=' + str(int(time.time()))
                library.data['tracks'][ident]['cover_provider'] = 'Загружена пользователем'
                library.data['tracks'][ident].pop('cover_source', None)
                library.save()
            return handler._send(200, {'ok': True})
        if path == '/api/library/import':
            size = int(handler.headers.get('Content-Length', 0))
            if size > 150 * 1024 * 1024:
                return handler._send(413, {'error': 'Файл больше 150 МБ'})
            name = unquote(handler.headers.get('X-Filename', 'track.mp3'))
            return handler._handle(lambda: library.import_file(name, handler._body()))
        data = handler._json()
        if path == '/api/library/animations':
            return handler._handle(lambda: library.lights.animation_action(data))
        if path == '/api/library/lights':
            return handler._handle(lambda: library.lights.configure(data))
        if path == '/api/library/voice':
            return handler._handle(lambda: library.voice.configure(data))
        if path == '/api/library/remove':
            return handler._handle(lambda: library.remove_track(data.get('id'),service=data.get('service') is True))
        if path == '/api/library/restore':
            return handler._handle(lambda: library.restore_track(data.get('id')))
        if path == '/api/library/alarm-sound':
            def alarm_sound():
                from alarm_music import prepare
                return prepare(library,data.get('id'))
            return handler._handle(alarm_sound)
        if path == '/api/library/yandex/favorite':
            return handler._handle(lambda: library.yandex.favorite(data.get('id'),bool(data.get('liked')),data.get('kind','track')))
        if path == '/api/library/lyrics':
            def lyrics_upload():
                from music_metadata import lyrics_text
                text = data.get('text')
                if not isinstance(text, str) or not text.strip() or len(text.encode('utf8')) > 1024 * 1024:
                    raise ValueError('Выберите текст или LRC до 1 МБ')
                with library.lock:
                    t = library.data['tracks'].get(data.get('id'))
                    if not t:
                        raise ValueError('Сначала сохраните трек в библиотеку')
                    t['_lyrics'] = lyrics_text(text)
                    library.save()
                return {'ok': True}
            return handler._handle(lyrics_upload)
        if path == '/api/library/control':
            return handler._handle(lambda: library.action(data))
        if path == '/api/library/soundcloud':
            return handler._handle(lambda: library.soundcloud(data.get('query', ''), data.get('search', True)))
        if path == '/api/library/artwork':
            def artwork():
                ident = data.get('id')
                with library.lock:
                    if ident not in library.data['tracks']:
                        raise ValueError('Сначала сохраните трек в библиотеку')
                url = data.get('url', '')
                host = urlparse(url).hostname or ''
                if not url.startswith('https://') or not host.endswith('.sndcdn.com'):
                    raise ValueError('Нужна обложка из результатов SoundCloud')
                opener = build_opener(ProxyHandler({'https': library.proxy}))
                with opener.open(url, timeout=15) as response:
                    final_host = urlparse(response.url).hostname or ''
                    if not final_host.endswith('.sndcdn.com'):
                        raise ValueError('Неожиданный адрес обложки')
                    blob = response.read(8 * 1024 * 1024 + 1)
                if len(blob) > 8 * 1024 * 1024 or not (blob.startswith(b'\xff\xd8\xff') or blob.startswith(b'\x89PNG')):
                    raise ValueError('Не удалось прочитать JPEG/PNG обложку')
                with library.lock:
                    (library.root / (ident + '.cover')).write_bytes(blob)
                    library.data['tracks'][ident]['cover'] = '/api/library/cover/' + ident + '?v=' + str(int(time.time()))
                    library.data['tracks'][ident]['cover_source'] = data.get('source', '')
                    library.data['tracks'][ident]['cover_provider'] = 'SoundCloud'
                    library.save()
                return {'ok': True}
            return handler._handle(artwork)
        if path == '/api/library/artwork/retry':
            def retry_artwork():
                with library.lock:
                    track=library.data['tracks'].get(data.get('id'))
                    if not track:raise ValueError('Трек не найден')
                    if track.get('cover'):return {'queued':False,'reason':'already_has_cover'}
                    track['cover_checked_at']=0
                    track['cover_lookup']='pending'
                    library.save()
                return {'queued':library.artwork.enqueue(track['id'])}
            return handler._handle(retry_artwork)
        if path == '/api/library/edit':
            def edit():
                with library.lock:
                    ident = data['id']
                    t = library.data['tracks'].get(ident) or library.remote.get(ident)
                    if not t:
                        raise ValueError('Трек не найден')
                    t = dict(t)
                    t['favorite'] = bool(data.get('favorite', t.get('favorite')))
                    changed=False
                    for key in ('title','artist','album','voice_title','voice_artist'):
                        if key not in data:continue
                        value=data[key]
                        if not isinstance(value,str) or len(value)>200 or (key in ('title','artist') and not value.strip()):
                            raise ValueError('Проверь данные трека: строки до 200 символов, название и исполнитель не должны быть пустыми')
                        value=value.strip()
                        changed=changed or (key in ('title','artist','album') and value!=t.get(key,''))
                        t[key]=value
                    if changed and not t.get('cover'):
                        t['cover_checked_at']=0
                        t['cover_lookup']='pending'
                    library.data['tracks'][ident] = t
                    library.save()
                if changed and not t.get('cover'):
                    library.artwork.enqueue(ident)
                return library.public(t)
            return handler._handle(edit)
        if path == '/api/library/playlist':
            def playlist():
                with library.lock:
                    name = str(data.get('name', '')).strip()[:100]
                    if not name:
                        raise ValueError('Укажите название плейлиста')
                    library.data['playlists'][name] = [i for i in data.get('tracks', []) if i in library.data['tracks']]
                    library.save()
                    return library.tracks()
            return handler._handle(playlist)
        return handler._send(404, {'error': 'Not found'})

    server.Handler.do_GET, server.Handler.do_POST = get, post
    return library
