"""Opt-in PC-side Micro voice controls. No files or services on the speaker.

One IPC reader and one serialized control worker. Unknown phrases belong to
Alice. Recognized text and request IDs are held in memory only, never logged.
"""
import atexit
import queue
import socket
import struct
import subprocess
import threading
from native_alarms import fields
from voice_events import FinalRecognition, cancel_request_frame, normalized_phrase
from voice_catalog import parse_intent, select, CatalogError
from alarm_music import AlarmMusicError


COMMANDS = {
    'пауза': {'action': 'pause'},
    'поставь на паузу': {'action': 'pause'},
    'поставь музыку на паузу': {'action': 'pause'},
    'поставь трек на паузу': {'action': 'pause'},
    'включи паузу': {'action': 'pause'},
    'пауза музыку': {'action': 'pause'},
    'останови': {'action': 'pause'},
    'останови плеер': {'action': 'pause'},
    'останови трек': {'action': 'pause'},
    'останови музыку': {'action': 'pause'},
    'выключи музыку': {'action': 'pause'},
    'выруби музыку': {'action': 'pause'},
    'отключи музыку': {'action': 'pause'},
    'поставь мой плеер на паузу': {'action': 'pause'},
    'сделай паузу': {'action': 'pause'},
    'приостанови музыку': {'action': 'pause'},
    'продолжай': {'action': 'resume'},
    'продолжи': {'action': 'resume'},
    'продолжай музыку': {'action': 'resume'},
    'продолжи музыку': {'action': 'resume'},
    'продолжи воспроизведение': {'action': 'resume'},
    'сними с паузы': {'action': 'resume'},
    'возобнови': {'action': 'resume'},
    'возобнови музыку': {'action': 'resume'},
    'запусти музыку': {'action': 'resume'},
    'включи музыку': {'action': 'resume'},
    'играй дальше': {'action': 'resume'},
    'верни музыку': {'action': 'resume'},
    'следующий трек': {'action': 'next'},
    'следующая песня': {'action': 'next'},
    'следующий трек включи': {'action': 'next'},
    'включи следующий трек': {'action': 'next'},
    'включи следующую песню': {'action': 'next'},
    'переключи трек': {'action': 'next'},
    'переключи на следующий трек': {'action': 'next'},
    'перейди к следующему треку': {'action': 'next'},
    'следующий': {'action': 'next'},
    'дальше': {'action': 'next'},
    'дальше трек': {'action': 'next'},
    'дальше по списку': {'action': 'next'},
    'следом': {'action': 'next'},
    'предыдущий трек': {'action': 'previous'},
    'предыдущая песня': {'action': 'previous'},
    'предыдущий трек включи': {'action': 'previous'},
    'включи предыдущий трек': {'action': 'previous'},
    'включи предыдущую песню': {'action': 'previous'},
    'переключи на предыдущий трек': {'action': 'previous'},
    'перейди к предыдущему треку': {'action': 'previous'},
    'верни предыдущий трек': {'action': 'previous'},
    'верни прошлый трек': {'action': 'previous'},
    'верни назад': {'action': 'previous'},
    'верни обратно': {'action': 'previous'},
    'обратно': {'action': 'previous'},
    'назад': {'action': 'previous'},
    'предыдущий': {'action': 'previous'},
    'включи перемешивание': {'action': 'options', 'shuffle': True},
    'вруби перемешивание': {'action': 'options', 'shuffle': True},
    'запусти перемешивание': {'action': 'options', 'shuffle': True},
    'включи случайный порядок': {'action': 'options', 'shuffle': True},
    'перемешай треки': {'action': 'options', 'shuffle': True},
    'перемешай музыку': {'action': 'options', 'shuffle': True},
    'выключи перемешивание': {'action': 'options', 'shuffle': False},
    'выруби перемешивание': {'action': 'options', 'shuffle': False},
    'отключи перемешивание': {'action': 'options', 'shuffle': False},
    'выключи случайный порядок': {'action': 'options', 'shuffle': False},
    'повторяй этот трек': {'action': 'options', 'repeat_mode': 'One'},
    'повторяй эту песню': {'action': 'options', 'repeat_mode': 'One'},
    'повторяй трек': {'action': 'options', 'repeat_mode': 'One'},
    'включи повтор трека': {'action': 'options', 'repeat_mode': 'One'},
    'повторяй текущий трек': {'action': 'options', 'repeat_mode': 'One'},
    'повторяй все треки': {'action': 'options', 'repeat_mode': 'All'},
    'повторяй весь плейлист': {'action': 'options', 'repeat_mode': 'All'},
    'включи повтор плейлиста': {'action': 'options', 'repeat_mode': 'All'},
    'включи повтор': {'action': 'options', 'repeat_mode': 'All'},
    'выключи повтор': {'action': 'options', 'repeat_mode': 'None'},
    'выруби повтор': {'action': 'options', 'repeat_mode': 'None'},
    'отключи повтор': {'action': 'options', 'repeat_mode': 'None'},
    'отключи повтор трека': {'action': 'options', 'repeat_mode': 'None'},
}


class VoiceBridge:
    def __init__(self, library, supported, start=True):
        self.library, self.supported = library, supported
        self.lock = threading.RLock()
        self.config_lock = threading.Lock()
        self.enabled = bool(supported and library.data.get('voice_control') is True)
        self.wake = threading.Event()
        self.done = threading.Event()
        self.jobs = queue.Queue(maxsize=1)
        self.parser = FinalRecognition()
        self.epoch, self.latest_request, self.speech_state = 0, None, None
        self.pending, self.stream, self.forward_port = None, None, None
        self.connected, self.error, self.last_action = False, None, None
        self.last_result=None
        self.result_revision=0
        self.search_result=None
        self.alarm_revision=0
        self.alarm_event=None
        self.accepted, self.executed, self.cancelled = 0, 0, 0
        if self.enabled:self.wake.set()
        if start:
            threading.Thread(target=self.reader, daemon=True, name='panel-voice-ipc').start()
            threading.Thread(target=self.worker, daemon=True, name='panel-voice-control').start()
            atexit.register(self.close)

    def status(self):
        with self.lock:
            return {'supported':self.supported, 'enabled':self.enabled,
                    'connected':self.connected, 'error':self.error,
                    'accepted':self.accepted, 'executed':self.executed,
                    'cancelled':self.cancelled, 'last_action':self.last_action,
                    'last_result':self.last_result,
                    'result_revision':self.result_revision, 'search_result':self.search_result,
                    'alarm_revision':self.alarm_revision, 'alarm_event':self.alarm_event,
                    'commands':['pause','resume','next','previous','shuffle','repeat','play_catalog','play_artist','search_catalog','search_soundcloud','set_alarm','alarm_prepare'],
                    'experimental':True}

    def configure(self, data):
        value=data.get('enabled')
        if not isinstance(value,bool) or (value and not self.supported):
            raise ValueError('Голосовое управление доступно только для проверенного профиля Лайт 1')
        with self.config_lock:
            with self.library.lock:
                previous=self.library.data.get('voice_control')
                self.library.data['voice_control']=value
                try:self.library.save()
                except Exception:
                    if previous is None:self.library.data.pop('voice_control',None)
                    else:self.library.data['voice_control']=previous
                    raise
            with self.lock:
                self.enabled=value
                self.epoch+=1
                self.pending=None
                if value:self.wake.set()
                else:
                    self.wake.clear()
                    self.disconnect()
        return self.status()

    def disconnect(self):
        stream=self.stream
        self.stream=None
        self.connected=False
        if stream:
            try:stream.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            stream.close()

    def close(self):
        self.done.set()
        self.wake.set()
        with self.lock:
            self.enabled=False
            self.epoch+=1
            self.pending=None
            self.disconnect()
            port=self.forward_port
            self.forward_port=None
        if port is not None:self.remove_forward(port)
        try:self.jobs.put_nowait(None)
        except queue.Full:pass

    def remove_forward(self, port):
        try:
            adb=self.library.panel.adb
            subprocess.run(adb._base()+['forward','--remove',f'tcp:{port}'],
                capture_output=True,timeout=5,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except Exception:pass

    def context(self):
        state=self.library.state()
        panel=getattr(self.library,'panel',None)
        local_alarm=bool(getattr(getattr(panel,'metrics',None),'ringing',None))
        with self.library.lock:
            generation=self.library.play_generation
            native=getattr(getattr(panel,'glagol',None),'state',None) or {}
        return {'active':state['active'], 'matched':state['native']['matches_track'] and not local_alarm,
                'phase':state['phase'], 'track':(state.get('track') or {}).get('id'),
                'generation':generation, 'queue_revision':state.get('queue_revision',0),
                'has_next':state.get('has_next',False), 'has_previous':state.get('has_previous',False),
                'native_id':str((native.get('playerState') or {}).get('id','')), 'protected':local_alarm}

    def catalog(self):
        if hasattr(self.library,'voice_catalog'):return self.library.voice_catalog()
        with self.library.lock:return [self.library.public(t) for t in self.library.data.get('tracks',{}).values()]

    def command(self, phrase):
        fixed=COMMANDS.get(phrase)
        if fixed:return dict(fixed)
        command=parse_intent(phrase)
        catalog=None
        if not command:
            prefix=next((v for v in ('включи ','запусти ','поставь ','проиграй ','воспроизведи ')
                         if phrase.startswith(v)),None)
            query=phrase[len(prefix):].strip() if prefix else ''
            # Explicit music prefixes still allow songs named "Свет". The
            # generic form yields to common smart-home commands instead.
            if prefix and query.split(' ',1)[0] in {'свет','лампу','лампочку','люстру','розетку','телевизор','кондиционер','вентилятор','сценарий','будильник','таймер'}:
                return None
            if prefix and query:
                catalog=self.catalog()
                for action in ('play_catalog','play_artist'):
                    candidate={'action':action,'query':query}
                    try:select(candidate,catalog)
                    except CatalogError:continue
                    command=candidate
                    break
        # Keep explicit local-play intents even if the catalog has no match:
        # dispatch reports the exact catalog error instead of letting Alice
        # silently start a cloud track with an unrelated identity.
        return command

    def valid(self, job):
        with self.lock:
            current=(self.enabled and self.epoch==job['epoch'] and
                     (job.get('direct') or (self.connected and self.latest_request==job['request'] and self.speech_state==0)))
        if not current:return False
        state=self.context()
        # The panel owns its queue across Quasar's transient playerState changes.
        # A stale native id must not silently send the command back to Alice.
        ownership=(not state['protected'] and state['native_id']==job['native_id']) if job.get('claim_source') else (state['active'] and bool(state['track']))
        return (ownership and state['track']==job['track']
                and state['generation']==job['generation']
                and state['queue_revision']==job['queue_revision'])

    def expire_pending(self, job):
        """Report a recognized command when Alice never sends its IDLE event."""
        with self.lock:
            if self.pending is not job:
                return
            self.pending = None
            self.cancelled += 1
            self.error = 'Алиса не завершила голосовой запрос. Повтори команду.'

    def receive(self, message, send):
        envelope=fields(message)
        if envelope.get(253)!=[3]:return
        state=fields(envelope.get(252,[b''])[0])
        request=state.get(7,[None])[0]
        if isinstance(request,bytes):request=request.decode('utf8',errors='strict')
        status=state.get(1,[None])[0]
        event=self.parser.read(message)
        command=self.command(event['normalized']) if event else None
        scenes=getattr(getattr(self.library,'panel',None),'scenes',None)
        scene=scenes.match_voice(event['normalized']) if event and scenes and hasattr(scenes,'match_voice') else None
        if scene: command={'action':'scene','id':scene}
        with self.lock:
            if not self.enabled:return
            self.speech_state=status
            if request and request!=self.latest_request:
                self.latest_request=request
                self.epoch+=1
                self.pending=None
            if event and command:
                context=self.context()
                owned=context['active'] and context['track']
                claim=command['action'] in ('play_catalog','play_artist','search_catalog','search_soundcloud','scene')
                if not context['protected'] and (owned or claim):
                    self.pending={**context,'request':event['request_id'],
                                  'epoch':self.epoch,'command':dict(command),'claim_source':bool(claim and not owned)}
                    pending=self.pending
                    timer=threading.Timer(3.0,self.expire_pending,args=(pending,))
                    timer.daemon=True
                    timer.start()
                    # Unlike stopConversation(), Micro cancelDialog has the
                    # silent/request-ID arguments. Always gate current request
                    # locally too; never clear Alice's request queue globally.
                    if command['action']!='pause':
                        send(cancel_request_frame(event['request_id']))
                    self.accepted+=1
            if status==0 and self.pending:
                job=self.pending
                self.pending=None
                try:self.jobs.put_nowait(job)
                except queue.Full:
                    try:self.jobs.get_nowait();self.jobs.task_done()
                    except queue.Empty:pass
                    self.jobs.put_nowait(job)

    def dispatch(self, job):
        lib=self.library
        action=job['command']['action']
        if action=='scene':
            if self.valid(job):self.library.panel.scenes.start_run(job['command']['id'])
            return
        if action in ('alarm_prepare','set_alarm'):
            if not self.valid(job):
                with self.lock:self.cancelled+=1
                return
            from alarm_music import prepare,create
            guard=lambda:self.valid(job)
            try:
                event=(create(lib,job['track'],job['command'],guard) if action=='set_alarm'
                       else {**prepare(lib,job['track'],guard),'created':False})
            except AlarmMusicError:
                if not guard():
                    with self.lock:self.cancelled+=1
                    return
                raise
            if action=='alarm_prepare' and not guard():
                with self.lock:self.cancelled+=1
                return
            with self.lock:
                self.alarm_event=event
                self.alarm_revision+=1
                self.executed+=1
                self.last_action=action
                self.last_result='alarm_created' if event['created'] else 'alarm_prepared'
                self.error=('Будильник сохранён, но служба не подтвердила запуск' if event['created'] and not event['running'] else None)
            if event['created'] and event['running']:
                try:lib.native_music_cmd({'command':'sendText','text':'Повтори за мной: Музыкальный будильник на '+event['time']+' готов'})
                except Exception:pass  # the saved alarm remains valid if confirmation is unavailable
            return
        if action in ('search_catalog','search_soundcloud'):
            if not self.valid(job):
                with self.lock:self.cancelled+=1
                return
            if action=='search_soundcloud':
                tracks=lib.soundcloud(job['command']['query'])['tracks'][:12]
            else:
                catalog=self.catalog()
                tracks=select(job['command'],catalog)
            # Network search never holds the playback control lock. A newer
            # request or source change prevents obsolete results appearing.
            if not self.valid(job):
                with self.lock:self.cancelled+=1
                return
            with self.lock:
                self.search_result={'kind':'soundcloud' if action=='search_soundcloud' else 'library','tracks':tracks}
                self.result_revision+=1
                self.executed+=1
                self.last_action=action
                self.last_result='found' if tracks else 'not_found'
                self.error=None if tracks else 'По этому запросу ничего не найдено.'
            return
        with lib.control:
            if not self.valid(job):
                with self.lock:self.cancelled+=1
                return
            context=self.context()
            result='executed'
            aborted=[False]
            def guard():
                valid=self.valid(job)
                aborted[0]=aborted[0] or not valid
                return valid
            if action=='resume':
                if context['phase']=='loading':result='loading'
                elif context['phase']=='playing':result='already_playing'
                else:lib.play(job['track'],lib.offset if context['phase']=='paused' else 0,_guard=guard)
            elif action in ('next','previous'):
                available=context['has_previous'] if action=='previous' else context['has_next']
                if available:lib.advance(previous=action=='previous',_guard=guard)
                else:result='no_previous' if action=='previous' else 'no_next'
            elif action in ('play_catalog','play_artist'):
                catalog=self.catalog()
                try:matches=select(job['command'],catalog)
                except CatalogError as error:
                    with self.lock:self.error=str(error)
                    return
                if action=='play_artist':lib.play(matches[0]['id'],queue=[t['id'] for t in matches],_guard=guard)
                else:lib.play(matches[0]['id'],_guard=guard)
            else:lib.action(job['command'])
        with self.lock:
            if aborted[0]:
                self.cancelled+=1
                return
            self.executed+=1
            self.last_action=action
            self.last_result=result
            self.error=None

    def direct(self, text):
        with self.lock:
            if not self.enabled:return False
        command=self.command(normalized_phrase(text))
        if not command:return False
        context=self.context()
        claim=command['action'] in ('play_catalog','play_artist','search_catalog','search_soundcloud')
        if context['protected'] or (not claim and not (context['active'] and context['track'])):return False
        with self.lock:
            self.epoch+=1
            self.pending=None
            self.accepted+=1
            job={**context,'epoch':self.epoch,'request':None,'direct':True,
                 'claim_source':bool(claim),'command':command}
        try:self.dispatch(job)
        except CatalogError as error:
            with self.lock:self.error=str(error)
        return True

    def worker(self):
        while not self.done.is_set():
            job=self.jobs.get()
            try:
                if job is None:return
                self.dispatch(job)
            except CatalogError as error:
                with self.lock:self.error=str(error)
            except AlarmMusicError as error:
                with self.lock:self.error=str(error)
            except ValueError as error:
                # Source resolvers provide actionable messages for unavailable
                # tracks (such as SoundCloud DRM). Do not hide them behind the
                # generic voice-command failure below.
                with self.lock:self.error=str(error)[:400] or 'Команда не выполнена'
            except Exception:
                with self.lock:self.error='Не удалось выполнить голосовую команду плеера'
            finally:self.jobs.task_done()

    def reader(self):
        while not self.done.is_set():
            self.wake.wait()
            if self.done.is_set():break
            port=None
            try:
                port=self.library.panel.adb.forward(9875)
                stream=socket.create_connection(('127.0.0.1',port),timeout=3)
                stream.settimeout(.5)
                with self.lock:
                    if not self.enabled:
                        stream.close()
                        continue
                    self.stream=stream
                    self.forward_port=port
                    self.connected=True
                    self.error=None
                def read(size):
                    blob=b''
                    while len(blob)<size and self.enabled and not self.done.is_set():
                        try:part=stream.recv(size-len(blob))
                        except socket.timeout:continue
                        if not part:raise ConnectionError('IPC closed')
                        blob+=part
                    if len(blob)!=size:raise ConnectionError('IPC stopped')
                    return blob
                while self.enabled and not self.done.is_set():
                    size=struct.unpack('<I',read(4))[0]
                    if not 0<size<=256*1024:raise ValueError('Invalid IPC size')
                    self.receive(read(size),stream.sendall)
            except Exception:
                with self.lock:
                    if self.enabled:self.error='Нет соединения с голосовым каналом колонки'
            finally:
                with self.lock:
                    self.disconnect()
                    self.epoch+=1
                    self.pending=None
                    self.forward_port=None
                if port is not None:
                    self.remove_forward(port)
            if self.enabled:self.done.wait(2)
