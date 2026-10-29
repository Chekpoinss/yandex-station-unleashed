"""Conservative MusicBrainz/CAA artwork lookup on the PC, one bounded worker."""
import json
import queue
import re
import threading
import time
import unicodedata
import ssl
import socket
import logging
from urllib.parse import urlencode, urlparse
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import HTTPError, URLError

UA = 'YandexStationWeb-Local/0.1 (+https://github.com/dimap/yandex-station-web)'
LOG=logging.getLogger(__name__)


def normalized(value):
    value=''.join(c for c in unicodedata.normalize('NFKD',str(value)) if not unicodedata.combining(c))
    return re.sub(r'[\W_]+', ' ', value.casefold()).strip()


def artist_parts(value):
    return [s.strip() for s in re.split(r'\s*(?:[,;&]|\bfeat\.?|\bfeaturing|\bft\.?)\s*', str(value), flags=re.I) if s.strip()]


def album_base(value):
    # A remaster can identify the same album; deluxe/live/mix qualifiers remain.
    return normalized(re.sub(r'\s*[\[(](?:\d{4}\s*)?remaster(?:ed)?(?:\s*\d{4})?[\])]\s*$', '', str(value), flags=re.I))


def artist_match(expected, names):
    names=[name for name in names if name]
    return (normalized(' '.join(names))==normalized(expected) or
            sorted(map(normalized,names))==sorted(map(normalized,artist_parts(expected))))


def deezer_match(track, recording, album=None):
    album=album or recording.get('album') or {}
    names=[a.get('name','') for a in recording.get('contributors',[])]
    if not names:names=[(recording.get('artist') or album.get('artist') or {}).get('name','')]
    if normalized(track.get('title',''))!=normalized(recording.get('title','')) or not artist_match(track.get('artist',''),names):
        return False
    if not track.get('album') or album_base(track['album'])!=album_base(album.get('title','')):
        return False  # no guessing between different albums/editions without tags
    duration=recording.get('duration',0)
    return bool(duration and track.get('duration') and abs(duration-track['duration'])<=5)


class ArtworkFailure(Exception):
    def __init__(self,code,retryable=True):
        super().__init__(code)
        self.code,self.retryable=code,retryable


def matching_releases(track, recordings):
    """Require exact title/credit and close duration; album too when present."""
    title, artist = normalized(track.get('title', '')), normalized(track.get('artist', ''))
    if not title or not artist or artist in ('неизвестный исполнитель', 'unknown artist'):
        return []
    duration = track.get('duration', 0)
    if not duration:
        return []
    matches = []
    for r in recordings:
        credit = ' '.join(x.get('name') or x.get('artist', {}).get('name', '') for x in r.get('artist-credit', []))
        credits = [x.get('name') or x.get('artist', {}).get('name', '') for x in r.get('artist-credit', [])]
        same_artist = normalized(credit) == artist or sorted(map(normalized, credits)) == sorted(map(normalized, artist_parts(track['artist'])))
        if normalized(r.get('title', '')) != title or not same_artist:
            continue
        if not r.get('length') or abs(r['length'] / 1000 - duration) > min(5, max(2, duration * .02)):
            continue
        for release in r.get('releases', []):
            if track.get('album') and normalized(release.get('title', '')) != normalized(track['album']):
                continue
            ident = release.get('id', '')
            if re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', ident):
                matches.append(ident)
    unique = list(dict.fromkeys(matches))
    # With no album tag, multiple possible releases are too ambiguous.
    return unique[:3] if track.get('album') or len(unique) == 1 else []


class ArtworkWorker:
    def __init__(self, library):
        self.library = library
        self.pending = queue.Queue(maxsize=128)
        self.enqueued = set()
        self.lock = threading.Lock()
        self.last_request = 0
        self.last_error = None
        proxies = {'http': library.proxy, 'https': library.proxy} if library.proxy else {}
        self.opener = build_opener(ProxyHandler(proxies))
        threading.Thread(target=self.run, daemon=True, name='music-artwork').start()

    def enqueue(self, ident):
        with self.library.lock:
            track = dict(self.library.data['tracks'].get(ident) or {})
        if not track or track.get('cover') or track.get('source') != 'local' or not track.get('duration'):
            return False
        if normalized(track.get('artist', '')) in ('', 'неизвестный исполнитель', 'unknown artist'):
            return False
        ttl = 300 if track.get('cover_lookup') == 'unavailable' else 86400
        if time.time() - track.get('cover_checked_at', 0) < ttl:
            return False
        with self.lock:
            if ident in self.enqueued:
                return False
            try:
                self.pending.put_nowait(ident)
            except queue.Full:
                return False
            self.enqueued.add(ident)
            return True

    def fetch(self, url, limit):
        # MusicBrainz's IP limit is one request/s. This single worker waits only
        # on the PC and never blocks import, playback or the speaker.
        delay = 1.1 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        try:
            with self.opener.open(Request(url, headers={'User-Agent': UA}), timeout=12) as response:
                data = response.read(limit + 1)
        except HTTPError as error:
            code=error.code
            error.close()
            raise ArtworkFailure('http_'+str(code),code==429 or code>=500) from None
        except (URLError,OSError) as error:
            cause=getattr(error,'reason',error)
            code=('tls_error' if isinstance(cause,ssl.SSLError) else 'dns_error' if isinstance(cause,socket.gaierror)
                  else 'timeout' if isinstance(cause,(TimeoutError,socket.timeout)) else 'connection_error')
            raise ArtworkFailure(code) from None
        if len(data) > limit:
            raise ArtworkFailure('response_too_large',False)
        return data

    def _musicbrainz(self, track):
        def quoted(s):
            return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'
        query = 'recording:' + quoted(track['title']) + ' AND artist:' + quoted(artist_parts(track['artist'])[0])
        result = self.json('https://musicbrainz.org/ws/2/recording/?' +
                           urlencode({'query': query, 'fmt': 'json', 'limit': 10}))
        releases=matching_releases(track,result.get('recordings',[]))
        self.attempts.append({'provider':'MusicBrainz','result':'matched' if releases else 'no_confident_match','count':len(releases)})
        transient=None
        for ident in releases:
            try:
                blob = self.fetch('https://coverartarchive.org/release/' + ident + '/front-500', 2 * 1024 * 1024)
                if blob.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n')):
                    return blob, 'https://musicbrainz.org/release/' + ident
                self.attempts.append({'provider':'Cover Art Archive','result':'invalid_image'})
            except ArtworkFailure as error:
                self.attempts.append({'provider':'Cover Art Archive','result':error.code})
                if error.retryable:transient=error
        if transient:raise transient
        return None

    def json(self,url):
        try:
            result=json.loads(self.fetch(url,1024*1024))
        except (ValueError,UnicodeError):
            raise ArtworkFailure('invalid_response') from None
        if not isinstance(result,dict) or result.get('error'):
            raise ArtworkFailure('provider_error')
        return result

    def _deezer_image(self,album):
        ident=album.get('id')
        url=album.get('cover_big') or album.get('cover_medium')
        if not isinstance(ident,int) or ident<=0 or not isinstance(url,str):return None
        parsed=urlparse(url)
        if parsed.scheme!='https' or not (parsed.hostname or '').endswith('.dzcdn.net'):return None
        blob=self.fetch(url,2*1024*1024)
        if not blob.startswith((b'\xff\xd8\xff',b'\x89PNG\r\n\x1a\n')):
            raise ArtworkFailure('invalid_image',False)
        return blob,'https://www.deezer.com/album/'+str(ident)

    def _deezer(self,track):
        artist=artist_parts(track['artist'])[0]
        result=self.json('https://api.deezer.com/search?'+urlencode({'q':track['title']+' '+artist,'limit':25}))
        matches=0
        for candidate in result.get('data',[])[:25]:
            if normalized(candidate.get('title',''))!=normalized(track['title']):continue
            album=candidate.get('album') or {}
            if not track.get('album') or album_base(album.get('title',''))!=album_base(track['album']):continue
            if len(artist_parts(track['artist']))>1:
                ident=candidate.get('id')
                if not isinstance(ident,int):continue
                candidate=self.json('https://api.deezer.com/track/'+str(ident))
                album=candidate.get('album') or album
            if deezer_match(track,candidate,album):
                matches+=1
                found=self._deezer_image(album)
                if found:
                    self.attempts.append({'provider':'Deezer','result':'matched_track','album':album.get('title')})
                    return found
        # Full-text track search may return compilations. Verify the named album
        # and its track list rather than choosing an unrelated compilation cover.
        if track.get('album'):
            result=self.json('https://api.deezer.com/search/album?'+urlencode({'q':track['album']+' '+artist,'limit':10}))
            for album in result.get('data',[])[:10]:
                if album_base(album.get('title',''))!=album_base(track['album']):continue
                if not artist_match(track['artist'],[(album.get('artist') or {}).get('name','')]):continue
                ident=album.get('id')
                if not isinstance(ident,int):continue
                detail=self.json('https://api.deezer.com/album/'+str(ident))
                for candidate in (detail.get('tracks') or {}).get('data',[]):
                    if deezer_match(track,candidate,detail):
                        found=self._deezer_image(detail)
                        if found:
                            self.attempts.append({'provider':'Deezer','result':'matched_album_track','album':detail.get('title')})
                            return found
        self.attempts.append({'provider':'Deezer','result':'no_confident_match','count':matches})
        return None

    def lookup(self,track):
        self.attempts=[]
        incomplete=False
        for provider,lookup in (('MusicBrainz',self._musicbrainz),('Deezer',self._deezer)):
            try:
                found=lookup(track)
                if found:return found
            except ArtworkFailure as error:
                self.attempts.append({'provider':provider,'result':error.code})
                incomplete=incomplete or error.retryable
            except (KeyError,TypeError,ValueError):
                self.attempts.append({'provider':provider,'result':'invalid_response'})
                incomplete=True
        if incomplete:raise ArtworkFailure('incomplete_lookup')
        return None

    def process(self, ident):
        lib = self.library
        with lib.lock:
            track = dict(lib.data['tracks'].get(ident) or {})
        if not track or track.get('cover') or track.get('source') != 'local':
            return
        retry_after = 300 if track.get('cover_lookup') == 'unavailable' else 24 * 3600
        if time.time() - track.get('cover_checked_at', 0) < retry_after:
            return
        if normalized(track.get('artist', '')) in ('', 'неизвестный исполнитель', 'unknown artist'):
            return
        try:
            found = self.lookup(track)
            status = 'found' if found else 'not_found'
        except Exception:
            found, status = None, 'unavailable'
        with lib.lock:
            current = lib.data['tracks'].get(ident)
            # A manually selected/embedded cover always wins a concurrent lookup.
            if not current or current.get('cover'):
                return
            if any(current.get(key)!=track.get(key) for key in ('title','artist','album','duration')):
                return True  # requeue after the old lookup releases its dedupe key
            current['cover_checked_at'] = time.time()
            current['cover_lookup'] = status
            current['cover_lookup_details']=list(getattr(self,'attempts',[]))[-8:]
            if found:
                blob, source = found
                target = lib.root / (ident + '.cover')
                temporary = target.with_suffix('.cover.tmp')
                try:
                    temporary.write_bytes(blob)
                    temporary.replace(target)
                    current['cover'] = '/api/library/cover/' + ident
                    current['cover_source'] = source
                    current['cover_provider'] = 'Deezer' if urlparse(source).hostname=='www.deezer.com' else 'MusicBrainz / Cover Art Archive'
                except OSError:
                    current['cover_lookup']='unavailable'
                    current['cover_lookup_details'].append({'provider':'cache','result':'write_error'})
            lib.save()

    def run(self):
        while True:
            ident = self.pending.get()
            retry=False
            try:
                retry=self.process(ident) is True
                self.last_error=None
            except Exception as error:
                # A corrupt record or failed disk write must not kill the sole worker.
                self.last_error=type(error).__name__
                LOG.warning('Artwork cache worker failure: %s',self.last_error)
            finally:
                with self.lock:
                    self.enqueued.discard(ident)
                self.pending.task_done()
            if retry:self.enqueue(ident)
