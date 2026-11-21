"""Spectral slices from the stereo mix sent to the output device."""
from collections import deque

import numpy as np


class MixSpectrum:
    def __init__(self, rate=48000, block_size=960):
        self.window = np.hanning(4 * block_size)[:, None]
        self.scale = 2 / (32768 * self.window.sum())
        self.edges = np.rint(np.geomspace(38, 16500, 57) * len(self.window) / rate).astype(int)
        self.blocks = deque(maxlen=4)
        self.frames = deque(maxlen=12)
        self.epoch = 0
        self.reset()

    def reset(self):
        self.blocks.clear()
        self.frames.clear()
        self.epoch += 1
        self.block_count = 0

    def push(self, stereo):
        self.blocks.append(stereo)
        self.block_count += 1
        if len(self.blocks) < 4 or self.block_count % 2:
            return
        samples = np.concatenate(self.blocks) * self.window
        fft = np.fft.rfft(samples, axis=0)
        # Combine channel power so that out-of-phase stereo is still visible.
        magnitude = np.sqrt(np.mean(np.abs(fft) ** 2, axis=1)) * self.scale
        peaks = np.maximum.reduceat(magnitude[:self.edges[-1]], self.edges[:-1])
        db = 20 * np.log10(np.maximum(peaks, 1e-8))
        # Reserve the top of the scale for 0 dBFS so a strong band keeps its
        # dynamics between -12 dBFS and full scale.
        levels = np.clip((db + 70) / 70, 0, 1)
        self.frames.append((self.block_count // 2, np.rint(levels * 255).astype(int).tolist()))
