import numpy as np
import pytest
from fastapi.testclient import TestClient

from prat.audio import to_wav_bytes
from prat.llm import Partner
from prat.server import create_app
from prat.store import Store


class FakeTTS:
    def __init__(self):
        self.calls = []

    def speak(self, text, voice="piper:talesyntese", speed=1.0):
        if voice == "bad":
            raise ValueError("unknown voice: bad")
        self.calls.append((text, voice, speed))
        return to_wav_bytes(np.zeros(160), 16000)


class FakeSTT:
    def __init__(self, text="jeg vil ha en kaffe takk"):
        self.text = text

    def transcribe(self, audio: bytes) -> str:
        return self.text

    def _load(self):
        pass


class FakeBackend:
    name = "fake"
    explains = True

    def complete(self, system, messages, max_tokens, temperature):
        if system.startswith("You are a Norwegian (bokmål) teacher"):
            return "Hvor mye koster det?\nPresent tense of 'koste' is 'koster'."
        if system.startswith("Translate"):
            return "How much does it cost?"
        if "dictionary" in system:
            return "cost (koste)"
        if "Skriv 3 korte" in system:
            return "1. Ja, takk.\n2. Nei, takk.\n3. Hva koster det?"
        return f"Svar nummer {len(messages)}."


@pytest.fixture
def client():
    app = create_app(tts=FakeTTS(), stt=FakeSTT(), partner=Partner(FakeBackend()), store=Store(":memory:"))
    return TestClient(app)


def wav():
    return ("a.wav", to_wav_bytes(np.zeros(16000), 16000), "audio/wav")


def test_config_lists_voices_scenarios_levels(client):
    c = client.get("/api/config").json()
    ids = [v["id"] for v in c["voices"]]
    assert "piper:talesyntese" in ids and "mms:nob" in ids
    assert sum(i.startswith("piper:nvcc:") for i in ids) == 10
    assert {s["id"] for s in c["scenarios"]} >= {"kafe", "norskprove", "fri"}
    assert c["levels"] == ["A2", "B1", "B2"]


def test_tts_returns_wav(client):
    r = client.post("/api/tts", json={"text": "Hei", "voice": "piper:talesyntese", "speed": 0.8})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"
    assert client.post("/api/tts", json={"text": "Hei", "voice": "bad"}).status_code == 400
    assert client.post("/api/tts", json={"text": ""}).status_code == 422


def test_stt(client):
    assert client.post("/api/stt", files={"audio": wav()}).json() == {"text": "jeg vil ha en kaffe takk"}


def test_shadow_scores_against_target(client):
    r = client.post("/api/shadow", files={"audio": wav()}, data={"target": "Jeg vil ha en kaffe, takk."}).json()
    assert r["heard"] == "jeg vil ha en kaffe takk"
    assert r["score"] == 100


def test_conversation_flow(client):
    r = client.post("/api/sessions", json={"scenario": "kafe", "level": "A2"}).json()
    sid = r["session"]["id"]
    assert r["turn"]["role"] == "partner" and r["turn"]["text"]

    t = client.post(f"/api/sessions/{sid}/turn", json={"text": "Hvor mye koste det?"}).json()
    assert t["user_turn"]["text"] == "Hvor mye koste det?"
    assert t["partner_turn"]["role"] == "partner"

    fb = client.post(f"/api/turns/{t['user_turn']['id']}/feedback").json()["feedback"]
    assert fb["corrected"] == "Hvor mye koster det?"
    # cached on the turn
    assert client.post(f"/api/turns/{t['user_turn']['id']}/feedback").json()["feedback"] == fb
    # feedback only for learner turns
    assert client.post(f"/api/turns/{t['partner_turn']['id']}/feedback").status_code == 400

    tr = client.post(f"/api/turns/{t['partner_turn']['id']}/translation").json()
    assert tr["translation"] == "How much does it cost?"

    sug = client.post(f"/api/sessions/{sid}/suggestions").json()["suggestions"]
    assert sug == ["Ja, takk.", "Nei, takk.", "Hva koster det?"]

    s = client.get(f"/api/sessions/{sid}").json()
    assert [x["role"] for x in s["turns"]] == ["partner", "user", "partner"]
    assert s["turns"][1]["extra"]["feedback"]["corrected"] == "Hvor mye koster det?"

    listed = client.get("/api/sessions").json()
    assert listed[0]["id"] == sid and listed[0]["n_turns"] == 3
    assert listed[0]["first_user"] == "Hvor mye koste det?"

    client.delete(f"/api/sessions/{sid}")
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_bad_session_inputs(client):
    assert client.post("/api/sessions", json={"scenario": "nope"}).status_code == 400
    assert client.post("/api/sessions/missing/turn", json={"text": "hei"}).status_code == 404


def test_gloss_and_vocab(client):
    g = client.post("/api/gloss", json={"word": "koster", "sentence": "Hvor mye koster det?"}).json()
    assert g == {"word": "koster", "gloss": "cost (koste)"}

    v = client.post("/api/vocab", json={"word": "koster", "meaning": "costs"}).json()
    client.post("/api/vocab", json={"word": "koster", "example": "Hvor mye koster det?"})  # upsert keeps meaning
    items = client.get("/api/vocab").json()
    assert len(items) == 1 and items[0]["meaning"] == "costs" and items[0]["example"] == "Hvor mye koster det?"
    client.delete(f"/api/vocab/{v['id']}")
    assert client.get("/api/vocab").json() == []
