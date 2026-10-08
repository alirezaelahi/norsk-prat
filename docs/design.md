# Design goals and status

Prat was built against a short design spec ("Norwegian Voice Tutor"). Its central requirement:

> A local, offline, spoken Norwegian conversation partner. Continuous back-and-forth
> conversation, not dictation and not push-to-talk. **It must feel smooth**: lag, talking over
> the user, cutting them off mid-thought, or losing the thread make it unusable.

This page records how the build meets each goal and where it differs. Measurements were taken
on an **M1 Pro with 16 GB** (the spec targeted a 40 GB MacBook) using the headless real-time
test `tests/e2e/live_client.py`, which streams synthetic learner speech at real-time pace.

## Acceptance criteria

| Criterion | Target | Measured | |
|---|---|---|---|
| First audio after the learner stops | ≤ ~1.0 s (700–900 ms ideal) | **median ≈ 0.95–1.0 s**, range 0.81–1.3 s over typical turns | ✅ / borderline |
| Perceived gap after the silence window | ≤ ~700 ms | **10–500 ms**, usually under 250 ms | ✅ |
| No premature cut-off | wait through learner pauses | 800 ms window (live slider 400–2000), **+700 ms if the transcript looks unfinished** ("…og", "eh", no final punctuation); a reply already queued is dropped if the learner starts talking again, and the two parts are merged | ✅ (tested: "Jeg har to barn og … en hund") |
| Barge-in | tutor stops ≤ ~200 ms | **~90–180 ms after speech is confirmed** (confirmation needs 256 ms of speech, to ignore coughs) | ✅ |
| No self-echo | tutor never transcribed | browser echo cancellation + stricter VAD threshold while the tutor talks + headphones hint | ⚠️ not testable headless; see deviations |
| Correct Norwegian | bokmål, natural | Borealis 4B 8-bit: natural, recasts errors; occasional slips (see bake-off) | ✅ mostly |
| Fully local | no keys, no network after download | all models local; `prat` switches to offline mode once everything is downloaded | ✅ |

Every turn's timings are written to `data/latency.jsonl` and shown live in the UI.

## Per-stage latency

| Stage | Budget | Measured (median) |
|---|---|---|
| End-of-turn silence | 700–900 ms by design | 800 ms default |
| STT of the final chunk | ≤ 150 ms | ~220 ms (NB-Whisper small, MLX), **hidden**: see speculation below |
| LLM time to first token | < 300 ms | **~250 ms** (prompt cache: only the new words are processed) |
| LLM to first sentence | ≤ 400 ms | ~350–500 ms |
| TTS first audio | ≤ 150 ms | **~50 ms** (Piper) |

**Speculation.** STT, the LLM and TTS start as soon as the learner has been quiet for 160 ms.
The audio is held back until the 800 ms window confirms the turn, and thrown away if they keep
talking. Most of the pipeline runs *inside* the silence window, which is why the gap after the
window is usually under 250 ms even though the stages add up to about 800 ms.

## Components

| Spec | Built |
|---|---|
| 4.1 Audio I/O + AEC (macOS voice-processing I/O) | Browser `getUserMedia({echoCancellation:true})` (WebRTC AEC) → 16 kHz PCM over WebSocket; headphones recommended in the UI |
| 4.2 Silero VAD, adaptive end-of-turn, live threshold | Silero v5 ONNX (MIT, 0.1 ms/frame, no torch); `prat/live/turn.py`; slider in the UI |
| 4.3 NB-Whisper on MLX, partials, don't "fix" errors | Community MLX conversions (`FredrikKarlssonSpeech/nb-whisper-{small,medium}-mlx`); partial transcripts every 1.5 s; temperature 0, no conditioning on previous text |
| 4.4 Borealis, streaming, warm, short context | `mlx-lm` streaming, model kept loaded, **KV prompt cache reused across turns**, rolling window of 16 messages trimmed in blocks; Metal kernels warmed on connect |
| 4.5 Sentence chunker | `prat/live/chunker.py`; first chunk may flush at a comma after 4 words |
| 4.6 Piper, ~0.9× speed, adjustable | Piper `talesyntese` (default) + 10 `nvcc` speakers; 0.9× default; slider plus "saktere"/"fortere" voice commands |
| 4.7 Barge-in, truthful history | Server stops the client, cancels LLM and TTS; the client reports how much of the current chunk was heard; history keeps only what was heard ("To barn – det…") |
| Tutor behaviour | `prat/live/tutor.py`: bokmål only (English questions answered briefly), 1–3 sentences, ends with a question, recasting with at most one explicit correction, start level from the UI with adaptation, no markdown or emoji |
| Voice modes | "gjenta" (instant replay, no LLM), "saktere"/"fortere", "forklar X"/"hva betyr X", "la oss øve på kafé/lege/NAV/jobbintervju/…" (switches role-play); free chat by default |
| v2: summary, live transcript | Already in: live transcript (tap a word for its meaning), end-of-session summary with corrections |
| Instrumentation | Per-turn timestamps: speech end, turn end, STT, first token, first sentence, TTS, first audio out. Shown in the UI and logged to JSONL |

## Deviations and why

- **Echo cancellation runs in the browser, not native macOS VPIO.** A browser front-end gave a UI
  (transcript, latency readout, settings) and reused the existing app. Chrome's AEC is the same
  class of solution, but **real-speaker echo hasn't been tested yet**: headless Chromium on macOS
  can't open the real microphone. With speakers, echo is guarded by the stricter VAD threshold and
  the 256 ms barge-in gate. If echo slips through, the next step is a native client using
  AVAudioEngine voice processing.
- **STT is NB-Whisper *small*, not medium.** On the M1 Pro, medium takes about 700 ms per
  utterance and small about 220 ms. On a newer 40 GB Mac, try medium:
  `PRAT_STT_MODEL=FredrikKarlssonSpeech/nb-whisper-medium-mlx`.
- **The LLM is Borealis 4B 8-bit.** 12B didn't fit alongside everything else in 16 GB. Try it on
  the 40 GB machine: `PRAT_MLX_MODEL=NbAiLab/borealis-12b-instruct-preview-mlx-8bit`.

## Model bake-off so far

| Model | First token | First sentence | Norwegian quality |
|---|---|---|---|
| Borealis 4B 8-bit (default) | ~155 ms | ~370 ms | natural, good recasts; once echoed the learner's word-order error back |
| Borealis 4B 4-bit (`models/borealis-4b-4bit`, quantised locally) | ~150 ms | ~270 ms | noticeably worse: repeats the learner's name each turn, occasional non-sequiturs |

(Benchmarked in isolation. In the live pipeline the first token takes about 250 ms.)

**Memory note.** On 16 GB with Chrome and VS Code open, the system swapped heavily during testing
and the first token jumped to 1.5–2 s. On the 40 GB target this shouldn't happen. On small
machines, the 4-bit model halves the LLM's memory.

## Open questions, answered

- *Does NB-Whisper have an MLX build?* Yes: community conversions for tiny to large (small and medium used here).
- *Piper Norwegian voice quality?* `talesyntese` is clear (an NB-Whisper round trip is word-perfect); the
  `nvcc` speakers are noticeably less clear. No National Library Piper voice for Norwegian NST
  turned up on Hugging Face (only a Swedish one), so this remains open, along with F5/Chatterbox Norwegian.
- *Is silence-only end-of-turn enough?* Mostly, with the unfinished-transcript extension and the
  "merge if they continue" rule. A semantic end-of-turn model is still a possible upgrade.
- *Known STT limitation:* Whisper smooths over inflection errors ("koste" → "koster"), so the tutor
  can't recast those. Word-order errors come through.

## Not yet done

- Native macOS client with VPIO echo cancellation (only needed if browser AEC isn't good enough with speakers).
- Running summary for very long sessions (the rolling window currently drops old turns).
- A persistent learner profile across sessions.
- A semantic end-of-turn model; an F5-TTS / Chatterbox voice comparison.
