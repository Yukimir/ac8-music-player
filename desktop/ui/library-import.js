'use strict';
window.prepareAudioLibrary=async function(api,settings=false) {
  let state=await api('importStatus');
  if (!settings && state.configured && state.phase!=='running') return;
  const get=id=>document.getElementById(id);
  const dialog=get('libraryImport'), path=get('importPath'), browse=get('importBrowse');
  const start=get('importStart'), cancel=get('importCancel'), message=get('importMessage');
  const direct=get('readDirect'), extract=get('readExtract'), native=get('extractNative');
  const bar=get('importProgress'), info=get('importInfo');
  path.value=state.game || '';
  extract.checked=Boolean(state.extract);direct.checked=!extract.checked;
  native.checked=Boolean(state.native);
  cancel.hidden=!settings;
  start.textContent=settings?'保存':'进入播放器';
  message.textContent='';
  dialog.showModal();
  return new Promise(resolve=>{
    function options() {
      const running=state.phase==='running';
      path.disabled=browse.disabled=direct.disabled=extract.disabled=start.disabled=cancel.disabled=running;
      native.disabled=running || !extract.checked;
      info.textContent=extract.checked
        ? '提取到播放器旁的 audio 文件夹。基础音频约 '+(state.bytes/1024**3).toFixed(1)+' GiB，原生 7.1.4 另需约 '+(state.nativeBytes/1024**3).toFixed(1)+' GiB。'
        : '优先播放已提取的素材，缺少的音频在播放时从游戏读取。';
      bar.hidden=!running;bar.max=state.total || 1;bar.value=state.done || 0;
    }
    function close(saved) {dialog.close();resolve(saved);}
    function fail(e) {state={...state,phase:'error'};message.textContent=String(e);options();}
    async function follow() {
      if (state.phase==='ready') {close(true);return;}
      message.textContent=state.message || '';
      options();
      if (state.phase==='running') setTimeout(async()=>{
        try {state=await api('importStatus');await follow();} catch(e) {fail(e);}
      },250);
    }
    direct.onchange=extract.onchange=options;
    cancel.onclick=()=>close(false);
    dialog.oncancel=e=>{e.preventDefault();if (settings && !start.disabled) close(false);};
    get('importForm').onsubmit=async e=>{
      e.preventDefault();if (start.disabled) return;
      start.disabled=browse.disabled=cancel.disabled=true;
      try {
        state=await api('importStart',{game:path.value.trim(),extract:extract.checked,
                                     native:extract.checked && native.checked});
        await follow();
      } catch(e) {fail(e);}
    };
    browse.onclick=async()=>{
      try {const picked=await api('importBrowse');if (picked) {path.value=picked;start.focus();}}
      catch(e) {fail(e);}
    };
    if (state.phase==='running') follow();else options();
  });
};
