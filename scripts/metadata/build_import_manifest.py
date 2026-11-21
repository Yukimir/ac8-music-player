"""Build a release recipe of file locations, block lengths and hashes only."""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

base=Path(__file__).resolve().parents[2]
work=base/'work'
sys.path.insert(0,str(base/'app'))
from import_cockpit import SOURCE_IDS, MIXES, ENGINES

parser=argparse.ArgumentParser()
parser.add_argument('game',type=Path)
parser.add_argument('build')
parser.add_argument('--reference-library',type=Path,required=True,
                    help='Local extraction containing media-index.json and the generated cockpit WAVs')
args=parser.parse_args()
paks=args.game/'Game/Content/Paks'
lib=base/'library'
rows=json.loads((work/'types.json').read_bytes())
media=json.loads((args.reference_library/'media-index.json').read_bytes())
score=json.loads((lib/'score.json').read_bytes())
needed={c['src'] for s in score['segments'].values() for l in s['lanes'] for c in l['clips']}

def entry(index,offset,size,raw_hash):
    row=rows[index];pak=row['pak']+'.pak';pos=row['pos'];block=row['block']
    assert not row['flag'] and row['m']==1 and block==65536 and 0<size<=row['u']-offset
    first=offset//block;last=(offset+size-1)//block
    with (paks/pak).open('rb') as f:
        f.seek(pos+52+16*first)
        spans=list(struct.iter_unpack('<QQ',f.read(16*(last-first+1))))
        assert all(a[1]==b[0] for a,b in zip(spans,spans[1:]))
        at=pos+spans[0][0];f.seek(at)
        packed=f.read(spans[-1][1]-spans[0][0])
    return dict(pak=pak,at=at,blocks=[b-a for a,b in spans],trim=offset%block,
                last=min(block,row['u']-last*block),size=size,sha256=raw_hash,
                packed=hashlib.sha256(packed).hexdigest())

music={}
for k in sorted(needed):
    m=media[k]
    music[k]=entry(m['entry'],0,m['size'],m['sha256'])
effects={}
with (work/'meta/4727.bnk').open('rb') as f:
    while header:=f.read(8):
        tag,size=struct.unpack('<4sI',header)
        if tag==b'DIDX':index=list(struct.iter_unpack('<III',f.read(size)))
        elif tag==b'DATA':
            start=f.tell()
            for k,off,n in index:
                if k not in SOURCE_IDS:continue
                f.seek(start+off)
                effects[str(k)]=entry(4727,start+off,n,hashlib.sha256(f.read(n)).hexdigest())
            break
        else:f.seek(size,1)
assert len(effects)==len(SOURCE_IDS)
cues={k:(args.reference_library/'cockpit'/(k+'.wav')).stat().st_size for k in sorted(MIXES.keys()|ENGINES.keys())}
manifest=dict(build=args.build,recipe=1,music=music,effects=effects,cues=cues)
(lib/'import.json').write_text(json.dumps(manifest,separators=(',',':'),sort_keys=True),encoding='utf8')
print('music',len(music),'effect sources',len(effects),'rendered cues',len(cues))
