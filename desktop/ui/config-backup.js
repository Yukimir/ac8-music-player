(() => {
  'use strict';
  const key = id => 'ac8.arrangements.v1.' + id;
  function collect(catalog, playlist) {
    const arrangements = {};
    for (const song of catalog) {
      if (!song.defaultPlan) continue;
      const items = Arrangements.plans(song).filter(p => p.id !== 'default');
      if (items.length) arrangements[song.id] = items;
    }
    return {format:'ac8-library-settings', version:1, arrangements,
      playlist:{items:playlist.items, mode:playlist.mode}};
  }
  function mergeItems(local, incoming) {
    // Existing local-only rows keep their relative order. Shared rows follow the source order.
    const ids = new Set(incoming.map(row => row.id));
    const overlap = local.filter(row => ids.has(row.id)).length;
    if (!overlap) return [...local, ...incoming];
    let cursor = 0, slots = overlap;
    return local.flatMap(row => {
      if (!ids.has(row.id)) return [row];
      if (--slots === 0) return incoming.slice(cursor);
      return [incoming[cursor++]];
    });
  }
  function restore(file, catalog, playlist) {
    if (file?.format !== 'ac8-library-settings' || file.version !== 1 ||
        !file.arrangements || typeof file.arrangements !== 'object' || Array.isArray(file.arrangements) ||
        !Array.isArray(file.playlist?.items) || !Object.hasOwn(Playlist.modes,file.playlist.mode))
      throw Error('请选择 AC8 播放器导出的 JSON 文件');
    const songs = new Map(catalog.map(s=>[s.id,s])), changes = new Map(), known = new Map();
    for (const [sid, incoming] of Object.entries(file.arrangements)) {
      const song = songs.get(Number(sid));
      if (!song?.defaultPlan || String(song.id)!==sid || !Array.isArray(incoming)) throw Error('备份中有无法识别的互动曲目');
      const existing = Arrangements.plans(song).filter(p=>p.id!=='default');
      const items = new Map(existing.map(p=>[p.id,p])), seen = new Set();
      for (const p of incoming) {
        if (!p || ['free','default'].includes(p.id) || seen.has(p.id) || typeof p.name!=='string' || !p.name.trim())
          throw Error('备份中的编排名称或 ID 无效');
        Arrangements.validate(song,p); seen.add(p.id); items.set(p.id,p);
      }
      known.set(song.id,items);
      const last = Arrangements.saved(song)?.id ?? 'default';
      changes.set(key(sid),JSON.stringify({last,items:[...items.values()]}));
    }
    const seen = new Set();
    for (const row of file.playlist.items) {
      const song = songs.get(row?.songId);
      if (!song || typeof row.id!=='string' || !row.id || seen.has(row.id) ||
          (song.defaultPlan ? row.planId!=='default' &&
            !(known.get(song.id)?.has(row.planId) || Arrangements.plans(song).some(p=>p.id===row.planId))
            : row.planId!==null)) throw Error('播放列表条目或引用的编排无效');
      seen.add(row.id);
    }
    const items = mergeItems(playlist.items,file.playlist.items);
    changes.set('ac8.playlist.v1',JSON.stringify({items,mode:file.playlist.mode}));
    const previous = new Map();
    try {
      for (const [k,v] of changes) {
        const before=localStorage.getItem(k);
        localStorage.setItem(k,v); previous.set(k,before);
      }
    } catch (e) {
      for (const [k,v] of previous) v===null ? localStorage.removeItem(k) : localStorage.setItem(k,v);
      throw e;
    }
    playlist.items=items; playlist.mode=file.playlist.mode; playlist.played.clear();
    Arrangements.reload();
  }
  window.ConfigBackup={collect,restore};
})();
