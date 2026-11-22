"""Mix stereo music and individual cockpit cues onto the negotiated speaker bed."""
import math
from functools import lru_cache

import numpy as np

from audio_layout import LFE, STEREO

# WAVEFORMATEXTENSIBLE speaker bits, azimuth clockwise from directly ahead.
FLOOR = {1:-30, 2:30, 4:0, 16:-145, 32:145, 64:-15, 128:15,
         256:180, 512:-90, 1024:90}
HEIGHT = {2048:0, 4096:-45, 8192:0, 16384:45, 32768:-135, 65536:180, 131072:135}
FULL = 0x2d63f


@lru_cache(maxsize=32)
def native_matrix(mask):
    """Rows follow the channel order that vgmstream reports for the Wwise 7.1.4 bed."""
    speakers=[1<<i for i in range(18) if mask & (1<<i)]
    source=[1<<i for i in range(18) if FULL & (1<<i)]
    matrix=np.zeros((len(source),len(speakers)),np.float32)
    for row,bit in enumerate(source):
        if bit in speakers:
            matrix[row,speakers.index(bit)]=1
            continue
        if bit in (4,LFE):
            matrix[row,:2]=.7071 if bit==4 else .5
            continue
        left=bit in (16,512,4096,32768)
        front=1 if left else 2
        back=16 if left else 32
        side=512 if left else 1024
        top_front=4096 if left else 16384
        top_back=32768 if left else 131072
        if bit==top_front:preferred=(front,)
        elif bit==top_back:preferred=(back,side,top_front,front)
        else:preferred=(back,side,front)
        dest=next(b for b in preferred if b in speakers)
        matrix[row,speakers.index(dest)]=.7071
    matrix.setflags(write=False)
    return matrix


class BassCrossover:
    """Two cascaded Butterworth stages per speaker, including the 80 Hz LFE send."""
    def __init__(self, mask):
        self.channels = mask.bit_count()
        self.sub = (mask & (LFE-1)).bit_count()
        k = math.tan(math.pi * 80 / 48000)
        norm = 1 / (1 + math.sqrt(2)*k + k*k)
        self.feedback = (2*(k*k-1)*norm, (1-math.sqrt(2)*k+k*k)*norm)
        self.low = (k*k*norm, 2*k*k*norm, k*k*norm)
        self.high = (norm, -2*norm, norm)
        self.reset()

    def reset(self):
        self.state = [(0., 0., 0., 0.)] * self.channels

    def route(self, bed, native=None):
        output = np.empty(bed.shape, dtype='<i2')
        a1, a2 = self.feedback
        for ch in range(self.channels):
            b0, b1, b2 = self.low if ch == self.sub else self.high
            z1, z2, z3, z4 = self.state[ch]
            result = []
            for x in bed[:,ch].tolist():
                first = b0*x + z1
                z1, z2 = b1*x - a1*first + z2, b2*x - a2*first
                y = b0*first + z3
                z3, z4 = b1*first - a1*y + z4, b2*first - a2*y
                result.append(y)
            self.state[ch] = (z1,z2,z3,z4)
            if native is not None:result=np.asarray(result)+native[:,ch]
            output[:,ch] = np.clip(np.rint(result),-32768,32767)
        return output


def pan_ring(ring, azimuth, channels):
    gains = np.zeros(channels, np.float32)
    if len(ring) == 1:
        gains[ring[0][1]] = 1.
        return gains
    angle = azimuth % 360
    for i, (right, ri) in enumerate(ring):
        if right >= angle:break
    else:
        i = 0
        right, ri = ring[0]
    if angle == right:
        gains[ri] = 1.
        return gains
    left, li = ring[i-1]
    phase = (angle-left) % 360 / ((right-left) % 360) * math.pi/2
    gains[li], gains[ri] = math.cos(phase), math.sin(phase)
    return gains


class SurroundMix:
    def __init__(self, mask):
        self.bits = [1 << i for i in range(18) if mask & (1 << i)]
        self.channels = len(self.bits)
        self.mask = mask
        self.index = {bit:i for i,bit in enumerate(self.bits)}
        self.front_only = mask in (STEREO, STEREO | LFE)
        angles = dict(FLOOR)
        if not mask & (512 | 1024):
            angles.update({16:-110,32:110})
        self.floor = sorted((angles[b]%360, self.index[b]) for b in self.bits if b in angles)
        # Top center and top-front-center can coexist. Divide a coincident direction
        # between them after panning instead of creating a zero-width azimuth range.
        self.height = sorted((HEIGHT[b]%360, self.index[b]) for b in self.bits if b in HEIGHT and
                             not (b == 2048 and 8192 in self.index))
        self.crossover = BassCrossover(mask) if mask & LFE else None
        # A short, channel-specific delay sends stereo width to the room and ceiling.
        ambience = [(bit,i) for i,bit in enumerate(self.bits) if bit not in (1,2,4,LFE)]
        self.taps = [(i, 480+89*j, (.25 if bit in FLOOR else .14)
                      * (-1 if (angles | HEIGHT)[bit] > 0 else 1) / math.sqrt(max(1,len(ambience)/2)))
                     for j,(bit,i) in enumerate(ambience)]
        self.delay = np.zeros(max((d for _,d,_ in self.taps),default=0),np.float32)
        self.engine_gains = np.stack([self.pan(-130,8), self.pan(130,8)])
        self.flybys = {s:[self.pan(145*s,0),self.pan(90*s,50),self.pan(-25*s,0)] for s in (-1,1)}
        self.reset()

    def reset(self):
        self.delay.fill(0)
        if self.crossover:self.crossover.reset()

    def pan(self, azimuth, elevation=0):
        floor = pan_ring(self.floor,azimuth,self.channels)
        if not self.height or elevation <= 0:return floor
        top = pan_ring(self.height,azimuth,self.channels)
        if 2048 in self.index and 8192 in self.index:
            a,b = self.index[2048],self.index[8192]
            top[a] = top[b] = top[b]*math.sqrt(.5)
        phase = min(1.,elevation/45)*math.pi/2
        return floor*math.cos(phase) + top*math.sin(phase)

    def begin(self, music):
        self.bed = np.zeros((len(music),self.channels),np.float32)
        self.bed[:,:2] = music
        if 4 in self.index:
            center = .32 * music.mean(axis=1)
            self.bed[:,self.index[4]] = center
            self.bed[:,:2] -= (.5*center)[:,None]
        if self.taps:
            side = (music[:,0]-music[:,1])*.5
            history = np.concatenate((self.delay,side))
            for channel,delay,gain in self.taps:
                start = len(self.delay)-delay
                self.bed[:,channel] = history[start:start+len(side)] * gain
            self.delay[:] = history[-len(self.delay):]

    def add_engine(self, stereo):
        self.bed[:len(stereo)] += (stereo @ self.engine_gains) * .75

    def add_cue(self, cue, offset, pos, data, duration):
        name = cue.clip
        mono = data.mean(axis=1,keepdims=True)
        target = self.bed[offset:offset+len(data)]
        if name.startswith('missile_pass'):
            sign = -1 if name.endswith('1') else 1
            rear, overhead, front = self.flybys[sign]
            progress = np.clip(2*(pos+np.arange(len(data)))/max(1,duration-1),0,2)
            # Equal-power interpolation with power normalization where two anchors overlap.
            phase = np.minimum(progress,1)*math.pi/2
            tail = np.maximum(progress-1,0)*math.pi/2
            gains = (np.cos(phase)[:,None]*rear + (np.sin(phase)*np.cos(tail))[:,None]*overhead
                     + np.sin(tail)[:,None]*front)
            gains /= np.linalg.norm(gains,axis=1,keepdims=True)
            target += mono*gains
        else:
            if name in ('boom1','boom2','missile_hit'):
                # Fix the bearing for the lifetime of the cue, including a shortened tail.
                azimuth = ((cue.start*2654435761) % 360)-180
                gains = self.pan(azimuth, 10+12*cue.distance)
            elif name.startswith('launch'):
                gains = self.pan(-35 if name.endswith('1') else 35)
            else:
                gains = self.pan(0)
            target += mono*gains
        if data.shape[1] == 2:
            side = .20*(data[:,0]-data[:,1])
            target[:,0] += side
            target[:,1] -= side

    def finish(self, stereo, master, native=None):
        monitor=stereo if native is None else stereo+native @ native_matrix(STEREO)
        self.monitor = np.clip(monitor*master,-32768,32767).astype('<i2')
        if native is not None and self.mask!=FULL:native=native @ native_matrix(self.mask)
        if self.front_only:self.bed[:,:2] = stereo
        if self.crossover:
            self.bed[:,self.crossover.sub] = stereo.mean(axis=1)
            return self.crossover.route(self.bed*master,None if native is None else native*master)
        if native is not None:self.bed+=native
        return np.clip(self.bed*master,-32768,32767).astype('<i2')
