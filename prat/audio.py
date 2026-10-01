"""Small audio helpers: WAV encode/decode and resampling (no ffmpeg needed)."""

from __future__ import annotations

import io
from math import gcd

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


def to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.asarray(samples, dtype=np.float32), sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def read_audio(data: bytes) -> tuple[np.ndarray, int]:
    """Decode WAV/FLAC/OGG bytes into mono float32 samples."""
    samples, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return samples.mean(axis=1), sr


def resample(samples: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    if sr == target_sr:
        return samples.astype(np.float32, copy=False)
    g = gcd(sr, target_sr)
    return resample_poly(samples, target_sr // g, sr // g).astype(np.float32)


def trim_silence(samples: np.ndarray, sr: int, threshold: float = 0.01, pad_s: float = 0.15) -> np.ndarray:
    """Cut leading/trailing near-silence; push-to-talk recordings usually have some."""
    loud = np.flatnonzero(np.abs(samples) > threshold)
    if loud.size == 0:
        return samples[:0]
    pad = int(pad_s * sr)
    return samples[max(0, loud[0] - pad) : loud[-1] + pad]
