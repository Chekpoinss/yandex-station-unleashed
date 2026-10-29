"""Original upstream graph schema and editor, executed through this panel's shared player."""
import collections, copy, json, math, os, random, re, threading, time, uuid
from datetime import datetime, timedelta
from pathlib import Path
from panel_scenes import Scenes, MOSCOW

HM=re.compile(r'(?:[01]\d|2[0-3]):[0-5]\d')
KINDS={'query','say','ask','assistant','vol','sound','ring','wait','wait_until','wait_quiet','stop','scene','anim','if','repeat','note','music'}
CHECKS={'time','days','playing','online','vol_above','chance','assistant_mode','phase'}
def number(value,lo,hi):
    if type(value) not in (float,int) or not math.isfinite(value) or not lo<=value<=hi: raise ValueError(f'Число должно быть в диапазоне {lo}–{hi}')
    return value

def validate_graph(raw, identifiers):
    if not isinstance(raw,dict):raise ValueError('Неверный сценарий')
    s={k:copy.deepcopy(raw.get(k)) for k in ('id','name','trigger','time','days','on','start','phrases','nodes','execution','date')}
    if not isinstance(s['id'],str) or not re.fullmatch('[a-z0-9]{1,12}',s['id']):raise ValueError('Некорректный ID сценария')
    if not isinstance(s['name'],str) or not 1<=len(s['name'].strip())<=40:raise ValueError('Название: 1–40 символов')
    if s['trigger'] not in ('time','boot','voice','manual'):raise ValueError('Неверный запуск')
    if type(s['on']) is not bool:raise ValueError('Неверный признак включения')
    if not isinstance(s['days'],str) or not re.fullmatch('[1-7]*',s['days']):raise ValueError('Неверные дни')
    s['execution']=s['execution'] or 'pc'
    if s['execution'] not in ('pc','device'):raise ValueError('Неверное место выполнения')
    s['days']=''.join(sorted(set(s['days'])))
    s['time']=s['time'] or ''
    if s['trigger']=='time' and (not isinstance(s['time'],str) or not HM.fullmatch(s['time'])):raise ValueError('Время: ЧЧ:ММ')
    if s.get('date'):
        try:datetime.strptime(s['date'],'%Y-%m-%d')
        except (TypeError,ValueError):raise ValueError('Неверная дата') from None
    s['phrases']=s['phrases'] or []
    if not isinstance(s['phrases'],list) or len(s['phrases'])>8 or any(not isinstance(v,str) or not 1<=len(v.strip())<=60 for v in s['phrases']):raise ValueError('До 8 фраз по 60 символов')
    if s['trigger']=='voice' and not s['phrases']:raise ValueError('Укажите голосовую фразу')
    if not isinstance(s['nodes'],list) or not 1<=len(s['nodes'])<=60:raise ValueError('От 1 до 60 нод')
    nodes=[];ids=set()
    for src in s['nodes']:
        if not isinstance(src,dict) or not isinstance(src.get('id'),str) or not re.fullmatch('[0-9]{1,4}',src['id']) or src['id'] in ids:raise ValueError('Номера нод должны быть уникальны')
        ids.add(src['id']);n={k:copy.deepcopy(src.get(k)) for k in ('id','kind','next','else','x','y')}
        if n['kind'] not in KINDS:raise ValueError('Неизвестная нода')
        n['next']=n['next'] or '';n['else']=n['else'] or ''
        for axis in ('x','y'):n[axis]=number(src.get(axis,0),0,20000)
        kind=n['kind']
        if kind in ('query','say','ask','note'):
            v=src.get('text','')
            if not isinstance(v,str) or len(v)>300 or (kind!='note' and not v.strip()):raise ValueError('Текст ноды: 1–300 символов')
            n['text']=v
        elif kind=='vol':
            n['level']=number(src.get('level'),0,10);n['ramp']=0
            if src.get('ramp',0):raise ValueError('Нарастание громкости отключено владельцем')
        elif kind=='sound':
            v=src.get('sound','')
            if not isinstance(v,str) or not (re.fullmatch('u_[a-f0-9]{10}\\.wav',v) or re.fullmatch(r'sys:[\w/-]+\.[\w]+',v)) or '..' in v:raise ValueError('Выберите звук')
            n['sound']=v
        elif kind in ('ring','anim'):
            n['secs']=number(src.get('secs',5),1,600)
            if kind=='ring':
                if not re.fullmatch('#[a-fA-F0-9]{6}',src.get('color','')):raise ValueError('Неверный цвет')
                n['color']=src['color']
            else:
                v=src.get('anim','')
                if not isinstance(v,str) or not re.fullmatch(r'ring:(my/)?[\w.-]+',v) or '..' in v:raise ValueError('Выберите анимацию логотипа')
                n['anim']=v
        elif kind in ('wait','wait_quiet'):n['secs']=number(src.get('secs'),0,86400 if kind=='wait' else 7200)
        elif kind=='wait_until':
            if not isinstance(src.get('time'),str) or not HM.fullmatch(src['time']):raise ValueError('Время ожидания: ЧЧ:ММ')
            n['time']=src['time']
        elif kind=='scene':
            if src.get('target') not in identifiers or src['target']==s['id']:raise ValueError('Выберите другой сценарий')
            n['target']=src['target']
        elif kind=='assistant':
            if type(src.get('mode')) is not bool:raise ValueError('Неверный режим ассистента')
            n['mode']=src['mode']
        elif kind=='if':
            check=src.get('check');arg=src.get('arg','')
            if check not in CHECKS:raise ValueError('Неверное условие')
            if check=='time' and (not isinstance(arg,str) or len(arg.split('-'))!=2 or not all(HM.fullmatch(v) for v in arg.split('-'))):raise ValueError('Временной интервал: ЧЧ:ММ-ЧЧ:ММ')
            if check=='phase' and arg not in ('playing','paused','stopped','loading'):raise ValueError('Неверное состояние плеера')
            if check=='days' and (not isinstance(arg,str) or not re.fullmatch('[1-7]+',arg)):raise ValueError('Дни: 1–7')
            if check in ('vol_above','chance'):arg=number(float(arg),0,10 if check=='vol_above' else 100)
            n.update(check=check,arg=arg)
        elif kind=='repeat':n['count']=int(number(src.get('count'),1,20))
        elif kind=='music':
            if src.get('action') not in ('play','resume','pause','stop','next','previous','options'):raise ValueError('Неверное действие плеера')
            n['action']=src['action'];n['track']=str(src.get('track') or '')
            if n['action']=='options':
                if src.get('repeat_mode') not in ('None','One','All') or type(src.get('shuffle')) is not bool:raise ValueError('Проверьте shuffle и repeat')
                n.update(repeat_mode=src['repeat_mode'],shuffle=src['shuffle'])
            if n['action']=='play' and not re.fullmatch('[a-z0-9-]{1,64}',n['track']):raise ValueError('Укажите ID трека медиатеки')
        nodes.append(n)
    if not isinstance(s['start'],str) or s['start'] not in ids:raise ValueError('Соедините «Запуск» с первой нодой')
    for n in nodes:
        if not isinstance(n['next'],str) or not isinstance(n['else'],str):raise ValueError('Неверная связь')
        if n['next'] and n['next'] not in ids or n['else'] and n['else'] not in ids:raise ValueError('Связь указывает на отсутствующую ноду')
        if n['kind'] not in ('if','repeat') and n['else']:raise ValueError('У этой ноды только один выход')
    s['nodes']=nodes
    return s

def legacy_graph(ident,old):
    nodes=[]
    def add(kind,**values):
        n={'id':str(len(nodes)+1),'kind':kind,'next':'','else':'','x':0,'y':0,**values};nodes.append(n);return n
    def build(steps,tail=''):
        # Compile backwards so both condition branches and nested loops retain
        # the same continuation, without traversing a graph containing cycles.
        cursor=tail
        for step in reversed(steps):
            k=step['type']
            if k=='if':
                condition='phase' if step['condition']=='phase' else 'online'
                if condition=='online' and not step['value']:raise ValueError('Условие offline требует ручной миграции')
                n=add('if',check=condition,arg=step['value'] if condition=='phase' else '')
                n['next']=build(step['steps'],cursor);n['else']=build(step.get('else',[]),cursor)
            elif k=='repeat':
                n=add('repeat',count=step['count']);n['else']=cursor;n['next']=build(step['steps'],n['id'])
            else:
                fields={'wait':lambda:('wait',{'secs':step['seconds']}),'volume':lambda:('vol',{'level':step['level']/10,'ramp':0}),
                        'say':lambda:('say',{'text':step['text']}),'animation':lambda:('anim',{'anim':'ring:my/'+step['name'].replace(' ','_')+'.led','secs':5}),
                        'music':lambda:('music',{'action':step['action'],'track':step.get('id',''),**({v:step[v] for v in ('repeat_mode','shuffle')} if step['action']=='options' else {})})}
                if k not in fields:raise ValueError('Редактор старого сценария требует ручной миграции; исходник сохранён')
                kind,values=fields[k]();n=add(kind,**values);n['next']=cursor
            cursor=n['id']
        return cursor
    start=build(old['steps']);sc=old.get('schedule')
    return {'id':ident,'name':old['name'][:40],'on':bool(old['enabled']),'trigger':'time' if sc else 'manual','time':sc['time'] if sc else '',
            'days':''.join(str(v+1) for v in sc['days']) if sc else '', 'phrases':[],'date':sc.get('date') if sc else None,'execution':'pc','start':start,'nodes':nodes}

class GraphScenes(Scenes):
    def __init__(self,panel,path,journal=None,start=True):
        self.panel,self.path,self.journal=panel,Path(path),journal
        self.lock=threading.RLock();self.items={};self.fired={};self.runs={};self.revision=0
        self.storage_error=False;self.last_minute=int(time.time()//60);self.last_up=None;self.native_pending=False;self.boot_wait={};self.migration_warning=None
        self.log=collections.deque(maxlen=100)
        if self.path.exists():
            try:
                d=json.loads(self.path.read_text('utf8'));ids=set(d['items'])
                self.items={k:validate_graph(v,ids) for k,v in d['items'].items()}
                self.fired=d.get('fired',{});self.revision=d.get('revision',0);self.native_pending=bool(d.get('native_pending'))
            except (ValueError,KeyError,TypeError,AttributeError):self.storage_error=True
        legacy=self.path.parent/'.panel_scenes.json'
        if not self.path.exists() and legacy.exists():
            try:
                raw=json.loads(legacy.read_text('utf8'))
                converted={i:legacy_graph(i,v) for i,v in raw['items'].items()}
                self.items={i:validate_graph(v,set(converted)) for i,v in converted.items()}
            except (ValueError,KeyError,TypeError):
                self.storage_error=True;self.migration_warning='Старые сценарии сохранены в .panel_scenes.json; автоматическая миграция неоднозначна'
        if start:threading.Thread(target=self.scheduler,daemon=True,name='panel-graph-scenes').start()
    def persist(self):
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps({'schema':2,'items':self.items,'fired':self.fired,'revision':self.revision,'native_pending':self.native_pending},ensure_ascii=False),encoding='utf8');os.replace(tmp,self.path)
    def status(self):
        with self.lock:
            voice=getattr(getattr(self.panel.library,'voice',None),'enabled',False) is True
            return {'scenes':copy.deepcopy(list(self.items.values())),'running':[i for i,r in self.runs.items() if r['state'] in ('running','cancelling')]+list(getattr(getattr(self.panel,'native_scenes',None),'running',[])),
                    'log':list(self.log)+list(getattr(getattr(self.panel,'native_scenes',None),'log',[])),'daemon':True,'hosted_on':'pc','revision':self.revision,'storage_error':self.storage_error,'native_pending':self.native_pending,'migration_warning':self.migration_warning,
                    'capabilities':{'voice':voice,'assistant':getattr(self.panel,'assistant_ready',False) is True,'boot':True},
                    'runs':{i:{k:r[k] for k in ('state','step','error')} for i,r in self.runs.items()}}
    def save(self,data):
        raw=data.get('scenes')
        if not isinstance(raw,list) or len(raw)>20:raise ValueError('До 20 сценариев')
        raw=copy.deepcopy(raw)
        for s in raw:
            if not isinstance(s,dict):raise ValueError('Неверный сценарий')
            s['id']=s.get('id') or uuid.uuid4().hex[:12]
            if not isinstance(s['id'],str) or not re.fullmatch('[a-z0-9]{1,12}',s['id']):raise ValueError('Некорректный ID')
        ids=[s['id'] for s in raw]
        if len(set(ids))!=len(ids):raise ValueError('ID сценариев повторяются')
        items={s['id']:validate_graph(s,set(ids)) for s in raw}
        with self.lock:
            if self.storage_error:raise ValueError('Исходный файл сценариев повреждён; запись отменена')
            if data.get('revision')!=self.revision:raise ValueError('Сценарии изменились. Обновите список.')
            if any(r['state'] in ('running','cancelling') and items!=self.items for i,r in self.runs.items()):raise ValueError('Сначала остановите изменяемый сценарий')
            old,oldfired=self.items,dict(self.fired);old_pending=self.native_pending
            if getattr(getattr(self.panel,'native_scenes',None),'running',[]) and {k:v for k,v in old.items() if v.get('execution')=='device'}!={k:v for k,v in items.items() if v.get('execution')=='device'}:
                raise ValueError('Сначала остановите автономные сценарии')
            if {k:v for k,v in old.items() if v.get('execution')=='device'}!={k:v for k,v in items.items() if v.get('execution')=='device'}:self.native_pending=True
            self.items=items;self.revision+=1
            for i,s in items.items():
                if old.get(i)!=s:self.fired[i]=datetime.now(MOSCOW).strftime('%Y-%m-%d %H:%M')
            try:self.persist()
            except OSError:
                self.items,self.fired=old,oldfired;self.native_pending=old_pending;self.revision-=1;raise ValueError('Не удалось сохранить сценарии') from None
            return self.status()
    def start_run(self, ident):
        if self.items.get(ident, {}).get('execution') == 'device':
            return self.panel.native_scenes.run(ident)
        return super().start_run(ident)
    def cancel(self, ident):
        if self.items.get(ident, {}).get('execution') == 'device':
            return self.panel.native_scenes.stop(ident)
        return super().cancel(ident)
    def match_voice(self,phrase):
        key=' '.join(phrase.casefold().replace('ё','е').split());day=str(datetime.now(MOSCOW).isoweekday())
        with self.lock:
            return next((s['id'] for s in self.items.values() if s['on'] and s['trigger']=='voice' and (not s['days'] or day in s['days']) and key in [' '.join(v.casefold().replace('ё','е').split()) for v in s['phrases']]),None)
    def tick(self,now):
        minute=int(now.timestamp()//60)
        with self.lock:
            up=getattr(getattr(self.panel,'metrics',None),'prev_up',None)
            reboot=type(up) in (float,int) and type(self.last_up) in (float,int) and up+30<self.last_up
            if type(up) in (float,int):self.last_up=up
            for i,due in list(self.boot_wait.items()):
                if now.timestamp()>=due:
                    self.boot_wait.pop(i,None)
                    if self.items.get(i,{}).get('on'):
                        try:self.start_run(i)
                        except ValueError:self.log.append('Запуск после включения пропущен')
            if minute<=self.last_minute and not reboot:return
            self.last_minute=minute;key=now.strftime('%Y-%m-%d %H:%M')
            for i,s in self.items.items():
                if s.get('execution','pc')!='pc' or not s['on'] or s['trigger'] not in ('time','boot'):continue
                if s['trigger']=='boot' and not reboot:continue
                if s['trigger']=='time' and (s['time']!=now.strftime('%H:%M') or s['days'] and str(now.isoweekday()) not in s['days']):continue
                if s.get('date') and s['date']!=now.date().isoformat():continue
                if self.fired.get(i)==key:continue
                self.fired[i]=key
                if s['trigger']=='time' and (not s['days'] or s.get('date')):s['on']=False;self.revision+=1
                try:self.persist()
                except OSError:self.storage_error=True;return
                if s['trigger']=='boot':
                    self.boot_wait[i]=now.timestamp()+60
                    continue
                try:self.start_run(i)
                except ValueError:self.log.append('Пропущен запуск: исполнитель занят или колонка недоступна')
    def execute(self,ident,scene,stop):
        deadline=time.monotonic()+172800;count=[0];snap=copy.deepcopy(self.items)
        def guard():
            if stop.is_set():raise InterruptedError()
            if time.monotonic()>deadline or not self.panel.conn.online:raise ValueError('Выполнение недоступно')
        def wait(seconds):
            until=time.monotonic()+seconds
            while time.monotonic()<until:
                guard()
                if stop.wait(min(.2,max(0,until-time.monotonic()))):raise InterruptedError()
        def playing():return bool((self.panel.glagol.state or {}).get('playing')) or self.panel.library.state().get('phase')=='playing'
        def check(n):
            now=datetime.now(MOSCOW);c=n['check'];arg=n.get('arg')
            if c=='time':
                a,b=arg.split('-');v=now.strftime('%H:%M');return a<=v<b if a<=b else v>=a or v<b
            if c=='days':return str(now.isoweekday()) in arg
            if c=='phase':return self.panel.library.state().get('phase')==arg
            if c=='playing':return playing()
            if c=='online':return bool(getattr(self.panel.glagol,'ws',None))
            if c=='vol_above':return float((self.panel.glagol.state or {}).get('volume',0))*10>float(arg)
            if c=='chance':return random.randrange(100)<float(arg)
            if c=='assistant_mode':return bool(self.panel.assistant_status().get('state',{}).get('mode'))
            return False
        def walk(s,stack):
            if s['id'] in stack or len(stack)>=5:raise ValueError('Цикл между сценариями')
            nodes={n['id']:n for n in s['nodes']};cursor=s['start'];repeat={}
            while cursor:
                guard();n=nodes[cursor];kind=n['kind'];count[0]+=1
                if count[0]>1000:raise ValueError('Лимит 1000 шагов')
                with self.lock:self.runs[ident]['step']=count[0]
                nxt=n['next']
                if kind=='if':nxt=n['next'] if check(n) else n['else']
                elif kind=='repeat':
                    v=repeat.get(cursor,0)
                    if v<n['count']:repeat[cursor]=v+1
                    else:repeat[cursor]=0;nxt=n['else']
                elif kind=='wait':wait(n['secs'])
                elif kind=='wait_until':
                    now=datetime.now(MOSCOW);h,m=map(int,n['time'].split(':'));target=now.replace(hour=h,minute=m,second=0,microsecond=0)
                    if target<=now:target+=timedelta(days=1)
                    wait((target-now).total_seconds())
                elif kind=='wait_quiet':
                    end=time.monotonic()+n['secs']
                    while playing() and time.monotonic()<end:wait(.2)
                elif kind=='note':pass
                elif kind=='scene':walk(snap[n['target']],stack+[s['id']])
                else:
                    while (getattr(getattr(self.panel,'metrics',None),'ringing',None) or str((self.panel.glagol.state or {}).get('aliceState','IDLE')).upper() not in ('IDLE','NONE','STANDBY','')):wait(.2)
                    if kind=='music':
                        if n['action']=='play':self.panel.library.play(n['track'],_guard=lambda:not stop.is_set())
                        else:self.panel.library.action({k:n[k] for k in ('action','repeat_mode','shuffle') if k in n})
                    elif kind=='query':self.panel.music_cmd({'command':'sendText','text':n['text']})
                    elif kind=='say':self.panel.say(n['text'])
                    elif kind=='vol':self.panel.set_volume({'level':n['level']*10})
                    elif kind=='stop':self.panel.music_cmd({'command':'stop'})
                    elif kind=='sound':
                        if n['sound'].startswith('sys:'):self.panel.play_system(n['sound'][4:])
                        else:self.panel._play_path('/data/quasar/webpanel/alarm/'+n['sound'])
                    elif kind=='ring':self.panel.animations.try_text(' '.join([n['color'][1:]]*3)+' '+str(int(n['secs']*1000)),n['secs']);wait(n['secs'])
                    elif kind=='anim':self.panel.animations.play(n['anim'].split(':',1)[1],n['secs']);wait(n['secs'])
                    elif kind=='ask':self.panel.assistant_ask(n['text'])
                    elif kind=='assistant':self.panel.assistant_command({'mode':n['mode']})
                    guard()
                cursor=nxt
        state,error='completed',None
        try:walk(scene,[])
        except InterruptedError:state='cancelled'
        except Exception:state,error='failed','Не удалось выполнить ноду; проверьте подключение и настройки'
        with self.lock:
            self.runs[ident].update(state=state,error=error);self.log.append(f'{datetime.now(MOSCOW):%d.%m %H:%M:%S} {ident}: {state}, шагов {count[0]}')
        if self.journal:self.journal.record('/api/scenes/run',200 if state=='completed' else 409)
