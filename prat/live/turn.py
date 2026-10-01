"""End-of-turn and barge-in detection from per-frame VAD probabilities.

Pure state machine (no audio, no clocks): feed one probability per 32 ms frame and
react to the events it returns. Timing is measured in frames so it is deterministic
and unit-testable.

Events
------
``speech_start``  user started talking (after ``start_ms`` of speech)
``pause``         user went quiet for ``pause_ms`` — time to transcribe speculatively
``resume``        user continued after a ``pause`` — discard speculative work
``end``           user finished the turn (silence ≥ end threshold, which can be extended)
``barge_in``      user talked over the tutor for ≥ ``barge_ms``
"""

from __future__ import annotations

from dataclasses import dataclass

from .vad import FRAME_MS


@dataclass
class TurnConfig:
    speech_threshold: float = 0.5
    silence_threshold: float = 0.35
    start_ms: float = 96  # speech needed to open a user turn
    pause_ms: float = 160  # silence before speculative STT/LLM starts (cheap to cancel)
    end_ms: float = 800  # silence that ends a turn (live-adjustable; learners pause a lot)
    barge_ms: float = 256  # speech over the tutor needed to interrupt (ignores coughs)
    barge_threshold: float = 0.6  # stricter while the tutor talks (echo residue)


class TurnDetector:
    def __init__(self, cfg: TurnConfig | None = None):
        self.cfg = cfg or TurnConfig()
        self.tutor_speaking = False
        self.reset()

    def reset(self) -> None:
        self.in_turn = False
        self.paused = False
        self._speech = 0  # consecutive speech frames
        self._silence = 0  # consecutive silence frames
        self._extra_ms = 0.0  # end-of-turn extension (e.g. sentence looks unfinished)

    @staticmethod
    def _frames(ms: float) -> int:
        return max(1, round(ms / FRAME_MS))

    @property
    def end_frames(self) -> int:
        return self._frames(self.cfg.end_ms + self._extra_ms)

    def extend(self, ms: float) -> None:
        """Wait longer before ending this turn (called when the transcript looks unfinished)."""
        self._extra_ms = max(self._extra_ms, ms)

    def update(self, prob: float) -> list[str]:
        cfg = self.cfg
        threshold = cfg.barge_threshold if self.tutor_speaking and not self.in_turn else cfg.speech_threshold
        events: list[str] = []

        if prob >= threshold:
            self._speech += 1
            self._silence = 0
        elif prob < cfg.silence_threshold:
            self._silence += 1
            self._speech = 0
        else:
            # Ambiguous frame: keeps the current run going without starting a new one.
            if self._speech:
                self._speech += 1
            if self._silence:
                self._silence += 1

        if not self.in_turn:
            need = cfg.barge_ms if self.tutor_speaking else cfg.start_ms
            if self._speech >= self._frames(need):
                self.in_turn, self.paused, self._extra_ms = True, False, 0.0
                events.append("barge_in" if self.tutor_speaking else "speech_start")
            return events

        if self._speech and self.paused:
            self.paused = False
            self._extra_ms = 0.0
            events.append("resume")
        if self._silence >= self._frames(cfg.pause_ms) and not self.paused:
            self.paused = True
            events.append("pause")
        if self._silence >= self.end_frames:
            self.in_turn = False
            self.paused = False
            self._extra_ms = 0.0
            events.append("end")
        return events
