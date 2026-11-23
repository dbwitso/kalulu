"""Small, dependency-free fuzzy matcher tuned for launcher queries.

Returns a score (higher is better) or None when there is no match.
Order of preference: exact > prefix > acronym ("vsc" -> Visual Studio Code)
> word-start substring > substring > scattered subsequence.
"""
from __future__ import annotations

import re
import unicodedata

_WORD_SPLIT = re.compile(r"[\s\-_./:]+|(?<=[a-z])(?=[A-Z])")


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _words(text: str) -> list[str]:
    return [w for w in _WORD_SPLIT.split(text) if w]


def score(query: str, text: str) -> float | None:
    if not query:
        return 0.0
    if not text:
        return None
    q = normalize(query.strip())
    raw = text
    t = normalize(text)
    if not q:
        return 0.0

    if t == q:
        return 1000.0
    if t.startswith(q):
        return 900.0 - min(len(t) - len(q), 80)

    words = [normalize(w) for w in _words(raw)]
    initials = "".join(w[0] for w in words if w)
    if len(q) >= 2 and initials.startswith(q):
        return 820.0 - (len(initials) - len(q)) * 5

    # word-start substring ("code" in "visual studio code")
    for i, w in enumerate(words):
        if w.startswith(q):
            return 760.0 - i * 10 - min(len(w) - len(q), 40)

    # multi-word query where every token prefixes some word ("fire priv")
    tokens = q.split()
    if len(tokens) > 1 and all(any(w.startswith(tok) for w in words) for tok in tokens):
        return 720.0

    idx = t.find(q)
    if idx >= 0:
        return 620.0 - min(idx, 60)

    return _subsequence(q.replace(" ", ""), t)


def _subsequence(q: str, t: str) -> float | None:
    if len(q) < 2:
        return None
    pos = -1
    total = 0.0
    prev = -2
    for ch in q:
        pos = t.find(ch, pos + 1)
        if pos < 0:
            return None
        bonus = 0.0
        if pos == prev + 1:
            bonus += 12            # consecutive
        if pos == 0 or not t[pos - 1].isalnum():
            bonus += 10            # word start
        gap = pos - prev - 1 if prev >= 0 else pos
        total += 10 + bonus - min(gap, 12) * 1.5
        prev = pos
    # normalize so subsequence never beats a real substring
    return max(1.0, min(480.0, 200.0 + total * 3 / max(len(q), 1)))


def best(query: str, *texts: str, weights: tuple[float, ...] = ()) -> float | None:
    """Best score across several fields with optional per-field weights."""
    top = None
    for i, txt in enumerate(texts):
        s = score(query, txt)
        if s is None:
            continue
        w = weights[i] if i < len(weights) else 1.0
        s *= w
        if top is None or s > top:
            top = s
    return top
