"""Runtime settings, read from environment variables with local-first defaults."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = Path(os.environ.get("PRAT_MODELS_DIR", ROOT / "models"))
DATA_DIR = Path(os.environ.get("PRAT_DATA_DIR", ROOT / "data"))
WEB_DIR = ROOT / "web"

# Speech recognition: NB-Whisper from the National Library of Norway.
STT_MODEL = os.environ.get("PRAT_STT_MODEL", "FredrikKarlssonSpeech/nb-whisper-small-mlx")

# Conversation partner backend. Default is fully local (spec: no API keys).
# "auto" picks Claude if credentials exist, else Borealis, else the scripted partner.
LLM_BACKEND = os.environ.get("PRAT_LLM", "mlx")  # mlx (local, default) | claude | auto | scripted
CLAUDE_MODEL = os.environ.get("PRAT_CLAUDE_MODEL", "claude-opus-5-5")
MLX_MODEL = os.environ.get("PRAT_MLX_MODEL", "NbAiLab/borealis-4b-instruct-preview-mlx-8bit")

DEFAULT_VOICE = os.environ.get("PRAT_VOICE", "piper:talesyntese")

# Live conversation tuning (all adjustable at runtime from the UI too).
END_OF_TURN_MS = int(os.environ.get("PRAT_END_MS", "800"))
TTS_SPEED = float(os.environ.get("PRAT_SPEED", "0.9"))  # spec: slightly slower than native
LATENCY_LOG = DATA_DIR / "latency.jsonl"
