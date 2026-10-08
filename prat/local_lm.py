"""Local LLM (Borealis on MLX) with streaming and a reusable prompt cache.

The prompt cache is the main latency trick for conversation: the system prompt and
the conversation so far stay in the KV cache between turns, so each new turn only
has to process the learner's latest words before the first token comes out.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from . import config

# All MLX work (Whisper + LLM) goes through this lock: one GPU, one stream at a time.
MLX_LOCK = threading.RLock()

_instances: dict[str, LocalLM] = {}
_instances_lock = threading.Lock()


def get_local_lm(name: str = config.MLX_MODEL) -> LocalLM:
    with _instances_lock:
        if name not in _instances:
            _instances[name] = LocalLM(name)
        return _instances[name]


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Make a history the chat template accepts: starts with the user, roles alternate.

    Interruptions can leave two learner turns in a row (e.g. the learner talks before the
    tutor's reply was heard at all); merge such runs instead of crashing.
    """
    out: list[dict] = []
    for m in messages:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if out and out[-1]["role"] == m["role"]:
            out[-1] = {"role": m["role"], "content": f"{out[-1]['content']}\n{content}"}
        else:
            out.append({"role": m["role"], "content": content})
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "(Samtalen begynner.)"})
    return out


def _common_prefix(a: list[int], b: list[int]) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class LocalLM:
    def __init__(self, name: str):
        from mlx_lm import load

        self.name = name
        with MLX_LOCK:
            self.model, self.tokenizer = load(name)
        self._cache = None
        self._cached: list[int] = []  # tokens currently held in self._cache
        self.last_stats: dict = {}

    def _tokens(self, system: str, messages: list[dict]) -> list[int]:
        return self.tokenizer.apply_chat_template(
            [{"role": "system", "content": system}, *normalize_messages(messages)],
            add_generation_prompt=True,
            tokenize=True,
        )

    def _prepare_cache(self, tokens: list[int]) -> int:
        """Reuse the cached prefix; return how many prompt tokens are already cached."""
        from mlx_lm.models.cache import (
            can_trim_prompt_cache,
            make_prompt_cache,
            trim_prompt_cache,
        )

        k = _common_prefix(self._cached, tokens) if self._cache is not None else 0
        k = min(k, len(tokens) - 1)  # always feed at least one token
        if self._cache is not None and k > 0:
            extra = len(self._cached) - k
            if extra == 0 or (can_trim_prompt_cache(self._cache) and trim_prompt_cache(self._cache, extra) == extra):
                return k
        self._cache = make_prompt_cache(self.model)
        self._cached = []
        return 0

    def generate(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 160,
        temperature: float = 0.7,
        cancel: threading.Event | None = None,
        use_cache: bool = True,
    ) -> Iterator[str]:
        """Yield text pieces as they are generated. Stops early when ``cancel`` is set."""
        from mlx_lm import stream_generate
        from mlx_lm.models.cache import make_prompt_cache
        from mlx_lm.sample_utils import make_sampler

        with MLX_LOCK:
            tokens = self._tokens(system, messages)
            if use_cache:
                k = self._prepare_cache(tokens)
                cache = self._cache
            else:
                k, cache = 0, make_prompt_cache(self.model)
            generated: list[int] = []
            self.last_stats = {"prompt_tokens": len(tokens), "cached_tokens": k}
            try:
                for r in stream_generate(
                    self.model,
                    self.tokenizer,
                    tokens[k:],
                    max_tokens=max_tokens,
                    sampler=make_sampler(temp=temperature),
                    prompt_cache=cache,
                ):
                    generated.append(r.token)
                    if r.text:
                        yield r.text
                    if cancel is not None and cancel.is_set():
                        break
            finally:
                if use_cache:
                    # The cache holds the prompt plus the generated tokens it has processed.
                    offset = getattr(cache[0], "offset", None)
                    seq = tokens + generated
                    self._cached = seq[:offset] if isinstance(offset, int) else []
                    if not self._cached:
                        self._cache = None

    def warmup(self) -> None:
        """Compile Metal kernels for both the cold and the cached-continuation paths once,
        so the learner's first turns are as fast as later ones."""
        if getattr(self, "_warm", False):
            return
        sys_ = "Du er en vennlig samtalepartner. Svar kort på norsk."
        msgs = [{"role": "user", "content": "Hei!"}]
        reply = "".join(self.generate(sys_, msgs, max_tokens=8))
        msgs += [{"role": "assistant", "content": reply}, {"role": "user", "content": "Hvordan har du det?"}]
        for _ in self.generate(sys_, msgs, max_tokens=4):
            pass
        self._warm = True

    def complete(self, system: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
        """One-shot completion for side tasks (feedback, translation…); leaves the chat cache alone."""
        return "".join(self.generate(system, messages, max_tokens, temperature, use_cache=False))
