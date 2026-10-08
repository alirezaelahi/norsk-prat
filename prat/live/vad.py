"""Silero VAD (v5, MIT) run directly with onnxruntime — no torch needed.

Feed 512-sample frames of 16 kHz float32 audio (32 ms each); get speech probability.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .. import config

SR = 16_000
FRAME = 512  # samples per VAD frame at 16 kHz
FRAME_MS = FRAME * 1000 / SR  # 32 ms
_CONTEXT = 64
MODEL_URL = "https://github.com/snakers4/silero-vad/raw/v6.2.1/src/silero_vad/data/silero_vad.onnx"


def model_path() -> Path:
    path = config.MODELS_DIR / "silero" / "silero_vad.onnx"
    if not path.exists():
        import urllib.request

        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, path)
    return path


class SileroVAD:
    def __init__(self, path: Path | None = None):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(path or model_path()), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        if frame.shape[-1] != FRAME:
            raise ValueError(f"VAD needs {FRAME}-sample frames, got {frame.shape[-1]}")
        x = np.concatenate([self._context, frame.reshape(1, -1).astype(np.float32)], axis=1)
        out, self._state = self.session.run(
            None, {"input": x, "state": self._state, "sr": np.array(SR, dtype=np.int64)}
        )
        self._context = x[:, -_CONTEXT:]
        return float(out[0][0])
