// Page-owned URL/storage context; gateway never rewrites HTML or JavaScript.
(() => {
  const base=new URL('./',document.baseURI);
  const deviceId=base.pathname.match(/^\/speaker\/([a-z0-9_-]+)\/$/)?.[1]||'';
  const url=path=>new URL(String(path).replace(/^\//,''),base).href;
  window.PanelContext=Object.freeze({
    basePath:base.pathname,deviceId,url,
    fetch:(path,options)=>fetch(url(path),options),
    websocket:path=>{const value=new URL(url(path));value.protocol=value.protocol==='https:'?'wss:':'ws:';return value.href;},
    storageKey:key=>deviceId&&/^(fs|snd|ov|ch|al|pl|mu|music|say|sc)\./.test(key)?'speaker.'+deviceId+'.'+key:key,
  });
})();
