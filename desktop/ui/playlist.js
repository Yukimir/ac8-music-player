(() => {
  'use strict';
  const storageKey='ac8.playlist.v1';
  const modes={ordered:'顺序播放',loopList:'列表循环',shuffle:'随机播放',repeatOne:'单曲循环'};

  class Playlist {
    constructor(storage) {
      this.storage=storage;
      this.items=[];
      this.mode='ordered';
      this.leave();
      let data;
      try {data=JSON.parse(storage.getItem(storageKey));} catch {return;}
      if (!Array.isArray(data?.items)) return;
      const ids=new Set();
      this.items=data.items.filter(item=>{
        if (!item || typeof item.id!=='string' || !item.id || ids.has(item.id) ||
            !Number.isInteger(item.songId) || !(item.planId===null || typeof item.planId==='string')) return false;
        ids.add(item.id);return true;
      });
      if (Object.hasOwn(modes,data.mode)) this.mode=data.mode;
    }
    save(items,mode=this.mode) {
      this.storage.setItem(storageKey,JSON.stringify({items,mode}));
      this.items=items;this.mode=mode;
    }
    add(songId,planId) {
      const id='entry-'+crypto.randomUUID();
      this.save([...this.items,{id,songId,planId}]);
    }
    keep(predicate) {
      const retained=this.items.filter(predicate);
      if (retained.length===this.items.length) return;
      const position=this.items.findIndex(item=>item.id===this.current);
      const gap=this.items.slice(0,position<0?this.gap:position).filter(predicate).length;
      this.save(retained);
      this.gap=gap;
    }
    move(id,delta) {
      const items=this.items.slice(), from=items.findIndex(item=>item.id===id), to=from+delta;
      if (from<0 || to<0 || to>=items.length) return;
      items.splice(to,0,items.splice(from,1)[0]);this.save(items);
    }
    setMode(mode) {
      if (!Object.hasOwn(modes,mode)) return;
      this.save(this.items,mode);
      this.played=new Set([this.current]);
    }
    leave() {
      this.current=null;this.gap=0;this.played=new Set();
    }
    start(id,fresh) {
      if (fresh || this.items.every(item=>this.played.has(item.id))) this.played.clear();
      this.current=id;this.played.add(id);
    }
    get active() {return this.current!==null;}
    engineMode() {
      return this.mode==='repeatOne' && this.items.some(item=>item.id===this.current)?'repeatOne':'ordered';
    }
    next(delta,automatic=false) {
      const items=this.items;
      if (!items.length) return null;
      const current=items.findIndex(item=>item.id===this.current);
      if (this.mode==='shuffle') {
        const others=items.filter(item=>item.id!==this.current);
        const unplayed=others.filter(item=>!this.played.has(item.id));
        const pool=unplayed.length?unplayed:others.length?others:items;
        return pool[Math.floor(Math.random()*pool.length)];
      }
      if (automatic && this.mode==='repeatOne' && current>=0) return items[current];
      let next=current<0?this.gap+(delta<0?-1:0):current+delta;
      if (this.mode==='loopList') next=(next+items.length)%items.length;
      return items[next] || null;
    }
  }
  // The transport and tray share one shuffle draw until the playing queue changes.
  class TrackNavigation {
    constructor() {this.reset();}
    reset() {this.back=[];this.forward=[];this.view=null;}
    neighbors(items,current,mode,choose) {
      const old=this.view;
      if (old && old.items===items && old.current===current && old.mode===mode) return old;
      if (old && old.mode!==mode) this.reset();
      let previous, following;
      if (mode==='shuffle') {
        const byId=new Map(items.map(item=>[item.id,item]));
        this.back=this.back.filter(id=>byId.has(id));
        this.forward=this.forward.filter(id=>byId.has(id));
        previous=byId.get(this.back.at(-1)) ?? null;
        following=byId.get(this.forward.at(-1)) ?? choose(1);
      } else {
        previous=choose(-1);following=choose(1);
      }
      return this.view={items,current,mode,previous,following};
    }
    moved(delta) {
      const view=this.view;
      if (!delta || !view) {this.reset();return;}
      if (view.mode==='shuffle') {
        const [from,to]=delta>0?[this.forward,this.back]:[this.back,this.forward];
        from.pop();
        const target=delta>0?view.following:view.previous;
        if (target?.id!==view.current && view.items.some(item=>item.id===view.current))
          to.push(view.current);
      }
      this.view=null;
    }
  }
  window.Playlist=Object.assign(Playlist,{modes,TrackNavigation});
})();
