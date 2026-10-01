"""SQLite persistence for sessions, turns and the vocabulary notebook."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, scenario TEXT NOT NULL, level TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL, text TEXT NOT NULL, extra TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS vocab (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL UNIQUE, meaning TEXT NOT NULL DEFAULT '', example TEXT NOT NULL DEFAULT '', created REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock, self.db:
            return self.db.execute(sql, args)

    # sessions -----------------------------------------------------------
    def create_session(self, scenario: str, level: str) -> dict:
        sid = uuid.uuid4().hex[:12]
        now = time.time()
        self._exec("INSERT INTO sessions VALUES (?,?,?,?)", (sid, scenario, level, now))
        return {"id": sid, "scenario": scenario, "level": level, "created": now}

    def get_session(self, sid: str) -> dict | None:
        row = self._exec("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        return {**dict(row), "turns": self.turns(sid)}

    def list_sessions(self, limit: int = 50) -> list[dict]:
        rows = self._exec(
            """SELECT s.*, COUNT(t.id) AS n_turns,
                      (SELECT text FROM turns WHERE session_id=s.id AND role='user' ORDER BY id LIMIT 1) AS first_user
               FROM sessions s LEFT JOIN turns t ON t.session_id = s.id
               GROUP BY s.id ORDER BY s.created DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, sid: str) -> None:
        self._exec("DELETE FROM sessions WHERE id=?", (sid,))

    # turns --------------------------------------------------------------
    def add_turn(self, sid: str, role: str, text: str, extra: dict | None = None) -> dict:
        now = time.time()
        cur = self._exec(
            "INSERT INTO turns (session_id, role, text, extra, created) VALUES (?,?,?,?,?)",
            (sid, role, text, json.dumps(extra or {}, ensure_ascii=False), now),
        )
        return {"id": cur.lastrowid, "role": role, "text": text, "extra": extra or {}, "created": now}

    def update_turn_extra(self, turn_id: int, **fields) -> None:
        row = self._exec("SELECT extra FROM turns WHERE id=?", (turn_id,)).fetchone()
        if row:
            extra = {**json.loads(row["extra"]), **fields}
            self._exec("UPDATE turns SET extra=? WHERE id=?", (json.dumps(extra, ensure_ascii=False), turn_id))

    def get_turn(self, turn_id: int) -> dict | None:
        row = self._exec("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
        return {**dict(row), "extra": json.loads(row["extra"])} if row else None

    def turns(self, sid: str) -> list[dict]:
        rows = self._exec("SELECT * FROM turns WHERE session_id=? ORDER BY id", (sid,)).fetchall()
        return [{**dict(r), "extra": json.loads(r["extra"])} for r in rows]

    # vocab --------------------------------------------------------------
    def add_vocab(self, word: str, meaning: str = "", example: str = "") -> dict:
        self._exec(
            """INSERT INTO vocab (word, meaning, example, created) VALUES (?,?,?,?)
               ON CONFLICT(word) DO UPDATE SET
                 meaning = CASE WHEN excluded.meaning != '' THEN excluded.meaning ELSE vocab.meaning END,
                 example = CASE WHEN excluded.example != '' THEN excluded.example ELSE vocab.example END""",
            (word.strip(), meaning.strip(), example.strip(), time.time()),
        )
        return dict(self._exec("SELECT * FROM vocab WHERE word=?", (word.strip(),)).fetchone())

    def list_vocab(self) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT * FROM vocab ORDER BY created DESC").fetchall()]

    def delete_vocab(self, vid: int) -> None:
        self._exec("DELETE FROM vocab WHERE id=?", (vid,))
