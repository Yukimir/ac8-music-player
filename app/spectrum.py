"""Spectral slices from the stereo mix sent to the output device."""
from collections import deque

import numpy as np


class MixSpectrum:
    def __init__(self, rate=48000, block_size=960):
        self.block_size = block_size
        self.window = np.hanning(4 * block_size)[:, None]
        self.scale = 2 / (32768 * self.window.sum())
        edges_hz = np.geomspace(38, 16500, 57)
        self.edges = np.rint(edges_hz * len(self.window) / rate).astype(int)
        # Keep the long window for pitch resolution in the bass. A shorter,
        # more recent window follows percussion and attacks in the upper bands.
        self.split = int(np.searchsorted(edges_hz, 400))
        self.short_window = np.hanning(2 * block_size)[:, None]
        self.short_edges = np.rint(edges_hz[self.split:] * len(self.short_window) / rate).astype(int)
        self.short_scale = 2 / (32768 * self.short_window.sum())
        self.hop_ms = 1000 * block_size / rate
        self.blocks = deque(maxlen=4)
        self.frames = deque(maxlen=12)
        self.epoch = 0
        self.reset()

    def reset(self):
        self.pending = np.empty((0,2),dtype='<i2')
        self.blocks.clear()
        self.frames.clear()
        self.epoch += 1
        self.block_count = 0

    def push(self, stereo):
        if len(self.pending):
            stereo = np.concatenate((self.pending,stereo))
        stop = len(stereo)//self.block_size*self.block_size
        self.pending = stereo[stop:].copy()
        for start in range(0,stop,self.block_size):
            self.analyze(stereo[start:start+self.block_size])

    def analyze(self, stereo):
        self.blocks.append(stereo)
        self.block_count += 1
        if len(self.blocks) < 4:
            return
        samples = np.concatenate(self.blocks)
        low = self.peaks(samples, self.window, self.edges[:self.split+1], self.scale)
        high = self.peaks(samples[-len(self.short_window):], self.short_window, self.short_edges, self.short_scale)
        peaks = np.concatenate((low, high))
        db = 20 * np.log10(np.maximum(peaks, 1e-8))
        # Reserve the top of the scale for 0 dBFS so a strong band keeps its
        # dynamics between -12 dBFS and full scale.
        levels = np.clip((db + 70) / 70, 0, 1)
        self.frames.append((self.block_count, np.rint(levels * 255).astype(int).tolist()))

    @staticmethod
    def peaks(samples, window, edges, scale):
        fft = np.fft.rfft(samples * window, axis=0)
        # Combine channel power so that out-of-phase stereo is still visible.
        magnitude = np.sqrt(np.mean(np.abs(fft) ** 2, axis=1)) * scale
        return np.maximum.reduceat(magnitude[:edges[-1]], edges[:-1])
