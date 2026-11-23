"""Regression for changing Android cockpit settings during an in-flight sample load."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import wave

import numpy as np

repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo/'android/app/src/main/python'), str(repo/'app')]
from mobile_player import Player


class Music:
    def render(self, frames):
        return np.ones((frames, 2), dtype=np.int16)


def settle(player):
    deadline = time.monotonic() + 5
    while player.loading:
        assert time.monotonic() < deadline, "Cue loader failed to finish"
        player.render(256)
        time.sleep(.001)


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    with wave.open(str(root/'sound.wav'), 'wb') as wav:
        wav.setparams((1, 2, 48000, 0, 'NONE', ''))
        wav.writeframes(bytes(48000))
    player = Player(root)
    player.t = Music()
    cues = {key: 'sound' for keys in player.effects.SCENE_CLIPS.values() for key in keys}
    (root/'catalog.json').write_text(json.dumps({'cues': cues}))
    started, release = threading.Event(), threading.Event()
    load = player._load_samples

    def delayed(request):
        started.set()
        assert release.wait(5), "Audio thread waited for cue preparation"
        return load(request)

    player._load_samples = delayed
    try:
        player.configure_effects(True, False, 'gun', 1.)
        assert started.wait(2)
        for i in range(100):
            player.configure_effects(True, False, 'stall', i/100)
            assert len(player.render(256)) == 1024
        assert player.t.se_volume == .99
        assert not player.effects.enabled
        release.set()
        settle(player)
        assert player.effects.categories == ('stall',) and player.effects.enabled
        assert not player.effects_error
        player.configure_effects(True, False, 'gun', .5)
        assert player.loading is None and player.effects.categories == ('gun',)
        player.configure_effects(True, False, '', .5)
        assert not player.effects.enabled
        player.configure_effects(False, True, '', .5)
        settle(player)
        assert player.effects_error and not player.effects.engine.enabled
        assert len(player.render(256)) == 1024
    finally:
        release.set()
        player.close()
print('Passed nonblocking audio, latest volume and category, cached selection and isolated cue failure.')
