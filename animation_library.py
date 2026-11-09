"""Original .led animation editor adapter for Micro's three RGB points."""
import hashlib,json,math,os,re,threading,time
from pathlib import Path
from sound_overrides import SoundOverrides

LINE=re.compile(r'(loop|gammacorrection|smooth\s+\d{1,6}|gradient(\s+[0-9a-fA-F]{1,6})+|(rotate\s+)?([0-9a-fA-F]{6}\s+){1,32}\d{1,6}|#[^\x00-\x1f]*)?')
def validate_led(text):
    if not isinstance(text,str) or not text.strip() or len(text.encode('utf8'))>65536:raise ValueError('Текст анимации: до 64 КБ')
    text=text.replace('\r','')
    if any(not LINE.fullmatch(line.strip()) for line in text.splitlines()):raise ValueError('Неверный синтаксис .led')
    if not any(re.fullmatch(r'(rotate\s+)?([0-9a-fA-F]{6}\s+)+\d+|gradient.*',line.strip()) for line in text.splitlines()):raise ValueError('В анимации нет кадров')
    return text if text.endswith('\n') else text+'\n'
def parse_led(text):
    frames=[];simple=True;loop=False
    for line in text.splitlines():
        p=line.split()
        if not p or p[0].startswith('#'):continue
        if p==['loop']:loop=True;continue
        if p==['gammacorrection']:simple=False;continue
        if len(p)>=2 and all(re.fullmatch('[a-fA-F0-9]{6}',v) for v in p[:-1]) and p[-1].isdigit():
            frames.append({'c':[[int(c[i:i+2],16) for i in (0,2,4)] for c in p[:-1]],'ms':int(p[-1])})
        else:simple=False
    return {'text':text,'frames':frames,'loop':loop,'simple':simple}

class AnimationOverrides(SoundOverrides):
    def __init__(self,panel,server,library):
        super().__init__(panel,server,folder='animation-backups',remote_folder='animation-overrides')
        self.library=library
    def paths(self,name):
        if name not in self.library.stock() or not re.fullmatch(r'[\w.-]+',name) or '..' in name:raise ValueError('Выберите встроенную анимацию')
        key=hashlib.sha256(name.encode()).hexdigest()
        return self.library.target+'/'+name,self.remote+'/'+key,key
    def _convert(self,original,body):return validate_led(body.decode('utf8')).encode()
    def status(self):return {'ring':list(self.records),'clock':[],'persistent':self.persistent()}

class AnimationLibrary:
    def __init__(self,panel,server):
        self.panel,self.server=panel,server;self.lock=threading.RLock();self.target=server.QUASAR+'/ledpatterns'
        self.root=Path(panel.data_dir)/'animations';self.root.mkdir(exist_ok=True)
        self.path=self.root/'ring.json';self.custom=json.loads(self.path.read_text('utf8')) if self.path.exists() else {}
        if not self.path.exists():
            for name,frames in (panel.library.data.get('ring_animations') or {}).items():
                safe=re.sub(r'[^\w-]','_',name)[:40]
                if safe and safe not in self.custom:
                    self.custom[safe]='loop\n'+'\n'.join(' '.join(''.join(f'{v:02x}' for v in zone) for zone in f['rgb'])+' '+str(f['ms']) for f in frames)+'\n'
        self.overrides=AnimationOverrides(panel,server,self);self.cache=(0,[])
    def stock(self):
        if time.monotonic()-self.cache[0]>10:
            out,_,_=self.panel.adb.shell(f'find {self.target} -maxdepth 1 -type f -name "*.led"',check=False)
            self.cache=(time.monotonic(),sorted({v.rsplit('/',1)[-1] for v in self.server.strip_ansi(out).replace('\r','').splitlines() if re.fullmatch(r'[\w.-]+\.led',v.rsplit('/',1)[-1])}))
        return self.cache[1]
    def patterns(self):return {'ring':self.stock()+['my/'+n+'.led' for n in self.custom],'clock':[]}
    def text(self,name,original=False):
        with self.lock:
            if name.startswith('my/'):
                key=name[3:].removesuffix('.led')
                if key not in self.custom:raise ValueError('Анимация не найдена')
                return self.custom[key]
            if name not in self.stock():raise ValueError('Анимация не найдена')
            if original:
                _,_,key=self.overrides.paths(name);p=self.overrides.root/(key+'.original')
                if p.exists():return p.read_text('utf8')
            return self.panel.adb.read_bytes(self.target+'/'+name).decode('utf8')
    def frames(self,name,original=False):return parse_led(self.text(name,original))
    def save(self,kind,name,text=None,frames=None):
        if kind!='ring':raise ValueError('У Лайт 1 нет дисплея часов')
        if not isinstance(name,str) or not re.fullmatch(r'[\w-]{1,40}',name):raise ValueError('Имя: буквы, цифры, _ и -, до 40 символов')
        text=validate_led(text)
        with self.lock:
            updated=dict(self.custom);updated[name]=text
            if len(updated)>30:raise ValueError('До 30 своих анимаций')
            tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(updated,ensure_ascii=False),encoding='utf8');os.replace(tmp,self.path);self.custom=updated
        return {'name':'my/'+name+'.led','patterns':self.patterns(),'stored_on':'pc'}
    def delete(self,kind,name):
        if kind!='ring' or not isinstance(name,str) or not name.startswith('my/'):raise ValueError('Можно удалить только свою анимацию')
        key=name[3:].removesuffix('.led')
        with self.lock:
            if key not in self.custom:raise ValueError('Анимация не найдена')
            updated=dict(self.custom);updated.pop(key);tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(updated,ensure_ascii=False),encoding='utf8');os.replace(tmp,self.path);self.custom=updated
        return {'patterns':self.patterns()}
    def play(self,name,seconds):return self.try_text(self.text(name),seconds)
    def try_text(self,text,seconds):
        seconds=float(seconds)
        if not math.isfinite(seconds) or not 1<=seconds<=600:raise ValueError('Длительность: 1–600 секунд')
        text=validate_led(text);parsed=parse_led(text)
        self.panel.library.lights.animation_action({'action':'stop'})
        if parsed['simple'] and parsed['frames'] and all(len(f['c']) in (1,3) and f['ms']>=10 for f in parsed['frames']):
            frames=[{'ms':f['ms'],'rgb':f['c']*3 if len(f['c'])==1 else f['c']} for f in parsed['frames']]
            with self.panel.library.lights.lock:
                self.panel.library.lights.animation={'name':'editor','frames':frames,'start':time.monotonic(),'loops':math.ceil(seconds*1000/sum(f['ms'] for f in frames)) if parsed['loop'] else 1,'until':time.monotonic()+seconds}
        else:
            self.panel.library.lights.start_native_animation(text,seconds)
        return {'ok':True,'requested_seconds':seconds}
