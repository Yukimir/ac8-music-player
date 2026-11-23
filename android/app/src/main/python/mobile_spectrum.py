"""Read the same stereo PCM blocks that AudioTrack receives, on the visual worker."""
import json
from collections import deque
from itertools import count

import numpy as np

from spectrum import MixSpectrum

_epochs=count(1)

class Analyzer(MixSpectrum):
    def __init__(self):
        super().__init__()
        self.frames=deque(maxlen=64)

    def ingest(self, pcm, reset):
        if reset:
            self.reset()
            # A resumed view gets a new analyzer and starts numbering FFT frames at one.
            self.epoch=next(_epochs)
        self.push(np.frombuffer(pcm,dtype='<i2').reshape(-1,2))
    def packet(self, pcm, reset, after=-1):
        self.ingest(pcm,reset)
        return json.dumps(dict(playing=True,ready=True,holdHistory=True,spectrumEpoch=self.epoch,
                               spectrumHopMs=self.hop_ms,spectralFrames=[f for f in self.frames if f[0]>after]),
                          separators=(',',':'))
