import pytest

from prat.live.chunker import SentenceChunker
from prat.live.turn import TurnConfig, TurnDetector
from prat.live.tutor import looks_unfinished, parse_command, system_prompt
from prat.live.vad import FRAME_MS


def run(det, probs):
    events = []
    for i, p in enumerate(probs):
        for e in det.update(p):
            events.append((i, e))
    return events


def frames(ms):
    return round(ms / FRAME_MS)


# --------------------------------------------------------------------------- turn detection


def test_turn_start_pause_end():
    det = TurnDetector(TurnConfig(end_ms=800))
    ev = run(det, [0.0] * 5 + [0.9] * 20 + [0.0] * 40)
    names = [e for _, e in ev]
    assert names == ["speech_start", "pause", "end"]
    end_frame = dict((e, i) for i, e in ev)["end"]
    assert end_frame - 24 == frames(800)  # silence started at frame 25


def test_short_noise_does_not_open_a_turn():
    det = TurnDetector()
    assert run(det, [0.9, 0.0, 0.0, 0.9, 0.0] * 5) == []


def test_learner_pause_shorter_than_end_threshold_resumes_same_turn():
    det = TurnDetector(TurnConfig(end_ms=900))
    probs = [0.9] * 10 + [0.0] * frames(600) + [0.9] * 10 + [0.0] * 40
    names = [e for _, e in run(det, probs)]
    assert names == ["speech_start", "pause", "resume", "pause", "end"]


def test_extend_delays_end_and_resets_after_turn():
    det = TurnDetector(TurnConfig(end_ms=800))
    ev = run(det, [0.9] * 10 + [0.0] * frames(300))
    assert [e for _, e in ev] == ["speech_start", "pause"]
    det.extend(700)
    ev = run(det, [0.0] * frames(1000))
    assert "end" not in [e for _, e in ev]  # 1300 ms silence < 1500 ms
    ev = run(det, [0.0] * frames(300))
    assert [e for _, e in ev] == ["end"]
    assert det.end_frames == frames(800)


def test_barge_in_needs_sustained_speech_over_stricter_threshold():
    det = TurnDetector(TurnConfig(barge_ms=256, barge_threshold=0.6))
    det.tutor_speaking = True
    assert run(det, [0.55] * 30) == []  # echo residue below barge threshold
    assert run(det, [0.9] * 4 + [0.0] * 3) == []  # a cough
    ev = run(det, [0.9] * 10)
    assert [e for _, e in ev] == ["barge_in"]
    assert ev[0][0] == frames(256) - 1


def test_ambiguous_frames_continue_speech():
    det = TurnDetector()
    names = [e for _, e in run(det, [0.9, 0.4, 0.9, 0.4] + [0.0] * 40)]
    assert names[0] == "speech_start" and names[-1] == "end"


# --------------------------------------------------------------------------- chunker


def test_chunker_splits_sentences_from_token_stream():
    c = SentenceChunker()
    out = []
    for tok in ["Hei", "!", " Så", " fint", " at", " du", " er", " her", ".", " Hva", " vil", " du", " ha", "?"]:
        out += c.feed(tok)
    out += c.flush()
    assert out == ["Hei!", "Så fint at du er her.", "Hva vil du ha?"]


def test_chunker_first_chunk_flushes_early_at_comma():
    c = SentenceChunker(comma_words=8, first_comma_words=4)
    out = c.feed("Ja, det er en fin dag i dag, og jeg tror vi skal gå tur, men først kaffe, ")
    assert out[0] == "Ja, det er en fin dag i dag,"
    assert out[1:] == ["og jeg tror vi skal gå tur, men først kaffe,"]


def test_chunker_keeps_abbreviations_together():
    c = SentenceChunker()
    assert c.feed("Vi kan f.eks. gå på kino. ") == ["Vi kan f.eks. gå på kino."]


def test_chunker_drops_punctuation_only_chunks():
    c = SentenceChunker()
    assert c.feed("... ") == [] and c.flush() == []


# --------------------------------------------------------------------------- tutor


@pytest.mark.parametrize(
    "text, kind, arg",
    [
        ("Kan du snakke saktere?", "slower", ""),
        ("Litt fortere, takk.", "faster", ""),
        ("Kan du snakke sakte?", "slower", ""),  # Whisper often drops the "-re"
        ("Kan du snakke saktige?", "slower", ""),  # ...or mishears it
        ("Unnskyld, kan du gjenta?", "repeat", ""),
        ("Hva sa du?", "repeat", ""),
        ("Forklar ordet dagpenger.", "explain", "dagpenger"),
        ("Hva betyr utlendingskontoret", "explain", "utlendingskontoret"),
        ("La oss øve på jobbintervju.", "scenario", "intervju"),
        ("Kan vi spille at jeg er hos legen?", "scenario", "lege"),
        ("Skal vi bytte til NAV?", "scenario", "nav"),
    ],
)
def test_parse_command(text, kind, arg):
    cmd = parse_command(text)
    assert cmd is not None and cmd.kind == kind and cmd.arg == arg


@pytest.mark.parametrize(
    "text",
    [
        "Jeg liker å gå på tur i marka om helgene sammen med familien min.",  # long: conversation
        "Hva er klokka?",
        "Jeg jobber som ingeniør.",
        "",
    ],
)
def test_not_commands(text):
    assert parse_command(text) is None


@pytest.mark.parametrize(
    "text, unfinished",
    [
        ("Jeg bor i Oslo.", False),
        ("Hva gjør du i helgen?", False),
        ("Hva er det?", False),
        ("Jeg liker å gå på tur og.", True),
        ("Jeg har bodd her i fem år, men", True),
        ("Jeg vil gjerne ha en eh", True),
        ("Jeg kommer fra", True),
        ("Jeg har to barn òg.", True),  # Whisper writes a trailing "og" as "òg"
        ("Jeg har to barn og er.", True),  # ...or hallucinates "er" from "eh"
        ("", False),
    ],
)
def test_looks_unfinished(text, unfinished):
    assert looks_unfinished(text) is unfinished


def test_system_prompt_scenarios():
    free = system_prompt("fri", "A2")
    assert "A2" in free and "Sigrid" in free and "bokmål" in free
    role = system_prompt("nav", "B1")
    assert "saksbehandler på et NAV-kontor" in role
