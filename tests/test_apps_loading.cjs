const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('static/index.html','utf8');
const source=html.slice(html.indexOf('async function loadYam()'),html.indexOf('const yamCall ='));
(async()=>{
 const dom={},yam={registry:[],plugins:[]};let paints=0,releaseRegistry;
 const registry=new Promise(resolve=>releaseRegistry=resolve);
 const context={yam,$:s=>dom[s]||=( {} ),esc:s=>s,toast:()=>{},loadYamVersion:()=>{},renderYam:()=>paints++,api:url=>{
  if(url==='/api/yam/status')return Promise.resolve({installed:false,running:true});
  if(url==='/api/yam/plugins')return Promise.resolve([{manifest:{id:'existing-app'}}]);
  if(url==='/api/yam/registry')return registry;
  if(url==='/api/yam/settings')return Promise.resolve({allow_plugin_binaries:false});
  throw new Error(url);
 }};
 vm.createContext(context);vm.runInContext(source,context);
 const loading=context.loadYam();
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(dom['#yam-card'].hidden,false,'a running manager must show its packages');
 assert.equal(yam.plugins[0].manifest.id,'existing-app');
 assert.equal(paints,1,'installed packages must render while external registry is pending');
 releaseRegistry({packages:[]});await loading;
 assert.equal(paints,2);
 console.log('Running legacy YAM and slow registry regression checks passed');
})().catch(e=>{console.error(e);process.exitCode=1});
