(() => {
  'use strict';
  const invoke=window.__TAURI__.tauri.invoke;
  let active=false, timer=0, generation=0, lastFit=-Infinity;
  async function refresh(current) {
    let delay=350;
    try {
      if (performance.now()-lastFit>2000) {
        lastFit=performance.now();
        await invoke('fit_wallpaper');
      }
      const state=await invoke('audio',{request:{op:'visualState'}});
      if (generation!==current) return;
      window.updateSandTable(state);
      if (state.playing) delay=35;
    } catch {delay=850;}
    if (generation===current) timer=setTimeout(()=>refresh(current),delay);
  }
  function setActive(value) {
    if (active===value) return;
    active=value;generation++;
    clearTimeout(timer);
    window.setSandTableEnabled(value);
    if (value) refresh(generation);
  }
  window.wakeWallpaper=()=>setActive(true);
  window.sleepWallpaper=()=>setActive(false);
  const initial=generation;
  invoke('desktop_active').then(value=>{if (initial===generation) setActive(value);});
})();
