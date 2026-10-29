const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('static/index.html','utf8').replace(/\r\n/g,'\n');
// Parse every inline executable block, including the newly integrated YAM UI.
let parsed=0;
for(const match of html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)){
 if(!match[2].trim()||match[1].includes('importmap'))continue;
 new vm.Script(match[2].replace(/^import .*;$/gm,''));parsed++;
}
assert(parsed>=2);
const source=html.slice(html.indexOf('const TILES ='),html.indexOf('async function refreshStatus()'));
const values={},dom={};
const c={store:{get:k=>values[k],set:(k,v)=>values[k]=v},$:(s)=>dom[s]||=( {} ),$$:()=>[],Date,Number};
vm.createContext(c);vm.runInContext(source,c);
const evalJSON=s=>JSON.parse(vm.runInContext('JSON.stringify('+s+')',c));
assert.deepEqual(evalJSON('tileSlots()'),['temp','mem','rssi','uptime']);
values['ov.tiles']='["cpu","disk","load","temp"]';assert.deepEqual(evalJSON('tileSlots()'),['cpu','disk','load','temp']);
values['ov.tiles']='["bad"]';assert.deepEqual(evalJSON('tileSlots()'),['temp','mem','rssi','uptime']);
let path=evalJSON('sparkPath([1,2,null,3,4],300,42,8)');
assert.equal((path.line.match(/M/g)||[]).length,2,'disconnected history must remain a gap');
assert(!/NaN|Infinity/.test(path.line+path.area));
path=evalJSON('sparkPath([2,2,2],300,42,8)');assert(!/NaN|Infinity/.test(path.line+path.area));
assert.equal(evalJSON('sparkPath([null,1,null],300,42)'),null);
assert(html.includes('loadAccess();\nfillTileSettings(); buildTiles(); loadSparks();\n\n// ---------- графики'));
const tools=fs.readFileSync('static/panel-tools.js','utf8');new vm.Script(tools);
const voice=fs.readFileSync('static/voice-input.js','utf8'); assert(voice.includes('bindVoiceInput')); assert(html.includes('voice-auto')); assert(html.includes('id="sc-field"'));
new vm.Script(['upstream-scenes.js','upstream-assistant.js','upstream-radio.js'].map(n=>fs.readFileSync('static/'+n,'utf8')).join('\n'));
assert(tools.includes("api('/api/extensions')"));
assert(!tools.includes('form.requestSubmit('),'browser speech must not submit automatically');
console.log('Inline syntax, overview startup/slots/gaps, microphone and backend readiness checks passed');
