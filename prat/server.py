"""FastAPI app: REST API + static web UI."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Generic, TypeVar

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .llm import Partner, Turn, make_partner
from .scenarios import LEVELS, SCENARIOS
from .scoring import score
from .store import Store
from .stt import STT
from .tts import TTS, list_voices

log = logging.getLogger("prat")
T = TypeVar("T")


class Lazy(Generic[T]):
    """Build an expensive object on first use (models load on demand, not at startup)."""

    def __init__(self, factory: Callable[[], T]):
        self._factory, self._obj, self._lock = factory, None, threading.Lock()

    def get(self) -> T:
        if self._obj is None:
            with self._lock:
                if self._obj is None:
                    self._obj = self._factory()
        return self._obj

    @property
    def loaded(self) -> bool:
        return self._obj is not None


class TTSReq(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    voice: str = config.DEFAULT_VOICE
    speed: float = 1.0


class NewSession(BaseModel):
    scenario: str
    level: str = "A2"


class TurnReq(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class GlossReq(BaseModel):
    word: str = Field(min_length=1, max_length=60)
    sentence: str = ""


class VocabReq(BaseModel):
    word: str = Field(min_length=1, max_length=120)
    meaning: str = ""
    example: str = ""


def create_app(
    tts: TTS | None = None,
    stt: STT | None = None,
    partner: Partner | Callable[[], Partner] | None = None,
    store: Store | None = None,
) -> FastAPI:
    app = FastAPI(title="Prat — norsk samtaletrening")
    tts = tts or TTS()
    stt_lazy = Lazy(lambda: stt or STT())
    if isinstance(partner, Partner):
        partner_lazy = Lazy(lambda: partner)
    else:
        partner_lazy = Lazy(partner or make_partner)
    store = store or Store(config.DATA_DIR / "prat.db")
    app.state.partner = partner_lazy

    def _session(sid: str) -> dict:
        s = store.get_session(sid)
        if not s:
            raise HTTPException(404, "session not found")
        return s

    def _history(s: dict) -> list[Turn]:
        return [Turn(t["role"], t["text"]) for t in s["turns"]]

    # ------------------------------------------------------------ config
    @app.get("/api/config")
    def get_config():
        return {
            "voices": [v.__dict__ for v in list_voices()],
            "default_voice": config.DEFAULT_VOICE,
            "scenarios": [
                {"id": s.id, "title": s.title, "title_en": s.title_en, "goal": s.goal} for s in SCENARIOS
            ],
            "levels": list(LEVELS),
            "partner": partner_lazy.get().name if partner_lazy.loaded else None,
            "stt_model": config.STT_MODEL,
        }

    @app.post("/api/warmup")
    def warmup():
        """Load the models so the first real turn is fast."""
        p = partner_lazy.get()
        stt_lazy.get()._load()
        tts.speak("Hei!")
        return {"partner": p.name}

    # ------------------------------------------------------------ speech
    @app.post("/api/tts")
    def post_tts(req: TTSReq):
        try:
            wav = tts.speak(req.text, req.voice, req.speed)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return Response(wav, media_type="audio/wav", headers={"Cache-Control": "max-age=86400"})

    @app.post("/api/stt")
    def post_stt(audio: UploadFile = File(...)):
        try:
            return {"text": stt_lazy.get().transcribe(audio.file.read())}
        except Exception as e:  # unreadable audio etc.
            log.exception("stt failed")
            raise HTTPException(400, f"could not transcribe audio: {e}")

    @app.post("/api/shadow")
    def post_shadow(audio: UploadFile = File(...), target: str = Form(...)):
        heard = stt_lazy.get().transcribe(audio.file.read())
        return {"heard": heard, **score(target, heard)}

    # ------------------------------------------------------------ sessions
    @app.post("/api/sessions")
    def new_session(req: NewSession):
        if req.scenario not in {s.id for s in SCENARIOS} or req.level not in LEVELS:
            raise HTTPException(400, "unknown scenario or level")
        s = store.create_session(req.scenario, req.level)
        opener = partner_lazy.get().reply(req.scenario, req.level, [])
        turn = store.add_turn(s["id"], "partner", opener)
        return {"session": s, "turn": turn}

    @app.get("/api/sessions")
    def list_sessions():
        return store.list_sessions()

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str):
        return _session(sid)

    @app.delete("/api/sessions/{sid}")
    def delete_session(sid: str):
        store.delete_session(sid)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/turn")
    def take_turn(sid: str, req: TurnReq):
        s = _session(sid)
        user_turn = store.add_turn(sid, "user", req.text.strip())
        history = _history(s) + [Turn("user", user_turn["text"])]
        reply = partner_lazy.get().reply(s["scenario"], s["level"], history)
        partner_turn = store.add_turn(sid, "partner", reply)
        return {"user_turn": user_turn, "partner_turn": partner_turn}

    @app.post("/api/sessions/{sid}/summary")
    def summary(sid: str):
        """All learner turns with their feedback (computing any that are missing)."""
        s = _session(sid)
        items = []
        for t in s["turns"]:
            if t["role"] != "user":
                continue
            if "feedback" not in t["extra"]:
                t["extra"]["feedback"] = partner_lazy.get().feedback(t["text"])
                store.update_turn_extra(t["id"], feedback=t["extra"]["feedback"])
            items.append({"id": t["id"], "text": t["text"], "feedback": t["extra"]["feedback"]})
        return {
            "turns": len(items),
            "correct": sum(1 for i in items if not i["feedback"]),
            "words": sum(len(i["text"].split()) for i in items),
            "corrections": [i for i in items if i["feedback"]],
        }

    @app.post("/api/sessions/{sid}/suggestions")
    def suggestions(sid: str):
        s = _session(sid)
        return {"suggestions": partner_lazy.get().suggest(s["scenario"], s["level"], _history(s))}

    # ------------------------------------------------------------ per-turn extras (cached on the turn)
    def _turn(tid: int) -> dict:
        t = store.get_turn(tid)
        if not t:
            raise HTTPException(404, "turn not found")
        return t

    @app.post("/api/turns/{tid}/feedback")
    def feedback(tid: int):
        t = _turn(tid)
        if t["role"] != "user":
            raise HTTPException(400, "feedback is only for learner turns")
        if "feedback" not in t["extra"]:
            fb = partner_lazy.get().feedback(t["text"])
            store.update_turn_extra(tid, feedback=fb)
            return {"feedback": fb}
        return {"feedback": t["extra"]["feedback"]}

    @app.post("/api/turns/{tid}/translation")
    def translation(tid: int):
        t = _turn(tid)
        if not t["extra"].get("translation"):
            tr = partner_lazy.get().translate(t["text"])
            store.update_turn_extra(tid, translation=tr)
            return {"translation": tr}
        return {"translation": t["extra"]["translation"]}

    @app.post("/api/gloss")
    def gloss(req: GlossReq):
        return {"word": req.word, "gloss": partner_lazy.get().gloss(req.word, req.sentence)}

    # ------------------------------------------------------------ vocab
    @app.get("/api/vocab")
    def list_vocab():
        return store.list_vocab()

    @app.post("/api/vocab")
    def add_vocab(req: VocabReq):
        return store.add_vocab(req.word, req.meaning, req.example)

    @app.delete("/api/vocab/{vid}")
    def delete_vocab(vid: int):
        store.delete_vocab(vid)
        return {"ok": True}

    # ------------------------------------------------------------ live conversation
    @app.websocket("/ws/live")
    async def live(ws: WebSocket):
        import asyncio
        import json as _json

        from .live.session import MLX_EXEC, LiveSession
        from .local_lm import get_local_lm

        await ws.accept()
        loop = asyncio.get_running_loop()
        # Load models off the event loop (first connection only).
        lm = await loop.run_in_executor(MLX_EXEC, get_local_lm)
        stt_obj = await loop.run_in_executor(MLX_EXEC, stt_lazy.get)
        session = LiveSession(ws.send_json, ws.send_bytes, stt_obj, lm, tts, store)
        await ws.send_json({"type": "ready"})
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await session.handle_audio(msg["bytes"])
                elif msg.get("text"):
                    await session.handle_message(_json.loads(msg["text"]))
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()

    # ------------------------------------------------------------ web UI
    @app.get("/")
    def index():
        return FileResponse(config.WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")
    return app
