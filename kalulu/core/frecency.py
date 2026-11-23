"""Frecency + learned picks.

Two signals:
  * usage: how often / how recently a result was used at all
  * picks: which result you chose for a given query ("c" -> Chrome),
    so the launcher learns your shorthand the way Raycast does.
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

_DAY = 86400


class Frecency:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.usage: dict[str, dict] = {}
        self.picks: dict[str, dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            self.usage = data.get("usage", {})
            self.picks = data.get("picks", {})
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"usage": self.usage, "picks": self.picks}))
            tmp.replace(self.path)

    def record(self, result_id: str, query: str = "") -> None:
        now = time.time()
        u = self.usage.setdefault(result_id, {"count": 0, "last": now})
        u["count"] += 1
        u["last"] = now
        q = query.strip().lower()
        if q:
            # learn every prefix of the query, so "fi" and "fir" both remember Firefox
            for i in range(1, min(len(q), 12) + 1):
                bucket = self.picks.setdefault(q[:i], {})
                bucket[result_id] = bucket.get(result_id, 0) + 1
        self._trim()
        self.save()

    def _trim(self) -> None:
        if len(self.picks) > 3000:
            for k in sorted(self.picks, key=lambda k: sum(self.picks[k].values()))[:1000]:
                del self.picks[k]

    def boost(self, result_id: str, query: str = "") -> float:
        b = 0.0
        u = self.usage.get(result_id)
        if u:
            age = time.time() - u.get("last", 0)
            recency = 1.0 if age < _DAY else 0.8 if age < 7 * _DAY else 0.6 if age < 30 * _DAY else 0.4
            b += min(150.0, 40.0 * math.log2(1 + u.get("count", 0)) * recency)
        q = query.strip().lower()
        if q and q in self.picks:
            bucket = self.picks[q]
            n = bucket.get(result_id, 0)
            if n:
                share = n / max(1, sum(bucket.values()))
                b += 120.0 + 180.0 * share
        return b

    def recent(self, limit: int = 8) -> list[str]:
        items = sorted(self.usage.items(), key=lambda kv: kv[1].get("last", 0), reverse=True)
        return [k for k, _ in items[:limit]]
