const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('static/index.html','utf8');
async function check(){
 let calls=0,resolve;const events=[];
 const c={mu:{track:{id:'sc-1',liked:false},info:{library:{track:{id:'sc-1',favorite:false}}}},
  api:()=>{calls++;return new Promise(r=>resolve=r)},window:{dispatchEvent:e=>events.push(e)},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail}}};
 vm.createContext(c);vm.runInContext(html.slice(html.indexOf('const pendingTrackLikes'),html.indexOf('function musicAction(')),c);
 const first=c.setTrackLike('sc-1',true);assert.equal(await c.setTrackLike('sc-1',true),null);assert.equal(calls,1);resolve({liked:true});await first;
 assert.equal(c.mu.track.liked,true);assert.equal(c.mu.info.library.track.favorite,true);assert.equal(events[0].detail.liked,true);
 c.api=async()=>{throw Error('synthetic service failure')};await assert.rejects(c.setTrackLike('sc-1',false),/synthetic/);assert.equal(c.mu.track.liked,true);
 c.api=async()=>({liked:false});await c.setTrackLike('sc-1',false);assert.equal(c.mu.track.liked,false);
 c.api=async()=>({});await assert.rejects(c.setTrackLike('sc-1',true),/не подтвердил/);assert.equal(c.mu.track.liked,false);
 const notes=[];const z={mu:{info:{source:'yandex'},track:{id:'1',title:'keep metadata',liked:false}},musicProvider:'zvuk',zvukData:{track:{id:'1',liked:false}},
  muState:()=>({}),muPlayer:()=>({id:'1'}),act:async(b,fn)=>fn(),api:async()=>({liked:true}),$$:()=>[],renderZvuk(){},toast:t=>notes.push(t)};
 vm.createContext(z);vm.runInContext(html.slice(html.indexOf('function musicAction('),html.indexOf('// ---- провайдер «Сбер Звук»',html.indexOf('function musicAction('))),z);
 await z.musicAction('like',null);assert.equal(z.mu.track.title,'keep metadata');assert.equal(z.mu.track.liked,true);assert.equal(notes.length,1);
 console.log('Likes: canonical state, duplicate requests, failure/retry, malformed response and Zvuk metadata/notification passed');
}
check().catch(e=>{console.error(e);process.exitCode=1});
