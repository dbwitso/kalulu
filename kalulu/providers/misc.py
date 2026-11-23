"""Shell commands (">") and emoji picker (":")."""
from __future__ import annotations

import unicodedata
from functools import lru_cache

from ..core import fuzzy
from ..core.models import Action, Result
from .base import Provider


class ShellProvider(Provider):
    name = "shell"
    prefixes = (">",)

    def query(self, text: str) -> list[Result]:
        cmd = text[1:].strip()
        if not cmd:
            return [Result(id="shell:hint", title="Run a shell command", subtitle="Type a command after >",
                           icon="utilities-terminal-symbolic", category="Shell", learn=False, score=1000)]
        return [Result(
            id=f"shell:{cmd}", title=cmd, subtitle="Run in terminal", icon="utilities-terminal-symbolic",
            category="Shell", score=1000, learn=False,
            actions=[
                Action("Run in Terminal", "terminal", cmd),
                Action("Run and Show Output", "script", {"shell": cmd, "mode": "fullOutput"},
                       shortcut="Ctrl+↵", close=False),
                Action("Run in Background", "script", {"shell": cmd, "mode": "silent"}),
                Action("Copy Command", "copy", cmd),
            ])]


_RANGES = [(0x1F300, 0x1F5FF), (0x1F600, 0x1F64F), (0x1F680, 0x1F6FF), (0x1F900, 0x1F9FF),
           (0x1FA70, 0x1FAFF), (0x2600, 0x26FF), (0x2700, 0x27BF), (0x1F1E6, 0x1F1FF)]


@lru_cache(maxsize=1)
def emoji_table() -> list[tuple[str, str]]:
    out = []
    for a, b in _RANGES:
        for cp in range(a, b + 1):
            ch = chr(cp)
            name = unicodedata.name(ch, "")
            if name:
                out.append((ch, name.lower()))
    return out


class EmojiProvider(Provider):
    name = "emoji"
    prefixes = (":",)

    def query(self, text: str) -> list[Result]:
        term = text[1:].strip()
        if not term:
            return [Result(id="emoji:hint", title="Search emoji", subtitle="e.g.  :fire   :thumbs up",
                           icon="face-smile-symbolic", category="Emoji", learn=False, score=1000)]
        res = []
        for ch, name in emoji_table():
            s = fuzzy.score(term, name)
            if s is None or s < 500:
                continue
            res.append(Result(
                id=f"emoji:{ord(ch):x}", title=f"{ch}  {name.title()}", subtitle="Copy to clipboard",
                icon="face-smile-symbolic", category="Emoji", score=s,
                actions=[Action("Copy Emoji", "copy", ch), Action("Copy Name", "copy", name)]))
        res.sort(key=lambda r: -r.score)
        return res[:40]
