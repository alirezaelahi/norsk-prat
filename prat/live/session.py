"""Live, hands-free conversation: one async pipeline per WebSocket connection.

    mic PCM → VAD → TurnDetector ─pause→ STT → LLM (stream) → chunker → TTS ─┐
                               └─end──→ commit: send buffered/following audio ┘
                               └─barge_in→ stop playback, cancel generation

Speculation: when the learner goes quiet for ``pause_ms`` we already transcribe and start
generating + synthesising the reply, but hold the audio back. If the silence reaches the
end-of-turn threshold the held audio is released at once; if the learner resumes, the
speculative work is thrown away. So the processing hides inside the silence window that
the turn detector needs anyway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np

from .. import config
from ..scenarios import BY_ID, LEVELS
from ..local_lm import normalize_messages
from .chunker import SentenceChunker
from .tutor import (
    Command,
    explain_instruction,
    looks_unfinished,
    parse_command,
    scenario_intro_instruction,
    system_prompt,
)
from .turn import TurnConfig, TurnDetector
from .vad import FRAME, FRAME_MS, SR

log = logging.getLogger("prat.live")

MLX_EXEC = ThreadPoolExecutor(1, thread_name_prefix="mlx")  # Whisper + LLM share the GPU
TTS_EXEC = ThreadPoolExecutor(1, thread_name_prefix="tts")
MAX_HISTORY = 16  # messages kept in the prompt; trimmed in blocks to keep the KV cache useful
UNFINISHED_EXTRA_MS = 700
PREROLL_MS = 320
PARTIAL_EVERY_S = 1.5
OPENING = "(Samtalen begynner nå. Hils kort og start samtalen med et spørsmål.)"


def now() -> float:
    return time.perf_counter()


@dataclass
class Metrics:
    turn: int
    speculative: bool = False
    t: dict = field(default_factory=dict)

    def mark(self, name: str, at: float | None = None) -> None:
        self.t.setdefault(name, at if at is not None else now())

    def summary(self) -> dict:
        t = self.t

        def ms(a: str, b: str) -> int | None:
            return round((t[b] - t[a]) * 1000) if a in t and b in t else None

        return {
            "turn": self.turn,
            "speculative": self.speculative,
            "silence_window_ms": ms("speech_end", "turn_end"),
            "stt_ms": ms("stt_start", "stt_done"),
            "llm_first_token_ms": ms("llm_start", "llm_first_token"),
            "llm_first_sentence_ms": ms("llm_start", "first_sentence"),
            "tts_first_ms": ms("first_sentence", "tts_first_done"),
            "gap_after_window_ms": ms("turn_end", "first_audio_out"),
            "speech_end_to_audio_ms": ms("speech_end", "first_audio_out"),
            "prompt_cache": t.get("cache"),
        }


class Response:
    """One tutor turn: LLM stream (or fixed text) → sentence chunks → TTS audio.

    Audio is held back until :meth:`commit`; afterwards chunks go straight to the client.
    """

    def __init__(self, session: LiveSession, metrics: Metrics, messages: list[dict] | None, fixed_text: str | None = None):
        self.s = session
        self.metrics = metrics
        self.messages = messages
        self.fixed_text = fixed_text
        self.cancel = threading.Event()
        self.committed = False
        self.held: list[tuple[dict, bytes]] = []
        self.chunks: dict[int, str] = {}  # chunk id -> text
        self.sent_ids: list[int] = []
        self.done = asyncio.Event()  # all chunks produced
        self.generated_text = ""
        self.played: set[int] = set()
        self._finished = False
        self.task = asyncio.create_task(self._run())

    # -------------------------------------------------------------- production
    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        tts_queue: asyncio.Queue = asyncio.Queue()
        tts_task = asyncio.create_task(self._tts_worker(tts_queue))
        try:
            if self.fixed_text is not None:
                chunker = SentenceChunker()
                for c in chunker.feed(self.fixed_text + " ") + chunker.flush():
                    await tts_queue.put(c)
                self.generated_text = self.fixed_text
            else:
                await self._stream_llm(loop, tts_queue)
        except asyncio.CancelledError:
            self.cancel.set()
            raise
        except Exception as e:
            log.exception("response failed")
            self.cancel.set()
            await self.s.send_json({"type": "error", "message": f"Svaret feilet: {e}"})
        finally:
            await tts_queue.put(None)
            try:
                await tts_task
            finally:
                self.done.set()
                if self.committed:
                    await self._finish()

    async def _stream_llm(self, loop: asyncio.AbstractEventLoop, tts_queue: asyncio.Queue) -> None:
        pieces: asyncio.Queue = asyncio.Queue()
        lm = self.s.lm

        def produce() -> None:
            try:
                for piece in lm.generate(self.s.system, self.messages, max_tokens=140, temperature=0.7, cancel=self.cancel):
                    loop.call_soon_threadsafe(pieces.put_nowait, piece)
            finally:
                loop.call_soon_threadsafe(pieces.put_nowait, None)

        self.metrics.mark("llm_start")
        fut = loop.run_in_executor(MLX_EXEC, produce)
        chunker = SentenceChunker()
        while True:
            piece = await pieces.get()
            if piece is None:
                break
            if self.cancel.is_set():
                continue  # drain until the producer stops
            if "llm_first_token" not in self.metrics.t:
                self.metrics.mark("llm_first_token")
                self.metrics.t["cache"] = dict(lm.last_stats)
            self.generated_text += piece
            for c in chunker.feed(piece):
                self.metrics.mark("first_sentence")
                await tts_queue.put(c)
        await fut
        if not self.cancel.is_set():
            for c in chunker.flush():
                self.metrics.mark("first_sentence")
                await tts_queue.put(c)

    async def _tts_worker(self, queue: asyncio.Queue) -> None:
        loop = asyncio.get_running_loop()
        while (text := await queue.get()) is not None:
            if self.cancel.is_set():
                continue
            text = _speakable(text)
            if not text:
                continue
            speed, voice = self.s.speed, self.s.voice
            pcm, sr = await loop.run_in_executor(TTS_EXEC, self.s.tts_pcm, text, voice, speed)
            if self.cancel.is_set():
                continue
            self.metrics.mark("tts_first_done")
            cid = self.s.next_chunk_id()
            self.chunks[cid] = text
            msg = {"type": "chunk", "id": cid, "turn": self.metrics.turn, "text": text, "sr": sr}
            if self.committed:
                await self._send(msg, pcm)
            else:
                self.held.append((msg, pcm))

    async def _send(self, msg: dict, pcm: bytes) -> None:
        self.metrics.mark("first_audio_sent")
        self.sent_ids.append(msg["id"])
        await self.s.send_json(msg)
        await self.s.send_bytes(msg["id"].to_bytes(4, "little") + pcm)

    # -------------------------------------------------------------- control
    async def commit(self) -> None:
        self.committed = True
        held, self.held = self.held, []
        for msg, pcm in held:
            await self._send(msg, pcm)
        if self.done.is_set():
            await self._finish()

    async def _finish(self) -> None:
        if not self._finished:
            self._finished = True
            await self.s._response_finished(self)

    async def abort(self) -> None:
        self.cancel.set()
        self.task.cancel()
        try:
            await self.task
        except (asyncio.CancelledError, Exception):
            pass


def _speakable(text: str) -> str:
    """Strip things a TTS voice should not read aloud."""
    import html
    import re

    text = html.unescape(text).replace("&", " og ")
    text = re.sub(r"[*_#`~<>\[\]{}|]", "", text)
    text = re.sub(r"\([^)]*\)", "", text)  # parenthetical stage directions
    return " ".join(text.split()).strip()


class LiveSession:
    def __init__(
        self,
        send_json: Callable[[dict], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
        stt,
        lm,
        tts,
        store=None,
        vad_factory=None,
    ):
        from .vad import SileroVAD

        self._send_json, self._send_bytes = send_json, send_bytes
        self.stt, self.lm, self.tts, self.store = stt, lm, tts, store
        self.vad = (vad_factory or SileroVAD)()
        self.detector = TurnDetector(TurnConfig(end_ms=config.END_OF_TURN_MS))
        self.scenario, self.level = "fri", "B1"
        self.voice, self.speed = config.DEFAULT_VOICE, config.TTS_SPEED
        self.system = system_prompt(self.scenario, self.level)
        self.messages: list[dict] = []
        self.session_id: str | None = None
        self.last_tutor = ""

        self._pcm = np.zeros(0, dtype=np.float32)
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, round(PREROLL_MS / FRAME_MS)))
        self._turn_audio: list[np.ndarray] | None = None
        self._turn_no = 0
        self._chunk_id = 0
        self._last_partial = 0.0
        self._partial_task: asyncio.Task | None = None

        self.spec: dict | None = None  # speculative work for the current pause
        self.response: Response | None = None  # committed tutor response
        self._interrupted: dict | None = None
        self._carry = ""  # learner text from a turn that was followed too quickly by more speech
        self._eot_task: asyncio.Task | None = None
        self._closed = False

    # ================================================================ I/O helpers
    async def send_json(self, msg: dict) -> None:
        if not self._closed:
            await self._send_json(msg)

    async def send_bytes(self, data: bytes) -> None:
        if not self._closed:
            await self._send_bytes(data)

    async def state(self, name: str) -> None:
        await self.send_json({"type": "state", "state": name})

    def next_chunk_id(self) -> int:
        self._chunk_id += 1
        return self._chunk_id

    def tts_pcm(self, text: str, voice: str, speed: float) -> tuple[bytes, int]:
        samples, sr = self.tts.synthesize(text, voice, speed)
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        return pcm, sr

    async def run_mlx(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(MLX_EXEC, fn, *args)

    # ================================================================ control messages
    async def handle_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "start":
            await self.start(msg)
        elif kind == "settings":
            self.apply_settings(msg)
        elif kind == "text":
            text = (msg.get("text") or "").strip()
            if text:
                await self._cancel_spec()
                if self.response:
                    await self._barge_in()
                m = Metrics(0)
                m.mark("speech_end")
                m.mark("turn_end")
                await self._commit_user_turn(text, m)
        elif kind == "playing":
            r = self.response
            if r and msg.get("id") in r.sent_ids and "first_audio_out" not in r.metrics.t:
                r.metrics.mark("first_audio_out")
                await self.state("speaking")
                await self._report(r.metrics)
        elif kind == "played":
            r = self.response
            if r:
                r.played.add(msg.get("id"))
                await self._maybe_finish_speaking()
        elif kind == "stopped":
            if self._interrupted is not None:
                self._interrupted["report"] = msg
                self._interrupted["event"].set()
        elif kind == "stop":
            await self.close()

    def apply_settings(self, msg: dict) -> None:
        if "end_ms" in msg:
            self.detector.cfg.end_ms = float(min(max(msg["end_ms"], 300), 3000))
        if "speed" in msg:
            self.speed = float(min(max(msg["speed"], 0.6), 1.3))
        if msg.get("voice"):
            self.voice = msg["voice"]

    async def start(self, msg: dict) -> None:
        await self._reset_conversation()
        self.apply_settings(msg)
        self.scenario = msg.get("scenario") if msg.get("scenario") in BY_ID else "fri"
        self.level = msg.get("level") if msg.get("level") in LEVELS else "B1"
        self.system = system_prompt(self.scenario, self.level)
        if self.store is not None:
            self.session_id = self.store.create_session(self.scenario, self.level)["id"]
        await self.send_json({"type": "session", "id": self.session_id, "scenario": self.scenario, "level": self.level,
                              "speed": self.speed, "end_ms": self.detector.cfg.end_ms})
        await self.state("thinking")
        # The tutor opens the conversation.
        m = Metrics(self._next_turn())
        self.messages = [{"role": "user", "content": OPENING}]
        await self._activate(Response(self, m, list(self.messages)))

    async def _reset_conversation(self) -> None:
        await self._cancel_spec()
        if self.response:
            await self.response.abort()
            self.response = None
        self.messages = []
        self.detector.reset()
        self.detector.tutor_speaking = False
        self._turn_audio = None
        self.vad.reset()

    async def close(self) -> None:
        self._closed = True
        if self._eot_task:
            self._eot_task.cancel()
        await self._cancel_spec()
        if self.response:
            await self.response.abort()
            self.response = None

    # ================================================================ audio path
    async def handle_audio(self, data: bytes) -> None:
        pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        self._pcm = np.concatenate([self._pcm, pcm])
        while len(self._pcm) >= FRAME:
            frame, self._pcm = self._pcm[:FRAME], self._pcm[FRAME:]
            await self._frame(frame)

    async def _frame(self, frame: np.ndarray) -> None:
        prob = self.vad(frame)
        if self._turn_audio is not None:
            self._turn_audio.append(frame)
        else:
            self._preroll.append(frame)
        for event in self.detector.update(prob):
            await self._on_event(event)
        if self._turn_audio is not None and not self.detector.paused:
            await self._maybe_partial()

    def _silence_start(self) -> float:
        """Wall time when the learner's current silence started."""
        return now() - self.detector._silence * FRAME_MS / 1000

    async def _on_event(self, event: str) -> None:
        if event in ("speech_start", "barge_in"):
            if event == "barge_in":
                await self._barge_in()
            self._turn_audio = list(self._preroll)
            self._preroll.clear()
            self._last_partial = now()
            await self.state("user")
        elif event == "pause":
            await self._start_spec(self._silence_start())
        elif event == "resume":
            await self._cancel_spec()
            await self.state("user")
        elif event == "end":
            audio = self._turn_audio or []
            self._turn_audio = None
            speech_end = self._silence_start()
            # Don't block the audio loop: VAD must keep running (barge-in, next turn).
            self._eot_task = asyncio.create_task(
                self._end_of_turn(np.concatenate(audio) if audio else np.zeros(0, np.float32), speech_end)
            )

    def _next_turn(self) -> int:
        self._turn_no += 1
        return self._turn_no

    # ---------------------------------------------------------------- partial transcripts (UI)
    async def _maybe_partial(self) -> None:
        if self._partial_task and not self._partial_task.done():
            return
        if now() - self._last_partial < PARTIAL_EVERY_S or self.spec or (self.response and not self.response.done.is_set()):
            return
        self._last_partial = now()
        audio = np.concatenate(self._turn_audio)

        async def run():
            text = await self.run_mlx(self.stt.transcribe_array, audio)
            if text and self._turn_audio is not None:
                await self.send_json({"type": "partial", "text": text})

        self._partial_task = asyncio.create_task(run())

    # ---------------------------------------------------------------- speculation
    async def _start_spec(self, speech_end: float) -> None:
        await self._cancel_spec()
        audio = np.concatenate(self._turn_audio) if self._turn_audio else np.zeros(0, np.float32)
        m = Metrics(0, speculative=True)
        m.mark("speech_end", speech_end)
        spec = {"metrics": m, "frames": len(self._turn_audio or []), "text": None, "response": None}
        spec["task"] = asyncio.create_task(self._speculate(spec, audio))
        self.spec = spec

    async def _speculate(self, spec: dict, audio: np.ndarray) -> None:
        m: Metrics = spec["metrics"]
        if self._partial_task and not self._partial_task.done():
            self._partial_task.cancel()
        m.mark("stt_start")
        text = await self.run_mlx(self.stt.transcribe_array, audio)
        m.mark("stt_done")
        spec["text"] = text
        if not text:
            return
        if looks_unfinished(text):
            self.detector.extend(UNFINISHED_EXTRA_MS)
            await self.send_json({"type": "waiting", "text": text, "extra_ms": UNFINISHED_EXTRA_MS})
        if self.response and not self.response.done.is_set():
            return  # previous reply still being produced; don't speculate on top of it
        await self._settle_interruption()
        if self._carry:
            return  # rare: merged turn, plan it at commit time
        if parse_command(text) and parse_command(text).kind == "scenario":
            return  # switching role-play has side effects: only do it once the turn is final
        plan = self._plan(text)
        if plan is not None:
            spec["response"] = Response(self, m, *plan)

    async def _cancel_spec(self) -> None:
        spec, self.spec = self.spec, None
        if not spec:
            return
        spec["task"].cancel()
        try:
            await spec["task"]
        except (asyncio.CancelledError, Exception):
            pass
        if spec.get("response"):
            await spec["response"].abort()

    # ---------------------------------------------------------------- turn commit
    async def _end_of_turn(self, audio: np.ndarray, speech_end: float) -> None:
        spec, self.spec = self.spec, None
        if spec:
            # Reuse the speculative work (the learner has said nothing new since the pause).
            m: Metrics = spec["metrics"]
            m.mark("turn_end")
            try:
                await spec["task"]
            except Exception:
                log.exception("speculation failed")
            text = spec["text"] or ""
            if not text:
                await self.state("listening")
                return
            await self._commit_user_turn(text, m, spec.get("response"))
            return
        m = Metrics(0)
        m.mark("speech_end", speech_end)
        m.mark("turn_end")
        await self.state("thinking")
        m.mark("stt_start")
        text = await self.run_mlx(self.stt.transcribe_array, audio)
        m.mark("stt_done")
        if not text:
            await self.state("listening")
            return
        await self._commit_user_turn(text, m)

    async def _commit_user_turn(self, text: str, m: Metrics, prepared: Response | None = None) -> None:
        if self._carry:
            text, self._carry = f"{self._carry} {text}", ""
            if prepared:  # it was planned without the carried text
                await prepared.abort()
                prepared = None
        if self.detector.in_turn:
            # The learner is already talking again: never answer over them. Merge into the next turn.
            self._carry = text
            if prepared:
                await prepared.abort()
            return
        m.turn = self._next_turn()
        await self._settle_interruption()
        await self.send_json({"type": "user", "text": text, "turn": m.turn})
        if self.store is not None and self.session_id:
            self.store.add_turn(self.session_id, "user", text)
        cmd = parse_command(text)
        if cmd and cmd.kind in ("slower", "faster"):
            self.speed = round(min(max(self.speed + (-0.1 if cmd.kind == "slower" else 0.1), 0.6), 1.3), 2)
            await self.send_json({"type": "settings", "speed": self.speed})
            if prepared:
                await prepared.abort()  # speed changed: re-synthesise
                prepared = None
        if prepared is None:
            plan = self._plan(text)
            if plan is None:
                await self.state("listening")
                return
            prepared = Response(self, m, *plan)
        # Commit to history (fixed-text replies like "repeat" don't enter the LLM history).
        if prepared.messages is not None:
            self.messages = prepared.messages
        await self._activate(prepared)

    def _plan(self, text: str) -> tuple[list[dict] | None, str | None] | None:
        """Decide how to answer: (messages for the LLM, None) or (None, fixed text)."""
        cmd: Command | None = parse_command(text)
        if cmd and cmd.kind in ("repeat", "slower") and self.last_tutor:
            return None, self.last_tutor
        if cmd and cmd.kind == "faster":
            return None, "Greit, jeg snakker litt fortere."
        content = text
        if cmd and cmd.kind == "explain":
            content = f"{text}\n{explain_instruction(cmd.arg)}"
        if cmd and cmd.kind == "scenario":
            self.scenario = cmd.arg
            self.system = system_prompt(self.scenario, self.level)
            asyncio.create_task(self.send_json({"type": "scenario", "scenario": self.scenario}))
            return [{"role": "user", "content": scenario_intro_instruction(cmd.arg)}], None
        msgs = normalize_messages(self.messages + [{"role": "user", "content": content}])
        if len(msgs) > MAX_HISTORY:  # trim in blocks so the prompt cache stays valid most turns
            msgs = msgs[-(MAX_HISTORY // 2):]
            if msgs[0]["role"] != "user":
                msgs = msgs[1:]
        return msgs, None

    async def _activate(self, r: Response) -> None:
        old, self.response = self.response, r
        if old is not None and old is not r:
            await old.abort()
        self.detector.tutor_speaking = True
        await self.state("thinking")
        await r.commit()

    # ---------------------------------------------------------------- tutor turn lifecycle
    async def _response_finished(self, r: Response) -> None:
        """All chunks produced and sent; record the tutor turn once playback is done."""
        if r is not self.response:
            return
        text = " ".join(r.chunks[i] for i in sorted(r.chunks))
        r.final_text = text
        if r.messages is not None:
            self.messages = r.messages + [{"role": "assistant", "content": r.generated_text.strip() or text}]
        if text:
            self.last_tutor = text
        await self.send_json({"type": "tutor_done", "turn": r.metrics.turn, "text": text})
        if self.store is not None and self.session_id and text:
            self.store.add_turn(self.session_id, "partner", text)
        if not r.chunks:
            self.detector.tutor_speaking = False
            self.response = None
            await self.state("listening")
            return
        await self._maybe_finish_speaking()

    async def _maybe_finish_speaking(self) -> None:
        r = self.response
        if r and r._finished and set(r.sent_ids) <= r.played:
            self.detector.tutor_speaking = False
            self.response = None
            if not self.detector.in_turn:
                await self.state("listening")

    async def _barge_in(self) -> None:
        """Learner talked over the tutor: stop audio now, cancel the rest, keep history truthful."""
        r = self.response
        if r is None:
            return
        self.response = None
        self.detector.tutor_speaking = False
        await self.send_json({"type": "stop"})
        r.cancel.set()
        self._interrupted = {"response": r, "event": asyncio.Event(), "report": None}
        await r.abort()
        await self.state("user")

    async def _settle_interruption(self) -> None:
        """Rewrite the interrupted tutor turn in history to what was actually heard."""
        intr, self._interrupted = self._interrupted, None
        if intr is None:
            return
        try:
            await asyncio.wait_for(intr["event"].wait(), 1.0)
        except asyncio.TimeoutError:
            pass
        r: Response = intr["response"]
        heard = _heard_text(r, intr.get("report") or {})
        if r.messages is not None:
            self.messages = r.messages + ([{"role": "assistant", "content": heard}] if heard else [])
        if heard:
            self.last_tutor = heard
            if self.store is not None and self.session_id:
                self.store.add_turn(self.session_id, "partner", heard, {"interrupted": True})
        await self.send_json({"type": "tutor_done", "turn": r.metrics.turn, "text": heard, "interrupted": True})

    async def _report(self, m: Metrics) -> None:
        s = m.summary()
        await self.send_json({"type": "metrics", **s})
        log.info("latency %s", s)
        try:
            config.LATENCY_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(config.LATENCY_LOG, "a") as f:
                f.write(json.dumps({"ts": time.time(), **s}) + "\n")
        except OSError:
            pass


def _heard_text(r: Response, report: dict) -> str:
    """Text of the chunks the learner actually heard, cutting the last one proportionally."""
    played = r.played
    parts = [r.chunks[i] for i in r.sent_ids if i in played and i in r.chunks]
    cid, secs, dur = report.get("id"), report.get("played") or 0, report.get("duration") or 0
    if cid in r.chunks and cid not in played and dur > 0 and secs > 0.15:
        words = r.chunks[cid].split()
        n = max(1, round(len(words) * min(1.0, secs / dur)))
        parts.append(" ".join(words[:n]) + ("…" if n < len(words) else ""))
    return " ".join(parts).strip()
