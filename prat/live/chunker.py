"""Turn a stream of LLM tokens into speakable chunks for TTS.

Flush at sentence ends (. ? !), or at a comma once the chunk is long enough.
The first chunk may flush earlier (shorter) to cut time-to-first-audio.
"""

from __future__ import annotations

import re

_END = re.compile(r"[.!?…]+[\"»”)]*\s")
_COMMA = re.compile(r"[,;:]\s")
# Don't split after common Norwegian abbreviations.
_ABBREV = re.compile(r"\b(f\.eks|bl\.a|osv|ca|dvs|mht|nr|kl|dr|t\.o\.m|o\.l)\.\s$", re.I)


class SentenceChunker:
    def __init__(self, comma_words: int = 8, first_comma_words: int = 4):
        self.buf = ""
        self.comma_words = comma_words
        self.first_comma_words = first_comma_words
        self.emitted = 0

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out: list[str] = []
        while True:
            chunk = self._take()
            if chunk is None:
                break
            out.append(chunk)
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        if rest:
            self.emitted += 1
            return [rest]
        return []

    def _take(self) -> str | None:
        for m in _END.finditer(self.buf):
            if _ABBREV.search(self.buf[: m.end()]):
                continue
            return self._cut(m.end())
        min_words = self.first_comma_words if self.emitted == 0 else self.comma_words
        for m in _COMMA.finditer(self.buf):
            if len(self.buf[: m.start()].split()) >= min_words:
                return self._cut(m.end())
        return None

    def _cut(self, end: int) -> str | None:
        chunk, self.buf = self.buf[:end].strip(), self.buf[end:]
        if not re.search(r"\w", chunk):
            return None if not self.buf else self._take()
        self.emitted += 1
        return chunk
