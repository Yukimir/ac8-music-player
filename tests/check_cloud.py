"""Check cloud scene timing and the music-only filter across output callbacks."""
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from cockpit import CLOUD_CANCEL, CLOUD_CLIPS, CockpitEffects
from engine import RATE


def scene():
    effects=CockpitEffects(rng=random.Random(8))
    effects.samples.update(dict.fromkeys(CLOUD_CLIPS,np.full((4*RATE,2),2000,np.int16)))
    effects.set_categories(('cloud',))
    effects.set_enabled(True)
    effects.previous='cloud'
    effects.upcoming.extend(effects.make_scene('cloud',1000))
    effects.next_scene=10*RATE
    return effects


def render(effects,source,chunk,cancel=None):
    stereo=source.copy()
    native=np.tile(source,(1,6))
    air=np.zeros_like(source)
    for start in range(0,len(stereo),chunk):
        if cancel is not None and start==cancel:
            effects.set_enabled(False)
        block=slice(start,start+chunk)
        effects.filter_music(stereo[block],native[block])
        effects.mix(air[block])
    assert np.array_equal(native,np.tile(stereo,(1,6)))
    return stereo,air


frames=np.arange(5*RATE)
tones=np.sin(2*np.pi*frames[:,None]*np.array([80,10000])/RATE).astype(np.float32)
music,air=render(scene(),tones,960)
inside=slice(2*RATE,3*RATE)
levels=np.linalg.norm(music[inside],axis=0)/np.linalg.norm(tones[inside],axis=0)
assert levels[0]>.90 and levels[1]<.25
assert np.array_equal(music[:1000],tones[:1000]) and np.array_equal(music[-RATE//2:],tones[-RATE//2:])
assert not air[:1000].any() and not air[-RATE//2:].any() and np.max(air[inside])>0
other,other_air=render(scene(),tones,257)
np.testing.assert_allclose(other,music,atol=1e-6)
np.testing.assert_allclose(other_air,air,atol=.001)
stop=2*RATE
restored,tail=render(scene(),tones,960,stop)
assert np.array_equal(restored[:stop],music[:stop]) and np.array_equal(tail[:stop],air[:stop])
assert abs(tail[stop,0]-air[stop,0])<.01
assert np.array_equal(restored[stop+CLOUD_CANCEL:],tones[stop+CLOUD_CANCEL:])
assert not tail[stop+CLOUD_CANCEL:].any()
effects=scene()
effects.set_categories(())
dry,silent=render(effects,tones[:RATE],257)
assert np.array_equal(dry,tones[:RATE]) and not silent.any()

for categories in [('cloud',), ('cloud','gun')]:
    effects=CockpitEffects(rng=random.Random(101))
    effects.samples=dict.fromkeys(effects.CLIPS,np.broadcast_to(np.int16(0),(10*RATE,2)))
    effects.set_categories(categories)
    cloud_slots=clouds=guns=0
    for _ in range(6000):
        start=effects.next_scene
        effects.schedule()
        if effects.previous=='cloud':
            cloud_slots+=1
            clouds+=bool(effects.upcoming)
            # Even a silent slot reserves its original cloud duration and inter-scene delay.
            assert start+10.79*RATE<=effects.next_scene<=start+23.21*RATE
        else:
            assert effects.upcoming
            guns+=1
        effects.upcoming.clear()
        effects.accents.clear()
    assert cloud_slots==6000//len(categories) and guns==6000-cloud_slots
    assert .31<clouds/cloud_slots<.36
print('Passed cloud filtering, clean exit, cancellation, 12 channels and one-in-three scheduling.')
