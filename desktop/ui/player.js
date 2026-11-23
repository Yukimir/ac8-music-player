(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const el = (tag,cls,label) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (label !== undefined) node.textContent = label;
    return node;
  };
  const rate = 48000;
  function time(frame) {
    const sec = Math.max(0,Math.floor(frame/rate));
    return String(Math.floor(sec/60)).padStart(2,'0') + ':' + String(sec%60).padStart(2,'0');
  }
  const api = (op, payload = {}) => window.__TAURI__.tauri.invoke('audio',
    {request: Object.assign({op},payload)});
  let catalog = [], visible = [], songs = new Map(), entries = new Map(), focused = null;
  let state = null, shownSong = null, shownSection = '', diagram = null, pendingDiagram = '';
  let barsLimit = -1, dragging = false;
  const modeLabels={ordered:'顺序播放',shuffle:'随机播放',repeatOne:'单曲循环'};
  const playlist=new Playlist(localStorage);
  const navigation=new Playlist.TrackNavigation();
  let shownNeighbors=null;
  let libraryQueue=[], libraryMode='ordered';
  const sessionKey='ac8.selection.v1';
  let sessionReady=false;
  const browsingList=()=>byId('filter').value==='playlist';
  let actionChain = Promise.resolve(), revision = 0, busy = 0, polling = false, ended = null;
  let uiError = '', laneViews = [];
  let sectionClick = null;
  const repeatValues = [0,1,2,4,8,16], barValues = [1,2,4,8,16,32,64,128];

  function report(message) {
    const box = byId('error');
    box.textContent = message || '';
    box.hidden = !message;
  }
  function readSelection() {
    try {return JSON.parse(localStorage.getItem(sessionKey));} catch {return null;}
  }
  function rememberSelection() {
    if (!sessionReady || !state) return;
    try {
      localStorage.setItem(sessionKey,JSON.stringify({
        song:state.song,arrangement:state.arrangementId,entry:playlist.current,
        queue:playlist.active?null:libraryQueue.map(song=>song.id),libraryMode,
        tab:byId('filter').value,query:byId('query').value,focused
      }));
    } catch {byId('listNotice').textContent='无法保存本次选曲。';}
  }
  function command(op,args={},target=null,accepted=null) {
    revision++; busy++;
    actionChain = actionChain.then(async () => {
      try {
        const updated = await api(op,Object.assign({},target,args));
        if (accepted) accepted(updated);
        uiError = ''; update(updated);
        if (['select','arrangement','setPlayMode','cyclePlayMode'].includes(op)) rememberSelection();
        return updated;
      } catch (e) { uiError = String(e); report(uiError); }
      finally { busy--; }
    });
    return actionChain;
  }
  const onSection = (op,args) => state && command(op,args,{song:state.song,segment:state.segment});
  function option(select,value,label) {
    select.add(new Option(label,value === null ? '' : String(value)));
  }
  function planOf(item) {
    return Arrangements.plans(songs.get(item.songId)).find(plan=>plan.id===item.planId);
  }
  function validEntry(item) {
    const song=songs.get(item.songId);
    return song && (song.defaultPlan ? Boolean(planOf(item)) : item.planId===null);
  }
  function listRows() {
    return playlist.items.filter(validEntry).map(item=>{
      const song=songs.get(item.songId), plan=planOf(item);
      const infinite=plan?plan.loopFrom!=null || plan.steps.some(step=>step.repeat===0):song.singleLoops;
      const detail=(plan?(plan.name || '默认编排'):'单曲')+' · '+(infinite?'∞ 无限循环':'播放至结束');
      return {id:item.id,song,plan,detail};
    });
  }
  function editList(edit) {
    try {edit();syncNavigation(true);return true;}
    catch(e) {byId('listNotice').textContent='播放列表保存失败。'+e.message;return false;}
  }
  function addToPlaylist(song,planId) {
    if (!editList(()=>playlist.add(song.id,planId))) return;
    if (browsingList()) listChanged();
    const plan=Arrangements.plans(song).find(p=>p.id===planId);
    byId('listNotice').textContent='已加入「'+song.title+'」'+(plan?' · '+(plan.name || '默认编排'):'');
  }
  function listActions() {
    const inList=browsingList(), song=songs.get(focused);
    byId('catalogActions').hidden=inList || !song;
    byId('playlistActions').hidden=!inList;
    if (inList) {
      const index=playlist.items.findIndex(item=>item.id===focused);
      byId('startList').disabled=index<0;
      byId('removeEntry').disabled=index<0;
      byId('moveUp').disabled=index<=0;
      byId('moveDown').disabled=index<0 || index===playlist.items.length-1;
      byId('listMode').value=playlist.mode;
    } else if (song) {
      const select=byId('addPlan'), plans=Arrangements.plans(song);
      const keep=select.dataset.song===String(song.id)?select.value:null;
      select.hidden=!song.defaultPlan;
      select.replaceChildren();
      for (const plan of plans) option(select,plan.id,plan.name || '默认编排');
      select.dataset.song=String(song.id);
      const preferred=keep || (state?.song===song.id?state.arrangementId:Arrangements.saved(song)?.id);
      if (plans.some(plan=>plan.id===preferred)) select.value=preferred;
    }
  }
  function listChanged(scroll=false) {
    const phrase = byId('query').value.trim().toLocaleLowerCase();
    const category = byId('filter').value;
    const inList=browsingList();
    visible = inList?listRows().filter(row=>(row.song.search+' '+row.detail.toLocaleLowerCase()).includes(phrase)):
      catalog.filter(s=>s.bank===category && s.search.includes(phrase)).map(song=>({id:song.id,song,detail:song.origin}));
    const currentId=inList?playlist.current:state?.song;
    if (!visible.some(s=>s.id===focused))
      focused = visible.some(s=>s.id===currentId) ? currentId : (visible[0]?.id ?? null);
    const frag = document.createDocumentFragment();
    entries = new Map();
    for (const {id,song,detail} of visible) {
      const row = el('div','song'), copy = el('div','copy');
      row.id = 'song-'+id; row.dataset.id = String(id); row.setAttribute('role','option');
      row.title = song.title+' · '+detail+'\n单击浏览，双击播放';
      copy.append(el('strong','',song.title),el('small','',detail));
      const mark = el('span','mark'); mark.setAttribute('aria-hidden','true');
      mark.append(el('i'),el('i'),el('i')); row.append(copy,mark);
      entries.set(id,row); frag.append(row);
    }
    byId('list').replaceChildren(frag);
    byId('total').textContent = String(visible.length).padStart(2,'0')+' 首';
    byId('empty').hidden = visible.length > 0;
    byId('empty').textContent=inList && !playlist.items.length?'播放列表为空，可以从互动配乐或单曲中添加。':'没有匹配的曲目';
    paintFocus(scroll); paintCurrent();
  }
  function paintFocus(scroll) {
    for (const [id,row] of entries) {
      const selected = focused === id;
      row.classList.toggle('focused',selected);
      row.setAttribute('aria-selected',String(selected));
      if (selected && scroll) row.scrollIntoView({block:'nearest'});
    }
    if (focused !== null) byId('list').setAttribute('aria-activedescendant','song-'+focused);
    else byId('list').removeAttribute('aria-activedescendant');
    listActions();
    rememberSelection();
  }
  function paintCurrent() {
    const playing=browsingList()?playlist.current:state?.song;
    for (const [id,row] of entries) {
      row.classList.toggle('current',id===playing);
      if (id===playing) row.setAttribute('aria-current','true');
      else row.removeAttribute('aria-current');
    }
  }
  function focusSong(id,scroll=true) {
    if (!entries.has(id)) return;
    focused=id; paintFocus(scroll);
  }
  function playChoice(id) {
    if (browsingList()) {
      const item=playlist.items.find(entry=>entry.id===id);
      if (item) playEntry(item,true);
      return;
    }
    if (!songs.has(id)) return;
    const queue=visible.map(row=>row.song);
    if (!playlist.active && id===state?.song && !state.finished) {
      libraryQueue=queue;syncNavigation();rememberSelection();
      if (!state.playing && !(state.wantsPlay && !state.ready)) command('play');
    } else selectSong(id,queue);
  }
  function selectSong(id,queue=libraryQueue,delta=0) {
    return command('select',{id,play:true,arrangement:Arrangements.saved(songs.get(id)),playMode:libraryMode,
      hasNextSong:queue.length>1},null,()=>{
      navigation.moved(delta);
      playlist.leave();libraryQueue=queue;ended=null;paintCurrent();
    });
  }
  function playEntry(item,fresh=false,delta=0) {
    const plan=planOf(item);
    if (!validEntry(item)) return;
    const playMode=playlist.mode==='repeatOne'?'repeatOne':'ordered';
    const previous=playlist.current;
    const hasNextSong=playlist.mode!=='ordered' || item.id!==playlist.items.at(-1)?.id;
    return command('select',{id:item.songId,arrangement:plan,play:true,playMode,hasNextSong},null,()=>{
      navigation.moved(delta);
      playlist.start(item.id,fresh);ended=null;paintCurrent();
      if (browsingList() && (focused===previous || focused===item.id)) focusSong(item.id);
    });
  }
  Arrangements.connect((song,arrangement)=>{
    if (sectionClick) clearTimeout(sectionClick.timer);
    sectionClick=null;
    const entry=playlist.items.find(item=>item.id===playlist.current);
    const leave=playlist.active && (entry?.songId!==song || entry?.planId!==arrangement.id);
    return command('arrangement',{song,arrangement,...(leave?{playMode:libraryMode}:{})},null,updated=>{
      if (leave && updated.song===song && updated.arrangementId===arrangement.id) {
        navigation.reset();
        playlist.leave();libraryQueue=catalog.filter(s=>s.bank===songs.get(song).bank);paintCurrent();
      }
    });
  },()=>{
    editList(()=>playlist.keep(validEntry));
    delete byId('addPlan').dataset.song;
    listChanged();
  },addToPlaylist);
  function libraryNeighbor(delta) {
    if (!libraryQueue.length) return null;
    let ix=libraryQueue.findIndex(s=>s.id===state?.song);
    if (libraryMode==='shuffle' && libraryQueue.length>1) delta=1+Math.floor(Math.random()*(libraryQueue.length-1));
    if (ix<0) ix=delta>0?-1:0;
    return libraryQueue[(ix+delta+libraryQueue.length)%libraryQueue.length];
  }
  function neighbors() {
    return playlist.active
      ? navigation.neighbors(playlist.items,playlist.current,playlist.mode,delta=>playlist.next(delta))
      : navigation.neighbors(libraryQueue,state.song,libraryMode,libraryNeighbor);
  }
  function syncNavigation(refreshLabels=false) {
    if (!state) return;
    const view=neighbors();
    if (view===shownNeighbors && !refreshLabels) return;
    shownNeighbors=view;
    const labels={};
    for (const [key,label,shortcut] of [['previous','上一首','A'],['following','下一首','D']]) {
      const item=view[key], song=playlist.active?songs.get(item?.songId):item;
      const plan=item && playlist.active?planOf(item):null;
      labels[key]=song?song.title+(plan?' · '+(plan.name || '默认编排'):''):null;
      const button=byId(key);
      button.disabled=!song;
      button.title=label+'（'+shortcut+'）'+(song?'　'+labels[key]:'');
    }
    invokeDesktop('tray_tracks',labels).catch(e=>{shownNeighbors=null;report(String(e));});
  }
  function stepSong(delta,automatic=false) {
    if (!state || (automatic && playlist.active && playlist.engineMode()==='repeatOne')) return;
    const view=neighbors(), item=delta<0?view.previous:view.following;
    if (!item) return;
    if (playlist.active) return playEntry(item,false,delta);
    focusSong(item.id);return selectSong(item.id,libraryQueue,delta);
  }
  function transition(node) {
    node.classList.remove('swap'); void node.offsetWidth; node.classList.add('swap');
  }
  function loadSong(s) {
    if (sectionClick) clearTimeout(sectionClick.timer);
    sectionClick=null;
    byId('title').textContent = s.title; byId('origin').textContent = s.origin;
    const other = byId('other');
    other.textContent = s.also.length ? '同曲其他段落　'+s.also.join(' / ') : '';
    other.hidden = !other.textContent;
    transition(byId('now'));
    const frag = document.createDocumentFragment();
    for (const part of s.sections) {
      const label = part.name ? part.label+' · '+part.name : part.label;
      const button=el('button','section',label);
      button.dataset.id = String(part.id); button.title = label+'　'+time(part.length)+' · 单击排队，双击立即切换';
      button.append(el('i','',time(part.length))); frag.append(button);
    }
    byId('sections').replaceChildren(frag); paintCurrent();
  }
  function loopOptions(s) {
    const locked=s.arrangementId && s.arrangementId!=='free';
    document.querySelector('.settings').hidden=Boolean(locked);
    if (locked) return;
    const max=s.bar>0?Math.max(0,Math.floor((s.exit-s.entry)/s.bar)):0;
    if (max!==barsLimit) {
      barsLimit=max;
      const select=byId('bars'); select.replaceChildren(); option(select,null,'游戏预设');
      for (const n of barValues) if (n<=max) option(select,n,n+' 小节');
    }
    byId('bars').value = s.bars===null?'':String(s.bars);
    byId('repeat').value = s.repeat===null?'':String(s.repeat);
    byId('repeat').disabled = !s.ready || s.bridge;
    byId('bars').disabled = !s.ready || s.bridge || max<1;
    byId('reset').disabled = !s.ready || s.bridge;
  }
  function showDiagram(data) {
    diagram = data; laneViews=[];
    const frag=document.createDocumentFragment();
    if (data) for (const [i,lane] of data.lanes.entries()) {
      const row=el('div','lane'), button=el('button'), light=el('span','light');
      const label='分轨 '+String(i+1).padStart(2,'0');
      button.title=lane.variant==null ? label+' · 开启或关闭' : label+' · 切换同组变体';
      button.dataset.key=lane.key; button.append(light,document.createTextNode(label));
      const canvas=el('canvas'); canvas.title='点击定位到此处';
      canvas.setAttribute('aria-hidden','true'); row.append(button,canvas); frag.append(row);
      laneViews.push({row,button,canvas,lane});
    }
    if (!laneViews.length) frag.append(el('p','silent',data?'此段没有独立音轨':'正在读取分轨…'));
    byId('lanes').replaceChildren(frag);
    if (state) paintLanes(state);
  }
  function paintLanes(s) {
    byId('laneCount').textContent = diagram ? s.enabled.filter(Boolean).length+' / '+laneViews.length+' 已开启' : '';
    laneViews.forEach(({row,button,canvas,lane},i)=>{
      const enabled=Boolean(s.enabled[i]); row.classList.toggle('off',!enabled);
      button.setAttribute('aria-pressed',String(enabled));
      drawWave(canvas,lane,s,diagram.marks,enabled);
    });
  }
  async function requestDiagram(key) {
    pendingDiagram=key;
    try {
      const result=await api('diagram');
      if (shownSection===key && result && key===result.song+'/'+result.segment) showDiagram(result);
    } catch (e) { report(String(e)); }
    finally { if (pendingDiagram===key) pendingDiagram=''; }
  }
  function position(frame,length) {
    const share=length?Math.max(0,Math.min(1,frame/length)):0;
    byId('seek').style.setProperty('--pos',(share*100)+'%');
    byId('time').textContent=time(frame);
  }
  function update(s) {
    if (!s) return;
    state=s;
    const song=songs.get(s.song); if (!song) return;
    if (!playlist.active) libraryMode=s.playMode;
    syncNavigation();
    if (s.song!==shownSong) {
      shownSong=s.song; loadSong(song);
    }
    const key=s.song+'/'+s.segment;
    if (shownSection!==key) {
      shownSection=key; dragging=false; showDiagram(null); transition(byId('lanes'));
    }
    if (s.ready && !diagram && pendingDiagram!==key) requestDiagram(key);
    const isActive=s.playing || (!s.ready && s.wantsPlay);
    const locked=Boolean(s.arrangementId && s.arrangementId!=='free');
    Arrangements.show(song,s);
    window.updateSandTable(s);
    document.body.classList.toggle('playing',s.playing);
    byId('play').disabled=false;
    byId('play').classList.toggle('can-pause',isActive);
    byId('playText').textContent=isActive?'暂停':s.finished?'重播':'播放';
    const order=byId('order'), label=playlist.active?Playlist.modes[playlist.mode]:modeLabels[s.playMode];
    if (order.textContent!==label) order.textContent=label;
    order.title=playlist.active?'切换列表播放模式（T）':'切换曲库播放模式（T）';
    byId('duration').textContent=time(s.length);
    byId('seek').disabled=!s.ready || s.bridge;
    if (!dragging) {
      byId('seek').value=String(Math.round(10000*s.position/Math.max(1,s.length)));
      position(s.position,s.length);
    }
    masterSlider(s.master);
    const source=s.sourceRate?'音源 '+s.sourceRate/1000+' kHz':'等待读取音源';
    const output=s.outputRate?(s.playing?'输出 ':'上次输出 ')+s.outputRate/1000+' kHz / '+s.outputFormat:'等待输出';
    const format=byId('audioFormat'), description=source+' · '+output;
    if (format.textContent!==description) format.textContent=description;
    format.title='游戏音源采样率 · 播放器提交给 Windows 的格式';
    const kind=s.sourceKind ? '（'+s.sourceKind+'）' : '';
    byId('outputLayout').textContent=s.outputLayout ? s.outputLayout+kind : '自动';
    const widths=(s.sourceChannels || []).map(n=>n===12?'7.1.4':n===2?'2.0':'单声道');
    byId('outputLayout').title=[widths.length?'曲目音源 '+widths.join(' / '):'',s.outputBackend || '播放时检测输出设备'].filter(Boolean).join(' · ');
    byId('volume').title=s.outputLayout?'总音量 · '+s.outputLayout+' · '+s.outputBackend:'总音量';
    seSlider(s.seVolume);
    byId('effects').checked=Boolean(s.effects);
    if (!categoryEdits && s.effectCategories) showCategories(s.effectCategories);
    byId('chooseEffects').disabled=false;
    byId('engineBed').checked=Boolean(s.engineBed);
    const loadStatus=s.error?'':!s.ready?'载入 '+s.progress.join(' / '):s.finished?'播放结束':s.playing?'播放中':'已暂停';
    byId('load').textContent=playlist.active && loadStatus?'播放列表 · '+loadStatus:loadStatus;
    const waiting=s.sectionQueue || (s.queued==null?[]:[s.queued]);
    for (const button of byId('sections').children) {
      const id=Number(button.dataset.id);
      button.classList.toggle('sel',id===s.segment);
      button.classList.toggle('wait',waiting.includes(id));
      button.setAttribute('aria-pressed',String(id===s.segment));
      button.disabled=!s.ready || locked;
      button.title=locked?'当前编排控制段落顺序':button.textContent+' · 单击排队，双击立即切换';
    }
    const current=song.sections.find(part=>part.id===s.segment);
    const itinerary=waiting.map(id=>song.sections.find(part=>part.id===id)?.label).join(' → ');
    const status=itinerary?(s.bridge?'过渡中 · ':'待切换 · ')+itinerary:s.bridge?'过渡中':current?.label || '';
    if (byId('phaseStatus').textContent!==status) byId('phaseStatus').textContent=status;
    byId('phaseStatus').title=status;
    byId('nextPhase').disabled=!s.ready || locked || s.bridge || song.sections.length<2;
    const hint=locked?'按编排自动切段 · 选择「自由编排」可手动选段':'单击排队 · 双击立即切换';
    byId('sections').title=hint;
    document.querySelector('.section-help').textContent=hint;
    loopOptions(s);
    const totalRuns=s.effectiveRepeat===0?'∞':s.effectiveRepeat;
    byId('loopInfo').textContent=time(s.entry)+' – '+time(s.effectiveEnd)+' · 第 '+s.run+' / '+totalRuns+' 遍';
    byId('loopInfo').title='循环区间 · '+(s.effectiveRepeat===0?'无限循环':s.effectiveRepeat+' 遍');
    paintLanes(s); drawSpectrum(byId('spectrum'),s.levels);
    report(s.error || uiError);
    const entryId=playlist.current ?? s.song;
    if (!busy && playlist.active && s.playMode!==playlist.engineMode()) {
      command('setPlayMode',{playMode:playlist.engineMode()});
    } else if (!busy && s.finished && s.playMode!=='repeatOne' && ended!==entryId && s.wantsPlay &&
               (playlist.active || libraryQueue.length>1)) {
      ended=entryId; stepSong(1,true);
    } else if (!s.finished) ended=null;
  }
  function readSelect(id) { const v=byId(id).value; return v===''?null:Number(v); }
  function changeLoop() { onSection('loop',{repeat:readSelect('repeat'),bars:readSelect('bars')}); }
  function toggleRepeat() {
    if (!state?.ready || state.bridge || (state.arrangementId && state.arrangementId!=='free')) return;
    onSection('loop',{repeat:state.repeat===0?null:0,bars:state.bars});
  }
  function seek(frame) {
    if (state?.ready && !state.bridge) onSection('seek',{frame:Math.round(frame)});
  }
  byId('filter').onchange=()=>{
    byId('query').value='';byId('listNotice').textContent='';listChanged(true);
  };
  byId('query').oninput=()=>listChanged(true);
  const list=byId('list');
  const rowId=row=>browsingList()?row.dataset.id:Number(row.dataset.id);
  list.addEventListener('click',e=>{
    const row=e.target.closest('.song'); if (!row) return;
    list.focus({preventScroll:true}); focusSong(rowId(row),false);
  });
  list.addEventListener('dblclick',e=>{
    const row=e.target.closest('.song'); if (row) playChoice(rowId(row));
  });
  byId('addEntry').onclick=()=>{
    const song=songs.get(focused);
    if (!song) return;
    const plan=song.defaultPlan?byId('addPlan').value:null;
    addToPlaylist(song,plan);
  };
  byId('startList').onclick=()=>playChoice(focused);
  byId('removeEntry').onclick=()=>{
    const position=visible.findIndex(row=>row.id===focused);
    if (!editList(()=>playlist.keep(item=>item.id!==focused))) return;
    focused=visible[position+1]?.id ?? visible[position-1]?.id ?? null;
    byId('listNotice').textContent='';listChanged(true);
  };
  for (const [id,delta] of [['moveUp',-1],['moveDown',1]]) byId(id).onclick=()=>{
    if (editList(()=>playlist.move(focused,delta))) listChanged(true);
  };
  const listModes=Object.keys(Playlist.modes);
  for (const mode of listModes) option(byId('listMode'),mode,Playlist.modes[mode]);
  function changeListMode(mode) {
    const ok=editList(()=>playlist.setMode(mode));
    byId('listMode').value=playlist.mode;
    if (ok && playlist.active) poll();
  }
  byId('listMode').onchange=e=>changeListMode(e.target.value);
  byId('play').onclick=()=>command('play');
  byId('previous').onclick=()=>stepSong(-1);
  byId('following').onclick=()=>stepSong(1);
  byId('order').onclick=()=>{
    if (playlist.active) changeListMode(listModes[(listModes.indexOf(playlist.mode)+1)%listModes.length]);
    else command('cyclePlayMode');
  };
  byId('nextPhase').onclick=()=>onSection('nextSection');
  byId('effects').onchange=e=>command('effects',{enabled:e.target.checked});
  byId('engineBed').onchange=e=>command('engineBed',{enabled:e.target.checked});
  const categoryPanel=byId('effectCategories'), categoryButton=byId('chooseEffects');
  const categoryBoxes=[...categoryPanel.querySelectorAll('input')];
  const categoryStorage='ac8.randomEffectCategories';
  let categoryEdits=0;
  function showCategories(selected) {
    for (const box of categoryBoxes) box.checked=selected.includes(box.value);
    byId('effectCount').textContent=selected.length+'/'+categoryBoxes.length;
  }
  function openCategories(open) {
    categoryPanel.hidden=!open;
    categoryButton.setAttribute('aria-expanded',String(open));
  }
  categoryButton.onclick=()=>openCategories(categoryPanel.hidden);
  categoryPanel.onkeydown=e=>{
    if (e.key==='Escape') {openCategories(false);categoryButton.focus();}
  };
  categoryPanel.onchange=async()=>{
    const categories=categoryBoxes.filter(box=>box.checked).map(box=>box.value);
    showCategories(categories);
    categoryEdits++;
    await command('effectCategories',{categories});
    categoryEdits--;
    if (!categoryEdits && state?.effectCategories) {
      showCategories(state.effectCategories);
      try {localStorage.setItem(categoryStorage,JSON.stringify(state.effectCategories));} catch {}
    }
  };
  function savedCategories() {
    try {
      const saved=JSON.parse(localStorage.getItem(categoryStorage));
      if (Array.isArray(saved) && saved.every(id=>categoryBoxes.some(box=>box.value===id))) return saved;
    } catch {}
    return null;
  }
  function commitSectionClick() {
    if (!sectionClick) return;
    const {id,song,timer}=sectionClick;
    clearTimeout(timer); sectionClick=null;
    command('queue',{id},{song});
  }
  byId('sections').onclick=e=>{
    const button=e.target.closest('button[data-id]');
    if (!button || button.disabled || !state?.ready || e.detail>1) return;
    commitSectionClick();
    const id=Number(button.dataset.id), song=state.song;
    if (e.detail===0) {command('queue',{id},{song});return;}
    sectionClick={id,song,timer:setTimeout(commitSectionClick,(state.doubleClickMs || 500)+20)};
  };
  byId('sections').ondblclick=e=>{
    const button=e.target.closest('button[data-id]');
    if (!button || button.disabled || !state?.ready) return;
    if (sectionClick) clearTimeout(sectionClick.timer);
    sectionClick=null;
    command('jump',{id:Number(button.dataset.id)},{song:state.song});
  };
  byId('repeat').onchange=changeLoop; byId('bars').onchange=changeLoop;
  byId('reset').onclick=()=>onSection('restore');
  byId('lanes').onclick=e=>{
    const button=e.target.closest('button[data-key]');
    if (button) onSection('toggle',{key:button.dataset.key});
    else if (e.target.tagName==='CANVAS' && state) {
      const box=e.target.getBoundingClientRect();
      if (box.width) seek((e.clientX-box.left)*state.length/box.width);
    }
  };
  const scrub=byId('seek');
  scrub.onpointerdown=()=>{dragging=true;};
  scrub.oninput=()=>{if(state){ dragging=true; position(Number(scrub.value)*state.length/10000,state.length); }};
  scrub.onchange=()=>{ dragging=false; if(state) seek(Number(scrub.value)*state.length/10000); };
  scrub.onpointerup=scrub.onpointercancel=scrub.onblur=()=>{dragging=false;};
  function volumeSlider(id, label, fullScale=1) {
    const slider=byId(id);
    let timer=0, sending=false, wanted=null;
    async function send() {
      timer=0;
      const value=wanted;wanted=null;sending=true;
      try {await command(id,{value});}
      finally {
        sending=false;
        if (wanted!==null) timer=setTimeout(send,30);
      }
    }
    slider.oninput=()=>{
      wanted=Number(slider.value)*fullScale/100;
      if (label) label.value=slider.value+'%';
      if (!timer && !sending) timer=setTimeout(send,30);
    };
    return value=>{
      if (wanted!==null || sending || !Number.isFinite(value)) return;
      slider.value=Math.round(value*100/fullScale);
      if (label) label.value=slider.value+'%';
    };
  }
  const masterSlider=volumeSlider('volume');
  const seSlider=volumeSlider('seVolume',byId('seLevel'),2);
  const eye=byId('toggleUi');
  const invokeDesktop=(name,args={})=>window.__TAURI__.tauri.invoke(name,args);
  const wallpaperButton=byId('wallpaperMode');
  window.desktopModeChanged=enabled=>{
    window.setSandTableEnabled(!enabled);
    document.body.classList.toggle('desktop-mode',enabled);
    wallpaperButton.setAttribute('aria-pressed',String(enabled));
    wallpaperButton.title=enabled?'关闭桌面背景':'设为桌面背景';
  };
  // Reopening the WebView can leave the native wallpaper running.
  if (window.__TAURI__?.tauri) {
    invokeDesktop('desktop_active').then(active=>{
      if (typeof active==='boolean' && !wallpaperButton.disabled) window.desktopModeChanged(active);
    }).catch(()=>{});
  }
  wallpaperButton.onclick=async()=>{
    wallpaperButton.disabled=true;
    try {
      const enabled=await invokeDesktop('set_wallpaper',{enabled:wallpaperButton.getAttribute('aria-pressed')!=='true'});
      window.desktopModeChanged(enabled);
    } catch(e){uiError=String(e);report(uiError);}
    finally {wallpaperButton.disabled=false;}
  };
  const closePrompt=byId('closePrompt');
  closePrompt.addEventListener('close',()=>{
    const action=closePrompt.returnValue;
    closePrompt.returnValue='';
    if (action==='exit' || action==='hide') invokeDesktop('player_window',{action}).catch(e=>report(String(e)));
  });
  window.trayPlayback=op=>{
    if (op==='previous' || op==='following') return stepSong(op==='previous'?-1:1);
    if (state) return command(op);
    else if (op==='startPlayback') invokeDesktop('player_window',{action:'show'});
  };
  eye.onclick=()=>{
    const hidden=eye.closest('.shell').classList.toggle('ui-hidden');
    eye.setAttribute('aria-expanded',String(!hidden));
    eye.title=hidden?'显示界面':'隐藏界面';
    eye.setAttribute('aria-label',eye.title);
  };
  document.addEventListener('keydown',e=>{
    if (byId('libraryImport').open || byId('mobilePanel').open || closePrompt.open) return;
    if (e.ctrlKey || e.altKey || e.metaKey || e.isComposing) return;
    const target=e.target, typing=target.tagName==='INPUT' || target.tagName==='SELECT';
    if (e.key==='Escape' && target===byId('query')) {
      target.value='';listChanged(true);list.focus();return;
    }
    if (typing || target.closest('#arrangements')) return;
    const key=e.key.toLowerCase();
    if (eye.getAttribute('aria-expanded')==='false' && ['arrowdown','arrowup','home','end','/'].includes(key)) return;
    if (key==='arrowdown' || key==='arrowup' || key==='home' || key==='end') {
      if (!visible.length) return;
      const index=visible.findIndex(s=>s.id===focused);
      const next=key==='home'?0:key==='end'?visible.length-1:index+(key==='arrowdown'?1:-1);
      e.preventDefault();list.focus();focusSong(visible[Math.max(0,Math.min(visible.length-1,next))].id);return;
    }
    if (target.closest('button') && (key==='enter' || key===' ')) return;
    if (!['enter',' ','/','r','a','d','t','1','3'].includes(key)) return;
    e.preventDefault(); if (e.repeat && !['1','3'].includes(key)) return;
    if (key==='enter') playChoice(focused);
    if (key===' ' && state) command('play');
    if (key==='/') byId('query').focus();
    if (key==='r') toggleRepeat();
    if (key==='a' || key==='d') stepSong(key==='a'?-1:1);
    if (key==='t') byId('order').click();
    if ((key==='1' || key==='3') && state) seek(state.position+(key==='1'?-5:5)*rate);
  });
  async function poll() {
    if (polling || busy) return;
    polling=true; const when=revision;
    try {
      const s=await api('state',state?{song:state.song,
        hasNextSong:playlist.active?Boolean(neighbors().following):libraryQueue.length>1}:{});
      if (when===revision) {uiError='';update(s);}
    } catch (e) {report(String(e));}
    finally {polling=false;}
  }
  async function restoreSelection(saved) {
    let initial=await api('state');
    const song=songs.get(saved?.song) ?? songs.get(initial.song);
    const entry=playlist.items.find(item=>item.id===saved?.entry && item.songId===song.id);
    const plan=entry?planOf(entry):Arrangements.saved(song,saved?.song===song.id?saved.arrangement:undefined);
    if (entry) playlist.start(entry.id,true);
    if (Object.hasOwn(modeLabels,saved?.libraryMode)) libraryMode=saved.libraryMode;
    const queue=Array.isArray(saved?.queue)?[...new Set(saved.queue)].map(id=>songs.get(id)).filter(s=>s?.bank===song.bank):[];
    libraryQueue=queue.includes(song)?queue:catalog.filter(s=>s.bank===song.bank);
    const playMode=entry?playlist.engineMode():libraryMode;
    if (song.id!==initial.song)
      initial=await api('select',{id:song.id,play:false,arrangement:plan,playMode});
    else if (plan && plan.id!==initial.arrangementId)
      initial=await api('arrangement',{song:song.id,arrangement:plan,playMode});
    else if (initial.playMode!==playMode)
      initial=await api('setPlayMode',{playMode});

    const filter=byId('filter');
    const tabs=Array.from(filter.options,option=>option.value);
    if (tabs.includes(saved?.tab)) {
      filter.value=saved.tab;
      byId('query').value=typeof saved.query==='string'?saved.query:'';
    } else if (entry) filter.value='playlist';
    else if (tabs.includes(song.bank)) filter.value=song.bank;
    focused=saved?.focused ?? (browsingList()?playlist.current:song.id);
    update(initial);
    listChanged(true);
    sessionReady=true;
    rememberSelection();
  }
  window.addEventListener('pagehide',rememberSelection);
  async function init() {
    try {
      await window.prepareAudioLibrary(api);
      const settings=byId('sourceSettings');
      settings.disabled=false;
      settings.onclick=async()=>{
        settings.disabled=true;
        try {await window.prepareAudioLibrary(api,true);await poll();}
        catch(e) {report(String(e));}
        finally {settings.disabled=false;settings.focus();}
      };
      option(byId('repeat'),null,'游戏预设');
      for (const n of repeatValues) option(byId('repeat'),n,n===0?'无限':n+' 遍');
      const selection=readSelection();
      catalog=await api('library',{initialSong:Number.isSafeInteger(selection?.song)?selection.song:null});
      for (const s of catalog) s.search=[s.title,s.origin,s.context,s.original,s.event,...s.also]
        .join(' ').toLocaleLowerCase();
      songs=new Map(catalog.map(s=>[s.id,s]));
      window.setupMobile?.(api,catalog,playlist,listRows,()=>{listChanged();syncNavigation(true);});
      editList(()=>playlist.keep(validEntry));
      await restoreSelection(selection);
      const saved=savedCategories();
      if (saved!==null) await command('effectCategories',{categories:saved});
      await poll();
      async function refresh() {
        if (!document.hidden || state?.playing || state?.wantsPlay && ended!==(playlist.current ?? state.song)) await poll();
        setTimeout(refresh,state?.playing && !document.hidden ? 35 : 100);
      }
      setTimeout(refresh,35);
      document.addEventListener('visibilitychange',()=>{if (!document.hidden) poll();});
    } catch(e) {
      byId('title').textContent='曲库未能载入'; report(String(e));
    }
  }
  init();
})();
