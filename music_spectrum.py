"""Offline FFT prototype for the existing 8 kHz PCM side channel.

Optional analysis for the existing LED worker. No computation on the speaker.
Three band RMS estimates, not source separation or voice/drum recognition.
"""
import array
import cmath
import math


def fft_bands(raw, sample_rate=8000, size=1024):
    if size < 32 or size > 8192 or size & (size - 1) or sample_rate <= 0:
        raise ValueError('Expected a power-of-two FFT size and positive sample rate')
    limit = min(len(raw), size * 2)
    samples = array.array('h', raw[:limit // 2 * 2])
    count = len(samples)
    if count < 2:
        return [0., 0., 0.]
    average = sum(samples) / count
    window = [.5 - .5 * math.cos(2 * math.pi * i / (count - 1)) for i in range(count)]
    norm = size * sum(w * w for w in window)
    if norm == 0:
        return [0., 0., 0.]
    values = [complex((value - average) / 32768 * window[i]) for i, value in enumerate(samples)]
    values += [0j] * (size - count)
    j = 0
    for i in range(1, size):
        bit = size >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            values[i], values[j] = values[j], values[i]
    width = 2
    while width <= size:
        step = cmath.exp(-2j * math.pi / width)
        for start in range(0, size, width):
            phase = 1 + 0j
            for k in range(width // 2):
                left = values[start + k]
                right = phase * values[start + k + width // 2]
                values[start + k] = left + right
                values[start + k + width // 2] = left - right
                phase *= step
        width <<= 1
    energy = [0., 0., 0.]
    for i in range(1, size // 2):
        frequency = i * sample_rate / size
        band = 0 if 40 <= frequency < 220 else 1 if 220 <= frequency < 1600 else 2 if 1600 <= frequency <= 4000 else None
        if band is not None:
            energy[band] += 2 * abs(values[i]) ** 2 / norm
    return [math.sqrt(power) for power in energy]
