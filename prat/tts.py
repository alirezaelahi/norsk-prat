"""Norwegian text-to-speech engines.

Each voice has an id like ``piper:talesyntese`` or ``piper:nvcc:KON``.
Engines load lazily on first use; synthesis results are cached in memory.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config
from .audio import to_wav_bytes

PIPER_REPO = "rhasspy/piper-voices"
PIPER_VOICES = {
    "talesyntese": "no/no_NO/talesyntese/medium/no_NO-talesyntese-medium",
    "nvcc": "no/no_NO/nvcc/medium/no_NO-nvcc-medium",
}
# nvcc speaker codes: first letter is gender (verified by pitch: K ~170-210 Hz,
# M ~100-115 Hz); the two-letter suffix is a regional code from the dataset.
GENDER = {"K": "Kvinne", "M": "Mann"}


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    engine: str
    note: str = ""


def list_voices() -> list[Voice]:
    voices = [Voice("piper:talesyntese", "Talesyntese (mann)", "piper", "Piper · clearest diction")]
    for code, _ in sorted(_nvcc_speakers().items(), key=lambda kv: kv[1]):
        voices.append(
            Voice(
                f"piper:nvcc:{code}",
                f"NVCC {GENDER.get(code[0], '?')} · {code[1:]}",
                "piper",
                "Piper · multi-speaker, regional variety",
            )
        )
    return voices


def _nvcc_speakers() -> dict[str, int]:
    cfg = config.MODELS_DIR / "piper" / f"{PIPER_VOICES['nvcc']}.onnx.json"
    if cfg.exists():
        return json.loads(cfg.read_text())["speaker_id_map"]
    # Known map, so the voice list is complete before the first download.
    codes = ["KNN", "KSV", "MMN", "KON", "MNN", "MSV", "MON", "MNV", "KMN", "KNV"]
    return {c: i for i, c in enumerate(codes)}


class PiperEngine:
    def __init__(self, name: str):
        self.name = name
        self._voice = None
        self._lock = threading.Lock()

    def download(self) -> Path:
        """Fetch the voice once into ./models/piper; afterwards it works offline."""
        from huggingface_hub import hf_hub_download

        base = PIPER_VOICES[self.name]
        local = config.MODELS_DIR / "piper"
        onnx = local / f"{base}.onnx"
        if not (onnx.exists() and onnx.with_suffix(".onnx.json").exists()):
            hf_hub_download(PIPER_REPO, f"{base}.onnx", local_dir=local)
            hf_hub_download(PIPER_REPO, f"{base}.onnx.json", local_dir=local)
        return onnx

    def _load(self):
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(self.download())
        return self._voice

    def synthesize(self, text: str, speed: float, speaker: str | None) -> tuple[np.ndarray, int]:
        from piper import SynthesisConfig

        with self._lock:
            voice = self._load()
            base_scale = voice.config.length_scale or 1.0
            speaker_id = voice.config.speaker_id_map.get(speaker) if speaker else None
            syn = SynthesisConfig(speaker_id=speaker_id, length_scale=base_scale / speed)
            chunks = list(voice.synthesize(text, syn))
            sr = voice.config.sample_rate
        if not chunks:
            return np.zeros(0, dtype=np.float32), sr
        # Short pause between sentence chunks reads more naturally for learners.
        gap = np.zeros(int(0.18 * sr), dtype=np.float32)
        parts: list[np.ndarray] = []
        for c in chunks:
            parts += [c.audio_float_array, gap]
        return np.concatenate(parts[:-1]), sr


class TTS:
    """Voice registry + LRU cache of rendered WAV bytes."""

    def __init__(self, cache_size: int = 512):
        self._engines: dict[str, object] = {}
        self._cache: OrderedDict[tuple, bytes] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()

    def _engine(self, key: str):
        with self._lock:
            if key not in self._engines:
                self._engines[key] = PiperEngine(key)
            return self._engines[key]

    def speak(self, text: str, voice_id: str = config.DEFAULT_VOICE, speed: float = 1.0) -> bytes:
        """WAV bytes, cached."""
        text = " ".join(text.split())
        speed = min(max(speed, 0.5), 1.5)
        key = (voice_id, round(speed, 2), text)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        wav = to_wav_bytes(*self.synthesize(text, voice_id, speed))
        self._cache[key] = wav
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return wav

    def synthesize(self, text: str, voice_id: str = config.DEFAULT_VOICE, speed: float = 1.0) -> tuple[np.ndarray, int]:
        """Raw float samples + sample rate (used by the live pipeline)."""
        speed = min(max(speed, 0.5), 1.5)
        parts = voice_id.split(":")
        if parts[0] == "piper" and len(parts) >= 2 and parts[1] in PIPER_VOICES:
            engine, speaker = self._engine(parts[1]), (parts[2] if len(parts) > 2 else None)
        else:
            raise ValueError(f"unknown voice: {voice_id}")

        return engine.synthesize(text, speed, speaker)
