"""Headless real-time test of the live pipeline (real models, no browser).

Streams synthesised learner speech into /ws/live at real-time pace, acts like a
browser playing the tutor's audio (sends playing/played/stopped), and prints the
server's per-turn latency metrics. Also exercises barge-in and an unfinished-sentence pause.

    ./run.sh --port 8765 &   uv run python tests/e2e/live_client.py
"""

import asyncio
import json
import os
import sys
import time

import numpy as np
import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from prat.audio import resample  # noqa: E402
from prat.tts import TTS  # noqa: E402

URL = os.environ.get("PRAT_WS", "ws://127.0.0.1:8765/ws/live")
FRAME = 512
VOICE = os.environ.get("PRAT_TEST_VOICE", "piper:talesyntese")  # clearest voice; no echo in this test


def learner(tts: TTS, text: str, speed: float = 1.0) -> np.ndarray:
    """Synthetic learner speech, with TTS sentence gaps squeezed to a natural ~350 ms."""
    samples, sr = tts.synthesize(text, VOICE, speed)
    a = resample(samples, sr, 16000)
    quiet = np.abs(a) < 0.01
    out, run = [], 0
    for x, q in zip(a, quiet):
        run = run + 1 if q else 0
        if run <= 16 * 350:
            out.append(x)
    return np.asarray(out, dtype=np.float32)


def vad_onset_ms(audio: np.ndarray, threshold: float = 0.6) -> float:
    from prat.live.vad import FRAME, SileroVAD

    v = SileroVAD()
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        if v(audio[i : i + FRAME]) >= threshold:
            return i / 16
    return 0.0


def silence(ms: float) -> np.ndarray:
    return (np.random.randn(int(16 * ms)) * 0.002).astype(np.float32)  # faint room noise


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.log: list[tuple[float, dict]] = []
        self.playing: dict | None = None  # {"id", "start", "duration"}
        self.queue: list[dict] = []
        self.pending_meta: dict[int, dict] = {}
        self.metrics: list[dict] = []
        self.t0 = time.perf_counter()

    def t(self):
        return time.perf_counter() - self.t0

    async def reader(self):
        async for msg in self.ws:
            if isinstance(msg, bytes):
                cid = int.from_bytes(msg[:4], "little")
                meta = self.pending_meta.pop(cid)
                dur = (len(msg) - 4) / 2 / meta["sr"]
                self.queue.append({"id": cid, "duration": dur, "text": meta["text"]})
                continue
            m = json.loads(msg)
            self.log.append((self.t(), m))
            if m["type"] == "chunk":
                self.pending_meta[m["id"]] = m
            elif m["type"] == "stop":
                await self.stop()
            elif m["type"] == "metrics":
                self.metrics.append(m)
                print(f"  [{self.t():6.2f}] METRICS {json.dumps({k: v for k, v in m.items() if k != 'type'})}")
            elif m["type"] in ("user", "tutor_done", "partial", "waiting", "scenario", "settings"):
                print(f"  [{self.t():6.2f}] {m['type']:10s} {m.get('text', '') or m}")

    async def player(self):
        """Plays queued chunks back-to-back in real time, like the browser."""
        while True:
            if self.playing is None and self.queue:
                c = self.queue.pop(0)
                self.playing = {**c, "start": time.perf_counter()}
                await self.ws.send(json.dumps({"type": "playing", "id": c["id"]}))
            if self.playing and time.perf_counter() - self.playing["start"] >= self.playing["duration"]:
                await self.ws.send(json.dumps({"type": "played", "id": self.playing["id"]}))
                self.playing = None
                continue
            await asyncio.sleep(0.005)

    async def stop(self):
        p, self.playing = self.playing, None
        self.queue.clear()
        report = {"type": "stopped", "id": p["id"] if p else None,
                  "played": time.perf_counter() - p["start"] if p else 0, "duration": p["duration"] if p else 0}
        print(f"  [{self.t():6.2f}] STOP received -> {report}")
        await self.ws.send(json.dumps(report))

    def tutor_busy(self):
        return self.playing is not None or bool(self.queue)

    async def stream(self, audio: np.ndarray):
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
        start = time.perf_counter()
        for i in range(0, len(pcm), FRAME):
            await self.ws.send(pcm[i : i + FRAME].tobytes())
            target = start + (i + FRAME) / 16000
            await asyncio.sleep(max(0, target - time.perf_counter()))

    async def wait_tutor_quiet(self, timeout=20):
        """Stream silence until the tutor has finished speaking (like a polite learner)."""
        end = time.perf_counter() + timeout
        heard_any = False
        while time.perf_counter() < end:
            await self.stream(silence(160))
            if self.tutor_busy():
                heard_any = True
            elif heard_any:
                return
        print("  !! timeout waiting for tutor")


async def main():
    tts = TTS()
    lines = [
        "Hei! Jeg heter Ali, og jeg bor i Oslo.",
        "Jeg jobber som ingeniør i et firma som lager værmeldinger.",
        "Jeg liker å gå på ski om vinteren.",
    ]
    async with websockets.connect(URL, max_size=None) as ws:
        c = Client(ws)
        reader = asyncio.create_task(c.reader())
        player = asyncio.create_task(c.player())
        while not any(m["type"] == "ready" for _, m in c.log):
            await asyncio.sleep(0.05)
        await ws.send(json.dumps({"type": "start", "scenario": "fri", "level": "B1", "end_ms": 800}))
        print("== opening")
        await c.wait_tutor_quiet()
        for line in lines:
            print(f"== learner: {line}")
            await c.stream(learner(tts, line))
            await c.wait_tutor_quiet()

        print("== learner pauses mid-sentence ('...og') then continues")
        await c.stream(learner(tts, "Jeg har to barn og"))
        await c.stream(silence(950))
        await c.stream(learner(tts, "en hund som heter Bamse."))
        await c.wait_tutor_quiet()

        print("== barge-in: learner talks over the tutor")
        await c.stream(learner(tts, "Hva liker du å gjøre i helgene?"))
        t_wait = time.perf_counter()
        while not c.tutor_busy() and time.perf_counter() - t_wait < 10:
            await c.stream(silence(64))
        await c.stream(silence(600))  # let the tutor talk a bit
        t_barge = time.perf_counter()
        n_log = len(c.log)
        barge = learner(tts, "Unnskyld, jeg må avbryte deg. Kan vi snakke om mat?")
        onset = vad_onset_ms(barge)
        await c.stream(barge)
        stops = [t for t, m in c.log[n_log:] if m["type"] == "stop"]
        if stops:
            after = (stops[0] + c.t0 - t_barge) * 1000 - onset
            print(f"  barge-in: stop arrived {after:.0f} ms after speech onset (gate 256 ms -> {after - 256:.0f} ms after confirmation)")
        await c.wait_tutor_quiet()

        print("== voice command: gjenta")
        await c.stream(learner(tts, "Kan du gjenta?"))
        await c.wait_tutor_quiet()

        await ws.send(json.dumps({"type": "stop"}))
        reader.cancel(), player.cancel()
        gaps = [m["gap_after_window_ms"] for m in c.metrics if m.get("gap_after_window_ms") is not None]
        tot = [m["speech_end_to_audio_ms"] for m in c.metrics if m.get("speech_end_to_audio_ms") is not None]
        print(f"\nSUMMARY gap after silence window: {gaps}\n        speech end -> first audio: {tot}")


if __name__ == "__main__":
    asyncio.run(main())
