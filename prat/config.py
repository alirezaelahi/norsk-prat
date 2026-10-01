"""Runtime settings, read from environment variables with local-first defaults."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = Path(os.environ.get("PRAT_MODELS_DIR", ROOT / "models"))
DATA_DIR = Path(os.environ.get("PRAT_DATA_DIR", ROOT / "data"))
WEB_DIR = ROOT / "web"

# Speech recognition: NB-Whisper from the National Library of Norway.
STT_MODEL = os.environ.get("PRAT_STT_MODEL", "NbAiLab/nb-whisper-small")

# Conversation partner backend: "auto" picks Claude if credentials exist,
# else the local Borealis MLX model, else the scripted offline partner.
LLM_BACKEND = os.environ.get("PRAT_LLM", "auto")  # auto | claude | mlx | scripted
CLAUDE_MODEL = os.environ.get("PRAT_CLAUDE_MODEL", "claude-opus-5-5")
MLX_MODEL = os.environ.get("PRAT_MLX_MODEL", "NbAiLab/borealis-4b-instruct-preview-mlx-8bit")

DEFAULT_VOICE = os.environ.get("PRAT_VOICE", "piper:talesyntese")
