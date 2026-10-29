const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context={};
vm.runInNewContext(fs.readFileSync(require.resolve('../static/music-timeline.js'),'utf8'),context);
const {project,paint}=context.MusicTimeline;
for(const duration of [.35,276.42,10000]){
 for(const position of [0,duration/2,duration,duration+10]){
  const frame=project({duration,progress:position},false,100,200);
  const inputs=[{},{},{}];paint(inputs,frame);
  assert.equal(frame.position,Math.min(position,duration));
  for(const input of inputs){assert.equal(Number(input.value),frame.position);assert.equal(Number(input.max),duration);assert.equal(input.step,'any');assert.equal(input.disabled,false);}
  if(position>=duration)assert.equal(frame.fraction,1);
 }
}
assert.equal(project({duration:120,progress:30},true,100,105).position,35);
assert.equal(project({duration:120,progress:30},false,100,105).position,30);
assert.equal(project({duration:120,progress:30},true,100,105,90).position,90);
assert.equal(project({duration:120,progress:30},true,110,105).position,30);
assert.equal(project({duration:120,progress:0},true,200,200).position,0); // new/repeated track
assert.equal(project({duration:Infinity,progress:NaN},false,0,0).duration,null);
const unknown=project({duration:0,progress:35},true,100,101),controls=[{},{}];paint(controls,unknown);
assert.equal(unknown.position,36);assert.equal(unknown.fraction,null);assert(controls.every(r=>r.disabled));
const preview=project({duration:276.42,progress:12},false,0,0,276.42),all=[{},{},{}];paint(all,preview);
assert(all.every(r=>Number(r.value)===Number(r.max))); // all three surfaces, one preview
const html=fs.readFileSync(require.resolve('../static/index.html'),'utf8');
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)){
 if(match[0].includes('type="importmap"'))continue;
 new vm.Script(match[1].replace(/^import .*;$/gm, ''));
}
assert(!html.includes('setTimeout(() => mu.seeking = false, 800)'));
console.log('PASS: timeline start/middle/end/pause/resume/seek/repeat/unknown duration and shared controls; inline syntax');
