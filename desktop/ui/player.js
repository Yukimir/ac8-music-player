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
  let actionChain = Promise.resolve(), revision = 0, busy = 0, polling = false, ended = null;
  let uiError = '', laneViews = [];
  let sectionClick = null;
  const repeatValues = [0,1,2,4,8,16], barValues = [1,2,4,8,16,32,64,128];

  function report(message) {
    const box = byId('error');
    box.textContent = message || '';
    box.hidden = !message;
  }
  function command(op,args={},target=null) {
    revision++; busy++;
    actionChain = actionChain.then(async () => {
      try {
        const updated = await api(op,Object.assign({},target,args));
        uiError = ''; update(updated);return updated;
      } catch (e) { uiError = String(e); report(uiError); }
      finally { busy--; }
    });
    return actionChain;
  }
  const onSection = (op,args) => state && command(op,args,{song:state.song,segment:state.segment});
  function option(select,value,label) {
    select.add(new Option(label,value === null ? '' : String(value)));
  }
  function listChanged(scroll=false) {
    const phrase = byId('query').value.trim().toLocaleLowerCase();
    const category = byId('filter').value;
    visible = catalog.filter(s => s.bank===category && s.search.includes(phrase));
    if (!visible.some(s=>s.id===focused))
      focused = visible.some(s=>s.id===state?.song) ? state.song : (visible[0]?.id ?? null);
    const frag = document.createDocumentFragment();
    entries = new Map();
    for (const s of visible) {
      const row = el('div','song'), copy = el('div','copy');
      row.id = 'song-'+s.id; row.dataset.id = String(s.id); row.setAttribute('role','option');
      row.title = s.title+' · '+s.origin+'\n单击浏览，双击播放';
      copy.append(el('strong','',s.title),el('small','',s.origin));
      const mark = el('span','mark'); mark.setAttribute('aria-hidden','true');
      mark.append(el('i'),el('i'),el('i')); row.append(copy,mark);
      entries.set(s.id,row); frag.append(row);
    }
    byId('list').replaceChildren(frag);
    byId('total').textContent = String(visible.length).padStart(2,'0')+' 首';
    byId('empty').hidden = visible.length > 0;
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
  }
  function paintCurrent() {
    for (const [id,row] of entries) {
      row.classList.toggle('current',id===state?.song);
      if (id===state?.song) row.setAttribute('aria-current','true');
      else row.removeAttribute('aria-current');
    }
  }
  function focusSong(id,scroll=true) {
    if (!entries.has(id)) return;
    focused=id; paintFocus(scroll);
  }
  function playChoice(id) {
    if (!songs.has(id)) return;
    if (id===state?.song && !state.finished) {
      if (!state.playing && !(state.wantsPlay && !state.ready)) command('play');
    } else selectSong(id);
  }
  function selectSong(id) {
    return command('select',{id,play:true,arrangement:Arrangements.saved(songs.get(id))});
  }
  Arrangements.connect((song,arrangement)=>{
    if (sectionClick) clearTimeout(sectionClick.timer);
    sectionClick=null;
    return command('arrangement',{song,arrangement});
  });
  function stepSong(delta) {
    if (!visible.length) return;
    let ix=visible.findIndex(s=>s.id===state?.song);
    if (state?.playMode==='shuffle' && visible.length>1) delta=1+Math.floor(Math.random()*(visible.length-1));
    if (ix<0) ix=delta>0?-1:0;
    const next=visible[(ix+delta+visible.length)%visible.length].id;
    focusSong(next); selectSong(next);
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
    if (s.song!==shownSong) {
      const first=shownSong===null;
      shownSong=s.song; loadSong(song);
      if (first && entries.has(s.song)) focusSong(s.song);
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
    byId('playIcon').textContent=isActive?'Ⅱ':'▶';
    byId('playText').textContent=isActive?'暂停':s.finished?'重播':'播放';
    const order=byId('order'), label=modeLabels[s.playMode];
    if (order.textContent!==label) order.textContent=label;
    byId('duration').textContent=time(s.length);
    byId('seek').disabled=!s.ready || s.bridge;
    if (!dragging) {
      byId('seek').value=String(Math.round(10000*s.position/Math.max(1,s.length)));
      position(s.position,s.length);
    }
    masterSlider(s.master);
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
    byId('load').textContent=s.error?'':!s.ready?'载入 '+s.progress.join(' / '):s.finished?'播放结束':s.playing?'播放中':'已暂停';
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
    if (s.finished && s.playMode!=='repeatOne' && ended!==s.song && visible.length>1 && s.wantsPlay) {
      ended=s.song; stepSong(1);
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
  byId('filter').onchange=()=>listChanged(true);
  byId('query').oninput=()=>listChanged(true);
  const list=byId('list');
  list.addEventListener('click',e=>{
    const row=e.target.closest('.song'); if (!row) return;
    list.focus({preventScroll:true}); focusSong(Number(row.dataset.id),false);
  });
  list.addEventListener('dblclick',e=>{
    const row=e.target.closest('.song'); if (row) playChoice(Number(row.dataset.id));
  });
  byId('play').onclick=()=>command('play');
  byId('previous').onclick=()=>stepSong(-1);
  byId('following').onclick=()=>stepSong(1);
  byId('order').onclick=()=>command('cyclePlayMode');
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
    if (state) command(op);
    else if (op==='startPlayback') invokeDesktop('player_window',{action:'show'});
  };
  eye.onclick=()=>{
    const hidden=eye.closest('.shell').classList.toggle('ui-hidden');
    eye.setAttribute('aria-expanded',String(!hidden));
    eye.title=hidden?'显示界面':'隐藏界面';
    eye.setAttribute('aria-label',eye.title);
  };
  document.addEventListener('keydown',e=>{
    if (byId('libraryImport').open || closePrompt.open) return;
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
      const s=await api('state');
      if (when===revision) {uiError='';update(s);}
    } catch (e) {report(String(e));}
    finally {polling=false;}
  }
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
      catalog=await api('library');
      for (const s of catalog) s.search=[s.title,s.origin,s.context,s.original,s.event,...s.also]
        .join(' ').toLocaleLowerCase();
      songs=new Map(catalog.map(s=>[s.id,s])); listChanged();
      const initial=await api('state'), plan=Arrangements.saved(songs.get(initial.song));
      if (plan && plan.id!==initial.arrangementId)
        await command('arrangement',{song:initial.song,arrangement:plan});
      const saved=savedCategories();
      if (saved!==null) await command('effectCategories',{categories:saved});
      await poll();
      async function refresh() {
        if (!document.hidden || state?.playing) await poll();
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
