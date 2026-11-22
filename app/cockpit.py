"""Sample-clock cockpit sound sequences layered over the current music mix."""
from collections import deque
from dataclasses import dataclass, replace
import random
import wave
from math import expm1

import numpy as np

from engine import RATE


@dataclass(frozen=True)
class Cue:
    clip: str
    start: int
    stop: int
    gain: float
    fade: int
    distance: float = 0.  # Relative near (0) to far (1), chosen once per explosion.
    cut_at: int | None = None


CLOUD_CLIPS = ('cloud_inside1','cloud_inside2')
CLOUD_ATTACK = round(.65*RATE)
CLOUD_RELEASE = RATE
CLOUD_CANCEL = round(.22*RATE)
# A truncated one-pole response keeps the bass and softens the upper mids.
CLOUD_KERNEL = np.exp(-2*np.pi*1250/RATE*np.arange(64)).astype(np.float32)
CLOUD_KERNEL /= CLOUD_KERNEL.sum()


def cloud_depth(cue,lo,hi):
    frames=np.arange(hi-lo,dtype=np.float32)
    enter=np.clip((lo-cue.start+frames)/CLOUD_ATTACK,0,1)
    leave=np.clip((cue.stop-1-lo-frames)/CLOUD_RELEASE,0,1)
    depth=enter*enter*(3-2*enter)*leave*leave*(3-2*leave)
    if cue.cut_at is not None:
        tail=np.clip((cue.cut_at+CLOUD_CANCEL-1-lo-frames)/CLOUD_CANCEL,0,1)
        depth*=tail*tail*(3-2*tail)
    return depth[:,None]


def cue_end(cue):
    return cue.stop if cue.cut_at is None else min(cue.stop,cue.cut_at+CLOUD_CANCEL)


def explosion_profiles(source):
    """Near adds bass impact; far softens the attack. Both share the original timing."""
    # Subtracting the longer low-pass keeps subsonic content out of the boost.
    kernels = [np.hanning(n).astype(np.float32) for n in (257,1537,49)]
    bass_filter, sub_filter, far_filter = [k/k.sum() for k in kernels]
    near = np.empty_like(source)
    far = np.empty_like(source)
    for channel in range(source.shape[1]):
        raw = source[:,channel].astype(np.float32)
        bass = np.convolve(raw,bass_filter,'same') - np.convolve(raw,sub_filter,'same')
        impact = raw + .95*bass
        # Round only the loudest peaks in the close recording before mixing.
        knee = .54*32767
        headroom = .17*32767
        peaks = np.abs(impact)>knee
        impact[peaks] = np.sign(impact[peaks])*(knee+headroom*np.tanh((np.abs(impact[peaks])-knee)/headroom))
        near[:,channel] = np.rint(impact)
        far[:,channel] = np.rint(np.convolve(raw,far_filter,'same'))
    return near,far


@dataclass(frozen=True)
class EngineAccent:
    start: int
    peak: int
    release: int
    end: int
    amount: float
    recovery: bool = False

    def at(self, frame):
        if frame <= self.start or frame >= self.end:
            return 0.
        if frame < self.peak:
            t = (frame-self.start) / (self.peak-self.start)
        elif frame > self.release:
            t = (self.end-frame) / (self.end-self.release)
        else:
            return self.amount
        return self.amount * t*t*(3-2*t)


def engine_accent(kind, cues):
    """Shape the engine sound against the actual start and release of this warning."""
    start = cues[0].start
    end = max(c.stop for c in cues)
    if kind == 'missile':
        end = max(c.stop for c in cues if c.clip in ('caution','danger','missile_voice'))
        danger = next((c for c in cues if c.clip == 'danger'), None)
        peak = danger.start if danger else start + (end-start)*.68
        amount = .36 if danger else min(.24, .055+.016*(end-start)/RATE)
    elif kind == 'railgun':
        end = next(c.start for c in cues if c.clip.startswith('boom'))
        peak, amount = end-.85*RATE, .22
    elif kind in ('pull_up', 'stall'):
        peak = start+(end-start)*(.40 if kind=='pull_up' else .70)
        amount = .16 if kind=='pull_up' else .25
    else:
        return None
    return EngineAccent(start, round(peak), end, end+round(2.8*RATE), amount, kind in ('pull_up','stall'))


class EngineBed:
    """Three phase-continuous power loops driven by a slowly wandering cruise setting."""
    FADE_FRAMES = round(.28 * RATE)
    HOP = RATE // 25

    def __init__(self, layers, shots, rng):
        self.layers = layers
        self.shots = shots
        self.playing_shots = {}
        self.rng = rng
        self.enabled = False
        self.phase = 0
        self.fade_position = 0
        self.cruise_start = self.cruise_target = self.power = self.next_power = .54
        self.leg_start = self.leg_end = 0
        self.tick = self.HOP
        self.next_surge = round(RATE*self.rng.uniform(22,42))
        self.excursion = None
        self.high_power = False
        self.last_recovery = -10*RATE

    def excursion_lift(self, clock, accents):
        if self.phase >= self.next_surge:
            if any(a.start <= clock < a.end for a in accents):
                self.next_surge = self.phase + 4*RATE
            else:
                peak = self.phase + round(self.rng.uniform(3,5)*RATE)
                release = peak + round(self.rng.uniform(2,4)*RATE)
                end = release + round(self.rng.uniform(3,5)*RATE)
                self.excursion = EngineAccent(self.phase,peak,release,end,.43)
                self.next_surge = end + round(self.rng.uniform(35,70)*RATE)
        return self.excursion.at(self.phase) if self.excursion else 0.

    def advance_power(self, clock, accents):
        if self.phase >= self.leg_end:
            self.cruise_start = self.cruise_target
            # Small excursions, biased back toward cruise after every leg.
            self.cruise_target = .54 + .35*(self.cruise_start-.54) + self.rng.uniform(-.105,.105)
            self.leg_start = self.phase
            self.leg_end = self.phase + round(RATE*self.rng.uniform(4.5,10.5))
        t = (self.phase-self.leg_start) / (self.leg_end-self.leg_start)
        target = self.cruise_start + (self.cruise_target-self.cruise_start) * t*t*(3-2*t)
        lift = max((accent.at(clock) for accent in accents), default=0.)
        target += max(lift, self.excursion_lift(clock, accents))
        self.power = self.next_power
        tau = .52 if target > self.power else 1.15
        self.next_power += (min(.90,target)-self.power) * -expm1(-self.HOP/(RATE*tau))
        self.tick = 0
        recovered = any(a.recovery and 0 <= clock-a.release-round(.32*RATE) < self.HOP for a in accents)
        if not self.high_power and self.next_power >= .82:
            self.high_power = True
            if self.enabled:
                self.playing_shots['engine_surge'] = self.phase
        elif self.high_power and self.next_power <= .71:
            self.high_power = False
            recovered = True
        if self.enabled and recovered and self.phase-self.last_recovery >= 6*RATE:
            self.playing_shots['engine_recovery'] = self.last_recovery = self.phase

    def mix(self, output, gain, clock, accents):
        size = len(output) if self.enabled else min(len(output), self.fade_position)
        offset = 0
        while offset < size:
            if self.tick == self.HOP:
                self.advance_power(clock+offset, accents)
            n = min(size-offset, self.HOP-self.tick)
            frame = np.arange(n, dtype=np.float32)
            power = self.power + (self.next_power-self.power) * ((self.tick+frame)/self.HOP)
            # F-15C blend points from the game's engine event.
            low = np.clip((.628-power)/(.628-.264), 0, 1)
            high = np.clip((power-.628)/(.895-.628), 0, 1)
            volume = np.full(n,gain,dtype=np.float32)
            direction = 1 if self.enabled else -1
            if not self.enabled or self.fade_position < self.FADE_FRAMES:
                volume *= np.clip((self.fade_position + direction*frame) / self.FADE_FRAMES, 0, 1)
            self.fade_position = min(self.FADE_FRAMES, max(0, self.fade_position+direction*n))
            level = volume * (.050 + .085*power)
            for samples, weight in zip(self.layers, (low, 1-low-high, high)):
                if not np.any(weight): continue
                pos = self.phase % len(samples)
                first = min(n, len(samples)-pos)
                amplitude = (level*np.sqrt(weight))[:,None]
                output[offset:offset+first] += samples[pos:pos+first] * amplitude[:first]
                if first<n:
                    output[offset+first:offset+n] += samples[:n-first] * amplitude[first:]
            for name, start in tuple(self.playing_shots.items()):
                sound, strength = self.shots[name]
                pos = self.phase-start
                count = min(n,len(sound)-pos)
                output[offset:offset+count] += sound[pos:pos+count] * (volume[:count]*strength)[:,None]
                if pos+count == len(sound):
                    del self.playing_shots[name]
            self.phase += n
            self.tick += n
            offset += n
        if not self.enabled and not self.fade_position:
            self.playing_shots.clear()


class CockpitEffects:
    SCENES = ('missile', 'railgun', 'pull_up', 'stall', 'launch', 'gun', 'cloud')
    CLIPS = ('missile_voice', 'caution', 'danger', 'rail_voice', 'rail_tone',
             'pull_voice', 'pull_tone', 'stall_voice', 'launch1', 'launch2',
             'gun_start', 'gun_body', 'gun_tail', 'boom1', 'boom2', 'missile_hit',
             'missile_pass1', 'missile_pass2', 'engine_surge', 'engine_recovery', *CLOUD_CLIPS)

    SCENE_CLIPS = {
        'missile': ('missile_voice','caution','danger','missile_pass1','missile_pass2'),
        'railgun': ('rail_voice','rail_tone','boom1','boom2'),
        'pull_up': ('pull_voice','pull_tone'), 'stall': ('stall_voice',),
        'launch': ('launch1','launch2','missile_hit'), 'gun': ('gun_start','gun_body','gun_tail'),
        'cloud': CLOUD_CLIPS,
    }
    ENGINE_LAYERS = ('engine_low','engine_cruise','engine_high')

    def __init__(self, directory=None, rng=None):
        self.samples = {}
        self.distant_samples = {}
        self.rng = rng or random.Random()
        self.engine = EngineBed([],{},random.Random(self.rng.getrandbits(64)))
        self.enabled = False
        self.categories = self.SCENES
        if directory is not None:self.prepare(directory,self.SCENES,True)
        self.reset()

    def prepare(self,directory,categories=(),engine=False):
        names={name for kind in categories for name in self.SCENE_CLIPS[kind]}
        if engine:names.update((*self.ENGINE_LAYERS,'engine_surge','engine_recovery'))
        for name in sorted(names-self.samples.keys()):
            path=directory(name) if callable(directory) else directory/(name+'.wav')
            with wave.open(str(path),'rb') as stream:
                channels=stream.getnchannels()
                if stream.getframerate()!=RATE or stream.getsampwidth()!=2 or channels not in (1,2):
                    raise ValueError('音效格式错误：'+name)
                data=np.frombuffer(stream.readframes(stream.getnframes()),'<i2').reshape(-1,channels)
            if name in ('missile_hit','boom1','boom2'):
                data,self.distant_samples[name]=explosion_profiles(data)
            self.samples[name]=data
        if engine and not self.engine.layers:
            self.engine.layers=[self.samples[name] for name in self.ENGINE_LAYERS]
            self.engine.shots={name:(self.samples[name],gain) for name,gain in [('engine_surge',.33),('engine_recovery',.26)]}

    def reset(self):
        self.clock = 0
        self.next_scene = round(RATE * self.rng.uniform(.6, 1.4))
        self.upcoming = deque()
        self.active = []
        self.accents = deque()
        self.bag = []
        self.previous = None
        self.cloud_start = None
        self.cloud_history = {}

    def set_enabled(self, enabled):
        if enabled == self.enabled:return
        self.enabled = enabled
        self.upcoming.clear();self.accents.clear();self.bag.clear()
        self.previous=None
        if enabled:
            self.next_scene=self.clock+round(RATE*self.rng.uniform(.6,1.4))
        else:
            self.active=[replace(c,cut_at=self.clock) if c.cut_at is None else c
                         for c in self.active if c.clip in CLOUD_CLIPS]

    def set_categories(self, categories):
        if categories == self.categories:
            return
        was_empty = not self.categories
        self.categories = categories
        self.bag.clear()
        if self.previous is not None and self.previous not in categories:
            self.upcoming.clear()
            self.accents.clear()
            fade = round(.045*RATE)
            stop = self.clock + fade
            for i,c in enumerate(self.active):
                if c.clip in CLOUD_CLIPS:
                    if c.cut_at is None:self.active[i]=replace(c,cut_at=self.clock)
                elif c.stop>stop:self.active[i]=replace(c,stop=stop,fade=fade)
            self.previous = None
        if was_empty or self.previous is None:
            self.next_scene = self.clock + round(RATE*self.rng.uniform(.6,1.4))

    def make_scene(self, kind, origin):
        """Timings below are in seconds. Cues are committed in output sample frames."""
        result = []
        def emit(clip, at, gain, duration=None, fade=.009, *, distance=0.):
            start = origin + round(at * RATE)
            length = len(self.samples[clip])
            if duration is not None:
                stop = origin + round((at + duration) * RATE)
                length = min(length, max(1, stop - start))
            result.append(Cue(clip, start, start + length, gain, min(length, round(fade * RATE)), distance))
            return at + length / RATE

        def explosion(clip, at, near_gain):
            distance = self.rng.random()
            gain = near_gain * 10**(-17*distance/20)
            emit(clip, at+.65*distance, gain, distance=distance)

        def calls(clip, count, delay, at=0):
            for _ in range(count):
                end = emit(clip, at, .36)
                at = end + delay
            return end

        rng = self.rng
        if kind == 'missile':
            stage = rng.choices(('early', 'approach', 'danger'), weights=(3, 3, 4))[0]
            if stage == 'early':
                pulse_end = clear = rng.uniform(1.02, 2.25)
                proximity = rng.uniform(.12, .32)
            elif stage == 'approach':
                pulse_end = clear = rng.uniform(2.8, 12.5)
                proximity = rng.uniform(.46, .91)
            else:
                pulse_end = rng.uniform(2.2, 16.7)
                clear = pulse_end + rng.uniform(.55, 2.35)
                proximity = 1.
                emit('danger', pulse_end, .23, clear - pulse_end, fade=.085)
            voice_at = .18
            while voice_at + len(self.samples['missile_voice']) / RATE <= clear:
                voice_at = emit('missile_voice', voice_at, .36) + 4.
            # The game's 350 ms alert tightens toward the length of its 195 ms pulse.
            at = 0.
            while at < pulse_end:
                approach = proximity * at / pulse_end
                emit('caution', at, .14 + .08 * approach, pulse_end - at)
                at += .350 + (len(self.samples['caution']) / RATE - .350) * approach
            if rng.random() < {'early':.20,'approach':.50,'danger':.75}[stage]:
                emit(rng.choice(('missile_pass1','missile_pass2')),clear+rng.uniform(.04,.26),
                     .17+.17*proximity)
        elif kind == 'railgun':
            end = calls('rail_voice', rng.randint(3, 6), 2., .10)
            clear = end + rng.uniform(.14, .65)
            for at in range(int(clear) + 1):
                if at < clear:
                    emit('rail_tone', at, .20, clear - at)
            explosion(rng.choice(('boom1', 'boom2')), clear + rng.uniform(.06, .24), .64)
        elif kind == 'pull_up':
            end = calls('pull_voice', rng.randint(2, 4), 1.8, 1.4)
            emit('pull_tone', 0, .14, end + .12, fade=.06)
        elif kind == 'stall':
            calls('stall_voice', rng.randint(2, 5), 1.15)
        elif kind == 'launch':
            at = 0.
            for _ in range(rng.randint(1, 2)):
                last_launch = at
                emit(rng.choice(('launch1', 'launch2')), at, .40)
                at += rng.uniform(.34, .76)
            if rng.random() < .55:
                explosion('missile_hit', last_launch+rng.uniform(1.5,3.55), .56)
        elif kind == 'gun':
            length = rng.uniform(.35, 3.1)
            emit('gun_start', 0, .36, length)
            emit('gun_body', .05, .32, length - .05, fade=.035)
            emit('gun_tail', length, .38)
        elif kind == 'cloud':
            emit(rng.choice(CLOUD_CLIPS),0,.31,rng.uniform(4.8,8.2),fade=0)
        return sorted(result, key=lambda cue: cue.start)

    def schedule(self):
        if not self.bag:
            self.bag = list(self.categories)
            self.rng.shuffle(self.bag)
            if self.bag[-1] == self.previous:
                self.bag[0], self.bag[-1] = self.bag[-1], self.bag[0]
        self.previous = self.bag.pop()
        cues = self.make_scene(self.previous, self.next_scene)
        self.next_scene = max(cue.stop for cue in cues) + round(self.rng.uniform(6, 15) * RATE)
        # Preserve the slot's duration so the other effects keep their original cadence.
        if self.previous == 'cloud' and self.rng.randrange(3) != 0:
            return
        self.upcoming.extend(cues)
        accent = engine_accent(self.previous, cues)
        if accent is not None:
            self.accents.append(accent)

    def begin_block(self, size):
        end = self.clock + size
        if self.enabled:
            while self.categories and self.next_scene < end:
                self.schedule()
        while self.upcoming and self.upcoming[0].start < end:
            self.active.append(self.upcoming.popleft())
        return end

    def filter_music(self,stereo,native=None):
        end=self.begin_block(len(stereo))
        for cue in self.active:
            if cue.clip not in CLOUD_CLIPS:continue
            lo,hi=max(self.clock,cue.start),min(end,cue_end(cue))
            if lo>=hi:continue
            if self.cloud_start!=cue.start:
                self.cloud_start=cue.start
                self.cloud_history.clear()
            wet=.92*cloud_depth(cue,lo,hi)
            for key,bank in (('stereo',stereo),('native',native)):
                if bank is None:continue
                block=bank[lo-self.clock:hi-self.clock]
                history=self.cloud_history.get(key)
                if history is None:history=np.repeat(block[:1],len(CLOUD_KERNEL)-1,axis=0)
                extended=np.concatenate((history,block))
                self.cloud_history[key]=extended[1-len(CLOUD_KERNEL):].copy()
                for ch in range(block.shape[1]):
                    low=np.convolve(extended[:,ch],CLOUD_KERNEL,'valid')
                    block[:,ch]+=wet[:,0]*(.96*low-block[:,ch])

    def mix(self, output, gain=1., spatial=None):
        end = self.begin_block(len(output))
        if spatial is None:
            engine_output = output
        else:
            engine_output = np.zeros_like(output)
        self.engine.mix(engine_output, gain, self.clock, self.accents if self.enabled else ())
        if spatial is not None:
            output += engine_output
            spatial.add_engine(engine_output)
        if not len(output) or (not self.enabled and not self.active):
            return
        while self.accents and self.accents[0].end <= end:
            self.accents.popleft()
        for cue in self.active:
            lo, hi = max(self.clock, cue.start), min(end, cue_end(cue))
            if lo >= hi:
                continue
            frames = slice(lo-cue.start, hi-cue.start)
            data = self.samples[cue.clip][frames].astype(np.float32)
            if cue.distance:
                muffling = .90 * (1 - (1-cue.distance)**1.7)
                data += muffling * (self.distant_samples[cue.clip][frames]-data)
            data *= np.float32(cue.gain * gain)
            if cue.clip in CLOUD_CLIPS:data *= cloud_depth(cue,lo,hi)
            fade_start = max(lo, cue.stop-cue.fade)
            if hi > fade_start:
                data[fade_start-lo:] *= ((cue.stop-1-np.arange(fade_start, hi))/cue.fade)[:, None]
            output[lo-self.clock:hi-self.clock] += data
            if spatial is not None:
                spatial.add_cue(cue,lo-self.clock,lo-cue.start,data,len(self.samples[cue.clip]))
        self.active = [cue for cue in self.active if cue_end(cue) > end]
        self.clock = end

