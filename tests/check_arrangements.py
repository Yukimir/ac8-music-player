import argparse
import json
import sys
from pathlib import Path
import numpy as np

base=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser()
parser.add_argument('--player',type=Path)
parser.add_argument('--audio-library',type=Path)
args=parser.parse_args()
if bool(args.player)!=bool(args.audio_library):parser.error('pass --player and --audio-library together')
sys.path.insert(0,str(base/'app'))
from arrangement import normalize
from engine import Media,Transport,DEFAULT_RULE

def section(sid):
    lanes=[dict(key=str(j),id=j,variant=None,on=j==1,db=0,clips=[dict(src=str(j),at=0,end=6000,offset=0,env=[])]) for j in (1,2)]
    return dict(id=sid,entry=0,exit=6000,length=6000,anc=[sid,99],bar=2000,beat=500,grid=100,
                gridOffset=0,marks=[],lanes=lanes)

song=dict(id=1,order=[1,2],labels={},next={},loops={})
segs={i:section(i) for i in (1,2,3)}
rule=dict(DEFAULT_RULE,src=[2],dst=[1],bridge=dict(id=3,pre=0,post=0,inc=[0,0,4],out=[0,0,4]))
t=Transport(dict(segments=segs,rules={99:[rule]}))
t.audio={str(j):np.full((6000,2),j*1000,np.int16) for j in (1,2)}
t.master=1
def row(sid,repeat,lanes,changes=()):
    return dict(segment=sid,repeat=repeat,lanes=lanes,changes=list(changes))
def change(run,bar,keys):return dict(run=run,bar=bar,lanes=keys)
steps=[row(2,2,['1'],[change(2,2,['1','2']),change(1,2,['2'])]),row(2,1,[]),row(1,1,['1'])]
a=normalize(dict(id='test',steps=steps),song,segs)
t.select(song,arrangement=a);t.playing=True
assert t.key==2 and t.run==1 and t.step_index==0 and t.queued is None
t.queue(1);assert not t.jump(1) and t.queued is None
r=t.render(6000)[:,0]
assert (r[:2001]==1000).all() and (r[2720:]==2000).all() and 1000<r[2380]<2000
assert t.key==2 and t.run==2 and t.step_index==0
r=t.render(6000)[:,0]
assert (r[:2001]==2000).all() and (r[2720:]==3000).all()
assert t.key==2 and t.run==1 and t.step_index==1 and not t.enabled(segs[2]['lanes'][0])
assert (t.render(6000)==0).all() and t.key==3 and t.active['bridge']
t.render(6000);assert t.key==1 and t.step_index==2
t.render(6000);assert t.finished and not t.playing
t.restart();assert (t.key,t.run,t.step_index)==(2,1,0)
t.start(2,3500,step_index=0,run=2);assert t.active['mask']=={'1','2'}
t.start(2,500,step_index=0,run=2);assert t.active['mask']=={'2'}
t.start(2,500,step_index=0,run=1);assert t.active['mask']=={'1'}
t.toggle(segs[2]['lanes'][0]);assert not t.active['mask'] and a['steps'][0]['lanes']==['1']
for invalid in ([row(2,0,[]),row(1,1,[])],[row(2,1,[],[change(2,1,['1'])])],
                [row(2,1,['invalid'])],[row(3,1,[])],[row(2,1,[],[change(1,4,[])])]):
    try:normalize(dict(id='test',steps=invalid),song,segs)
    except ValueError:pass
    else:raise AssertionError(invalid)
t.select(song,arrangement=normalize(dict(id='tail',steps=[row(1,1,[]),row(2,1,[])],loopFrom=0),song,segs))
t.playing=True;t.render(12000);assert t.active.get('bridge')
t.render(6000);assert (t.key,t.step_index,t.run)==(1,0,1)
t.select(song,arrangement=normalize(dict(id='end',steps=[row(2,0,[])]),song,segs))
t.playing=True;t.render(18000);assert t.run==4 and t.step_index==0
short=row(1,2,['1']);short['bars']=1
t.select(song,arrangement=normalize(dict(id='short',steps=[short,row(2,1,[])]),song,segs))
t.playing=True;t.render(2000);assert t.run==2 and t.key==1
t.render(2000);assert t.step_index==1 and t.key==2
try:normalize(dict(id='bad',steps=[{**short,'changes':[change(1,2,[])]}]),song,segs)
except ValueError:pass
else:raise AssertionError('cue outside shortened pass')

d=json.loads((base/'library/score.json').read_bytes())
a=json.loads((base/'library/arrangements.json').read_bytes());t=Transport(d)
assert len(a)==sum(s['bank']=='互动配乐' for s in d['songs'])
for s in d['songs']:
    if str(s['id']) not in a:continue
    p=normalize(a[str(s['id'])],s,t.segs)
    t.select(s,arrangement=p)
    # Use silent media while walking every real rule, bridge, exit and track state.
    for index,step in enumerate(p['steps']):
        seg=t.segs[step['segment']]
        t.start(seg['id'],max(0,seg['exit']-32),step_index=index,run=step['repeat'] or 1)
        target=index if not step['repeat'] else index+1 if index+1<len(p['steps']) else p['loopFrom']
        if target is None:assert not t.pending
        else:
            when,voice=t.pending[-1]
            assert voice['index']==target and voice['id']==p['steps'][target]['segment']
            if t.rule(t.key,voice['id'])['entry']==1:
                assert when-voice['origin']==t.segs[voice['id']]['entry']
print('Passed PCM automation, repeat and seek, natural bridges, terminal loops, free lock and all',len(a),'defaults.')

# The Ms28C play event enters a two-phrase intro followed by a repeating
# three-phrase playlist. Only its separate Stop event selects the B ending.
shadow=next(s for s in d['songs'] if s['event']=='AKE_Bgm_Ms28C_Play')
default=a[str(shadow['id'])]
ids=[195120364,569774478,109072791,385651075,78385800]
assert [r['segment'] for r in default['steps']]==ids
assert default['loopFrom']==2 and default['stopEvents']==['AKE_Bgm_Ms28C_Stop']
assert 293608425 in shadow['order']  # The ending can still be added to a custom arrangement.
# Ms20C also has an independent End_State event and keeps its ending.
other=next(s for s in d['songs'] if s['event']=='AKE_Bgm_Ms20C_Play')
assert a[str(other['id'])]['steps'][-1]['segment']==581231307
if not args.player:
    print('Shadow 22 playlist verified; pass --player and --audio-library for the optional PCM comparison.')
    sys.exit(0)
media=Media(args.player,args.audio_library)
try:
    free=Transport(d);free.select(shadow)
    t.select(shadow,arrangement=normalize(default,shadow,t.segs))
    media.load(free.assets(shadow))
    free.audio=t.audio=media.files
    free.playing=t.playing=True
    for index in [0,1,2,3,4,2,3,4,2,3,4]:
        assert free.key==t.key==ids[index] and t.step_index==index
        boundary=free.pending[0][0]
        assert t.pending[0][0]==boundary>t.clock
        while t.clock<boundary:
            n=min(32768,boundary-t.clock)
            assert np.array_equal(t.render(n),free.render(n))
        assert t.playing and not t.finished
    assert free.key==t.key==ids[2] and t.step_index==2
finally:
    media.close()
print('Shadow 22 matched free playback sample-for-sample through three A5 -> A3 returns.')
