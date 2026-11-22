"""Sample-clock playback for the extracted AC8 Wwise music objects."""
import copy
from collections import deque
from contextlib import nullcontext
from surround import FULL, native_matrix
import math
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np

RATE = 48000
MANUAL_FADE_FRAMES = round(.3 * RATE)
DEFAULT_RULE = dict(sync=7, cue=0, post=1, pre=1, out=[0,0,4], inc=[0,0,4], entry=0, dcue=0)


class Media:
    def __init__(self, root, library=None, resolver=None):
        self.root = root
        self.resolver = resolver
        self.library = library or root/'library'
        self.temp = tempfile.TemporaryDirectory(prefix="AC8-audio-")
        self.files = {}
        self.sample_rate = None

    def load(self, ids, progress=lambda *args: None, cancelled=lambda: False):
        keep = set(ids)
        for key in list(self.files):
            if key not in keep:
                self.files.pop(key)._mmap.close()
                (Path(self.temp.name) / (key + ".wav")).unlink(missing_ok=True)
        for i, key in enumerate(sorted(keep)):
            if cancelled(): return False
            progress(i, len(keep))
            if key in self.files: continue
            path = Path(self.temp.name) / (key + ".wav")
            source = (self.resolver.music(key,path.parent) if self.resolver else
                      nullcontext(self.library/'wem'/(key+'.wem')))
            with source as wem:
                proc = subprocess.run([self.root/'decoder/vgmstream-cli.exe','-i','-W','4','-o',path,wem],
                                      capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if proc.returncode or not path.exists():
                raise ValueError(f"无法解码音频 {key}")
            with path.open('rb') as f:
                if f.read(4) != b'RIFF': raise ValueError("音频缓存格式错误")
                f.seek(12)
                channels = 0
                while True:
                    block = f.read(8)
                    if len(block)!=8: raise ValueError(f"音频 {key} 缺少采样数据")
                    tag, size = struct.unpack('<4sI',block)
                    if tag == b'data': break
                    if tag == b'fmt ':
                        fmt = f.read(size)
                        codec, channels, rate = struct.unpack_from('<HHI',fmt)
                        if codec==0xfffe and len(fmt)>=40 and fmt[24:40]==bytes.fromhex('0300000000001000800000aa00389b71'):
                            if channels==12 and struct.unpack_from('<I',fmt,20)[0]!=FULL:
                                raise ValueError('原生素材的声道位置不匹配')
                            codec=3
                        expected=(12,) if key.startswith('native-') else (1,2)
                        if codec!=3 or rate!=RATE or channels not in expected or struct.unpack_from('<H',fmt,14)[0]!=32:
                            raise ValueError("不支持的解码格式")
                    else: f.seek(size,1)
                    if size%2: f.seek(1,1)
                off = f.tell()
                if not channels or not size or size%(4*channels) or off+size>path.stat().st_size:
                    raise ValueError(f"音频 {key} 的采样数据不完整")
            self.files[key]=np.memmap(path,dtype='<f4',mode='r',offset=off,shape=(size//(4*channels),channels))
            self.sample_rate=rate
        return True

    def close(self):
        for v in self.files.values(): v._mmap.close()
        self.files.clear()
        self.temp.cleanup()


def shape(t, code):
    if code == 4: return t
    if code == 1: return np.sin(t*math.pi/2)
    if code == 7: return 1-np.cos(t*math.pi/2)
    if code == 5: return t*t*(3-2*t)
    if code == 3: return .5+.5*np.cbrt(2*t-1)
    if code == 0: return 1-(1-t)**3
    if code == 8: return t**3
    if code == 2: return 1-(1-t)**1.5
    if code == 6: return t**1.5
    return np.where(t>=1,1.,0.)


def lane_gain(ramp, frame):
    start, end, initial, target = ramp
    progress = np.clip((frame-start)/(end-start), 0., 1.)
    return initial + (target-initial)*shape(progress, 5)


def envelope(points, times):
    """Clip envelopes are relative to the trimmed clip start."""
    if times[-1] <= points[0][0]: return points[0][1]
    if times[0] >= points[-1][0]: return points[-1][1]
    values = np.full(len(times), points[0][1], dtype=np.float32)
    for (a,va,curve),(b,vb,_) in zip(points,points[1:]):
        if b <= times[0] or a > times[-1] or b<=a: continue
        mask = (times>=a) & (times<b)
        values[mask] = va + (vb-va)*shape((times[mask]-a)/(b-a),curve)
    values[times>=points[-1][0]] = points[-1][1]
    return values


class Transport:
    def __init__(self, data):
        self.segs = {int(k):v for k,v in data['segments'].items()}
        self.rules = {int(k):v for k,v in data['rules'].items()}
        self.song = None
        self.audio = {}
        self.settings = {}
        self.mutes = {}
        self.master = .65
        self.se_volume = 1.
        self.playing = False
        self.play_mode = 'ordered'
        self.finished = False
        self.clock = 0
        self.voices = []
        self.pending = []
        self.last_pos = {}
        self.run = 1
        self.key = None
        self.section_queue = deque()
        self.effects = None
        self.arrangement = None
        self.step_index = 0

    @property
    def audio(self):return self._audio

    @audio.setter
    def audio(self,sources):
        self._audio=sources
        self.has_native=any(raw.shape[1]==12 for raw in sources.values())

    def assets(self,song):
        ids=set(song['order'])
        for sid in song['order']:
            for ancestor in self.segs[sid]['anc']:
                for rule in self.rules.get(ancestor,[]):
                    if rule.get('bridge',{}).get('id') in self.segs: ids.add(rule['bridge']['id'])
        return {c['src'] for sid in ids for l in self.segs[sid]['lanes'] for c in l['clips']}

    def select(self, song, sid=None, arrangement=None):
        self.song=song
        self.arrangement=arrangement
        self.settings={}
        self.mutes={}
        if self.effects is not None:self.effects.reset()
        self.restart(sid)
        self.playing=False

    def restart(self,sid=None):
        self.last_pos.clear()
        first=self.arrangement['steps'][0]['segment'] if self.arrangement else sid or self.song['order'][0]
        self.start(first)

    def start(self,sid,pos=0,keep_queue=False,step_index=0,run=1):
        self.clock=0
        self.free_ramps={}
        self.run=run
        self.step_index=step_index
        self.finished=False
        self.pending=[]
        if not keep_queue:self.section_queue.clear()
        self.voices=[self.voice(sid,-pos,0)]
        self.active=self.voices[0]
        self.key=sid
        if self.arrangement:self.prepare_step(self.active,step_index,run,pos)
        self.plan()

    def voice(self,sid,origin,start):
        # A manually toggled lane can ring across the boundary of a free-mode loop.
        ramps={} if self.arrangement else self.free_ramps
        return dict(id=sid,origin=origin,start=start,end=origin+self.segs[sid]['length'],inc=None,out=None,ramps=ramps)

    def prepare_step(self,voice,index,run,pos):
        step=self.arrangement['steps'][index]
        seg=self.segs[step['segment']]
        mask=step['lanes']
        events=deque()
        for event in step['changes']:
            frame=seg['entry']+(event['bar']-1)*seg['bar']
            if event['run']<run or (event['run']==run and frame<=pos):
                mask=event['lanes']
            elif event['run']==run:
                events.append((voice['origin']+frame,set(event['lanes'])))
        voice.update(index=index,run=run,mask=set(mask),events=events)

    def position(self):
        return min(self.segs[self.key]['length'],max(0,self.clock-self.active['origin']))

    def limits(self,sid):
        s=self.segs[sid]
        if self.arrangement:
            if self.active.get('bridge'):return 1,max(s['entry']+1,s['exit'])
            step=self.arrangement['steps'][self.step_index]
            end=s['exit'] if not step.get('bars') else min(s['exit'],s['entry']+step['bars']*s['bar'])
            return step['repeat'],max(s['entry']+1,end)
        repeat,bars=self.settings.get(sid,(None,None))
        if repeat is None:repeat=self.song['loops'].get(str(sid),1)
        end=s['exit']
        if bars and s['bar']: end=min(end,s['entry']+bars*s['bar'])
        return repeat,max(s['entry']+1,end)

    def change(self, repeats, bars):
        self.settings[self.key]=(repeats,bars)
        self.replan()

    def rule(self,src,dst):
        a=self.segs[src]['anc'];b=self.segs[dst]['anc']
        for anc in a:
            if anc not in b: continue
            for rr in reversed(self.rules.get(anc,[])):
                if (any(k in a for k in rr['src']) or -1 in rr['src']) and (any(k in b for k in rr['dst']) or -1 in rr['dst']):
                    return rr
        return DEFAULT_RULE

    def sync_frame(self,s,r,now,end):
        sync=r['sync'];entry=s['entry']
        if sync==0: return min(end,now)
        if sync==5:
            marks=[p for k,p,_ in s['marks'] if p>=now and (not r['cue'] or r['cue']==k)]
            return min([end,*marks])
        unit={1:s['grid'],2:s['bar'],3:s['beat']}.get(sync,0)
        if unit:
            ref=entry+(s['gridOffset'] if sync==1 else 0)
            return min(end,ref+max(0,math.ceil((now-ref)/unit))*unit)
        return end

    @property
    def queued(self):
        return self.section_queue[0] if self.section_queue else None

    def queue(self,sid):
        if self.arrangement or sid not in self.song['order']: return
        self.section_queue.append(sid)
        if len(self.section_queue)==1 and not self.finished:self.replan()

    def jump(self,sid):
        if self.arrangement or sid not in self.song['order']:return False
        self.start(sid,self.segs[sid]['entry'])
        if self.playing:self.active['inc']=(0,240,4)
        return True

    def replan(self):
        if self.active.get('bridge'): return
        # Discard only the uncommitted next transition. The previous voice's
        # post-exit tail can continue underneath the new active phrase.
        self.voices=[v for v in self.voices if not any(v is p[1] for p in self.pending)]
        self.active['end']=self.active['origin']+self.segs[self.key]['length']
        self.active['out']=None
        self.pending=[]
        self.plan()

    def destination(self,sid,rule,srcpos):
        s=self.segs[sid]
        if rule['entry']==1: return min(max(s['entry'],srcpos),max(s['entry'],s['exit']-1))
        if rule['entry']==4: return self.last_pos.get(sid,s['entry'])
        if rule['entry']==3:
            marks=[p for k,p,_ in s['marks'] if not rule['dcue'] or k==rule['dcue']]
            if marks: return marks[0]
        return s['entry']

    def join(self,source,dest,when,rule,point):
        seg=self.segs[dest]
        inc,off,curve=rule['inc']
        origin=when-point
        first=origin if rule['pre'] else when
        if inc: first=min(first,when+off)
        v=self.voice(dest,origin,max(self.clock,first,origin))
        if inc:v['inc']=(when+off,when+off+inc,curve)
        length,offset,curve=rule['out']
        if length:
            source['out']=(when+offset-length,when+offset,curve)
            source['end']=min(source['end'],when+offset)
        elif not rule['post']:
            source['end']=min(source['end'],when)
        elif when < source['origin']+self.segs[source['id']]['exit']-2:
            # At an early sync, stop at the sync unless a fade specifies an overlap.
            source['end']=min(source['end'],when)
        self.voices.append(v)
        self.pending.append((when,v))
        return v

    def plan(self):
        s=self.segs[self.key]
        repeat,end=self.limits(self.key)
        target=self.queued
        forced=target is not None
        if self.arrangement:
            steps=self.arrangement['steps']
            index=self.step_index
            run=self.run+1
            if repeat and self.run>=repeat:
                index=index+1 if index+1<len(steps) else self.arrangement['loopFrom']
                run=1
            if index is not None:target=steps[index]['segment']
        elif target is None:
            if repeat==0 or self.run<repeat: target=self.key
            else:target=self.song['next'].get(str(self.key))
        if target is None:
            self.end_at=self.active['end']
            if end<s['exit']:self.end_at=min(self.end_at,self.active['origin']+end)
            self.end_at=max(self.clock+1,self.end_at)
            return
        rule=self.rule(self.key,target)
        now=self.position()
        pos=self.sync_frame(s,rule,min(end,now+256),end) if forced else end
        when=max(self.clock+1,self.active['origin']+pos)
        # Shortened loops can cut a sustained note. De-click that edit without
        # changing the envelope at original authored loop points.
        if end<s['exit'] and pos>=end and not rule['out'][0]:
            rule={**rule,'out':[240,240,4],'inc':[240,0,4],'post':0,'pre':0}
        bridge=rule.get('bridge')
        destpos=self.destination(target,rule,pos)
        if self.arrangement and rule['entry']==1:
            # At a loop exit, SameTime lands on the next downbeat. Clamping
            # to the destination's last sample would skip an entire step.
            dest=self.segs[target]
            phase=0 if pos>=s['exit'] or target==self.key else max(0,pos-s['entry'])
            destpos=dest['entry']+phase%max(1,dest['exit']-dest['entry'])
        if bridge and bridge['id'] in self.segs and bridge['id'] not in (self.key,target):
            mid=bridge['id'];bs=self.segs[mid]
            v=self.join(self.active,mid,when,{**rule,'pre':bridge['pre'],'inc':bridge['inc']},bs['entry'])
            v['bridge']=True
            if self.arrangement:v['mask']={lane['key'] for lane in bs['lanes'] if lane['on']}
            next_when=when+max(1,bs['exit']-bs['entry'])
            self.join(v,target,next_when,{**rule,'post':bridge['post'],'out':bridge['out']},destpos)
        else:self.join(self.active,target,when,rule,destpos)
        if self.arrangement:
            destination=self.pending[-1][1]
            self.prepare_step(destination,index,run,destination['start']-destination['origin'])
        if forced:self.pending[-1][1]['fromQueue']=True
        self.end_at=None

    def enabled(self,lane,voice=None):
        voice=self.active if voice is None else voice
        if 'mask' in voice:return lane['key'] in voice['mask']
        return self.mutes.get(lane['key'],lane['on'])

    def toggle(self,lane):
        on=not self.enabled(lane)
        if on and lane['variant'] is not None:
            for other in self.segs[self.key]['lanes']:
                if other['id']==lane['id']: self.set_lane(other,False)
        self.set_lane(lane,on)

    def set_lane(self,lane,on):
        old=self.enabled(lane)
        if old==on:return
        if 'mask' in self.active:
            if on:self.active['mask'].add(lane['key'])
            else:self.active['mask'].discard(lane['key'])
        else:self.mutes[lane['key']]=on
        ramps=self.active['ramps']
        previous=ramps.get(lane['key'])
        initial=lane_gain(previous,self.clock) if previous and self.clock<previous[1] else float(old)
        ramps[lane['key']]=(self.clock,self.clock+MANUAL_FADE_FRAMES,initial,float(on))

    def mix_voice(self,v,out,n,native=None):
        a=max(self.clock,v['start'])
        b=min(self.clock+n,v['end'])
        if a>=b:return
        s=self.segs[v['id']];pa=a-v['origin'];pb=b-v['origin']
        block=np.zeros((b-a,2),np.float32)
        bed=np.zeros((b-a,12),np.float32) if native is not None else None
        for lane in s['lanes']:
            ramp=v['ramps'].get(lane['key'])
            enabled=self.enabled(lane,v)
            if not enabled and (not ramp or a>=ramp[1]):continue
            gain=10**(lane['db']/20)
            for clip in lane['clips']:
                lo=max(pa,clip['at']);hi=min(pb,clip['end'])
                if hi<=lo:continue
                raw=self.audio[clip['src']]
                ix=clip['offset']+lo-clip['at'];size=hi-lo
                if 0<=ix and ix+size<=len(raw): arr=raw[ix:ix+size]
                else: arr=raw[np.arange(ix,ix+size)%len(raw)]
                # Float WAV samples use +/-1 full scale. The mixer and cockpit use PCM16 units.
                g=gain*32768 if raw.dtype.kind=='f' else gain
                if clip['env']:
                    times=np.arange(lo-clip['at'],hi-clip['at'])
                    for points in clip['env']:g=g*envelope(points,times)
                if ramp and a<ramp[1]:
                    g=g*lane_gain(ramp,np.arange(lo,hi)+v['origin'])
                if isinstance(g,np.ndarray):g=g[:,None]
                target=bed if raw.shape[1]==12 else block
                target[lo-pa:hi-pa]+=arr*g
        for k in ('inc','out'):
            fade=v[k]
            if fade:
                lo,hi,curve=fade
                t=np.clip((np.arange(a,b)-lo)/max(1,hi-lo),0.,1.)
                gain=shape(t if k=='inc' else 1-t,curve)[:,None]
                block*=gain
                if bed is not None:bed*=gain
        out[a-self.clock:b-self.clock]+=block
        if bed is not None:native[a-self.clock:b-self.clock]+=bed

    def render(self,n,spatial=None,*,floating=False):
        mixed=np.zeros((n,2),np.float32);written=0
        native=np.zeros((n,12),np.float32) if self.has_native else None
        while written<n and self.playing:
            boundary=self.pending[0][0] if self.pending else self.end_at
            split=boundary
            for voice in self.voices:
                events=voice.get('events')
                while events and events[0][0]<=self.clock:
                    _,mask=events.popleft()
                    # Each cue specifies the complete lane state at this sample.
                    voice['ramps'].clear()
                    voice['mask']=mask
                if events:split=min(split,events[0][0])
            count=min(n-written,max(0,split-self.clock))
            if count:
                portion=slice(written,written+count)
                for v in self.voices:self.mix_voice(v,mixed[portion],count,None if native is None else native[portion])
                self.clock+=count;written+=count
            if self.clock<boundary:continue
            if not self.pending:
                if self.play_mode=='repeatOne':
                    self.restart()
                    continue
                self.playing=False;self.finished=True;break
            _,v=self.pending.pop(0)
            self.last_pos[self.key]=min(max(self.segs[self.key]['entry'],self.position()),self.segs[self.key]['exit']-1)
            self.run=v.get('run',self.run+1 if self.key==v['id'] else 1)
            self.step_index=v.get('index',self.step_index)
            self.active=v;self.key=v['id']
            self.voices=[item for item in self.voices if item['end']>self.clock or item is v]
            if not v.get('bridge'):
                if v.get('fromQueue'):self.section_queue.popleft()
                self.plan()
        if self.effects is not None:
            self.effects.filter_music(mixed[:written],None if native is None else native[:written])
        if spatial is not None:
            spatial.begin(mixed)
            if self.effects is not None:
                self.effects.mix(mixed[:written],self.se_volume,None if spatial.front_only else spatial)
            result=spatial.finish(mixed,self.master,native)
        else:
            if self.effects is not None:self.effects.mix(mixed[:written], self.se_volume)
            if native is not None:mixed+=native @ native_matrix(3)
            result=mixed*self.master
        if floating:return np.clip(result*(1/32768),-1.,1.)
        return np.clip(result,-32768,32767).astype('<i2')

