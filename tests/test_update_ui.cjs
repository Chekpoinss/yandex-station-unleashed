const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
const source=html.slice(html.indexOf('async function loadPanelVersion'),html.indexOf('\nloadPanelVersion().catch'));
const elements={},storage={},requests=[];
const node=id=>elements[id]||=( {style:{},hidden:true,classList:{toggle(){}},setAttribute(){}} );
const c={$:node,store:{get:k=>storage[k],set:(k,v)=>storage[k]=v},confirmAnswer:false,confirm:()=>c.confirmAnswer,
  UPD_HOW:{},toast:t=>c.toastText=t,act:async(b,fn)=>fn(),setTimeout:fn=>fn(),location:{reload:()=>{c.reloaded=true;}},
  PanelContext:{fetch:async()=>({ok:true,status:200,json:async()=>({version:'0.1.4'})})},
  response:{version:'0.1.3',latest:'0.1.4',checked:1700000000,update:true,install:'zip',multi:true},
  api:async route=>{requests.push(route);return route.includes('/update')?{ok:true,version:'0.1.4'}:c.response;}};
vm.createContext(c);
vm.runInContext('let panelVer=null;'+source,c);
(async()=>{
  // No background checks or overview notifications in the owner's local fork.
  await vm.runInContext('loadPanelVersion()',c);
  assert.equal(requests.length,0,'no automatic upstream request');
  assert.equal(node('#ov-update').hidden,true,'overview notifications disabled');
  await vm.runInContext('loadPanelVersion(true)',c);
  assert.equal(node('#ov-update').hidden,true,'manual check does not enable notifications');
  assert(node('#ov-update-text').textContent.includes('0.1.4'));
  assert.equal(node('#upd-row').hidden,false);
  assert(!requests.some(p=>p.includes('/update')),'must not update without confirmation');
  // Скрытая плашка не возвращается для той же версии
  node('#ov-update-hide').onclick();
  assert.equal(node('#ov-update').hidden,true,'dismissed banner stays hidden');
  // Отказ в подтверждении — ничего не делаем; согласие — обновляем и ждём именно новую версию
  await node('#upd-go').onclick({currentTarget:node('#upd-go')});
  assert(!requests.some(p=>p.includes('/update')),'declined confirmation');
  c.confirmAnswer=true;
  await node('#upd-go').onclick({currentTarget:node('#upd-go')});
  assert(requests.some(p=>p.includes('/api/panel/update')));assert(c.reloaded,'reloads once the new version answers');
  // Ошибка проверки видна, старый успех её не маскирует
  c.response={...c.response,error:'GitHub 503'};
  await vm.runInContext('loadPanelVersion(true)',c).catch(()=>{});
  assert(node('#upd-state').textContent.includes('GitHub 503'));
  // Кнопка «Проверить» показывает результат
  c.response={...c.response,latest:'0.1.3',update:false,error:null};
  await node('#upd-check').onclick({currentTarget:node('#upd-check')});
  assert.equal(node('#upd-check').textContent,'Проверить');assert(c.toastText.includes('последняя'));
  assert(node('#upd-state').textContent.includes('проверено'));
  console.log('Update banner, dismissal, explicit confirmation, version wait, error and refresh feedback cases passed');
})().catch(e=>{console.error(e);process.exitCode=1});
