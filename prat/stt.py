"""Speech recognition with NB-Whisper (National Library of Norway)."""

from __future__ import annotations

import threading

import numpy as np

from . import config
from .audio import read_audio, resample, trim_silence

SR = 16_000


class STT:
    def __init__(self, model: str = config.STT_MODEL):
        self.model = model
        self._pipe = None
        self._lock = threading.Lock()

    def _load(self):
        if self._pipe is None:
            import torch
            from transformers import pipeline

            if torch.backends.mps.is_available():
                device, dtype = "mps", torch.float16
            else:
                device, dtype = "cpu", torch.float32
            self._pipe = pipeline("automatic-speech-recognition", model=self.model, device=device, dtype=dtype)
        return self._pipe

    def transcribe(self, audio: bytes) -> str:
        samples, sr = read_audio(audio)
        samples = trim_silence(resample(samples, sr, SR), SR)
        if len(samples) < SR * 0.3:  # under 0.3 s: nothing was said
            return ""
        # Normalise quiet laptop-mic recordings.
        peak = float(np.abs(samples).max())
        if 0 < peak < 0.5:
            samples = samples * (0.5 / peak)
        with self._lock:
            out = self._load()(
                {"raw": samples, "sampling_rate": SR},
                generate_kwargs={"language": "no", "task": "transcribe"},
            )
        return out["text"].strip()
