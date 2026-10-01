"""Tutor behaviour for live conversation: system prompt, voice commands, turn heuristics."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..scenarios import BY_ID, LEVELS

# Spoken output only: keep it short, Norwegian, recast instead of lecturing.
_BASE = """Du er en vennlig samtalepartner for en voksen som lærer norsk, i en muntlig samtale.
Alt du skriver blir lest høyt, så skriv bare vanlig tale.

Regler:
- Snakk bare norsk bokmål. Hvis eleven spør om noe på engelsk, forklar kort på engelsk og gå så tilbake til norsk.
- Svar kort: 1–3 korte setninger.
- Avslutt alltid med noe eleven kan svare på, gjerne et spørsmål.
- Retting: Hvis eleven gjør en feil, gjenta ideen riktig i svaret ditt (for eksempel «Å, du har bodd i Oslo i fem år!») i stedet for å forklare grammatikk. Rett bare eksplisitt hvis feilen endrer meningen eller gjentar seg, og høyst én rettelse per svar.
- Nivå: Start på {level}. Bruk enklere ord og kortere setninger hvis eleven sliter, og litt rikere språk hvis eleven snakker flytende.
- Ingen emojier, lister, overskrifter, parenteser eller symboler. Skriv tall med bokstaver når det er naturlig."""

_FREE = "\n\nDette er en fri samtale. Du heter Sigrid. Følg det eleven er interessert i, og still spørsmål tilbake."

_ROLE = "\n\nRollespill: Du er {role}. Situasjon: {setting} Hold deg i rollen, men følg reglene over."


def system_prompt(scenario: str, level: str) -> str:
    level_name = level if level in LEVELS else "B1"
    prompt = _BASE.format(level=level_name)
    s = BY_ID.get(scenario)
    if s is None or s.id == "fri":
        return prompt + _FREE
    return prompt + _ROLE.format(role=s.role, setting=s.setting)


# --------------------------------------------------------------------------- voice commands


@dataclass
class Command:
    kind: str  # repeat | slower | faster | explain | scenario
    arg: str = ""


_SCENARIO_WORDS = {
    "kafe": r"kaf[eé]|kaffebar",
    "lege": r"lege|legen|fastlege",
    "nav": r"nav",
    "intervju": r"jobbintervju|intervju",
    "butikk": r"butikk|butikken|handle",
    "visning": r"visning|leilighet",
    "norskprove": r"norskprøve|norskprøven|eksamen|prøve",
    "fri": r"fri samtale|fritt|bare prate|bare snakke",
}


def _norm(text: str) -> str:
    """Lowercase, fold accents except æøå ("òg" -> "og"), drop punctuation."""
    import unicodedata

    out = []
    for ch in text.lower():
        if ch in "æøå":
            out.append(ch)
        else:
            out.append("".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c)))
    return " ".join(re.sub(r"[^\wæøå ]", " ", "".join(out)).split())


def parse_command(text: str) -> Command | None:
    """Recognise short spoken control phrases. Long utterances are always conversation."""
    t = _norm(text)
    words = t.split()
    if not words or len(words) > 8:
        return None
    if re.search(r"\b(saktere|langsommere|roligere)\b|\bsnakke? (litt )?(mer )?(sakt\w*|rolig\w*)", t):
        return Command("slower")
    if re.search(r"\b(fortere|raskere)\b", t):
        return Command("faster")
    if re.search(r"\b(gjenta|si det igjen|en gang til|hva sa du)\b", t):
        return Command("repeat")
    m = re.search(r"\b(?:forklar|hva betyr)\s+(?:ordet\s+)?(.+)$", t)
    if m and len(m.group(1).split()) <= 3:
        return Command("explain", m.group(1))
    if re.search(r"\b(la oss|kan vi|vil|skal vi)\b.*\b(øve|spille|snakke|ta|prøve|bytte)\b", t) or t.startswith("rollespill"):
        for sid, pat in _SCENARIO_WORDS.items():
            if re.search(rf"\b({pat})\b", t):
                return Command("scenario", sid)
    return None


# --------------------------------------------------------------------------- turn-taking heuristics

_TRAILING = {"og", "men", "eller", "så", "fordi", "at", "som", "hvis", "når", "der", "om", "eh", "ehm", "øh", "øhm", "hm", "er", "æh", "altså", "liksom", "jeg", "vi", "en", "et", "ei", "den", "det", "til", "i", "på", "med", "for"}


def looks_unfinished(transcript: str) -> bool:
    """True if the learner probably paused mid-thought (so wait longer before answering)."""
    t = transcript.strip()
    if not t:
        return False
    if t[-1] in "?!":
        return False
    words = _norm(t).split()
    if words and words[-1] in _TRAILING:
        return True  # "... og." — Whisper often adds a full stop anyway
    return t[-1] != "."  # no punctuation or "…": trailing off


def explain_instruction(word: str) -> str:
    return f"(Eleven vil at du forklarer «{word}». Forklar det enkelt på norsk med ett eksempel, og fortsett så samtalen.)"


def scenario_intro_instruction(scenario: str) -> str:
    s = BY_ID[scenario]
    return f"(Eleven vil bytte til rollespillet «{s.title}». Gå inn i rollen nå og start situasjonen med én kort replikk.)"
