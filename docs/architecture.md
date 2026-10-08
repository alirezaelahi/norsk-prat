# Architecture

Prat is one Python process (FastAPI + asyncio) plus a plain-JavaScript web page. The browser
handles audio input and output, including echo cancellation, and the UI. Everything else (turn-taking,
speech recognition, the language model, speech synthesis) runs in the server process.

```
 Browser                                   Server (one process)
 ───────                                   ───────────────────────────────────────────────────────────
 mic (echo-cancelled)                      LiveSession (prat/live/session.py), one per connection
   └─ AudioWorklet → 16 kHz PCM ──WS──▶      Silero VAD (32 ms frames) → TurnDetector
                                              │ pause ─▶ speculate: NB-Whisper → Borealis ─▶ chunker ─▶ Piper
                                              │ end   ─▶ commit: release held audio, keep streaming
                                              │ barge ─▶ "stop" → cancel generation, fix history
 speaker ◀── scheduled AudioBuffers ◀─WS──── audio chunks (one per sentence) + JSON events
```

## Turn-taking (`prat/live/turn.py`)

A pure state machine, fed one Silero speech probability per 32 ms frame. Timing is counted in
frames, so it's deterministic and fully unit-tested (`tests/test_live_logic.py`).

| Event | When | Default |
|---|---|---|
| `speech_start` | speech for `start_ms` | 96 ms |
| `pause` | silence for `pause_ms` → start speculative work | 160 ms |
| `resume` | speech again after a `pause` → discard speculative work | — |
| `end` | silence for `end_ms` (+ extension) → the learner's turn is over | 800 ms, live slider |
| `barge_in` | speech over the tutor for `barge_ms`, at a stricter threshold | 256 ms, p ≥ 0.6 |

**Waiting for learners.** When the speculative transcript looks unfinished (it ends in "og",
"men", "eh"…, or has no final punctuation; see `tutor.looks_unfinished`), the end threshold
is extended by 700 ms. If the learner starts talking again before a reply has started playing,
the reply is dropped and both parts are merged into one turn.

## Speculation (`prat/live/session.py`)

On `pause`, the session runs the whole reply pipeline straight away: transcription, prompt
planning, LLM streaming, sentence chunking and Piper synthesis. It keeps the finished audio in a
`Response` object instead of sending it. Then:

- on `end`, `Response.commit()` sends the held chunks at once and streams the rest as it arrives;
- on `resume`, the response is cancelled (the LLM checks a cancel flag after every token).

Most of the work therefore happens inside the silence window the turn detector needs anyway.
That is why the gap the learner hears after the window is usually under 250 ms.

## The language model (`prat/local_lm.py`)

Borealis runs through `mlx-lm` with a **reusable KV prompt cache**. Each turn's prompt (system
prompt + history) shares a long prefix with the previous turn's prompt plus its generated reply.
Only the new tokens are processed (prefix match, then `trim_prompt_cache` for any divergent tail),
which brings time-to-first-token from ~1.4 s down to ~250 ms. History is a rolling window of 16
messages, trimmed in blocks so the cache stays valid on most turns. `normalize_messages` guarantees
the strictly alternating user/assistant history that the chat template requires.

On startup, `models.warm_up` loads every model and runs one cold and one cached generation, so
that Metal kernels are compiled before the learner's first turn.

All MLX work (Whisper and the LLM) runs on a single worker thread (`MLX_EXEC`) behind one lock: one
GPU, one job at a time. Piper runs on its own thread, so sentence *n* is synthesised while
sentence *n + 1* is still being generated.

## Barge-in and truthful history

When the learner talks over the tutor:

1. the server sends `stop`, and the browser stops all scheduled audio right away;
2. the browser reports which chunk was playing and how many seconds of it were heard;
3. the server cancels generation and TTS, then rewrites the tutor's turn in the history to
   what was actually heard (whole chunks, plus a proportional part of the interrupted one, with "…").

## WebSocket protocol (`/ws/live`)

Binary frames from the browser carry 16 kHz mono Int16 PCM (512 samples each). Binary frames from the
server carry a 4-byte little-endian chunk id followed by Int16 PCM at the rate given in the matching `chunk` event.

**Browser → server (JSON)**

| type | fields | |
|---|---|---|
| `start` | `scenario, level, voice, speed, end_ms` | begin a conversation (the tutor opens) |
| `settings` | any of `end_ms, speed, voice` | live changes |
| `text` | `text` | typed input instead of speech |
| `playing` / `played` | `id` | a chunk started / finished playing |
| `stopped` | `id, played, duration` | reply to `stop`: what was heard |
| `stop` | — | end the conversation |

**Server → browser (JSON)**

| type | fields | |
|---|---|---|
| `ready` | — | models loaded; send `start` |
| `session` | `id, scenario, level, speed, end_ms` | conversation created (id is used for the summary) |
| `state` | `state` | `listening` / `user` / `thinking` / `speaking` (drives the orb) |
| `partial` | `text` | live transcript while the learner speaks |
| `waiting` | `text` | the sentence looked unfinished, so the tutor waits longer |
| `user` | `text, turn` | the learner's final transcript |
| `chunk` | `id, turn, text, sr` | metadata for the next binary audio frame |
| `stop` | — | barge-in: stop playback now, reply with `stopped` |
| `tutor_done` | `turn, text, interrupted?` | the tutor's turn as heard |
| `metrics` | per-stage ms | latency of the turn (also appended to `data/latency.jsonl`) |
| `settings`, `scenario` | | changed by a voice command |
| `error` | `message` | a reply failed |

## Practice mode (`prat/practice.py`, REST)

Practice mode uses plain REST endpoints and the same Borealis model. Each task has its own
small prompt: `reply`, `feedback` (correction shown as a word diff), `translate`, `gloss` (one word
in context) and `suggest`. These are more reliable from a 4B model than one prompt doing everything,
and the spoken reply comes back first.

| Endpoint | |
|---|---|
| `GET /api/config` | voices, scenarios, levels |
| `POST /api/warmup` | waits until the models are warm (the UI enables the orb after this) |
| `POST /api/tts`, `POST /api/stt` | speak text / transcribe a WAV upload |
| `POST /api/sessions`, `POST /api/sessions/{id}/turn` | practice conversation |
| `POST /api/sessions/{id}/summary`, `…/suggestions` | corrections overview / reply ideas |
| `POST /api/turns/{id}/feedback`, `…/translation` | per-turn extras (cached on the turn) |
| `POST /api/gloss`, `POST /api/shadow` | word meaning / shadowing score |
| `GET/POST/DELETE /api/vocab` | word list |

## Storage

`data/prat.db` (SQLite) holds the conversations from both modes and the word list.
`data/latency.jsonl` holds per-turn timings. Neither is committed to git.
