"""Exercise the game-backed resolver with a separate cache using the portable runtime."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--player',required=True,type=Path,help='Unpacked portable build')
parser.add_argument('--game',required=True,help='ACE COMBAT 8 installation')
args=parser.parse_args()
sys.path.insert(0,str(args.player/'app'))
from bridge import add_native_media
from engine import Media,Transport
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
        assert np.array_equal(transport.render(48000,SurroundMix(FULL)),media.files[key][:48000])
    finally:media.close()
    assert not library.cache.exists()
    # Simulate a portable copy with just one extracted WEM, then disconnect the game.
    normal=next(iter(library.spec['music']))
    dest=library.cache/'wem'/(normal+'.wem')
    dest.parent.mkdir(parents=True)
    with library.music(normal,root) as direct:dest.write_bytes(direct.read_bytes())
    assert library.start('')['phase']=='ready'
    offline=LibraryImport(args.player,root/'audio')
    with offline.music(normal,root) as stored:assert stored==dest
    try:
        with offline.music(key,root):raise AssertionError('unavailable native asset accepted')
    except ValueError as exc:assert key.removeprefix('native-') in str(exc)
    assert not offline.config['native'] and offline.source is None
    offline.close();library.close()
print('Passed direct native PCM identity, portable settings and partial offline cache fallback.')
