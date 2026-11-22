"""Exercise the Windows output loop while a replacement song is still decoding."""
import json
import random
import sys
import tempfile
import threading
from pathlib import Path
from queue import Queue
from unittest.mock import patch

import numpy as np

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'app'))
from bridge import Player
from cockpit import CockpitEffects, Cue
from engine import RATE
from surround import FULL, STEREO, SurroundMix


class Sink:
    backend='PCM'
    rate=RATE
    sample_format='Float32'

    def __init__(self,mask):
        self.mask=mask
        self.demand=threading.Event()
        self.blocks=Queue()
        self.closed=threading.Event()

    def available(self):
        if not self.demand.is_set():return 0
        self.demand.clear()
        return 480

    def write(self,block):self.blocks.put(block)
    def drained(self):return True
    def close(self):self.closed.set()

    def read(self):
        self.demand.set()
        return self.blocks.get(timeout=3)


def effects(engine,alerts):
    fx=CockpitEffects(rng=random.Random(9))
    signal=(2000+np.arange(4096,dtype=np.int16)%971)[:,None]
    fx.engine.layers=[signal]*3
    fx.engine.enabled=engine
    fx.set_categories(())
    fx.set_enabled(alerts)
    if alerts:
        fx.samples['gun_body']=signal
        # One voice straddles the song switch. Another starts during the slow read.
        for start in (0,1024):
            fx.upcoming.append(Cue('gun_body',start,start+len(signal),.5,0))
    return fx


def write_catalog(base):
    length=RATE
    songs=[];segments={}
    for i in (1,2):
        song=dict(id=i,title=str(i),bank='test',event='',order=[i],labels={str(i):str(i)},loops={str(i):1},next={str(i):None})
        lane=dict(key=str(i),id=i,on=True,variant=None,db=0,clips=[dict(src=str(i),at=0,end=length,offset=0,env=[])])
        segments[i]=dict(id=i,entry=0,exit=length,length=length,anc=[i],bar=0,beat=0,grid=0,tempo=0,marks=[],lanes=[lane])
        songs.append(song)
    (base/'library').mkdir()
    (base/'library/score.json').write_text(json.dumps(dict(songs=songs,segments=segments,rules={})))
    (base/'library/arrangements.json').write_text('{}')


def check(base,engine,alerts,mask):
    waiting=threading.Event();resume=threading.Event()
    def load(media,ids,progress,cancelled):
        if ids=={'2'}:
            waiting.set()
            assert resume.wait(4)
        media.files={key:np.zeros((128,2),np.float32) for key in ids}
        return not cancelled()

    sink=Sink(mask)
    with patch('bridge.Media.load',load),patch('bridge.Media.close'),patch('device.open_stream',return_value=sink) as opened:
        player=Player(base,initial_song=1)
        try:
            player.loader.submit(lambda:None).result(timeout=3)
            fx=effects(engine,alerts); reference=effects(engine,alerts)
            mixer=None if mask==STEREO else SurroundMix(mask)
            def assert_next():
                dry=np.zeros((480,2),np.float32)
                reference.filter_music(dry)
                if mixer:mixer.begin(dry)
                reference.mix(dry,1.,mixer)
                expected=(mixer.finish(dry,.65) if mixer else dry*.65)/32768
                np.testing.assert_allclose(sink.read(),expected,rtol=1e-6,atol=1e-9)

            player.transport.effects=fx
            player.dispatch({'op':'play'})
            assert_next()
            if engine and alerts:
                # Also cover the wait for the playlist controller after a mid-block end.
                player.dispatch({'op':'state','song':1,'hasNextSong':True})
                with player.lock:player.transport.start(1,RATE-73)
                assert_next();assert player.state()['finished']
                assert_next()
            player.select(2,True)
            assert waiting.wait(3)
            for _ in range(4):assert_next()
            assert player.transport.clock==0 and not player.ready
            assert not sink.closed.is_set() and opened.call_count==1
            resume.set();player.loader.submit(lambda:None).result(timeout=3)
            assert_next()
            assert player.ready and player.transport.clock==480
            # A finite queue with no successor must drain at its real end.
            with player.lock:player.transport.start(2,RATE-73)
            tail=sink.read()
            assert player.state()['finished']
            if mask==STEREO:assert not tail[73:].any()
            assert sink.closed.wait(3) and opened.call_count==1
            player.dispatch({'op':'state','song':2,'hasNextSong':True})
            assert not player.continue_cockpit()
        finally:
            resume.set();player.close()

with tempfile.TemporaryDirectory() as folder:
    base=Path(folder);write_catalog(base)
    for args in [(True,False,STEREO),(False,True,STEREO),(True,True,FULL)]:
        check(base,*args)
print('Passed continuous engine and cue PCM across natural ends and blocked song loads.')
