"""Model downloads and warm-up.

All models are fetched once (``prat setup`` or on first start) and then used offline:

* Silero VAD           ./models/silero         ~2 MB
* Piper voices         ./models/piper          ~130 MB
* NB-Whisper (MLX)     Hugging Face cache      ~500 MB (small)
* Borealis 4B (MLX)    Hugging Face cache      ~4.5 GB (8-bit)
"""

from __future__ import annotations

import logging

from . import config

log = logging.getLogger("prat.models")


def download_all() -> None:
    """Fetch every model the app needs. Safe to re-run: cached files are skipped."""
    from huggingface_hub import snapshot_download

    from .live.vad import model_path
    from .tts import PIPER_VOICES, PiperEngine

    steps = [
        ("Silero VAD (voice activity)", model_path),
        *[(f"Piper voice '{name}'", PiperEngine(name).download) for name in PIPER_VOICES],
        (f"NB-Whisper ({config.STT_MODEL})", lambda: snapshot_download(config.STT_MODEL)),
        (f"Borealis ({config.MLX_MODEL})", lambda: _download_lm(snapshot_download)),
    ]
    for i, (label, fn) in enumerate(steps, 1):
        print(f"[{i}/{len(steps)}] {label}", flush=True)
        fn()
    print("All models ready.", flush=True)


def _download_lm(snapshot_download) -> None:
    from pathlib import Path

    if not Path(config.MLX_MODEL).exists():  # a local folder needs no download
        snapshot_download(config.MLX_MODEL)


def all_cached() -> bool:
    """True if every model is already on disk (then the app can run fully offline)."""
    from pathlib import Path

    from huggingface_hub import snapshot_download

    from .tts import PIPER_VOICES

    piper = config.MODELS_DIR / "piper"
    if not (config.MODELS_DIR / "silero" / "silero_vad.onnx").exists():
        return False
    if not all((piper / f"{base}.onnx").exists() for base in PIPER_VOICES.values()):
        return False
    try:
        snapshot_download(config.STT_MODEL, local_files_only=True)
        if not Path(config.MLX_MODEL).exists():
            snapshot_download(config.MLX_MODEL, local_files_only=True)
    except Exception:
        return False
    return True


def go_offline() -> None:
    """Make huggingface_hub (used by mlx-lm / mlx-whisper loaders) read only the local cache."""
    import os

    import huggingface_hub.constants as hf_constants
    from huggingface_hub.utils import disable_progress_bars

    os.environ["HF_HUB_OFFLINE"] = "1"
    hf_constants.HF_HUB_OFFLINE = True
    disable_progress_bars()


def warm_up(stt, tts) -> str:
    """Load and warm every model so the learner's first turn is as fast as the rest."""
    from .live.vad import SileroVAD
    from .local_lm import get_local_lm

    SileroVAD()
    tts.speak("Hei!")
    stt._load()
    lm = get_local_lm()
    lm.warmup()
    return lm.name
