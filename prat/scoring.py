"""Shadowing score: compare what the recogniser heard with the target sentence."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_WORD = re.compile(r"[\wæøåÆØÅéèêóòôü'-]+", re.UNICODE)


def words(text: str) -> list[str]:
    return [w.lower().strip("-'") for w in _WORD.findall(text) if w.strip("-'")]


def _close(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= 0.75


def score(target: str, heard: str) -> dict:
    """Word-level alignment.

    Returns ``{"score": 0..100, "words": [{"word", "status"}], "extra": [...]}`` where status
    is ``ok`` (exact), ``close`` (near miss, e.g. one wrong vowel) or ``missed``.
    """
    t_words, h_words = words(target), words(heard)
    # Keep original spelling for display.
    display = [w for w in _WORD.findall(target) if w.strip("-'")]
    status = ["missed"] * len(t_words)
    extra: list[str] = []

    sm = SequenceMatcher(None, t_words, h_words, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for i in range(i1, i2):
                status[i] = "ok"
        elif op == "replace":
            pending = list(range(j1, j2))
            for i in range(i1, i2):
                for j in pending:
                    if _close(t_words[i], h_words[j]):
                        status[i] = "close"
                        pending.remove(j)
                        break
            extra += [h_words[j] for j in pending]
        elif op == "insert":
            extra += h_words[j1:j2]

    if not t_words:
        return {"score": 0, "words": [], "extra": extra}
    points = sum(1.0 if s == "ok" else 0.5 if s == "close" else 0.0 for s in status)
    # Penalise inserted words lightly so rambling doesn't score 100.
    pct = max(0.0, points - 0.25 * len(extra)) / len(t_words)
    return {
        "score": round(100 * pct),
        "words": [{"word": d, "status": s} for d, s in zip(display, status)],
        "extra": extra,
    }
