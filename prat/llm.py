"""Conversation partner: prompts for each tutoring task, over a pluggable text backend.

The tasks are deliberately small and separate (reply / feedback / translate / gloss /
suggest) so that a 4B local model handles each reliably and the spoken reply can be
produced first, with the rest fetched in parallel or on demand.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol

from . import config
from .scenarios import BY_ID, LEVELS, Scenario

log = logging.getLogger(__name__)

MAX_HISTORY = 20


@dataclass
class Turn:
    role: str  # "user" (learner) | "partner"
    text: str


class Backend(Protocol):
    name: str
    explains: bool  # whether its grammar explanations are reliable enough to show

    def complete(self, system: str, messages: list[dict], max_tokens: int, temperature: float) -> str: ...


# --------------------------------------------------------------------------- prompts


def _reply_system(s: Scenario, level: str) -> str:
    return (
        f"Du er {s.role}. Du snakker med en person som lærer norsk.\n"
        f"Situasjon: {s.setting}\n"
        f"Språknivå: {LEVELS[level]}\n"
        "Regler: Svar alltid på norsk bokmål. Skriv 1–2 korte setninger, som i en ekte muntlig samtale. "
        "Hold deg i rollen. Ikke rett feil og ikke forklar grammatikk. Driv samtalen videre, gjerne med et spørsmål. "
        "Ikke bruk emojier, lister eller anførselstegn."
    )


FEEDBACK_SYSTEM = (
    "You are a Norwegian (bokmål) teacher checking a student's sentence. Do NOT answer or reply to the sentence — "
    "only check its language. If it has grammar, word-order, spelling or word-choice errors, answer on line 1 with the "
    "same sentence corrected (change as little as possible), and on line 2 with a one-sentence explanation in English. "
    "Ignore capitalisation and punctuation. If the sentence is correct and natural, answer only: OK"
)

TRANSLATE_SYSTEM = "Translate the Norwegian text to natural English. Answer with the translation only."

GLOSS_SYSTEM = (
    "You are a Norwegian–English dictionary. Give the English meaning of the Norwegian word as it is used "
    "in the sentence. Gloss only that one word, not the sentence. Answer with only a short gloss (1–4 words), plus the dictionary form in brackets if "
    "different, e.g. 'cost (koste)'."
)


SUGGEST_SYSTEM = "Du er norsklærer og hjelper en elev i en rollespill-samtale."


def _suggest_prompt(level: str, convo: str, partner_name: str) -> str:
    return (
        f"Samtalen så langt:\n{convo}\n\n"
        f"Skriv tre forskjellige korte svar (nivå {level}) som ELEVEN kan si nå, som svar på den siste "
        f"replikken fra {partner_name}. Bruk akkurat dette formatet:\n1. ...\n2. ...\n3. ..."
    )


# --------------------------------------------------------------------------- parsing helpers


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\wæøå ]", " ", text.lower()).split())


def clean_reply(text: str) -> str:
    """Strip artefacts small models sometimes add: role prefixes, quotes, markdown."""
    text = text.strip().strip('"«»').strip()
    text = re.sub(r"^[A-ZÆØÅ][\w .]{0,29}:\s+", "", text)  # "Kari: Hei!" -> "Hei!"
    text = re.sub(r"[*_#`]", "", text)
    return " ".join(text.split())


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def parse_feedback(original: str, raw: str) -> dict | None:
    """Return ``{"corrected", "explanation"}`` or ``None`` if the sentence was fine.

    Guards against small-model failure modes: echoing the sentence back, appending the
    English explanation on the same line, or replying to the sentence instead of fixing it.
    """
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    lines = [ln for ln in lines if ln.upper().rstrip(".!") != "OK"]
    if not lines:
        return None
    first = re.sub(r"^(corrected|korrigert|rettet)\s*:\s*", "", lines[0], flags=re.I)
    # The correction has as many sentences as the original; anything after is explanation.
    n = max(1, len(_SENT_SPLIT.split(original.strip())))
    parts = _SENT_SPLIT.split(first)
    corrected = " ".join(parts[:n]).strip().strip('"«»').strip()
    explanation = " ".join(parts[n:] + lines[1:2]).strip()
    explanation = re.sub(r"^(explanation|forklaring)\s*:\s*", "", explanation, flags=re.I)
    a, b = _norm(original), _norm(corrected)
    if not b or a == b:
        return None
    if SequenceMatcher(None, a.split(), b.split()).ratio() < 0.5:
        return None  # not a correction of this sentence
    return {"corrected": corrected, "explanation": explanation}


def clean_gloss(word: str, raw: str) -> str:
    """First line only; drop a bracketed 'dictionary form' that is just a misspelling of the word."""
    line = raw.strip().splitlines()[0].strip(" .\"'") if raw.strip() else ""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)$", line)
    if m:
        gloss, lemma = m.group(1).strip(), m.group(2).strip().lower()
        w = word.lower()
        related = w.startswith(lemma[:-1] or lemma) or lemma.startswith(w)
        if lemma == w or (not related and SequenceMatcher(None, lemma, w).ratio() >= 0.75):
            return gloss
    return line


def parse_lines(raw: str, limit: int = 3) -> list[str]:
    out = []
    for ln in raw.splitlines():
        ln = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", ln).strip().strip('"«»').strip()
        if ln and len(ln) < 300:
            out.append(ln)
    return out[:limit]


# --------------------------------------------------------------------------- partner


class Partner:
    def __init__(self, backend: Backend):
        self.backend = backend

    @property
    def name(self) -> str:
        return self.backend.name

    def _history(self, history: list[Turn]) -> list[dict]:
        msgs = [{"role": "user" if t.role == "user" else "assistant", "content": t.text} for t in history[-MAX_HISTORY:]]
        # Chat APIs need the first message to be from the user.
        if not msgs or msgs[0]["role"] != "user":
            msgs.insert(0, {"role": "user", "content": "(Samtalen begynner. Du starter.)"})
        return msgs

    def reply(self, scenario_id: str, level: str, history: list[Turn]) -> str:
        s = BY_ID[scenario_id]
        raw = self.backend.complete(_reply_system(s, level), self._history(history), 150, 0.7)
        return clean_reply(raw)

    def feedback(self, text: str) -> dict | None:
        # No conversation context on purpose: with it, small models tend to answer the
        # sentence instead of correcting it.
        raw = self.backend.complete(FEEDBACK_SYSTEM, [{"role": "user", "content": f"Student sentence: «{text}»"}], 200, 0.0)
        fb = parse_feedback(text, raw)
        if fb and not self.backend.explains:
            fb["explanation"] = ""
        return fb

    def translate(self, text: str) -> str:
        return self.backend.complete(TRANSLATE_SYSTEM, [{"role": "user", "content": text}], 200, 0.0).strip()

    def gloss(self, word: str, sentence: str) -> str:
        msg = f"Word: {word}\nSentence: {sentence}"
        raw = self.backend.complete(GLOSS_SYSTEM, [{"role": "user", "content": msg}], 30, 0.0)
        return clean_gloss(word, raw)

    def suggest(self, scenario_id: str, level: str, history: list[Turn]) -> list[str]:
        s = BY_ID[scenario_id]
        name = s.role.split(",")[0]
        convo = "\n".join(f"{'Eleven' if t.role == 'user' else name}: {t.text}" for t in history[-6:])
        prompt = _suggest_prompt(level, convo, name)
        raw = self.backend.complete(SUGGEST_SYSTEM, [{"role": "user", "content": prompt}], 250, 0.6)
        return parse_lines(raw)


class ScriptedPartner(Partner):
    """Offline fallback when no LLM is available: cycles the scenario's scripted lines."""

    def __init__(self):
        self.backend = ScriptedBackend()

    def reply(self, scenario_id: str, level: str, history: list[Turn]) -> str:
        lines = BY_ID[scenario_id].opener
        n = sum(1 for t in history if t.role == "partner")
        return lines[n % len(lines)]

    def feedback(self, text: str) -> dict | None:
        return None

    def translate(self, text: str) -> str:
        return ""

    def gloss(self, word: str, sentence: str) -> str:
        return ""

    def suggest(self, scenario_id: str, level: str, history: list[Turn]) -> list[str]:
        return []


# --------------------------------------------------------------------------- backends


class ScriptedBackend:
    name = "scripted"
    explains = False

    def complete(self, system, messages, max_tokens, temperature) -> str:  # pragma: no cover - unused
        return ""


class ClaudeBackend:
    name = "claude"
    explains = True

    def __init__(self, model: str = config.CLAUDE_MODEL, client=None):
        import anthropic

        self.model = model
        self.client = client or anthropic.Anthropic()

    def complete(self, system: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
        # Short conversational tasks: low effort keeps latency down. Sampling params are
        # not accepted on current models, so `temperature` is ignored here.
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=max(max_tokens * 8, 2000),  # headroom for adaptive thinking
            system=system,
            messages=messages,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            log.warning("Claude declined a request: %s", resp.stop_details)
            return ""
        return "".join(b.text for b in resp.content if b.type == "text")


class MLXBackend:
    name = "borealis"
    explains = False  # 4B model's corrections are good, its explanations are not

    def __init__(self, model: str = config.MLX_MODEL):
        from mlx_lm import load

        self.model_name = model
        self.model, self.tokenizer = load(model)
        self._lock = threading.Lock()

    def complete(self, system: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        prompt = self.tokenizer.apply_chat_template(
            [{"role": "system", "content": system}, *messages], add_generation_prompt=True, tokenize=False
        )
        with self._lock:  # Metal work is not safe to interleave across threads
            return generate(self.model, self.tokenizer, prompt=prompt, max_tokens=max_tokens, sampler=make_sampler(temp=temperature))


def _has_claude_credentials() -> bool:
    import os
    from pathlib import Path

    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) or (
        Path.home() / ".config" / "anthropic"
    ).exists()


def make_partner(choice: str = config.LLM_BACKEND) -> Partner:
    """Pick a backend. ``auto``: Claude if credentials exist, else local Borealis, else scripted."""
    order = {"auto": ["claude", "mlx", "scripted"], "claude": ["claude"], "mlx": ["mlx"], "scripted": ["scripted"]}[choice]
    for name in order:
        try:
            if name == "claude":
                if choice == "auto" and not _has_claude_credentials():
                    continue
                return Partner(ClaudeBackend())
            if name == "mlx":
                return Partner(MLXBackend())
            return ScriptedPartner()
        except Exception as e:  # missing package, no model, no network...
            log.warning("LLM backend %s unavailable: %s", name, e)
    return ScriptedPartner()
