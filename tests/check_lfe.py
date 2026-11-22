"""Check spatial routing, crossover response and Windows format negotiation."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from audio_layout import SAMPLE_FORMATS, layout_candidates, layout_name, pcm_format
from device import encode_output, open_stream
from spectrum import MixSpectrum
from surround import BassCrossover, SurroundMix, FULL

def tone(hz):
    return np.rint(16000*np.sin(2*np.pi*hz*np.arange(24000)/48000)).astype('<i2')

def rms(x):
    return np.sqrt(np.mean(x.astype(float)**2))

for mask in (0xb,0x3f,0x63f,FULL):
    sub = (mask & 7).bit_count()
    for hz,expected in [(30,.981),(80,.5),(800,.0001)]:
        x = tone(hz)
        bed = np.zeros((len(x),mask.bit_count()))
        bed[:,[0,1,sub]] = x[:,None]
        y = BassCrossover(mask).route(bed)[4800:]
        level = rms(x[4800:])
        assert abs(rms(y[:,sub])/level-expected)<.002
        assert abs(rms(y[:,0])/level-(1-expected))<.002
        assert abs(rms(y[:,0].astype(float)+y[:,sub])/level-1)<.002
        assert np.array_equal(y[:,0],y[:,1]) and not np.delete(y,[0,1,sub],axis=1).any()

# A loud, one-sided signal keeps half its DC level on the sub, including across callbacks.
bed = np.zeros((12000,3))
bed[:,1],bed[:,2] = 32000,16000
crossover = BassCrossover(0xb)
y = crossover.route(bed)
assert abs(y[-1,2]-16000)<.001 and np.max(abs(y[-1,:2]))<1e-7 and not y[:,0].any()
crossover.reset()
assert np.array_equal(y,np.concatenate([crossover.route(b) for b in np.array_split(bed,[1,1101,2399,8405])]))

music = np.column_stack((tone(43)+tone(991)//2,tone(43)+tone(1811)//2)).astype(np.float32)
for mask in (0xb,0x7,0x33,0x3f,0x60f,0x63f,0x563f,FULL):
    mixer = SurroundMix(mask)
    mixer.begin(music)
    output = mixer.finish(music,.65)
    assert (np.max(abs(output.astype(float)),axis=0)>50).all(),hex(mask)
    mixer.reset()
    split = []
    for part in np.array_split(music,[1,320,512,1700,2701]):
        mixer.begin(part)
        split.append(mixer.finish(part,.65))
    assert np.array_equal(output,np.concatenate(split)),hex(mask)

mixer = SurroundMix(FULL)
zeros = np.zeros((1200,2),dtype=np.float32)
ones = np.ones_like(zeros)
mixer.begin(zeros)
mixer.add_cue(SimpleNamespace(clip='missile_voice'),0,0,ones,len(ones))
assert (mixer.bed[:,mixer.index[4]]==1).all()
assert not np.delete(mixer.bed,mixer.index[4],axis=1).any()
mixer.begin(zeros)
mixer.add_engine(ones)
assert np.sum(mixer.bed[:,[mixer.index[16],mixer.index[32]]]**2) > np.sum(mixer.bed[:,:3]**2)*10
mixer.begin(zeros)
mixer.add_cue(SimpleNamespace(clip='missile_pass1'),0,0,ones,len(ones))
top = [i for i,b in enumerate(mixer.bits) if b>=2048]
assert mixer.bed[0,mixer.index[16]]>.95 and mixer.bed[-1,mixer.index[2]]>.8
assert np.linalg.norm(mixer.bed[len(ones)//2,top])>.99
assert np.allclose(np.linalg.norm(mixer.bed,axis=1),1,atol=.00001)

# Spatial objects request variable buffer lengths. The spectrum still measures
# exactly the same time windows as the fixed-size PCM stream.
a,b = MixSpectrum(),MixSpectrum()
for part in np.array_split(music,len(music)//960):a.push(part)
for part in np.array_split(music,[17,333,2000,2143,4855]):b.push(part)
assert a.frames==b.frames and len(b.frames)>0

for pcm,objects,accepted,expected in [
    (0x3,0,{('PCM',3)},('PCM',3)),
    (0x33,0,{('PCM',0x33)},('PCM',0x33)),
    (0x63f,0,{('PCM',0x3f)},('PCM',0x3f)),
    (FULL,0,{('PCM',0x63f),('PCM',0x3f)},('PCM',0x63f)),
    (FULL,0,{('WASAPI',FULL),('PCM',0x63f)},('WASAPI',FULL)),
    (0x63f,FULL,{('PCM',0x63f),('Spatial',FULL)},('Spatial',FULL)),
    (0x63f,FULL,{('PCM',0x63f)},('PCM',0x63f)),
    (FULL,FULL,{('PCM',FULL),('Spatial',FULL)},('PCM',FULL)),
    (0x3f,0,{('PCM',0xb)},('PCM',0xb)),
]:
    attempts = []
    def open_format(kind,mask):
        attempts.append((kind,mask))
        if (kind,mask) not in accepted:raise OSError('unavailable')
        return SimpleNamespace(kind=kind,mask=mask)
    with patch('device.preferred_endpoint',return_value=(7,'test')), patch('device.mix_mask',return_value=pcm), \
         patch('device.spatial_client',return_value=(None,objects)), \
         patch('device.SpatialStream',side_effect=lambda c,m:open_format('Spatial',m)), \
         patch('device.WasapiStream',side_effect=lambda e,m,f:open_format('WASAPI',m)), \
         patch('device.WaveStream',side_effect=lambda mm,d,m,f:open_format('PCM',m)):
        stream = open_stream(None)
        assert (stream.kind,stream.mask)==expected
        counts = [mask.bit_count() for _,mask in attempts]
        assert counts==sorted(counts,reverse=True)
        if stream.kind=='Spatial':stream.endpoint_scope.close()
    for mask in layout_candidates(pcm):
        desc = pcm_format(mask)
        fmt = getattr(desc,'wave',desc)
        assert fmt.channels==mask.bit_count() and fmt.align==2*fmt.channels
        assert fmt.rate==48000 and fmt.bytesPerSec==48000*fmt.align
        for name,(code,bits) in SAMPLE_FORMATS.items():
            desc=pcm_format(mask,name)
            fmt=getattr(desc,'wave',desc)
            assert fmt.bits==bits and fmt.align==bits//8*fmt.channels and fmt.bytesPerSec==48000*fmt.align
            assert (desc.subformat[0] if hasattr(desc,'subformat') else fmt.format)==code

# Negotiation falls back in precision before reducing a device's channel count.
for allowed,expected in [
    ({(0x3f,'Float32'),(0x3f,'PCM32')},(0x3f,'Float32')),
    ({(0x3f,'PCM24'),(0x3f,'PCM16'),(3,'Float32')},(0x3f,'PCM24')),
]:
    def open_pcm(endpoint,mask,fmt):
        if (mask,fmt) not in allowed:raise OSError('unavailable')
        return mask,fmt
    with patch('device.preferred_endpoint',return_value=(1,'test')),patch('device.mix_mask',return_value=0x3f), \
         patch('device.WasapiStream',side_effect=open_pcm),patch('device.WaveStream',side_effect=OSError()):
        assert open_stream(None,allow_spatial=False)==expected
samples=np.array([[-1.,1.],[2**-22,-2**-22]],np.float32)
assert encode_output(samples,'Float32') is samples
assert encode_output(samples,'PCM32').tolist()==[[-2**31,2**31-1],[512,-512]]
assert encode_output(samples,'PCM24').tobytes()==bytes.fromhex('000080 ffff7f 020000 feffff')
assert encode_output(samples,'PCM16').tolist()==[[-32768,32767],[0,0]]
assert layout_name(FULL)=='7.1.4' and layout_name(0x33)=='4.0'
print('Passed crossover, channel routing, flyby trajectory, chunk continuity, spectrum and layout negotiation.')
