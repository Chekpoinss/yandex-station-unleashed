const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('static/index.html','utf8');
const code=html.slice(html.indexOf('function musicAction('),html.indexOf('// ---- провайдер «Сбер Звук»',html.indexOf('function musicAction(')));
async function check(){
 const sent=[],state={playing:false,playerState:{hasNext:true}},c={mu:{info:{source:'library'}},muState:()=>state,muPlayer:()=>state.playerState,muHas:()=>false,
  muOptimistic:v=>{state.playing=v;c.optimistic=v;},muCmd:(b,p)=>{sent.push(p);return Promise.resolve({status:'SUCCESS'});},matchMedia:()=>({matches:true}),loadMusic(){},muWant:null,musicProvider:'yandex',plSwapping:false,muPulse(){}};
 vm.createContext(c);vm.runInContext(code,c);
 await c.musicAction('play',null);assert.equal(c.optimistic,true);assert.equal(sent.at(-1).command,'play');assert(!sent.some(p=>p.command==='sendText'));
 await c.musicAction('play',null);assert.equal(c.optimistic,false);assert.equal(sent.at(-1).command,'stop');
 await c.musicAction('next',null,true);assert.equal(sent.at(-1).command,'next');
 const mobile=html.slice(html.indexOf('function plGesture('),html.indexOf('// ручная прокрутка текста'));
 assert(mobile.includes('return seekMusic(pos);'),'cover double-tap must use the common seek guards');
 for(const id of ['pl-library-card','pl-playlists-card','pl-music-light-level','pl-light-settings','pl-voice-settings','pl-voice-enabled','pl-voice-state'])assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1,id+' must survive once');
 console.log('Original 0.2 player: immediate play/pause, empty local source without cloud fallback, shared next/seek and preserved FFT/voice/library controls passed');
}
check().catch(e=>{console.error(e);process.exitCode=1;});
