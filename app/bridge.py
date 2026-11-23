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
from cockpit import CLOUD_CLIPS, CockpitEffects
from arrangement import normalize

DOUBLE_CLICK_MS = ctypes.windll.user32.GetDoubleClickTime()

def add_native_media(score,entries):
    # Keep stable UI IDs outside the 32-bit Wwise object ID range.
    for key,entry in sorted(entries.items(),key=lambda pair:(not pair[1]['event'],pair[1]['title'],pair[0])):
        media_id=int(key.removeprefix('pak-'))
        sid=(2 if key.startswith('pak-') else 1)*2**32+media_id
        name=str(sid)
        frames=entry['samples']
        lane=dict(key=name+'.0',id=sid,variant=None,on=True,db=0,
                  clips=[dict(src='native-'+key,at=0,end=frames,offset=0,env=[])])
        score['segments'][name]=dict(id=sid,anc=[sid],length=frames,entry=0,exit=frames,marks=[],
                                     bar=0,beat=0,grid=0,gridOffset=0,tempo=0,lanes=[lane])
        score['songs'].append(dict(id=sid,title=entry['title'],event=entry['event'] or key,
                                   originalLabel=entry['origin'],bank='原生 7.1.4',context=entry['origin'],
                                   order=[sid],loops={name:1},next={name:None},labels={name:'全段'}))


class Player:
    effect_categories = CockpitEffects.SCENES

    def __init__(self, base, library=None, resolver=None, initial_song=None):
        data=json.loads((base/'library/score.json').read_text(encoding='utf8'))
        self.resolver=resolver
        if resolver:add_native_media(data,resolver.native)
        self.catalog={s['id']:s for s in data['songs']}
        self.defaults=json.loads((base/'library/arrangements.json').read_bytes())
        library=library or base/'library'
        self.effects_dir=library/'cockpit'
        # An older offline extraction can still play its original cockpit scenes.
        if resolver and resolver.source is None and not all(
                resolver.cached(Path('cockpit')/(name+'.wav'),resolver.spec['cues'][name]) for name in CLOUD_CLIPS):
            self.effect_categories=tuple(kind for kind in CockpitEffects.SCENES if kind!='cloud')
        self.transport=Transport(data)
        self.lock=threading.RLock()
        self.media=Media(base,library,resolver)
        self.media_lock=threading.Lock()
        self.spectrum=MixSpectrum(RATE)
        self.loader=ThreadPoolExecutor(1)
        self.generation=0
        self.ready=False
        self.autoplay=False
        self.has_next_song=False
        self.error=''
        self.progress=[0,0]
        self.peaks={}
        self.output=Output(self.transport,self.lock,self.output_error,self.spectrum.push,self.continue_cockpit)
        self.songs=self.metadata()
        if not isinstance(initial_song,int) or initial_song not in self.catalog:
            initial_song=next((s['id'] for s in self.catalog.values() if s['event']=='AKE_Bgm_Ms29B_Play'),next(iter(self.catalog)))
        self.select(initial_song,False)

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
                defaultPlan=self.defaults.get(str(s['id'])),sections=sections,
                singleLoops=s['bank']=='单段配乐' and s['loops'].get(str(s['order'][0]),1)==0))
        return rows

    def output_error(self,message):
        with self.lock:self.error=message

    def continue_cockpit(self):
        effects=self.transport.effects
        return (self.autoplay and not self.error and (not self.ready or self.has_next_song)
                and effects is not None and effects.needs_output)

    def resolve(self,sid,request):
        default=self.defaults.get(str(sid))
        if default is None:return None
        if request is not None and request.get('id')=='free':
            # Earlier desktop builds include the button's display name when clicked.
            if request.keys()-{'id','name'}:raise ValueError('自由编排不能包含段落')
            return None
        if request is None or request.get('id')=='default':request=default
        return normalize(request,self.catalog[sid],self.transport.segs)

    def select(self,sid,play,request=None,play_mode=None,has_next_song=False):
        plan=self.resolve(sid,request)
        with self.lock:
            self.generation+=1
            gen=self.generation
            t=self.transport
            t.playing=False;t.audio={}
            self.spectrum.reset()
            self.peaks.clear();self.ready=False;self.error='';self.autoplay=play
            self.has_next_song=has_next_song
            if not self.continue_cockpit():self.output.flush.set()
            t.select(self.catalog[sid],arrangement=plan)
            if play_mode is not None:t.play_mode=play_mode
            ids=t.assets(t.song)
            self.progress=[0,len(ids)]
        def work():
            try:
                def progress(i,n):
                    if self.generation==gen:self.progress=[i,n]
                with self.media_lock:
                    loaded=self.media.load(ids,progress,lambda:self.generation!=gen)
                with self.lock:
                    if loaded and self.generation==gen:
                        t.audio=dict(self.media.files);self.ready=True
                        self.progress=[len(ids),len(ids)];t.playing=self.autoplay
            except Exception as exc:
                with self.lock:
                    if self.generation==gen:self.error=str(exc)
        self.loader.submit(work)

    def visual_state(self):
        with self.lock:
            t=self.transport;s=t.segs[t.key]
            return dict(song=t.song['id'],playing=t.playing,ready=self.ready,error=self.error,
                        bar=s['bar'],clock=t.clock,gridOrigin=t.active['origin']+s['entry'],sampleRate=RATE,
                        spectrumEpoch=self.spectrum.epoch,spectrumHopMs=self.spectrum.hop_ms,
                        spectralFrames=list(self.spectrum.frames) if t.playing else [])

    def state(self):
        with self.lock:
            t=self.transport;s=t.segs[t.key];rep,bars=t.settings.get(t.key,(None,None))
            count,end=t.limits(t.key)
            if t.arrangement:
                rep=count
                if not t.active.get('bridge'):bars=t.arrangement['steps'][t.step_index].get('bars')
            visual=self.visual_state()
            frames=visual['spectralFrames']
            active=[lane for lane in s['lanes'] if t.enabled(lane)] or s['lanes']
            widths={t.audio[c['src']].shape[1] for lane in active for c in lane['clips']
                    if c['src'] in t.audio}
            source_kind=('原生' if widths=={12} else '混音') if widths else ''
            if widths=={2} and self.output.layout=='2.0':source_kind='原生'
            return dict(**visual,segment=t.key,position=t.position(),length=s['length'],entry=s['entry'],exit=s['exit'],
                        effectiveEnd=end,tempo=s['tempo'],queued=t.queued,bridge=bool(t.active.get('bridge')),
                        sectionQueue=list(t.section_queue),effects=bool(t.effects and t.effects.enabled),
                        effectCategories=self.effect_categories,
                        engineBed=bool(t.effects and t.effects.engine.enabled),seVolume=t.se_volume,
                        doubleClickMs=DOUBLE_CLICK_MS,outputLayout=self.output.layout,outputBackend=self.output.backend,
                        sourceRate=self.media.sample_rate if self.ready and t.audio else None,
                        outputRate=self.output.rate,outputFormat=self.output.sample_format,
                        sourceKind=source_kind,sourceChannels=sorted(widths),
                        arrangementId=t.arrangement['id'] if t.arrangement else 'free',stepIndex=t.step_index,
                        repeat=rep,bars=bars,effectiveRepeat=count,run=t.run,finished=t.finished,playMode=t.play_mode,
                        wantsPlay=self.autoplay,progress=self.progress,master=t.master,
                        enabled=[t.enabled(l) for l in s['lanes']],
                        levels=[x/255 for x in frames[-1][1]] if frames else [])

    def diagram(self):
        # Media.load evicts and explicitly closes old memory maps. Pin them with
        # the media lock while sampling the waveform, outside the output lock.
        if not self.ready:return None
        with self.media_lock:
            return self.sample_diagram()

    def sample_diagram(self):
        with self.lock:
            t=self.transport;s=t.segs[t.key];sid=t.key
            if not self.ready:return None
            if sid in self.peaks:return self.peaks[sid]
            song=t.song['id'];audio=t.audio;generation=self.generation
        size=200;indexes=np.linspace(0,max(0,s['length']-1),size*48,dtype=np.int64)
        lanes=[]
        for l in s['lanes']:
            vals=np.zeros(len(indexes))
            for clip in l['clips']:
                raw=audio[clip['src']]
                mask=(indexes>=clip['at'])&(indexes<clip['end'])
                ix=(indexes[mask]-clip['at']+clip['offset'])%len(raw)
                vals[mask]=np.maximum(vals[mask],np.max(np.abs(raw[ix].astype(np.float32)),axis=1))
            peaks=np.round(100*np.sqrt(np.minimum(1,vals.reshape(size,-1).max(axis=1)))).astype(int).tolist()
            lanes.append(dict(key=l['key'],id=l['id'],variant=l['variant'],peaks=peaks,
                  clips=[[max(0,c['at']),min(s['length'],c['end'])] for c in l['clips']]))
        result=dict(song=song,segment=sid,lanes=lanes,marks=s['marks'])
        with self.lock:
            if generation!=self.generation:return None
            self.peaks[sid]=result
        return result

    def prepare_effects(self,effects,categories,engine=False):
        if self.resolver:
            with self.resolver.cockpit() as cue:effects.prepare(cue,categories,engine)
        else:effects.prepare(self.effects_dir,categories,engine)

    def dispatch(self,msg):
        op=msg['op'];t=self.transport
        modes=('ordered','shuffle','repeatOne')
        mode=msg.get('playMode')
        if (mode is not None or op=='setPlayMode') and mode not in modes:
            raise ValueError('无效的播放模式')
        if op=='library':return self.songs
        if op=='state':
            with self.lock:
                if 'hasNextSong' in msg and msg.get('song')==t.song['id']:
                    # A queue edit after a terminal stop must not restart the bed.
                    if not t.finished or not msg['hasNextSong']:
                        self.has_next_song=bool(msg['hasNextSong'])
                return self.state()
        if op=='diagram':return self.diagram()
        if op=='select':
            self.select(int(msg['id']),bool(msg.get('play')),msg.get('arrangement'),mode,
                        bool(msg.get('hasNextSong')));return self.state()
        if op=='effectCategories':
            selected=msg.get('categories')
            if not isinstance(selected,list) or any(c not in CockpitEffects.SCENES for c in selected):
                raise ValueError('无效的音效类别')
            categories=tuple(c for c in CockpitEffects.SCENES if c in selected)
            if t.effects and t.effects.enabled:self.prepare_effects(t.effects,categories)
            with self.lock:
                self.effect_categories=categories
                if t.effects is not None:t.effects.set_categories(categories)
            return self.state()
        if op in ('effects','engineBed'):
            enabled=bool(msg['enabled'])
            effects=t.effects
            if enabled:
                if effects is None:
                    effects=CockpitEffects()
                    effects.set_categories(self.effect_categories)
                self.prepare_effects(effects,self.effect_categories if op=='effects' else (),op=='engineBed')
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
                if mode is not None:t.play_mode=mode
                t.playing=was_playing
                if not (t.effects and t.effects.needs_output and (was_playing or self.continue_cockpit())):
                    self.output.flush.set()
                self.spectrum.reset()
            elif op in ('setPlayMode','cyclePlayMode'):
                t.play_mode=mode if op=='setPlayMode' else modes[(modes.index(t.play_mode)+1)%len(modes)]
                if t.play_mode=='repeatOne' and t.finished and self.autoplay and self.ready and not self.error:
                    t.restart()
                    t.playing=True
            elif t.arrangement and op in ('queue','jump','nextSection','loop','restore'):return self.state()
            elif op=='volume':
                t.master=max(0,min(1,float(msg['value'])))
            elif op=='seVolume':
                t.se_volume=max(0,min(2,float(msg['value'])))
            elif op in ('pause','stop'):
                self.autoplay=t.playing=False
                self.output.flush.set()
                self.spectrum.reset()
                if op=='stop':t.restart()
            elif op in ('play','startPlayback'):
                if op=='startPlayback' and (t.playing or (not self.ready and self.autoplay and not self.error)):
                    return self.state()
                if self.error and not self.ready:
                    self.select(t.song['id'],True,t.arrangement or {'id':'free'},has_next_song=self.has_next_song)
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
        self.generation+=1;self.output.close();self.loader.shutdown(wait=True,cancel_futures=True)
        with self.media_lock:self.media.close()


if __name__=='__main__':
    from game_import import LibraryImport, choose_folder
    from mobile_transfer import Transfer
    from config_export import save_backup
    base=Path(__file__).resolve().parent.parent
    importer=LibraryImport(base)
    transfer=Transfer(base,importer)
    player=None
    revision=importer.revision
    try:
        for line in sys.stdin:
            try:
                request=json.loads(line)
                if request.get('op')=='quit':break
                op=request.get('op')
                if op=='importStatus':result=importer.status()
                elif op=='mobileOpen':result=transfer.open()
                elif op=='mobileStatus':result=transfer.status()
                elif op=='mobileSend':result=transfer.queue(request)
                elif op=='mobileClose':result=transfer.close()
                elif op=='saveBackup':result=save_backup(request['data'])
                elif op=='visualState':result=player.visual_state() if player else {}
                elif op=='importStart':
                    result=importer.start(request.get('game',''),request.get('extract',False),request.get('native',False))
                elif op=='importBrowse':result=choose_folder()
                else:
                    if importer.path is None:raise ValueError('请先设置游戏目录。')
                    if player is None:player=Player(base,importer.path,importer,request.get('initialSong'))
                    elif revision!=importer.revision and not player.ready:
                        t=player.transport
                        player.select(t.song['id'],player.autoplay,t.arrangement or {'id':'free'})
                    revision=importer.revision
                    result=player.dispatch(request)
                response={'result':result}
            except Exception as exc:response={'error':str(exc)}
            print(json.dumps(response,ensure_ascii=False,separators=(',',':'),allow_nan=False),flush=True)
    finally:
        transfer.close()
        importer.close()
        if player:player.close()

