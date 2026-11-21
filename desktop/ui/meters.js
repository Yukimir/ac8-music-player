'use strict';
function fitSurface(canvas) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return null;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.round(w * ratio), height = Math.round(h * ratio);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width; canvas.height = height;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio,0,0,ratio,0,0);
  ctx.clearRect(0,0,w,h);
  return {ctx,w,h};
}
function drawWave(canvas,lane,s,marks,enabled) {
  const surface = fitSurface(canvas);
  if (!surface || !s.length) return;
  const {ctx,w,h} = surface;
  const x = frame => Math.min(w, Math.max(0, frame * w / s.length));
  const start = x(s.entry), end = x(s.effectiveEnd);
  ctx.fillStyle = enabled ? '#9bdcff0e' : '#9bdcff06';
  ctx.fillRect(start,0,end-start,h);
  ctx.fillStyle = enabled ? '#baebff36' : '#9bdcff16';
  for (const [a,b] of lane.clips) ctx.fillRect(x(a),h-2,x(b)-x(a),1);
  const step = w / lane.peaks.length, mid = h/2;
  ctx.fillStyle = enabled ? '#a3dcf3' : '#597084';
  lane.peaks.forEach((level,i) => {
    if (!level) return;
    const dy = Math.max(.7, level * (mid-2) / 100);
    ctx.fillRect(i*step,mid-dy,Math.max(1,step-.6),dy*2);
  });
  ctx.fillStyle = '#abdbf954';
  for (const mark of marks || []) ctx.fillRect(x(mark[1]),0,1,4);
  ctx.fillStyle = '#98d9fb9c'; ctx.fillRect(start,0,1,h); ctx.fillRect(Math.min(w-1,end),0,1,h);
  ctx.fillStyle = '#fff'; ctx.fillRect(Math.min(w-2,x(s.position)),0,1.7,h);
}
function drawSpectrum(canvas,levels) {
  const surface = fitSurface(canvas);
  if (!surface) return;
  const {ctx,w,h} = surface;
  const count=Math.max(36,Math.min(108,Math.round(w/6)));
  const step=w/count, width=Math.max(1,step*.52);
  const maxHeight=Math.min(h-2,Math.max(11,h*.36));
  ctx.fillStyle='#f1faff';
  for (let i=0;i<count;i++) {
    const band=(levels.length-1)*i/(count-1);
    const lo=Math.max(0,Math.floor(band)), t=band-lo;
    const a=levels[lo]||0, b=levels[lo+1]??a;
    const level=a+(b-a)*t;
    const size=Math.max(1,Math.round(level*maxHeight));
    ctx.fillRect(i*step,h-1-size,width,size);
  }
}
