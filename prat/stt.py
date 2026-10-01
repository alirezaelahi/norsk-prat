"""Speech recognition with NB-Whisper (National Library of Norway), run on MLX.

Community MLX conversions of NB-Whisper are used (fastest option on Apple silicon):
small ≈ 0.25 s, medium ≈ 0.7 s for a 4 s utterance on an M1 Pro.
"""

from __future__ import annotations

import re
import threading

import numpy as np

from . import config
from .audio import read_audio, resample, trim_silence

SR = 16_000

# Whisper's classic hallucinations on silence/noise (Norwegian subtitle credits etc.).
_HALLUCINATIONS = re.compile(
    r"^(teksting|tekst(et)? av|undertekst|nrk|takk for (at du så|oppmerksomheten)|musikk|\[.*\]|\(.*\))\b",
    re.I,
)


class STT:
    def __init__(self, model: str = config.STT_MODEL):
        self.model = model
        self._lock = threading.Lock()

    def _load(self):
        """Warm up: load weights and compile with a short silent clip."""
        self.transcribe_array(np.zeros(SR // 2, dtype=np.float32), min_seconds=0)

    def transcribe_array(self, samples: np.ndarray, min_seconds: float = 0.3) -> str:
        """Transcribe 16 kHz mono float32 audio. Temperature 0 and no conditioning on
        previous text, so the learner's own errors are kept rather than "fixed"."""
        import mlx_whisper

        if len(samples) < SR * min_seconds:
            return ""
        from .local_lm import MLX_LOCK

        with self._lock, MLX_LOCK:
            result = mlx_whisper.transcribe(
                samples.astype(np.float32),
                path_or_hf_repo=self.model,
                language="no",
                task="transcribe",
                temperature=0.0,
                condition_on_previous_text=False,
            )
        parts = []
        for seg in result.get("segments", []):
            text = seg.get("text", "").strip()
            if not text or seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -0.8:
                continue
            if _HALLUCINATIONS.match(text) or not re.search(r"\w", text):
                continue
            parts.append(text)
        return " ".join(parts).strip()

    def transcribe(self, audio: bytes) -> str:
        samples, sr = read_audio(audio)
        samples = trim_silence(resample(samples, sr, SR), SR)
        peak = float(np.abs(samples).max()) if len(samples) else 0.0
        if 0 < peak < 0.5:  # normalise quiet laptop-mic recordings
            samples = samples * (0.5 / peak)
        return self.transcribe_array(samples)
