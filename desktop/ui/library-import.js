window.prepareAudioLibrary=async function(api) {
  'use strict';
  let state=await api('importStatus');
  if (state.phase==='ready') return;
  const get=id=>document.getElementById(id);
  const dialog=get('libraryImport'), path=get('importPath'), browse=get('importBrowse');
  const form=get('importForm'), start=get('importStart'), msg=get('importMessage'), bar=get('importProgress');
  path.value=state.game || '';
  get('importInfo').textContent='BGM 和座舱音效会从游戏导入到播放器旁的 audio 文件夹，占用约 '+
    (state.bytes/1024**3).toFixed(1)+' GiB。完成后可以离线播放。';
  dialog.oncancel=e=>e.preventDefault();dialog.showModal();
  await new Promise(resolve=>{
    function paint() {
      const running=state.phase==='running';
      path.disabled=browse.disabled=start.disabled=running;
      start.hidden=running;bar.hidden=!running;
      bar.max=state.total || 1;bar.value=state.done || 0;
      msg.textContent=state.message || '请选择游戏安装目录。';
      start.textContent=state.phase==='error'?'重新导入':'导入音频';
    }
    function fail(e) {state={...state,phase:'error',message:String(e)};paint();}
    async function follow() {
      if (state.phase==='ready') {dialog.close();resolve();return;}
      paint();
      if (state.phase==='running') setTimeout(async()=>{
        try {state=await api('importStatus');await follow();} catch(e) {fail(e);}
      },350);
    }
    async function begin() {
      if (!path.value.trim()) {path.focus();return;}
      start.disabled=browse.disabled=true;
      try {state=await api('importStart',{game:path.value.trim()});await follow();}
      catch(e) {fail(e);}
    }
    form.onsubmit=e=>{e.preventDefault();begin();};
    browse.onclick=async()=>{
      try {const picked=await api('importBrowse');if (picked) {path.value=picked;start.focus();}}
      catch(e) {fail(e);}
    };
    if (state.phase==='missing' && path.value) begin();else follow();
  });
};
