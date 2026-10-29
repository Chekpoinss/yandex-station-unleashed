"""Prepare a self-contained alarm WAV; no scheduler or boot changes here."""
from pathlib import Path
import hashlib
import shlex
import subprocess
import tempfile
import sys
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import secrets

_packages=str(Path(__file__).parent/'runtime'/'packages')
if _packages not in sys.path:sys.path.insert(0,_packages)


class AlarmMusicError(ValueError):
    pass


def ensure_space(panel, size):
    text,_,code=panel.adb.shell('df -Pk /data',timeout=5,check=False)
    try:available=int(text.strip().splitlines()[-1].split()[3])*1024
    except (IndexError,ValueError):raise AlarmMusicError('Не удалось проверить свободное место на колонке') from None
    if code or available<size+16*1024*1024:
        raise AlarmMusicError('На колонке недостаточно места для копии песни и запаса для системы')


def prepare(library, ident, guard=None):
    panel=library.panel
    state=panel.alarms()
    if not state.get('installed'):raise AlarmMusicError('Сначала установи поддержку будильника в разделе «Будильник»')
    if not panel.ffmpeg:raise AlarmMusicError('Для копии песни нужен FFmpeg на этом компьютере')
    root=state.get('dir')
    if root!='/data/quasar/webpanel/alarm':raise AlarmMusicError('Неизвестный каталог будильника')
    with library.lock:
        track=library.data['tracks'].get(ident) or library.remote.get(ident)
        if not track:raise AlarmMusicError('Трек не найден')
        track=dict(track)
    if guard and not guard():raise AlarmMusicError('Подготовка будильника отменена новым действием')
    source,proxied=library.resolve(track)
    with panel.alarm_media_lock, tempfile.TemporaryDirectory() as directory:
        file=Path(directory)/'alarm.wav'
        args=[panel.ffmpeg,'-hide_banner','-loglevel','error','-nostdin']
        if proxied:
            args+=['-rw_timeout','15000000']
            if library.proxy:args+=['-http_proxy',library.proxy]
        args+=['-i',source,'-vn','-t','300','-ac','2','-ar','48000','-c:a','pcm_s16le','-y',str(file)]
        result=subprocess.run(args,capture_output=True,timeout=120,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or not file.exists() or file.stat().st_size<=44:
            raise AlarmMusicError('Не удалось подготовить звук будильника из этой песни')
        size=file.stat().st_size
        if size>58*1024*1024:raise AlarmMusicError('Копия песни превышает допустимый размер')
        digest=hashlib.sha256()
        with file.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        checksum=digest.hexdigest()
        name='u_'+checksum[:10]+'.wav'
        target=root+'/'+name
        old,_,_=panel.adb.shell('sha256sum '+shlex.quote(target)+' 2>/dev/null',timeout=10,check=False)
        cached=old.split()[:1]==[checksum]
        if guard and not guard():raise AlarmMusicError('Подготовка будильника отменена новым действием')
        if not cached:
            ensure_space(panel,size)
            temporary=target+'.new'
            try:
                panel.adb.push(file,temporary)
                if guard and not guard():raise AlarmMusicError('Подготовка будильника отменена новым действием')
                panel.adb.shell('mv '+shlex.quote(temporary)+' '+shlex.quote(target)+' && chmod 644 '+shlex.quote(target))
            finally:
                panel.adb.shell('rm -f '+shlex.quote(temporary),check=False)
        return {'sound':name,'sound_name':(track['title']+' — '+track['artist'])[:60],
                'size_bytes':size,'duration_limit':300,'cached':cached}


def target_time(snapshot, command):
    epoch=(snapshot.get('device_time') or {}).get('epoch')
    if not isinstance(epoch,(int,float)) or epoch<1577836800:
        raise AlarmMusicError('Проверь время на колонке перед установкой будильника')
    zone=ZoneInfo(snapshot.get('tz') or snapshot.get('pc_tz') or 'Europe/Moscow')
    now=datetime.fromtimestamp(epoch,zone)
    if 'delay_minutes' in command:
        minutes=command['delay_minutes']
        if not isinstance(minutes,int) or not 1<=minutes<=180:raise AlarmMusicError('Некорректное время будильника')
        target=now+timedelta(minutes=minutes)
        if target.second or target.microsecond:target+=timedelta(minutes=1)
        return target.replace(second=0,microsecond=0)
    try:hour,minute=map(int,command['time'].split(':'))
    except (KeyError,ValueError,AttributeError):raise AlarmMusicError('Некорректное время будильника') from None
    target=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if target<=now:target+=timedelta(days=1)
    return target


def native_overlap(items, target):
    for item in items:
        if item.get('enabled') is False:continue
        try:local=target.astimezone(ZoneInfo(item['timezone']))
        except Exception:raise AlarmMusicError('Не удалось проверить часовой пояс штатного будильника') from None
        if item.get('time')!=local.strftime('%H:%M'):continue
        if item.get('date')==local.date().isoformat() or (item.get('schedule')=='weekly' and local.isoweekday() in item.get('days',[])):
            return True
    return False


def create(library, ident, command, guard):
    panel=library.panel
    first=panel.alarms()
    if len(first.get('alarms',[]))>=30:raise AlarmMusicError('В панели уже 30 будильников')
    target=target_time(first,command)
    sound=prepare(library,ident,guard)
    if not guard():raise AlarmMusicError('Установка будильника отменена новым действием')
    current=panel.alarms()
    if (current.get('device_time') or {}).get('epoch',0)>=target.timestamp():
        raise AlarmMusicError('Пока готовилась песня, выбранное время прошло. Выбери более позднее время.')
    for item in current.get('alarms',[]):
        if (item.get('on') and item.get('time')==target.strftime('%H:%M')
                and (not item.get('days') or str(target.isoweekday()) in str(item['days']))):
            raise AlarmMusicError('На это время уже есть будильник панели. Второй будильник не создан.')
    from native_alarms import decode
    try:native=decode(panel.adb.read_bytes('/data/quasar/alarms.dat',timeout=10))
    except Exception:raise AlarmMusicError('Не удалось проверить штатные будильники. Новый будильник не сохранён.') from None
    if native['unrecognized']:raise AlarmMusicError('Не удалось проверить все штатные будильники. Поставь музыкальный будильник в панели.')
    if native_overlap(native['items'],target):
        raise AlarmMusicError('На это время уже есть будильник Алисы. Второй будильник не создан.')
    ident=secrets.token_hex(4)
    old=[]
    for item in current.get('alarms',[]):
        item=dict(item)
        if item.get('sound','').startswith('s_'):item['sound']='sys:'+item['sound_name']
        old.append(item)
    alarm={'id':ident,'on':True,'time':target.strftime('%H:%M'),'days':'','mode':'sound',
        'query':'','sound':sound['sound'],'sound_name':sound['sound_name'],'volume':5,'ramp':0,
        'duration':15,'snooze':9,'label':sound['sound_name'][:40]}
    saved=panel.alarms_save({'alarms':old+[alarm],'revision':current.get('revision'),'_guard':guard})
    if not any(a.get('id')==ident for a in saved.get('alarms',[])):
        raise AlarmMusicError('Не удалось подтвердить сохранение будильника')
    return {**alarm,'created':True,'running':bool(saved.get('running'))}
