import json

import httpx2
import pytest

from prat.llm import ClaudeBackend, Partner, ScriptedPartner, Turn, clean_reply, parse_feedback, parse_lines


class EchoBackend:
    """Records prompts and returns canned output."""

    name = "fake"

    def __init__(self, out: str = "", explains: bool = True):
        self.out, self.explains, self.calls = out, explains, []

    def complete(self, system, messages, max_tokens, temperature):
        self.calls.append({"system": system, "messages": messages})
        return self.out


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('"Hei! Hva vil du ha?"', "Hei! Hva vil du ha?"),
        ("Kari: Hei! Hva vil du ha?", "Hei! Hva vil du ha?"),
        ("**Hei!**  Hva vil\n du ha?", "Hei! Hva vil du ha?"),
    ],
)
def test_clean_reply(raw, expected):
    assert clean_reply(raw) == expected


def test_feedback_ok_variants_mean_no_feedback():
    assert parse_feedback("Jeg liker kaffe.", "OK") is None
    assert parse_feedback("Jeg liker kaffe.", "ok.") is None
    # Model echoes the sentence then says OK: still no error.
    assert parse_feedback("Jeg liker kaffe.", "Jeg liker kaffe.\n\nThe sentence is correct.\n\nOK") is None
    # Differences only in case/punctuation are not errors (speech transcripts).
    assert parse_feedback("jeg liker kaffe", "Jeg liker kaffe.") is None


def test_feedback_correction_and_explanation():
    fb = parse_feedback(
        "I går jeg gikk til butikken.",
        "I går gikk jeg til butikken.\nVerb comes second (V2) after a fronted adverb.",
    )
    assert fb == {"corrected": "I går gikk jeg til butikken.", "explanation": "Verb comes second (V2) after a fronted adverb."}


def test_feedback_strips_labels():
    fb = parse_feedback("Hvor mye koste det?", "Corrected: Hvor mye koster det?\nExplanation: present tense is 'koster'.")
    assert fb["corrected"] == "Hvor mye koster det?"
    assert fb["explanation"] == "present tense is 'koster'."


def test_parse_lines_strips_numbering_and_limits():
    raw = "1. Ta med, takk.\n- Spise her.\n* «Hva koster det?»\n4) Fjerde"
    assert parse_lines(raw) == ["Ta med, takk.", "Spise her.", "Hva koster det?"]


def test_reply_history_starts_with_user_and_maps_roles():
    be = EchoBackend("Hei!")
    p = Partner(be)
    p.reply("kafe", "A2", [Turn("partner", "Hei, hva vil du ha?"), Turn("user", "En kaffe")])
    msgs = be.calls[0]["messages"]
    assert msgs[0]["role"] == "user"
    assert [m["role"] for m in msgs[1:]] == ["assistant", "user"]
    assert "barista" in be.calls[0]["system"]
    assert "A2" in be.calls[0]["system"]


def test_explanations_hidden_for_unreliable_backends():
    p = Partner(EchoBackend("Hvor mye koster det?\nsome dubious explanation", explains=False))
    assert p.feedback("Hvor mye koste det?") == {"corrected": "Hvor mye koster det?", "explanation": ""}


def test_scripted_partner_cycles_lines():
    p = ScriptedPartner()
    first = p.reply("kafe", "A2", [])
    second = p.reply("kafe", "A2", [Turn("partner", first), Turn("user", "En kaffe")])
    assert first != second
    assert p.feedback("x") is None and p.suggest("kafe", "A2", []) == []


def test_claude_backend_request_shape():
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        return httpx2.Response(
            200,
            json={
                "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "Hei! Hva vil du ha?"}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    import anthropic

    client = anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    out = ClaudeBackend(client=client).complete("sys", [{"role": "user", "content": "Hei"}], 100, 0.7)
    assert out == "Hei! Hva vil du ha?"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["system"] == "sys"
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "low"}
    assert "temperature" not in body and "thinking" not in body
    assert "server-side-fallback-2026-07-01" in seen["beta"]


def test_claude_backend_refusal_returns_empty():
    def handler(request):
        return httpx2.Response(
            200,
            json={
                "id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "content": [], "stop_reason": "refusal", "stop_sequence": None,
                "stop_details": {"type": "refusal", "category": None, "explanation": None},
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        )

    import anthropic

    client = anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    assert ClaudeBackend(client=client).complete("sys", [{"role": "user", "content": "x"}], 10, 0) == ""


@pytest.mark.parametrize(
    "word, raw, expected",
    [
        ("kanelbolle", "cinnamon bun (kannelbolle)", "cinnamon bun"),  # misspelt lemma dropped
        ("koster", "costs (koste)", "costs (koste)"),  # real lemma kept
        ("gikk", "went (gå)", "went (gå)"),  # irregular lemma kept
        ("hytta", "the cabin (hytte)\nextra line", "the cabin (hytte)"),
        ("hei", "hello (hei)", "hello"),
        ("hei", "", ""),
    ],
)
def test_clean_gloss(word, raw, expected):
    from prat.llm import clean_gloss

    assert clean_gloss(word, raw) == expected


def test_suggest_prompt_names_partner_and_level():
    be = EchoBackend("1. Ta med, takk.\n2. Spise her.\n3. Kan jeg betale med kort?")
    out = Partner(be).suggest("kafe", "B1", [Turn("partner", "Spise her eller ta med?")])
    assert out == ["Ta med, takk.", "Spise her.", "Kan jeg betale med kort?"]
    prompt = be.calls[0]["messages"][0]["content"]
    assert "Kari: Spise her eller ta med?" in prompt and "nivå B1" in prompt


def test_feedback_explanation_on_same_line_is_split():
    fb = parse_feedback(
        "I går jeg gikk til butikken.",
        "I går gikk jeg til butikken. The verb should come before the subject.",
    )
    assert fb == {"corrected": "I går gikk jeg til butikken.", "explanation": "The verb should come before the subject."}


def test_feedback_multi_sentence_original():
    fb = parse_feedback("Hei. Hvor mye koste det?", "«Hei. Hvor mye koster det?» \nUse the present tense 'koster'.")
    assert fb == {"corrected": "Hei. Hvor mye koster det?", "explanation": "Use the present tense 'koster'."}


def test_feedback_that_answers_instead_of_correcting_is_dropped():
    raw = "Hei, nei. Strøm er ikke inkludert i husleien, men internett er det, og depositum er tre måneder."
    assert parse_feedback("Hei, ja. Er strøm inkludert i husleien?", raw) is None


def test_feedback_echo_with_comment_is_ok():
    assert parse_feedback("Jeg bor i Oslo.", "Jeg bor i Oslo. The word order is correct.") is None
