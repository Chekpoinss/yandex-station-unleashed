import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../static/panel-context.js',import.meta.url),'utf8');
for(const path of ['/', '/index.html','/speaker/room-a/','/speaker/room-b/index.html']){
  const calls=[],scope={window:{},document:{baseURI:'https://panel.test'+path},URL,fetch:(...args)=>calls.push(args)};
  vm.runInNewContext(source,scope);
  const context=scope.window.PanelContext,base=path.replace(/index.html$/,'');
  assert.equal(context.url('/api/status'),'https://panel.test'+base+'api/status');
  assert.equal(context.url('/static/model.js'),'https://panel.test'+base+'static/model.js');
  assert.equal(context.url('/model.stl'),'https://panel.test'+base+'model.stl');
  assert.equal(context.websocket('/api/term?cols=80'),'wss://panel.test'+base+'api/term?cols=80');
  context.fetch('/api/clock',{method:'POST'});assert.equal(calls[0][0],'https://panel.test'+base+'api/clock');
  assert.equal(context.storageKey('theme'),'theme');
  if(context.deviceId)assert.equal(context.storageKey('fs.path'),'speaker.'+context.deviceId+'.fs.path');
  else assert.equal(context.storageKey('fs.path'),'fs.path');
  for(const key of ['ov.layout','ov.tiles','ch.range','mu.queue','pl.filter','say.voice']) assert.equal(context.storageKey(key),context.deviceId?'speaker.'+context.deviceId+'.'+key:key);
}
const html=await readFile(new URL('../static/index.html',import.meta.url),'utf8');
assert(!/\bfetch\(/.test(html.replaceAll('PanelContext.fetch(','')));
assert(!/href="\/(static|ca\.crt|manifest)/.test(html));
assert(html.includes('new WebSocket(PanelContext.websocket('));
const manifest=JSON.parse(await readFile(new URL('../static/manifest.webmanifest',import.meta.url),'utf8'));
assert.equal(manifest.scope,'./');assert.equal(manifest.start_url,'./#overview');
// Exercise real service worker handlers without a browser or any network.
for(const path of ['/','/speaker/room-a/','/speaker/room-b/']){
  const handlers={};
  const sw={URL,self:{location:{href:'https://panel.test'+path+'sw.js'},addEventListener:(name,fn)=>handlers[name]=fn}};
  vm.runInNewContext(await readFile(new URL('../static/sw.js',import.meta.url),'utf8'),sw);
  for(const leaf of ['api/status','api/clock','static/models/lite2.stl','ca.crt'])handlers.fetch({request:{method:'GET',url:'https://panel.test'+path+leaf},respondWith:()=>assert.fail('must not cache '+leaf)});
}
console.log('Page context, scoped storage, WebSocket, manifest and uncached API cases passed');
