"""Android adapter to the desktop sample-clock engine and stereo AudioTrack."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import struct
import numpy as np
from arrangement import normalize
from cockpit import CockpitEffects
from engine import Transport


def pcm(path):
    with path.open('rb') as f:
        head=f.read(12)
        if head[:4]!=b'RIFF' or head[8:]!=b'WAVE':raise ValueError('音频格式无效')
        channels=0
        while h:=f.read(8):
            tag,n=struct.unpack('<4sI',h);start=f.tell()
            if tag==b'fmt ':
                fmt,channels,rate,_,_,bits=struct.unpack('<HHIIHH',f.read(16))
                if fmt!=1 or channels not in (1,2) or rate!=48000 or bits!=16:raise ValueError('音频格式不匹配')
            if tag==b'data' and channels:
                if not n or n%(2*channels) or start+n>path.stat().st_size:raise ValueError('音频不完整')
                return np.memmap(path,dtype='<i2',mode='r',offset=start,shape=(n//(2*channels),channels))
            f.seek(start+n+(n&1))
    raise ValueError('缺少采样数据')


class Player:
    def __init__(self,root):
        self.root=Path(root)
        self.t=None
        self.effects=CockpitEffects()
        self.bank=CockpitEffects()
        self.loader=ThreadPoolExecutor(max_workers=1,thread_name_prefix='AC8 cue loader')
        self.loading=None
        self.request=((),False)
        self.failed=None
        self.effects_error=''
        self.label=''
        self.plan_id=''
        self.play_mode='ordered'
    def select(self,sid,plan_id):
        data=json.loads((self.root/'catalog.json').read_bytes());sid=str(sid)
        song=data['songs'][sid];plans=data['plans'].get(sid,{})
        plan=plans.get(plan_id)
        if plans and plan_id!='free' and plan is None:raise ValueError('编排尚未传输')
        t=Transport(data)
        t.audio={key:pcm(self.root/(data['media'][key]+'.wav')) for key in t.assets(song)}
        t.select(song,arrangement=normalize(plan,song,t.segs) if plan else None)
        t.play_mode=self.play_mode
        t.effects=self.effects
        if self.t:t.master=self.t.master;t.se_volume=self.t.se_volume
        t.playing=True;self.t=t;self.plan_id=plan_id;self.label=song['title']
    def repeat_one(self,enabled):
        self.play_mode='repeatOne' if enabled else 'ordered'
        if self.t:self.t.play_mode=self.play_mode

    def sections(self):
        t=self.t
        steps=t.arrangement['steps'] if t.arrangement else [{'segment':sid} for sid in t.song['order']]
        return json.dumps([dict(id=step['segment'],label=t.song['labels'].get(str(step['segment']),str(step['segment'])),
                                repeat=step.get('repeat',1),bars=step.get('bars')) for step in steps],ensure_ascii=False)
    def action(self,kind,value):
        t=self.t
        if not t:return
        if kind=='pause':t.playing=False
        elif kind=='resume':
            if t.finished:t.restart()
            t.playing=True
        elif kind=='queue' and not t.arrangement:t.queue(int(value))
        elif kind=='jump' and not t.arrangement:t.jump(int(value))
        elif kind=='lane' and not t.arrangement:
            lane=next(l for l in t.segs[t.key]['lanes'] if l['key']==value);t.toggle(lane)
        elif kind=='repeat' and not t.arrangement:t.change(int(value),t.settings.get(t.key,(None,None))[1])
        elif kind=='bars' and not t.arrangement:t.change(t.settings.get(t.key,(None,None))[0],int(value) or None)
        elif kind=='volume':t.master=max(0,min(1,float(value)))
    def configure_effects(self,enabled,bed,categories,volume):
        if self.t:self.t.se_volume=max(0,min(2,volume))
        names=categories.split(',') if enabled else ()
        request=(tuple(c for c in self.effects.SCENES if c in names),bool(bed))
        if request!=self.request:
            self.request=request
            self.failed=None
            self.effects_error=''
            self._sync_effects()

    def _load_samples(self,request):
        # The worker inherits the submitting thread's priority on Android.
        if hasattr(os,'setpriority'):os.setpriority(os.PRIO_PROCESS,0,5)
        cues=json.loads((self.root/'catalog.json').read_bytes())['cues']
        self.bank.prepare(lambda key:self.root/(cues[key]+'.wav'),*request)
        return (self.bank.samples.copy(),self.bank.distant_samples.copy(),
                self.bank.engine.layers,self.bank.engine.shots)

    def _sync_effects(self):
        fx=self.effects
        if self.loading and self.loading.done():
            try:
                fx.samples,fx.distant_samples,fx.engine.layers,fx.engine.shots=self.loading.result()
            except Exception as exc:
                if self.loading_request==self.request:
                    self.failed=self.request
                    self.effects_error='音效读取失败：'+str(exc)
            self.loading=None
        categories,bed=self.request
        ready=tuple(c for c in categories if all(key in fx.samples for key in fx.SCENE_CLIPS[c]))
        fx.set_categories(ready)
        fx.set_enabled(bool(ready))
        fx.engine.enabled=bed and bool(fx.engine.layers)
        if (ready!=categories or (bed and not fx.engine.layers)) and not self.loading and self.failed!=self.request:
            self.loading_request=self.request
            self.loading=self.loader.submit(self._load_samples,self.request)

    def close(self):
        self.loader.shutdown(wait=False,cancel_futures=True)

    def render(self,n,floating=False):
        if self.loading and self.loading.done():self._sync_effects()
        return self.t.render(n,floating=floating).tobytes()
    def state(self):
        t=self.t
        if not t:return '{}'
        s=t.segs[t.key]
        return json.dumps(dict(song=t.song['id'],title=self.label,plan=self.plan_id,playing=t.playing,finished=t.finished,
            segment=t.key,pos=t.position(),length=s['length'],run=t.run,repeat=t.limits(t.key)[0],bars=t.settings.get(t.key,(None,None))[1],
            bar=s['bar'],clock=t.clock,gridOrigin=t.active['origin']+s['entry'],sampleRate=48000,
            maxBars=max(1,(s['exit']-s['entry']+s['bar']-1)//s['bar']) if s['bar'] else 1,
            queued=t.queued,volume=t.master,effectsError=self.effects_error,
            stepIndex=t.step_index,bridge=bool(t.active.get('bridge')),loopFrom=t.arrangement['loopFrom'] if t.arrangement else None,
            lanes=[dict(key=l['key'],on=t.enabled(l)) for l in s['lanes']]),ensure_ascii=False)
