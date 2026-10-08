"""LiveSession orchestration with fake models (no MLX): turn-taking, barge-in, history."""

import asyncio
import time

import numpy as np

from prat.live.session import LiveSession
from prat.live.vad import FRAME


class FakeVAD:
    """Speech = loud frame."""

    def __call__(self, frame):
        return 0.9 if float(np.abs(frame).mean()) > 0.05 else 0.0

    def reset(self):
        pass


class FakeSTT:
    def __init__(self):
        self.text = ""

    def transcribe_array(self, audio, min_seconds=0.3):
        return self.text if len(audio) else ""


class StrictLM:
    """Raises like the Borealis chat template if roles don't alternate."""

    def __init__(self, delay=0.02):
        self.delay = delay
        self.calls = []
        self.last_stats = {}

    def generate(self, system, messages, max_tokens=160, temperature=0.7, cancel=None, use_cache=True):
        from prat.local_lm import normalize_messages

        msgs = normalize_messages(messages)
        self.calls.append(msgs)
        roles = [m["role"] for m in msgs]
        if not roles or roles[0] != "user" or any(a == b for a, b in zip(roles, roles[1:])):
            raise ValueError(f"Conversation roles must alternate: {roles}")
        for piece in ["Hei", " der!", " Hva", " vil", " du", " snakke", " om?"]:
            if cancel is not None and cancel.is_set():
                return
            time.sleep(self.delay)
            yield piece


class FakeTTS:
    def synthesize(self, text, voice, speed):
        return np.zeros(2205, dtype=np.float32), 22050


def speech(ms):
    return (np.ones(int(16 * ms)) * 0.3 * 32767).astype("<i2").tobytes()


def silence(ms):
    return np.zeros(int(16 * ms), dtype="<i2").tobytes()


async def feed(session, data):
    step = FRAME * 2
    for i in range(0, len(data), step):
        await session.handle_audio(data[i : i + step])
        await asyncio.sleep(0)


async def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


def make_session(lm_delay=0.02):
    sent = []

    async def send_json(m):
        sent.append(m)

    async def send_bytes(b):
        pass

    stt = FakeSTT()
    s = LiveSession(send_json, send_bytes, stt, StrictLM(lm_delay), FakeTTS(), vad_factory=FakeVAD)
    return s, stt, sent


def test_learner_talks_before_opening_is_heard_then_conversation_continues():
    """Regression: speaking during the (slow) opening left two user turns in a row and
    every later reply crashed with 'Conversation roles must alternate'."""

    async def run():
        s, stt, sent = make_session(lm_delay=0.3)  # slow opening, like a cold model load
        await s.handle_message({"type": "start", "scenario": "fri", "level": "B1"})
        stt.text = "Hallo?"
        await feed(s, speech(500))  # barge-in during the opening, before any audio played
        assert await wait_for(lambda: any(m["type"] == "stop" for m in sent))
        await s.handle_message({"type": "stopped", "id": None, "played": 0, "duration": 0})
        s.lm.delay = 0.01
        await feed(s, silence(1200))
        assert await wait_for(lambda: any(m["type"] == "chunk" for m in sent))
        stt.text = "Jeg heter Ali."
        await feed(s, speech(600) + silence(1200))
        assert await wait_for(lambda: sum(m["type"] == "user" for m in sent) == 2)
        assert await wait_for(lambda: len({m["turn"] for m in sent if m["type"] == "chunk"}) >= 2)
        await s.close()
        return sent, s

    sent, s = asyncio.run(run())
    assert not [m for m in sent if m["type"] == "error"]
    assert [m["text"] for m in sent if m["type"] == "user"] == ["Hallo?", "Jeg heter Ali."]


def test_normal_turn_gets_spoken_reply_and_history_alternates():
    async def run():
        s, stt, sent = make_session()
        await s.handle_message({"type": "start", "scenario": "kafe", "level": "A2"})
        assert await wait_for(lambda: any(m["type"] == "tutor_done" for m in sent))
        # Pretend the client played everything.
        for m in [m for m in sent if m["type"] == "chunk"]:
            await s.handle_message({"type": "playing", "id": m["id"]})
            await s.handle_message({"type": "played", "id": m["id"]})
        assert await wait_for(lambda: not s.detector.tutor_speaking)
        stt.text = "En kaffe, takk."
        await feed(s, speech(700) + silence(1200))
        assert await wait_for(lambda: any(m["type"] == "tutor_done" and m["turn"] == 2 for m in sent))
        await s.close()
        return s, sent

    s, sent = asyncio.run(run())
    roles = [m["role"] for m in s.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert not [m for m in sent if m["type"] == "error"]


def test_normalize_messages_merges_runs_and_drops_empty():
    from prat.local_lm import normalize_messages

    msgs = [
        {"role": "assistant", "content": "Hei!"},
        {"role": "user", "content": "Hallo?"},
        {"role": "user", "content": "Hei."},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "Hva skjer?"},
    ]
    out = normalize_messages(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[2]["content"] == "Hallo?\nHei.\nHva skjer?"
