---
name: model-scout
description: Research the latest models and runtimes for Prat (Norwegian speech recognition, conversation LLM, text-to-speech, VAD), check them against the app's constraints, and propose upgrades as GitHub issues. Use when the user asks for new models, state of the art, "what's new", whether something can be upgraded, or runs /model-scout.
---

# Model scout

Find models that would make Prat better **within its constraints**, show evidence, and propose upgrades.
Never change the app's default models as part of a scout. Proposals go to the user, and the work goes into issues.

## 1. Load the current constraints (from the repo, not from memory)

- Current models: `prat/config.py` (`STT_MODEL`, `MLX_MODEL`, `DEFAULT_VOICE`) and `PIPER_VOICES` in `prat/tts.py`.
- Latency budget per stage: the tables in `docs/design.md`.
- Hardware and requirements: "Requirements" and "Performance" in `README.md`.
- Platform goals and open wishes: `gh issue list --state open` (e.g. #1, Windows/Linux support).

Hard constraints (reject a candidate that breaks any of these):

| | Constraint |
|---|---|
| Local | Runs fully offline after a one-time download. No hosted APIs, including Hugging Face Spaces. |
| Weights | Publicly downloadable. A gated model is OK if access is automatic; a private one is not. |
| License | Allows free redistribution of an open-source app that downloads it (Apache, MIT, CC-BY, CC0, Gemma terms and similar). Note anything non-commercial. |
| Hardware | Fits **16 GB of unified memory together with the other models** (today: Borealis 4B ~4.5 GB, Whisper small ~0.5 GB). |
| Runtime | Runs on Apple silicon now (MLX, or ONNX/CoreML). Also note whether it runs on CUDA/CPU (issue #1). |
| Norwegian | Bokmål quality at least as good as the current model. |

Stage-specific constraints:

- **STT:** about ≤ 250 ms for a 4 s utterance; must keep the learner's mistakes (verbatim, not "corrected").
- **LLM:** time to first token < 300 ms with prompt caching (prefix/KV-cache reuse is required); streaming.
- **TTS for live mode:** first audio ≤ 150 ms and faster than real time, with streaming. A slower TTS can still qualify **for practice mode only** (replay, slow, shadowing), so say which mode it fits.
- **VAD:** runs on CPU at 32 ms frames.

## 2. Scan the known sources

```bash
python3 .claude/skills/model-scout/scan.py            # changes since the last scan
python3 .claude/skills/model-scout/scan.py --since 2026-01-01
```

It checks the watched private NbAiLab repos for anything that became public, new and updated models from
the watched authors, keyword searches, and NbAiLab Spaces (a new Space often means a new model).
**A watched repo turning public is the top finding.** Report it first.

## 3. Research the state of the art

Run the searches for all four stages in parallel, or delegate them to a `deep-web-researcher` agent for a broad sweep.
Prefer primary sources: model cards, GitHub READMEs, papers, release notes and official benchmarks. For each stage, look for:

- New Norwegian models: NbAiLab, Språkbanken, NTNU, UiO/LTG, NRK, and fine-tunes on Hugging Face.
- New multilingual models with strong Norwegian (check the model card's language list *and* any Norwegian eval).
- New or faster runtimes for models we already use (MLX ports, GGUF, ONNX, CoreML, Nano-vLLM).
- Ports in `mlx-community` or by FredrikKarlssonSpeech for anything promising.

Before judging a candidate, check that its weights are actually reachable: query
`https://huggingface.co/api/models/<id>`. A 200 means public, or gated with a `gated` field. A 401 means private
or nonexistent, so the model is not usable.

## 4. Verify the best candidates

For anything that looks like a real upgrade, measure it on this machine instead of trusting the model card:

- Work in `scratch/` (git-ignored) with `uv run --with <pkg>` so the project environment stays untouched.
- **Ask the user before downloading more than ~2 GB.**
- Use Norwegian test sentences from a real scenario (`prat/scenarios.py`).
- TTS: generation time / audio length, time to first audio, and intelligibility. Transcribe the output with
  `prat.stt.STT` and compare it to the input text.
- STT: compare with the current model on the same clips, checking accuracy and whether learner errors are kept.
- LLM: time to first token, both cold and with a cached prefix, plus a quick Norwegian quality check against the current model.

Say clearly which numbers you measured and which come from model cards.

## 5. Report and propose

Report in this order:

1. **Watched repos that became public.**
2. **Recommended upgrades.** For each: the stage, the candidate, measured or claimed numbers versus the current model and the budget, memory, license, which mode it fits (live or practice), and the work involved (which files change).
3. **Worth watching.** Promising candidates that break one constraint today, and which one.
4. **Checked and rejected,** each with a one-line reason, so the next scout doesn't redo it.

List the sources at the end.

Then offer to file a GitHub issue for each recommended upgrade, using the label `model-upgrade` (create it if
missing). Check `gh issue list --label model-upgrade --state all` first and comment on an existing issue instead of
creating a duplicate. File only after the user agrees.

Finally, update `watchlist.json`: add newly found private repos to `private_watch` and remove ones that became
public and have been handled. Then run `scan.py --save` to record the scan date.
