# Prat — Norwegian conversation practice (working spec)

> **Provenance:** the original spec lives in Notion but could not be found via the
> Notion connector on 2026-10-01 (searched: Norwegian, norsk, TTS, Bokmål, Whisper,
> speech, conversation, language learning; scanned ~100 recent pages). This document
> reconstructs the intent from the brief: *"an app to learn Norwegian conversation by
> deploying some Norwegian text-to-speech models"*. Items marked **[ASSUMPTION]**
> should be checked against the Notion version.

## Goal
Practise *spoken* Norwegian (Bokmål) by holding role-play conversations with an AI
partner that speaks with real Norwegian TTS voices, listens with a Norwegian-tuned
speech recogniser, and gives gentle corrections.

## Principles
- **Local-first.** All speech models run on the Mac (M1 Pro, 16 GB). No cloud needed.
- **Pluggable models.** TTS, STT and LLM are swappable engines behind small interfaces,
  so new Norwegian models can be "deployed" by adding one class.
- **Conversation over drills.** The core loop is talk → hear reply → get feedback.

## Models deployed
| Role | Default | Alternatives |
|---|---|---|
| TTS | Piper `no_NO-talesyntese-medium` (ONNX, CPU, fast) | Piper `no_NO-nvcc-medium`; Meta MMS VITS for Bokmål (`thomasht86/mms-tts-nob` mirror, transformers) |
| STT | NB-Whisper (`NbAiLab/nb-whisper-small`) from the National Library | `nb-whisper-base` (faster), `-medium` (better) |
| Conversation LLM | Claude (`claude-opus-5-5`) when `ANTHROPIC_API_KEY` is set | NbAiLab **Borealis** 4B instruct (MLX, local, Norwegian-native); scripted offline fallback |

[ASSUMPTION] Bokmål only; Nynorsk/dialects out of scope for v1.

## Features (v1)
1. **Scenarios** — kafé, fastlegen, jobbintervju, lunsjprat på jobben, butikken,
   visning av leilighet, Norskprøven muntlig (exam-style questions), fri samtale.
2. **Level** — A2 / B1 / B2 controls vocabulary and sentence length of the partner.
3. **Speak or type** — push-to-talk (hold button / space bar) → NB-Whisper → partner.
4. **Partner turn** returns: Norwegian reply (spoken by TTS), English translation,
   feedback on the learner's last utterance (corrected sentence + short explanation),
   up to 3 new vocabulary items, and 2–3 suggested replies the learner could say.
5. **Listening controls** — hide Norwegian text until tapped (listening challenge),
   replay, slow replay, tap any word to hear it alone, choose voice and speed.
6. **Shadowing** — any partner sentence or suggestion can be practised: hear it,
   record yourself, get a word-level match score from the recogniser.
7. **Vocabulary notebook** — saved words with translations, persisted in SQLite,
   playable with TTS.
8. **Session history** — conversations saved locally and can be reviewed.

## Out of scope for v1 [ASSUMPTION]
Accounts/multi-user, mobile app, phoneme-level pronunciation scoring, spaced
repetition scheduling, streaming/full-duplex voice.

## Architecture
```
browser (vanilla JS, Web Audio → 16 kHz WAV)
   │  REST (JSON / WAV)
FastAPI  ──  engines/tts   (Piper, MMS)
         ──  engines/stt   (NB-Whisper via transformers)
         ──  engines/llm   (Claude | Borealis-MLX | scripted)
         ──  store         (SQLite: sessions, turns, vocab)
```
Models load lazily on first use and are cached in-process.

## API
- `GET  /api/config` — available voices, scenarios, levels, active LLM backend
- `POST /api/tts` `{text, voice, speed}` → `audio/wav`
- `POST /api/stt` multipart `audio` (wav) → `{text}`
- `POST /api/sessions` `{scenario, level}` → session + partner's opening turn
- `POST /api/sessions/{id}/turn` `{text}` → partner turn (reply, translation, feedback, vocab, suggestions)
- `GET  /api/sessions`, `GET /api/sessions/{id}`
- `POST /api/shadow` multipart `audio` + `target` → `{heard, score, words[]}`
- `GET/POST/DELETE /api/vocab`

## Success criteria for v1
- One-command start (`./run.sh`), works offline after first model download.
- Partner reply audible < ~4 s after the learner stops speaking on the M1 Pro (local LLM).
- Test suite covers scoring, LLM output parsing, and the API with fake engines.
