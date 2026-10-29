"""Explicit deployment of original graph scenarios into the existing alarm daemon."""
import collections,copy,hashlib,json,re,shlex,tempfile,threading,time
from pathlib import Path

class NativeScenes:
    def __init__(self,panel,server,start=True):
        self.panel,self.server=panel,server;self.deployed=False;self.running=[];self.log=[];self.snapshot={};self.checked=0;self.seen=collections.deque(maxlen=512)
        self.receipt_path=Path(panel.data_dir)/'.native_scene_receipts.json'
        try:self.receipts=json.loads(self.receipt_path.read_text('utf8'))
        except (OSError,ValueError):self.receipts={}
        if not isinstance(self.receipts,dict):self.receipts={}
        if start:threading.Thread(target=self.bridge,daemon=True,name='native-scene-common-player').start()
    def serialize(self,items):
        by_id={s['id']:s for s in items}
        def visit(ident,stack):
            if ident in stack or len(stack)>=5:raise ValueError('Цикл или слишком глубокая вложенность автономных сценариев')
            for n in by_id[ident]['nodes']:
                if n['kind']=='scene':
                    if n['target'] not in by_id or by_id[n['target']].get('execution')!='device':raise ValueError('Связанный сценарий должен быть автономным')
                    visit(n['target'],stack+[ident])
        for s in items:
            if s.get('execution')=='device':visit(s['id'],[])
        def text(v):
            v=format(v,'g') if type(v) is float else str(v)
            if any(c in v for c in '|"\\\r\n\x00'):raise ValueError('Для автономного сценария исключите кавычки, разделители и переносы либо выберите выполнение на ПК')
            return v
        lines=[]
        for s in items:
            if s.get('execution')!='device':continue
            if s.get('date'):raise ValueError('Сценарий с точной датой выполняйте на ПК')
            lines.append('|'.join(map(text,['S',s['id'],int(s['on']),s['trigger'],','.join(s['phrases']) if s['trigger']=='voice' else s['time'],s['days'],s['start'],s['name']])))
            for n in s['nodes']:
                k=n['kind'];a=b=''
                if k in ('query','say','ask','note'):a=n['text']
                elif k=='vol':
                    if int(n['level'])!=n['level']:raise ValueError('Для автономной громкости выберите целое значение 0–10')
                    a=n['level'];b=0
                elif k=='sound':
                    # Native sounds are prepared by the existing alarm sound importer, never deleted here.
                    if n['sound'].startswith('sys:'):a='s_'+hashlib.sha1(n['sound'][4:].encode()).hexdigest()[:10]+'.wav'
                    else:a=n['sound']
                    b=n['sound']
                elif k=='ring':a=n['color'][1:];b=n['secs']
                elif k=='anim':a=n['anim'];b=n['secs']
                elif k in ('wait','wait_quiet'):a=n['secs']
                elif k=='wait_until':a=n['time']
                elif k=='scene':
                    target=next((v for v in items if v['id']==n['target']),None)
                    if not target or target.get('execution')!='device':raise ValueError('Автономная нода «Другой сценарий» должна ссылаться на автономный сценарий')
                    a=n['target']
                elif k=='assistant':a='on' if n['mode'] else 'off'
                elif k=='if':
                    if n['check']=='phase':raise ValueError('Условие состояния общего плеера требует ПК')
                    if n['check'] in ('chance','vol_above') and int(float(n['arg']))!=float(n['arg']):raise ValueError('Для автономного условия выберите целое число')
                    a=n['check'];b=n['arg']
                elif k=='repeat':a=n['count']
                elif k=='music':
                    if n['action']=='options':raise ValueError('Настройки общего плеера выполняйте на ПК')
                    a=n['action'];b=n['track']
                lines.append('|'.join(map(text,['N',s['id'],n['id'],k,a,b,n['next'],n['else'],int(n['x']),int(n['y'])])))
        return '\n'.join(lines)+'\n'
    def deploy(self):
        with self.panel.scenes.lock:
            return self._deploy_locked()
    def _deploy_locked(self):
        p,s=self.panel,self.server
        items=copy.deepcopy(list(p.scenes.items.values()));content=self.serialize(items)
        if self.running:raise ValueError('Сначала остановите автономные сценарии')
        # All validation occurs before the first device write.
        p._ensure_hook()
        state,_,_=p.adb.shell(f'cat {s.ALARM_STATE} 2>/dev/null; for f in /tmp/webpanel_scene.*.active; do [ ! -f "$f" ] || echo scene-active; done; true',check=False)
        if state.strip().startswith('ringing') or 'scene-active' in state:raise ValueError('Дождитесь остановки будильника и автономных сценариев')
        stamp=str(time.time_ns());dest=s.ALARM_DIR+'/scenes.conf';backup=s.ALARM_DIR+'/backups/'+stamp
        p.adb.shell(f'mkdir -p {backup}; for f in {s.ALARMD} {s.ALARM_CONF} {dest}; do [ ! -f "$f" ] || cp "$f" {backup}/ || exit 4; done')
        for sc in items:
            if sc.get('execution')!='device':continue
            for n in sc['nodes']:
                if n['kind']=='sound' and n['sound'].startswith('sys:'):
                    name=n['sound'][4:]
                    if name not in p.sounds():raise ValueError('Системный звук отсутствует')
                    p._alarm_wav(p.adb.read_bytes(s.SOUNDS_DIR+'/'+name),'s_'+hashlib.sha1(name.encode()).hexdigest()[:10]+'.wav')
                if n['kind']=='anim' and n['anim'].startswith('ring:my/'):
                    name=n['anim'][8:];text=p.animations.text('my/'+name)
                    with tempfile.TemporaryDirectory() as td:
                        f=Path(td)/'ring.led';f.write_text(text,encoding='utf8');p.adb.shell(f'mkdir -p {s.WEB_DIR}/anims/ring');p.adb.push(f,s.WEB_DIR+'/anims/ring/'+name)
        with tempfile.TemporaryDirectory() as td:
            conf=Path(td)/'scenes';conf.write_text(content,encoding='utf8',newline='\n')
            daemon=Path(td)/'alarmd';daemon.write_bytes(s.alarm_script())
            p.adb.push(conf,dest+'.new');p.adb.push(daemon,s.ALARMD+'.new')
            digest=hashlib.sha256(daemon.read_bytes()).hexdigest()
            out,_,_=p.adb.shell('sha256sum '+s.ALARMD+'.new')
            if out.split()[:1]!=[digest]:raise ValueError('Контрольная сумма daemon не совпала')
            out,_,_=p.adb.shell('sha256sum '+dest+'.new')
            if out.split()[:1]!=[hashlib.sha256(conf.read_bytes()).hexdigest()]:raise ValueError('Контрольная сумма сценариев не совпала')
        p.adb.shell(f'test -f {s.ALARM_CONF} || : > {s.ALARM_CONF}')
        p.adb.shell(f'exec 9>{s.ALARM_DIR}/config.lock; flock -x 9; mv {dest}.new {dest} && chmod 755 {s.ALARMD}.new && mv {s.ALARMD}.new {s.ALARMD}')
        try:
            p._alarm_daemon(restart=True)
        except Exception:
            # Restore only this operation's /data files. Never touch alarms.conf
            # or the existing system init entry during rollback.
            p.adb.shell(f'exec 9>{s.ALARM_DIR}/config.lock; flock -x 9; '
                        f'if [ -f {backup}/alarmd.sh ]; then cp {backup}/alarmd.sh {s.ALARMD}; else rm -f {s.ALARMD}; fi; '
                        f'if [ -f {backup}/scenes.conf ]; then cp {backup}/scenes.conf {dest}; else rm -f {dest}; fi; true',check=False)
            try:p._alarm_daemon(restart=True)
            except Exception:pass
            raise ValueError('Запуск обновлённого daemon не подтверждён; восстановление предыдущих файлов запрошено. Резервная копия: '+backup) from None
        self.deployed=True;self.snapshot=copy.deepcopy({v['id']:v for v in items if v.get('execution')=='device'})
        with p.scenes.lock:p.scenes.native_pending=False;p.scenes.persist()
        return {'ok':True,'backup':backup,'note':'Файлы развёрнуты; запуск и offline-проверка ещё не подтверждены','revision':p.scenes.revision}
    def run(self,ident):
        if not self.deployed:raise ValueError('Сначала разверните автономные сценарии на колонке')
        if ident not in self.panel.scenes.items:raise ValueError('Нет сценария')
        if self.panel.scenes.items[ident]!=self.snapshot.get(ident):raise ValueError('Сначала разверните изменения сценария')
        self.panel.adb.shell('printf %s '+shlex.quote(ident)+' > /tmp/webpanel_scene.fire')
        return self.panel.scenes.status()
    def stop(self,ident):
        if not isinstance(ident,str) or not re.fullmatch('[a-z0-9]{1,12}',ident):raise ValueError('Нет сценария')
        self.panel.adb.shell(f'f=/tmp/webpanel_scene.{ident}; p=$(cat "$f" 2>/dev/null); case "$p" in ""|*[!0-9]*) ;; *) '
                             f'if [ -f "$f.active" ] && tr "\\000" " " < /proc/$p/cmdline | grep -Fq -- {shlex.quote(self.server.ALARMD)}; then kill "$p" 2>/dev/null; fi;; esac; rm -f "$f.request"; true',check=False)
        return self.panel.scenes.status()
    def remember(self,key,state):
        before=dict(self.receipts);self.receipts[key]=state
        self.receipts=dict(list(self.receipts.items())[-512:]);tmp=self.receipt_path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(self.receipts),encoding='utf8');tmp.replace(self.receipt_path)
        except OSError:
            self.receipts=before
            raise

    def discover(self):
        scenes=self.panel.scenes
        if scenes.native_pending or not any(v.get('execution')=='device' for v in scenes.items.values()):return
        expected=self.serialize(list(scenes.items.values()))
        out,_,rc=self.panel.adb.shell('cat /data/quasar/webpanel/alarm/scenes.conf 2>/dev/null; echo @daemon; sha256sum /data/quasar/webpanel/alarm/alarmd.sh 2>/dev/null; true',check=False,timeout=3)
        remote,_,sha=out.replace('\r','').partition('@daemon')
        norm=lambda text: '\n'.join('|'.join(row.split('|')[:2]+['-']+row.split('|')[3:]) if row.startswith('S|') else row for row in text.strip().splitlines())
        if rc or norm(remote)!=norm(expected) or sha.strip().split()[:1]!=[hashlib.sha256(self.server.alarm_script()).hexdigest()]:return
        self.deployed=True;self.snapshot=copy.deepcopy({i:v for i,v in scenes.items.items() if v.get('execution')=='device'})
        for row in remote.splitlines():
            f=row.split('|')
            if len(f)==8 and f[0]=='S' and f[1] in self.snapshot and f[2]=='0':
                with scenes.lock:
                    scenes.items[f[1]]['on']=False;scenes.revision+=1;scenes.persist()
                    self.snapshot[f[1]]['on']=False

    def bridge(self):
        while True:
            time.sleep(.5)
            if not self.panel.conn.online:continue
            if not self.deployed:
                if time.monotonic()-self.checked<5:continue
                self.checked=time.monotonic()
                try:self.discover()
                except Exception:pass
                if not self.deployed:continue
            try:
                out,_,_=self.panel.adb.shell('for f in /tmp/webpanel_scene.*.request; do [ ! -f "$f" ] || cat "$f"; done; echo @active; for f in /tmp/webpanel_scene.*.active; do [ ! -f "$f" ] || echo "$f"; done; echo @conf; cat /data/quasar/webpanel/alarm/scenes.conf 2>/dev/null; echo @log; tail -n 30 /data/quasar/webpanel/alarm/scenes.log 2>/dev/null; true',check=False,timeout=3)
                requests,_,rest=out.partition('@active');active,_,rest=rest.partition('@conf');conf,_,log=rest.partition('@log')
                self.running=[v.split('webpanel_scene.')[-1].split('.')[0] for v in active.splitlines() if v.strip()]
                self.log=log.replace('\r','').strip().splitlines()
                for row in conf.replace('\r','').splitlines():
                    f=row.split('|')
                    if len(f)==8 and f[0]=='S' and f[2]=='0' and self.snapshot.get(f[1],{}).get('on'):
                        with self.panel.scenes.lock:
                            if self.panel.scenes.items.get(f[1])==self.snapshot[f[1]]:
                                self.panel.scenes.items[f[1]]['on']=False;self.panel.scenes.revision+=1;self.panel.scenes.persist();self.snapshot[f[1]]['on']=False
                for row in requests.replace('\r','').splitlines():
                    fields=row.split('|')
                    if len(fields)!=5:continue
                    sid,kind,a,b,nonce=fields
                    if not re.fullmatch('[a-z0-9]{1,12}',sid) or not re.fullmatch('[a-f0-9]{16}',nonce):continue
                    receipt=sid+':'+nonce;file='/tmp/webpanel_scene.'+sid+'.request'
                    receipt_key=hashlib.sha256(receipt.encode()).hexdigest()
                    if receipt_key in self.receipts:
                        result=0 if self.receipts[receipt_key]=='done' else 1
                        self.panel.adb.shell(f"printf '%s' {result} > {file}.result; rm -f {file}",check=False)
                        continue
                    scene=self.panel.scenes.items.get(sid)
                    if not scene or scene.get('execution')!='device':continue
                    if not any((n['kind']=='query' and kind=='query' and n['text']==a) or (n['kind']=='stop' and kind=='music' and a=='stop' and not b) or (n['kind']=='music' and kind=='music' and n['action']==a and n['track']==b) for n in scene['nodes']):continue
                    self.remember(receipt_key,'pending');ok=False
                    try:
                        if kind=='query':self.panel.music_cmd({'command':'sendText','text':a})
                        elif a=='play':self.panel.library.play(b)
                        else:self.panel.library.action({'action':a})
                        ok=True
                    finally:
                        self.remember(receipt_key,'done' if ok else 'failed')
                        self.panel.adb.shell(f"printf '%s' {'0' if ok else '1'} > {file}.result; rm -f {file}",check=False)
            except Exception:pass
