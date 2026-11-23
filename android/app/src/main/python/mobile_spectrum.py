"""Read the same stereo PCM blocks that AudioTrack receives, on the visual worker."""
import json
from itertools import count

import numpy as np

from spectrum import MixSpectrum

_epochs=count(1)

class Analyzer(MixSpectrum):
    def packet(self, pcm, reset):
        if reset:
            self.reset()
            # A resumed view gets a new analyzer and starts numbering FFT frames at one.
            self.epoch=next(_epochs)
        self.push(np.frombuffer(pcm,dtype='<i2').reshape(-1,2))
        return json.dumps(dict(playing=True,ready=True,spectrumEpoch=self.epoch,
                               spectrumHopMs=self.hop_ms,spectralFrames=list(self.frames)),
                          separators=(',',':'))
