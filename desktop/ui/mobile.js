(() => {
  const $ = id => document.getElementById(id);
  window.setupMobile = (api,catalog,playlist,listRows,refresh) => {
    const songs=catalog.filter(s=>s.bank==='互动配乐'||s.bank==='单段配乐');
    const dialog=$('mobilePanel'), selected=new Set(), choices=new Map();
    const selectedEntries=new Set();
    const fromPlaylist=()=>$('mobilePlaylist').checked;
    let playlistRows=[], visibleEntries=[];
    let code='', status={}, fingerprint='', timer, pollBusy=false, starting=false, sending=false;
    const message = text => {$('mobileMessage').textContent=text;};
    function enable() {
      const count=fromPlaylist()
        ? new Set(playlistRows.filter(row=>selectedEntries.has(row.id)).map(row=>row.song.id)).size
        : selected.size;
      $('mobileSend').disabled=sending || !status.connected || status.busy || !(count || $('mobileEffects').checked);
      $('mobileSend').textContent=count?'发送到手机（'+count+' 首）':'发送到手机';
      if(fromPlaylist()){
        const checked=visibleEntries.filter(row=>selectedEntries.has(row.id)).length;
        const all=$('mobileSelectAll');
        all.disabled=visibleEntries.length===0;
        all.checked=checked>0 && checked===visibleEntries.length;
        all.indeterminate=checked>0 && checked<visibleEntries.length;
      }
    }
    function check(parent,label,value,onchange) {
      const line=document.createElement('label'), input=document.createElement('input');
      input.type='checkbox'; input.checked=value;
      input.onchange=()=>onchange(input.checked);
      line.append(input,document.createTextNode(label));parent.append(line);
    }
    function drawLibrary(query,rows,existing) {
      for (const song of songs.filter(s=>!query || s.search.includes(query))) {
        const row=document.createElement('div');
        check(row,song.title+' · '+song.origin+(existing.has(song.id)?'　✓ 手机上已有':''),selected.has(song.id),on=>{
          if(on) selected.add(song.id);else selected.delete(song.id);
          draw();
        });
        if (selected.has(song.id) && song.defaultPlan) {
          const plans=document.createElement('div');plans.className='mobile-plans';
          const defaultLabel=document.createElement('small');defaultLabel.textContent='随曲传输默认编排';plans.append(defaultLabel);
          const picked=choices.get(song.id) || new Set();choices.set(song.id,picked);
          for(const p of Arrangements.plans(song).filter(p=>p.id!=='default')){
            const has=status.inventory?.plans?.[song.id]?.includes(p.id);
            check(plans,p.name+(has?'　✓ 手机上已有':''),picked.has(p.id),v=>v?picked.add(p.id):picked.delete(p.id));
          }
          row.append(plans);
        }
        rows.append(row);
      }
    }
    function drawPlaylist(query,rows,existing) {
      playlistRows=listRows();
      visibleEntries=playlistRows.filter(({song,detail})=>(song.search+' '+detail.toLocaleLowerCase()).includes(query));
      $('mobileBulkLabel').textContent=query?'全选搜索结果':'全选播放列表';
      for(const {id,song,plan,detail} of visibleEntries){
        const hasSong=existing.has(song.id);
        const hasPlan=!plan || status.inventory?.plans?.[song.id]?.includes(plan.id);
        const inventory=hasSong?(hasPlan?'　✓ 手机上已有':'　手机已有曲目，待传编排'):'';
        check(rows,song.title+' · '+detail+inventory,selectedEntries.has(id),on=>{
          if(on)selectedEntries.add(id);else selectedEntries.delete(id);
          enable();
        });
      }
      if(!visibleEntries.length)rows.append(playlistRows.length?'没有匹配的曲目或编排。':'播放列表为空，可以先在主界面添加曲目或编排。');
    }
    function draw() {
      const query=$('mobileQuery').value.trim().toLocaleLowerCase(), rows=document.createDocumentFragment();
      const existing=new Set(status.inventory?.songs || []);
      $('mobileBulk').hidden=!fromPlaylist();
      if(fromPlaylist())drawPlaylist(query,rows,existing);else drawLibrary(query,rows,existing);
      $('mobileSongs').replaceChildren(rows);
      enable();
    }
    function sourceChanged(){
      const search=$('mobileQuery');
      search.value='';
      search.placeholder=fromPlaylist()?'搜索播放列表中的曲目或编排':'搜索要传输的曲目';
      search.setAttribute('aria-label',search.placeholder);
      $('mobileSongs').scrollTop=0;
      draw();
    }
    function transferSelection(){
      const plans={};
      if(!fromPlaylist()){
        for(const song of songs)if(selected.has(song.id) && choices.has(song.id)){
          plans[song.id]=Arrangements.plans(song).filter(p=>choices.get(song.id).has(p.id));
        }
        return {songs:[...selected],plans};
      }
      const bySong=new Map();
      const entries=[];
      for(const {id,song,plan} of playlistRows){
        if(!selectedEntries.has(id))continue;
        entries.push({id,songId:song.id,planId:plan?.id ?? null});
        if(!bySong.has(song.id))bySong.set(song.id,new Map());
        if(plan && plan.id!=='default')bySong.get(song.id).set(plan.id,plan);
      }
      for(const [id,custom] of bySong)if(custom.size)plans[id]=[...custom.values()];
      return {songs:[...bySong.keys()],plans,playlist:entries};
    }
    async function poll(){
      if (!dialog.open || !code || pollBusy) return;
      pollBusy=true;
      try{
        status=await api('mobileStatus');
        if(!dialog.open)return;
        const next=JSON.stringify(status.inventory);
        if(next!==fingerprint){fingerprint=next;draw();}
        $('phoneName').textContent=status.device ? status.device.name+(status.connected?' · 已连接':' · 等待重新连接')+
          (status.inventory?.effects?' · 已有音效包':''):'请用手机版扫描二维码';
        if(status.message!==poll.previous){message(status.message);poll.previous=status.message;}
        enable();
      } catch(e){message(String(e));} finally{pollBusy=false;}
    }
    $('mobileSettings').disabled=false;
    $('mobileSettings').onclick=()=>{
      ($('filter').value==='playlist'?$('mobilePlaylist'):$('mobileLibrary')).checked=true;
      dialog.showModal();sourceChanged();
    };
    $('mobileLibrary').onchange=$('mobilePlaylist').onchange=sourceChanged;
    $('mobileSelectAll').onchange=e=>{
      for(const {id} of visibleEntries){
        if(e.target.checked)selectedEntries.add(id);else selectedEntries.delete(id);
      }
      draw();
    };
    $('mobileQuery').oninput=draw;$('mobileEffects').onchange=enable;
    $('pairStart').onclick=async()=>{
      if(starting)return;starting=true;$('pairStart').disabled=true;
      try{
        const p=await api('mobileOpen');
        if(!dialog.open){await api('mobileClose');return;}
        code=p.code;$('pairImage').src=p.image;$('pairImage').hidden=false;
        $('pairAddresses').textContent=p.ips.join(' · ');$('pairCopy').hidden=false;$('pairStart').hidden=true;
        clearInterval(timer);timer=setInterval(poll,1000);await poll();
      }catch(e){message(String(e));}finally{starting=false;$('pairStart').disabled=false;}
    };
    $('pairCopy').onclick=async()=>{try{await navigator.clipboard.writeText(code);message('已复制配对码');}catch{message(code);}};
    $('mobileSend').onclick=async()=>{
      sending=true;enable();
      try{
        status=await api('mobileSend',{...transferSelection(),effects:$('mobileEffects').checked});await poll();
      }catch(e){message(String(e));}finally{sending=false;enable();}
    };
    $('backupSave').onclick=async()=>{
      try{if(await api('saveBackup',{data:ConfigBackup.collect(songs,playlist)}))message('已导出全部自定义编排和播放列表');}
      catch(e){message(String(e));}
    };
    $('backupLoad').onclick=()=>$('backupFile').click();
    $('backupFile').onchange=async e=>{
      try{
        const file=e.target.files[0];if(!file)return;
        if(file.size>16*1024*1024)throw Error('配置文件过大');
        ConfigBackup.restore(JSON.parse((await file.text()).replace(/^\uFEFF/,'')),songs,playlist);
        refresh();draw();message('已导入配置，同 ID 的编排和列表条目已更新');
      }catch(err){message(String(err));}finally{e.target.value='';}
    };
    $('mobileDone').onclick=()=>dialog.close();
    dialog.onclose=async()=>{
      clearInterval(timer);const opened=!!code;code='';fingerprint='';status={};poll.previous='';
      $('pairImage').hidden=true;$('pairCopy').hidden=true;$('pairStart').hidden=false;
      $('phoneName').textContent='手机和电脑连接同一局域网，在手机版中选择「扫码连接」。';
      $('pairAddresses').textContent='';message('');if(opened)await api('mobileClose');
    };
  };
})();
