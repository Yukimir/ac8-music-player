"""Local JSON-lines interface to the existing sample-clock player, used by Tauri."""
import ctypes
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from device import Output
from spectrum import MixSpectrum
from engine import Media, Transport, RATE
from cockpit import CockpitEffects
from arrangement import normalize

DOUBLE_CLICK_MS = ctypes.windll.user32.GetDoubleClickTime()

class Player:
    effect_categories = CockpitEffects.SCENES

    def __init__(self, base, library=None):
        data=json.loads((base/'library/score.json').read_text(encoding='utf8'))
        self.catalog={s['id']:s for s in data['songs']}
        self.defaults=json.loads((base/'library/arrangements.json').read_bytes())
        library=library or base/'library'
        self.effects_dir=library/'cockpit'
        self.transport=Transport(data)
        self.lock=threading.RLock()
        self.media=Media(base,library)
        self.spectrum=MixSpectrum(RATE)
        self.output=Output(self.transport,self.lock,self.output_error,self.spectrum.push)
        self.loader=ThreadPoolExecutor(1)
        self.generation=0
        self.ready=False
        self.autoplay=False
        self.error=''
        self.progress=[0,0]
        self.peaks={}
        self.songs=self.metadata()
        preferred=next((s for s in self.catalog.values() if s['event']=='AKE_Bgm_Ms29B_Play'),next(iter(self.catalog.values())))
        self.select(preferred['id'],False)

    @staticmethod
    def origin(song):
        original=song.get('originalLabel','')
        context=song.get('context','')
        if original and not original.startswith('配乐 ') and original!=song['title']:
            if context.startswith('任务 ') and original.startswith('任务 '):
                number=original[3:5]
                if number==context[3:5]:
                    return original
                return context.split(' · ')[0]+' · 素材 '+original.removeprefix('任务 ')
            if context=='序章':return '序章 · 素材 '+original.removeprefix('任务 ')
            return original
        return context or original or song['bank']

    def metadata(self):
        # Attach the same mission provenance to the in-game jukebox cut.
        origins={}
        for s in self.catalog.values():
            if s['bank']=='互动配乐' and s.get('named'):
                for name in [s['title'],*s.get('relatedTitles',[])]:
                    origins.setdefault(name,self.origin(s))
        rows=[]
        for s in self.catalog.values():
            origin=self.origin(s)
            if origin=='游戏内音乐播放器':
                hint=origins.get(s['title'])
                if not hint:
                    hint=next((self.origin(o) for o in self.catalog.values() if o['title']==s['title']
                               and o.get('context') not in (origin,'另一版本','共用音源')
                               and not o['originalLabel'].startswith('配乐 ')),None)
                origin=(hint+' · ' if hint else '')+'游戏内音乐播放器'
            elif origin.startswith('分支片段') or origin in ('另一版本','共用音源'):
                origin+=' · '+str(s['id'])
            sections=[]
            for sid in s['order']:
                seg=self.transport.segs[sid]
                part=dict(id=sid,label=s['labels'][str(sid)],name=s.get('sectionTitles',{}).get(str(sid),''),
                          length=seg['length'])
                if str(s['id']) in self.defaults:
                    part['bars']=max(1,(seg['exit']-seg['entry']+seg['bar']-1)//seg['bar']) if seg['bar'] else 1
                    part['lanes']=[{k:l[k] for k in ('key','id','variant','on')} for l in seg['lanes']]
                sections.append(part)
            rows.append(dict(id=s['id'],title=s['title'],origin=origin,context=s.get('context',''),
                original=s.get('originalLabel',''),event=s['event'],bank=s['bank'],also=s.get('relatedTitles',[]),
                defaultPlan=self.defaults.get(str(s['id'])),sections=sections))
        return rows

    def output_error(self,message):
        with self.lock:self.error=message

    def resolve(self,sid,request):
        default=self.defaults.get(str(sid))
        if default is None:return None
        if request is not None and request.get('id')=='free':
            # Earlier desktop builds include the button's display name when clicked.
            if request.keys()-{'id','name'}:raise ValueError('自由编排不能包含段落')
            return None
        if request is None or request.get('id')=='default':request=default
        return normalize(request,self.catalog[sid],self.transport.segs)

    def select(self,sid,play,request=None):
        plan=self.resolve(sid,request)
        with self.lock:
            self.generation+=1
            gen=self.generation
            t=self.transport
            t.playing=False;t.audio={}
            self.output.flush.set()
            self.spectrum.reset()
            self.peaks.clear();self.ready=False;self.error='';self.autoplay=play
            t.select(self.catalog[sid],arrangement=plan)
            ids=t.assets(t.song)
            self.progress=[0,len(ids)]
        def work():
            try:
                def progress(i,n):
                    if self.generation==gen:self.progress=[i,n]
                loaded=self.media.load(ids,progress,lambda:self.generation!=gen)
                with self.lock:
                    if loaded and self.generation==gen:
                        t.audio=dict(self.media.files);self.ready=True
                        self.progress=[len(ids),len(ids)];t.playing=self.autoplay
            except Exception as exc:
                with self.lock:
                    if self.generation==gen:self.error=str(exc)
        self.loader.submit(work)

    def state(self):
        with self.lock:
            t=self.transport;s=t.segs[t.key];rep,bars=t.settings.get(t.key,(None,None))
            count,end=t.limits(t.key)
            if t.arrangement:
                rep=count
                if not t.active.get('bridge'):bars=t.arrangement['steps'][t.step_index].get('bars')
            frames=list(self.spectrum.frames) if t.playing else []
            return dict(song=t.song['id'],segment=t.key,position=t.position(),length=s['length'],entry=s['entry'],exit=s['exit'],
                        effectiveEnd=end,bar=s['bar'],tempo=s['tempo'],queued=t.queued,bridge=bool(t.active.get('bridge')),
                        sectionQueue=list(t.section_queue),effects=bool(t.effects and t.effects.enabled),
                        effectCategories=self.effect_categories,
                        engineBed=bool(t.effects and t.effects.engine.enabled),seVolume=t.se_volume,
                        doubleClickMs=DOUBLE_CLICK_MS,
                        arrangementId=t.arrangement['id'] if t.arrangement else 'free',stepIndex=t.step_index,
                        repeat=rep,bars=bars,effectiveRepeat=count,run=t.run,playing=t.playing,finished=t.finished,
                        ready=self.ready,wantsPlay=self.autoplay,progress=self.progress,error=self.error,master=t.master,
                        enabled=[t.enabled(l) for l in s['lanes']],spectrumEpoch=self.spectrum.epoch,
                        spectralFrames=frames,levels=[x/255 for x in frames[-1][1]] if frames else [])

    def diagram(self):
        with self.lock:
            t=self.transport;s=t.segs[t.key];sid=t.key
            if not self.ready:return None
            if sid in self.peaks:return self.peaks[sid]
            size=200;indexes=np.linspace(0,max(0,s['length']-1),size*48,dtype=np.int64)
            lanes=[]
            for l in s['lanes']:
                vals=np.zeros(len(indexes))
                for clip in l['clips']:
                    raw=t.audio[clip['src']]
                    mask=(indexes>=clip['at'])&(indexes<clip['end'])
                    ix=(indexes[mask]-clip['at']+clip['offset'])%len(raw)
                    vals[mask]=np.maximum(vals[mask],np.max(np.abs(raw[ix].astype(np.float32)),axis=1))
                peaks=np.round(100*np.sqrt(vals.reshape(size,-1).max(axis=1)/32768)).astype(int).tolist()
                lanes.append(dict(key=l['key'],id=l['id'],variant=l['variant'],peaks=peaks,
                      clips=[[max(0,c['at']),min(s['length'],c['end'])] for c in l['clips']]))
            result=dict(song=t.song['id'],segment=sid,lanes=lanes,marks=s['marks'])
            self.peaks[sid]=result
            return result

    def dispatch(self,msg):
        op=msg['op'];t=self.transport
        if op=='library':return self.songs
        if op=='state':return self.state()
        if op=='diagram':return self.diagram()
        if op=='select':
            self.select(int(msg['id']),bool(msg.get('play')),msg.get('arrangement'));return self.state()
        if op=='effectCategories':
            selected=msg.get('categories')
            if not isinstance(selected,list) or any(c not in CockpitEffects.SCENES for c in selected):
                raise ValueError('无效的音效类别')
            with self.lock:
                self.effect_categories=tuple(c for c in CockpitEffects.SCENES if c in selected)
                if t.effects is not None:t.effects.set_categories(self.effect_categories)
            return self.state()
        if op in ('effects','engineBed'):
            enabled=bool(msg['enabled'])
            effects=t.effects
            if enabled and effects is None:
                effects=CockpitEffects(self.effects_dir)
                effects.set_categories(self.effect_categories)
            with self.lock:
                if effects is not None:
                    t.effects=effects
                    if op=='effects':effects.set_enabled(enabled)
                    else:effects.engine.enabled=enabled
            return self.state()
        with self.lock:
            if msg.get('song',t.song['id'])!=t.song['id'] or msg.get('segment',t.key)!=t.key:
                return self.state()
            if op=='arrangement':
                plan=self.resolve(t.song['id'],msg.get('arrangement'))
                was_playing=t.playing
                t.select(t.song,arrangement=plan)
                t.playing=was_playing
                self.output.flush.set();self.spectrum.reset()
            elif t.arrangement and op in ('queue','jump','nextSection','loop','restore'):return self.state()
            elif op=='volume':
                t.master=max(0,min(1,float(msg['value'])))
            elif op=='seVolume':
                t.se_volume=max(0,min(2,float(msg['value'])))
            elif op=='play':
                if self.error and not self.ready:
                    self.select(t.song['id'],True,t.arrangement or {'id':'free'})
                elif self.ready:
                    self.spectrum.reset()
                    if t.finished:
                        if t.arrangement:t.restart()
                        else:
                            first=t.section_queue.popleft() if t.section_queue else t.song['order'][0]
                            t.start(first,keep_queue=True)
                    self.error='';t.playing=not t.playing;self.autoplay=t.playing
                else:self.autoplay=not self.autoplay
            elif not self.ready:return self.state()
            elif op=='queue':t.queue(int(msg['id']))
            elif op=='jump':
                if t.jump(int(msg['id'])):
                    self.output.flush.set();self.spectrum.reset()
            elif op=='nextSection':
                order=t.song['order']
                target=t.song['next'].get(str(t.key))
                if target is None:
                    target=order[(order.index(t.key)+1)%len(order)] if t.key in order else order[0]
                if t.playing:t.queue(target)
                else:t.jump(target)
            elif op=='seek':
                if t.key in t.song['order']:
                    pos=min(t.segs[t.key]['length']-1,max(0,int(msg['frame'])))
                    if t.arrangement and t.arrangement['steps'][t.step_index].get('bars'):
                        pos=min(pos,t.limits(t.key)[1]-1)
                    t.start(t.key,pos,keep_queue=True,step_index=t.step_index,run=t.run if t.arrangement else 1)
                    self.output.flush.set();self.spectrum.reset()
            elif op=='toggle':
                lane=next((l for l in t.segs[t.key]['lanes'] if l['key']==msg['key']),None)
                if lane is not None:t.toggle(lane)
            elif op=='loop':
                rep=msg.get('repeat');bars=msg.get('bars')
                if rep not in (None,0,1,2,4,8,16):raise ValueError('无效的循环次数')
                if bars not in (None,1,2,4,8,16,32,64,128):raise ValueError('无效的小节数')
                if not t.active.get('bridge'):t.change(rep,bars)
            elif op=='restore':
                t.settings.pop(t.key,None)
                for l in t.segs[t.key]['lanes']:t.set_lane(l,l['on'])
                t.replan()
            else:raise ValueError('未知播放器操作')
        return self.state()

    def close(self):
        self.generation+=1;self.output.close();self.loader.shutdown(wait=True,cancel_futures=True);self.media.close()


if __name__=='__main__':
    from game_import import LibraryImport, choose_folder
    base=Path(__file__).resolve().parent.parent
    importer=LibraryImport(base)
    player=None
    try:
        for line in sys.stdin:
            try:
                request=json.loads(line)
                if request.get('op')=='quit':break
                op=request.get('op')
                if op=='importStatus':result=importer.status()
                elif op=='importStart':result=importer.start(request.get('game',''))
                elif op=='importBrowse':result=choose_folder()
                else:
                    if importer.path is None:raise ValueError('请先从本机游戏导入音频。')
                    if player is None:player=Player(base,importer.path)
                    result=player.dispatch(request)
                response={'result':result}
            except Exception as exc:response={'error':str(exc)}
            print(json.dumps(response,ensure_ascii=False,separators=(',',':'),allow_nan=False),flush=True)
    finally:
        importer.close()
        if player:player.close()

