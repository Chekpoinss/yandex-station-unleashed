// One projection drives elapsed time and every native progress control.
(function(root){
  function project(player,playing,anchor,now,preview=null){
    const finite=value=>typeof value==='number'&&Number.isFinite(value);
    const duration=finite(player?.duration)&&player.duration>0?player.duration:null;
    const base=finite(player?.progress)?Math.max(0,player.progress):0;
    const elapsed=playing&&finite(anchor)&&finite(now)?Math.max(0,now-anchor):0;
    const requested=finite(preview)?preview:base+elapsed;
    const position=duration===null?Math.max(0,requested):Math.max(0,Math.min(duration,requested));
    return {duration,position,fraction:duration===null?null:position/duration};
  }
  function paint(inputs,frame,blocked=false){
    for(const input of inputs){
      input.min='0';input.max=String(frame.duration??1);input.step='any';
      input.value=String(frame.duration===null?0:frame.position);
      input.disabled=blocked||frame.duration===null;
    }
  }
  root.MusicTimeline={project,paint};
})(typeof window==='undefined'?globalThis:window);
