(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  function node(tag, text, cls) {
    const n=document.createElement(tag);
    if (text!==undefined) n.textContent=text;
    if (cls) n.className=cls;
    return n;
  }
  function button(text,action,label) {
    const b=node('button',text); b.type='button'; b.onclick=action;
    if (label) {b.title=label;b.setAttribute('aria-label',label);}
    return b;
  }
  function field(text,control) {
    const label=node('label',text);label.append(control);return label;
  }
  function number(value,min,max,change) {
    const n=node('input');n.type='number';n.min=min;n.max=max;n.value=value;
    n.oninput=()=>change(n.valueAsNumber);return n;
  }
  const labelOf = part => [part.label,part.name].filter(Boolean).join(' · ');
  const key = song => 'ac8.arrangements.v1.'+song.id;
  const copies = new Map(), drafts = new Map();
  let song=null, selected='', apply=null, changed=null, addToPlaylist=null, pending=false;
  const clone = value => JSON.parse(JSON.stringify(value));
  const sources={
    playlist:'顺序来自游戏音库内的播放列表。',
    states:'按游戏音库的命名状态与播放列表排列，关卡触发时机未经核实。',
    partial:'按已识别的音乐状态播放，部分关卡分支的触发顺序尚未核实。',
    recording:'按游戏音乐状态排列，部分分支用游戏内收录版的音频指纹补全。关卡触发时机未经核实。'
  };
  function notice(text) {
    $('arrNotice').textContent=text;$('arrNotice').hidden=!text;
  }
  function partFor(s,id) {return s.sections.find(part=>part.id===id);}
  function validate(s,p) {
    if (!p || typeof p.id!=='string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(p.id) ||
        !Array.isArray(p.steps) || !p.steps.length || p.steps.length>512) throw Error('请保留 1 至 512 个段落');
    if (p.loopFrom!=null && (!Number.isInteger(p.loopFrom) || p.loopFrom<0 ||
        p.loopFrom>=p.steps.length || !p.steps[p.steps.length-1].repeat)) throw Error('请检查末尾返回的位置和遍数');
    p.steps.forEach((step,i)=>{
      const part=partFor(s,step.segment), msg='第 '+(i+1)+' 段';
      if (!part) throw Error(msg+' 的段落无效');
      if (!Number.isInteger(step.repeat) || step.repeat<0 || step.repeat>999 ||
          (step.repeat===0 && i!==p.steps.length-1)) throw Error(msg+' 的遍数无效，只有末段可以无限循环');
      if (step.bars!=null && (!Number.isInteger(step.bars) || step.bars<1 || step.bars>part.bars))
        throw Error(msg+' 的每遍长度应为 1 至 '+part.bars+' 小节');
      function mask(keys) {
        if (!Array.isArray(keys)) throw Error(msg+' 的分轨无效');
        const variants=new Set();
        for (const k of keys) {
          const lane=part.lanes.find(l=>l.key===k);
          if (!lane || (lane.variant!=null && variants.has(lane.id))) throw Error(msg+' 的分轨无效');
          if (lane.variant!=null) variants.add(lane.id);
        }
      }
      mask(step.lanes);
      if (!Array.isArray(step.changes) || step.changes.length>256) throw Error(msg+' 的分轨节点过多');
      const positions=new Set();
      for (const e of step.changes) {
        if (!Number.isInteger(e.run) || e.run<1 || e.run>(step.repeat || 999) ||
            !Number.isInteger(e.bar) || e.bar<1 || e.bar>(step.bars || part.bars)) throw Error(msg+' 的节点超出遍数或小节范围');
        if (positions.has(e.run+'/'+e.bar)) throw Error(msg+' 有重复的分轨节点');
        positions.add(e.run+'/'+e.bar);mask(e.lanes);
      }
    });
    return p;
  }
  function store(s) {
    if (copies.has(s.id)) return copies.get(s.id);
    let data={last:'default',items:[]};
    try {
      const raw=JSON.parse(localStorage.getItem(key(s)));
      if (raw && Array.isArray(raw.items)) {
        for (const p of raw.items) {
          try {
            if (p.id!=='free' && p.id!=='default' && typeof p.name==='string' && p.name.trim() &&
                !data.items.some(item=>item.id===p.id)) data.items.push(validate(s,p));
          } catch {}
        }
        if (raw.last==='free' || data.items.some(p=>p.id===raw.last)) data.last=raw.last;
      }
    } catch {}
    copies.set(s.id,data);return data;
  }
  function persist(s,data) {
    localStorage.setItem(key(s),JSON.stringify(data));copies.set(s.id,data);
  }
  function planFor(s,id) {
    if (id==='free') return {id:'free'};
    return store(s).items.find(p=>p.id===id) || s.defaultPlan;
  }
  function plans(s) {return s?.defaultPlan?[s.defaultPlan,...store(s).items]:[];}
  function saved(s,preferredId) {
    if (!s?.defaultPlan) return undefined;
    return planFor(s,typeof preferredId==='string'?preferredId:store(s).last);
  }
  function startStep(part) {
    return {segment:part.id,repeat:1,lanes:part.lanes.filter(l=>l.on).map(l=>l.key),changes:[]};
  }
  function edit(plan,copy=false) {
    if (drafts.has(song.id)) {notice('请先保存或取消当前编辑。');return;}
    let p=plan?clone(plan):{steps:[startStep(song.sections[0])],loopFrom:null};
    if (!plan || copy) {
      p.id='custom-'+Array.from(crypto.getRandomValues(new Uint32Array(4)),x=>x.toString(16).padStart(8,'0')).join('');
      p.name=plan?((plan.name || '默认编排')+' 副本'):'我的编排';
      const base=p.name;
      for (let i=2;store(song).items.some(item=>item.name===p.name);i++) p.name=base+' '+i;
    }
    delete p.source;
    delete p.stopEvents;
    drafts.set(song.id,{plan:p,index:0});notice('');drawEditor();
    $('arrEditor').querySelector('input').focus();
  }
  async function activate(s,p,data) {
    if (pending) return false;
    pending=true;
    const previous=store(s);
    let saved=false,accepted=false;
    try {
      persist(s,{...data,last:p.id});saved=true;
      const result=await apply(s.id,p);
      accepted=result?.song===s.id && result?.arrangementId===p.id;
      return accepted;
    } catch(e) {
      notice('未能保存编排到本机。'+e.message);return false;
    } finally {
      if (saved && !accepted) {
        try {persist(s,previous);} catch(e) {notice('未能恢复上次选择。'+e.message);}
      }
      pending=false;
      if (song===s) drawList();
      if (accepted) changed?.(s);
    }
  }
  async function save() {
    if (pending) return;
    const s=song, draft=drafts.get(s.id), p=clone(draft.plan);
    p.name=p.name.trim();
    if (!p.name) {notice('请填写编排名。');return;}
    try {validate(s,p);} catch(e) {notice(e.message);return;}
    const data=store(s),items=[...data.items],i=items.findIndex(item=>item.id===p.id);
    if (i===-1) items.push(p);else items[i]=p;
    if (await activate(s,p,{...data,items})) {
      drafts.delete(s.id);
      if (song===s) {drawEditor();drawList();notice('已保存并选用「'+p.name+'」。');}
    }
  }
  function drawList() {
    const data=store(song), list=$('arrList'), preview=$('arrPreview');
    list.replaceChildren();preview.replaceChildren();
    for (const p of [planFor(song,'free'),...plans(song)]) {
      const row=button('',async()=>{
        if (p.id===selected || pending) return;
        notice('');await activate(song,p,data);
      });
      row.dataset.arrangement=p.id;row.setAttribute('aria-pressed',String(p.id===selected));
      const title=p.id==='free'?'自由编排':p.name || '默认编排';
      row.append(node('span',title),node('small',p.id==='free'?'手动选段':p.id==='default'?'只读':p.steps.length+' 段'));
      list.append(row);
    }
    if (selected==='free') return;
    const p=planFor(song,selected);
    const actions=node('div',undefined,'arr-bar');
    const details=node('details');
    const total=p.steps.length;
    const summary=node('summary','播放顺序 · '+total+' 段'+(p.loopFrom==null?'':(
      p.loopFrom===total-1?' · 末段无限循环':' · 第 '+(p.loopFrom+1)+'–'+total+' 段整组循环 ∞')));
    const order=node('ol');
    for (const step of p.steps) {
      const part=partFor(song,step.segment);
      const item=node('li'),detail=node('details');
      const laneNames=keys=>part.lanes.flatMap((l,i)=>keys.includes(l.key)?[String(i+1).padStart(2,'0')]:[]).join('、') || '全关';
      detail.append(node('summary',labelOf(part)+'　× '+(step.repeat || '∞')+(step.bars?'　·　每遍 '+step.bars+' 小节':'')),
        node('p','进入段落　·　分轨 '+laneNames(step.lanes)));
      for (const e of step.changes) detail.append(node('p','第 '+e.run+' 遍，第 '+e.bar+' 小节　·　分轨 '+laneNames(e.lanes)));
      item.append(detail);order.append(item);
    }
    details.append(summary,order);
    if (p.source) details.append(node('p',sources[p.source]));
    if (p.stopEvents?.length) details.append(node('p','游戏收尾事件　'+p.stopEvents.join('、')));
    actions.append(button('加入播放列表',()=>addToPlaylist(song,p.id)),button('复制',()=>edit(p,true)));
    if (p.id!=='default') {
      actions.append(button('编辑',()=>edit(p)),button('删除',()=>{
        const s=song;
        actions.replaceChildren(button('确认删除',async()=>{
          if (pending) return;
          const data=store(s);
          if (await activate(s,s.defaultPlan,{...data,items:data.items.filter(item=>item.id!==p.id)})) {
            if (drafts.get(s.id)?.plan.id===p.id) drafts.delete(s.id);
            if (song===s) {drawEditor();notice('已删除「'+p.name+'」。');}
          }
        }),button('取消',drawList));
      }));
    }
    preview.append(details,actions);
  }
  function switches(part,config) {
    const group=node('div',undefined,'arr-lanes');
    part.lanes.forEach((lane,i)=>{
      const c=node('input');c.type='checkbox';c.checked=config.lanes.includes(lane.key);c.value=lane.key;
      c.onchange=()=>{
        if (c.checked && lane.variant!=null) {
          const otherKeys=part.lanes.filter(l=>l.id===lane.id && l.key!==lane.key).map(l=>l.key);
          for (const other of group.querySelectorAll('input')) if (otherKeys.includes(other.value)) other.checked=false;
        }
        config.lanes=[...group.querySelectorAll('input:checked')].map(n=>n.value);
      };
      const l=node('label');l.append(c,document.createTextNode('轨 '+String(i+1).padStart(2,'0')));group.append(l);
    });
    return group;
  }
  function drawEditor() {
    const host=$('arrEditor'), draft=drafts.get(song.id);host.replaceChildren();
    if (!draft) return;
    const p=draft.plan,i=draft.index,step=p.steps[i],part=partFor(song,step.segment);
    function reorder(to) {
      const loop=p.steps[p.loopFrom ?? -1];p.steps.splice(i,1);p.steps.splice(to,0,step);
      if (loop) p.loopFrom=p.steps.indexOf(loop);
      draft.index=to;drawEditor();
    }
    const name=node('input');name.type='text';name.maxLength=64;name.value=p.name;name.oninput=()=>{p.name=name.value;};
    host.append(field('编排名',name));
    const nav=node('div',undefined,'arr-steps');
    p.steps.forEach((st,j)=>{
      const b=button((j+1)+' · '+labelOf(partFor(song,st.segment)),()=>{draft.index=j;drawEditor();});
      b.setAttribute('aria-pressed',String(i===j));nav.append(b);
    });
    nav.append(button('＋ 添加段落',()=>{
      if (p.steps.length>=512) return;
      if (!p.steps.at(-1).repeat) p.steps.at(-1).repeat=1;
      p.steps.push(startStep(song.sections[0]));draft.index=p.steps.length-1;drawEditor();
    }));host.append(nav);
    const controls=node('div',undefined,'arr-bar'),choose=node('select');
    song.sections.forEach(sec=>choose.add(new Option(labelOf(sec),sec.id)));
    choose.value=step.segment;choose.onchange=()=>{
      p.steps[i]=startStep(partFor(song,Number(choose.value)));p.steps[i].repeat=step.repeat;drawEditor();
    };
    const up=button('前移',()=>reorder(i-1)),down=button('后移',()=>reorder(i+1));
    up.disabled=i===0;down.disabled=i===p.steps.length-1;
    const remove=button('移除',()=>{
      const loop=p.steps[p.loopFrom ?? -1];p.steps.splice(i,1);
      p.loopFrom=loop && loop!==step?p.steps.indexOf(loop):null;
      draft.index=Math.min(i,p.steps.length-1);drawEditor();
    });remove.disabled=p.steps.length===1;
    controls.append(field('段落',choose),up,down,remove);host.append(controls);
    const repeats=number(step.repeat,0,999,v=>{step.repeat=v;});
    const length=number(step.bars ?? '',1,part.bars,v=>{step.bars=Number.isNaN(v)?null:v;});
    length.placeholder='完整';
    const timing=node('div',undefined,'arr-bar');
    timing.append(field(i===p.steps.length-1?'播放遍数（0 为无限）':'播放遍数',repeats),field('每遍小节',length));
    host.append(timing,node('h4','进入段落时开启'),switches(part,step));
    step.changes.forEach((e,j)=>{
      const block=node('div',undefined,'arr-event'),bar=node('div',undefined,'arr-bar');
      bar.append(field('第几遍',number(e.run,1,step.repeat || 999,v=>{e.run=v;})),
        field('第几小节',number(e.bar,1,step.bars || part.bars,v=>{e.bar=v;})),
        button('移除节点',()=>{step.changes.splice(j,1);drawEditor();}));
      block.append(bar,switches(part,e));host.append(block);
    });
    host.append(button('＋ 分轨节点',()=>{
      const used=new Set(step.changes.map(e=>e.run+'/'+e.bar));
      for (let run=1;run<=(step.repeat || 999);run++) for (let bar=1;bar<=(step.bars || part.bars);bar++) {
        if ((run===1 && bar===1) || used.has(run+'/'+bar)) continue;
        const prior=step.changes.filter(e=>e.run<run || e.run===run && e.bar<bar)
          .sort((a,b)=>b.run-a.run || b.bar-a.bar)[0] || step;
        step.changes.push({run,bar,lanes:[...prior.lanes]});drawEditor();return;
      }
      notice('当前遍数的小节已用完，可增加播放遍数后再添加节点。');
    }));
    const tail=node('select');tail.add(new Option('结束播放',''));
    p.steps.forEach((_,j)=>tail.add(new Option('返回第 '+(j+1)+' 段',String(j))));
    tail.value=p.loopFrom==null?'':String(p.loopFrom);
    tail.onchange=()=>{p.loopFrom=tail.value===''?null:Number(tail.value);};
    const actions=node('div',undefined,'arr-bar');
    actions.append(field('编排末尾',tail),button('保存并选用',save),button('取消',()=>{
      drafts.delete(song.id);drawEditor();notice('');
    }));host.append(actions);
  }
  window.Arrangements={
    validate, reload(){copies.clear();selected='';if(song){drawList();drawEditor();}},
    saved, plans, connect(cb,onChange,onAdd){
      apply=cb;changed=onChange;addToPlaylist=onAdd;$('arrNew').onclick=()=>edit(null);
    },
    show(s,state) {
      const panel=$('arrangements');panel.hidden=!s.defaultPlan;
      if (panel.hidden) {song=null;return;}
      const changed=s!==song, id=state.arrangementId || 'default';
      if (changed || selected!==id) {
        song=s;selected=id;drawList();if (changed) {drawEditor();notice('');}
      }
      const p=planFor(song,id);
      $('arrPosition').textContent=p.steps?'第 '+(state.stepIndex+1)+' / '+p.steps.length+' 段':'';
    }
  };
})();
