"""Clipboard history kept on disk (SQLite), so it survives logouts, reboots and power cuts.

GTK-free so it can be unit tested. Thread-safe: the UI thread adds entries, worker threads
run pin/delete callbacks.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths

MAX_CHARS = 200_000          # bigger copies (whole log files, base64 blobs) are not kept

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id        INTEGER PRIMARY KEY,
    hash      TEXT UNIQUE NOT NULL,
    text      TEXT NOT NULL,
    created   REAL NOT NULL,
    last_used REAL NOT NULL,
    uses      INTEGER NOT NULL DEFAULT 1,
    pinned    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS clips_recent ON clips (pinned DESC, last_used DESC);
"""


@dataclass
class Clip:
    id: int
    text: str
    created: float
    last_used: float
    uses: int
    pinned: bool


class ClipStore:
    def __init__(self, path: Path | None = None, max_items: int = 1000, keep_days: int = 0):
        self.path = Path(path or paths.CLIPBOARD_DB)
        self.max_items = max_items
        self.keep_days = keep_days
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Clipboard history can hold passwords and tokens: owner-only from the first byte.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")      # an entry is on disk before add() returns
        self.db.executescript(_SCHEMA)
        self.prune()

    # ------------------------------------------------------------------ write
    def add(self, text: str) -> bool:
        """Record a copy. Re-copying something moves it back to the top. Returns True if stored."""
        if not text or not text.strip() or len(text) > MAX_CHARS:
            return False
        h = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
        now = time.time()
        with self.lock:
            self.db.execute(
                "INSERT INTO clips (hash, text, created, last_used) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(hash) DO UPDATE SET last_used = excluded.last_used, uses = uses + 1",
                (h, text, now, now))
        self.prune()
        return True

    def set_pinned(self, clip_id: int, pinned: bool) -> None:
        with self.lock:
            self.db.execute("UPDATE clips SET pinned = ? WHERE id = ?", (int(pinned), clip_id))

    def delete(self, clip_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM clips WHERE id = ?", (clip_id,))

    def clear(self, keep_pinned: bool = True) -> int:
        with self.lock:
            cur = self.db.execute("DELETE FROM clips" + (" WHERE pinned = 0" if keep_pinned else ""))
            n = cur.rowcount
        with self.lock:
            self.db.execute("VACUUM")         # don't leave cleared text lying around in free pages
        return n

    def prune(self) -> None:
        with self.lock:
            if self.keep_days and self.keep_days > 0:
                cutoff = time.time() - self.keep_days * 86400
                self.db.execute("DELETE FROM clips WHERE pinned = 0 AND last_used < ?", (cutoff,))
            if self.max_items and self.max_items > 0:
                self.db.execute(
                    "DELETE FROM clips WHERE pinned = 0 AND id NOT IN "
                    "(SELECT id FROM clips WHERE pinned = 0 ORDER BY last_used DESC LIMIT ?)",
                    (self.max_items,))

    # ------------------------------------------------------------------ read
    def all(self, limit: int = 0) -> list[Clip]:
        sql = "SELECT id, text, created, last_used, uses, pinned FROM clips ORDER BY pinned DESC, last_used DESC"
        args: tuple = ()
        if limit:
            sql += " LIMIT ?"
            args = (limit,)
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        return [Clip(r[0], r[1], r[2], r[3], r[4], bool(r[5])) for r in rows]

    def get(self, clip_id: int) -> Clip | None:
        with self.lock:
            r = self.db.execute("SELECT id, text, created, last_used, uses, pinned FROM clips WHERE id = ?",
                                (clip_id,)).fetchone()
        return Clip(r[0], r[1], r[2], r[3], r[4], bool(r[5])) if r else None

    def __len__(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM clips").fetchone()[0]

    def close(self) -> None:
        with self.lock:
            self.db.close()
