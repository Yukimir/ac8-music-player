"""Exercise the game-backed resolver with a separate cache using the portable runtime."""
import argparse
import json
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--player',required=True,type=Path,help='Unpacked portable build')
parser.add_argument('--game',required=True,help='ACE COMBAT 8 installation')
args=parser.parse_args()
sys.path.insert(0,str(args.player/'app'))
from bridge import add_native_media
from cockpit import CLOUD_CLIPS, CLOUD_SOURCE_START, CockpitEffects
from engine import Media,Transport,RATE
from game_import import LibraryImport
from surround import FULL,SurroundMix

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    library=LibraryImport(args.player,root/'audio')
    assert library.status()['phase']=='missing' and not library.status()['extract']
    assert library.start(args.game)['phase']=='ready'
    info=json.loads((args.player/'library/score.json').read_bytes())
    add_native_media(info,library.native)
    track=next(s for s in info['songs'] if s['bank']=='原生 7.1.4' and 'Hg' in s['event'])
    transport=Transport(info)
    transport.select(track)
    key,=transport.assets(track)
    media=Media(args.player,resolver=library)
    try:
        assert media.load([key])
        transport.audio=media.files
        transport.master=1
        transport.playing=True
        original=media.files[key][:48000]
        assert original.dtype==np.float32 and media.sample_rate==48000
        assert np.any(original*32768!=np.rint(original*32768))
        assert np.array_equal(transport.render(48000,SurroundMix(FULL),floating=True),np.clip(original,-1,1))
        # The mission recording loops mid-file. The lobby recording of the same song ends.
        versions=[s for s in info['songs'] if s.get('onlineBank') and s['title']=='Daredevil']
        assert len(versions)==2 and {s['loops'][str(s['id'])] for s in versions}=={0,1}
        for song in versions:
            transport.select(song)
            source,=transport.assets(song)
            assert media.load([source])
            transport.audio=media.files
            samples=media.files[source]
            segment=transport.segs[song['id']]
            assert len(samples)==segment['length']
            end=segment['exit']
            transport.start(song['id'],end-128)
            transport.playing=True
            rendered=transport.render(256,floating=True)
            gain=10**(segment['lanes'][0]['db']/20)
            np.testing.assert_allclose(rendered[:128],samples[end-128:end]*gain,atol=1e-7)
            if song['loops'][str(song['id'])]==0:
                start=segment['entry']
                assert 0<start<end<len(samples) and transport.run==2
                np.testing.assert_allclose(rendered[128:],samples[start:start+128]*gain,atol=1e-7)
            else:
                assert transport.finished and not np.any(rendered[128:])
    finally:media.close()
    effects=CockpitEffects()
    with library.cockpit() as fetch:effects.prepare(fetch,('cloud',))
    for name in CLOUD_CLIPS:
        assert effects.samples[name].shape[1]==2 and len(effects.samples[name])>8*48000
    assert not library.cache.exists()
    # Simulate a portable copy with just one extracted WEM, then disconnect the game.
    normal=source
    dest=library.cache/'wem'/(normal+'.wem')
    dest.parent.mkdir(parents=True)
    with library.music(normal,root) as direct:dest.write_bytes(direct.read_bytes())
    cloud_dir=library.cache/'cockpit'
    cloud_dir.mkdir()
    with library.cockpit() as fetch:
        for name in CLOUD_CLIPS:(cloud_dir/(name+'.wav')).write_bytes(fetch(name).read_bytes())
    assert library.start('')['phase']=='ready'
    offline=LibraryImport(args.player,root/'audio')
    with offline.music(normal,root) as stored:assert stored==dest
    cached_effects=CockpitEffects()
    with offline.cockpit() as fetch:
        cached_effects.prepare(fetch,('cloud',))
        for name in CLOUD_CLIPS:
            path=fetch(name)
            assert path==cloud_dir/(name+'.wav')
            with wave.open(str(path),'rb') as wav:
                raw=np.frombuffer(wav.readframes(wav.getnframes()),'<i2').reshape(-1,2)
            aligned=raw[CLOUD_SOURCE_START[name]:]
            assert len(aligned)>=round(8.2*RATE)
            np.testing.assert_array_equal(effects.samples[name],aligned)
            np.testing.assert_array_equal(cached_effects.samples[name],aligned)
    try:
        with offline.music(key,root):raise AssertionError('unavailable native asset accepted')
    except ValueError as exc:assert key.removeprefix('native-') in str(exc)
    assert not offline.config['native'] and offline.source is None
    offline.close();library.close()
print('Passed native PCM identity, online mission loop and lobby ending, cloud recordings and offline cache fallback.')
