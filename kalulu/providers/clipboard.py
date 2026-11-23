"""Clipboard history: type "clip " (or open it with Super+Shift+V)."""
from __future__ import annotations

import re
import time

from ..core import fuzzy
from ..core.models import Action, Result
from .base import Provider

PREFIX = "clip "
_URL = re.compile(r"^(https?|ftp)://\S+$", re.I)
_COLOR = re.compile(r"^(#[0-9a-f]{3,8}|rgba?\([\d\s,.%]+\))$", re.I)
_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")


def kind_of(text: str) -> tuple[str, str]:
    """(label, icon) for a clipboard entry."""
    t = text.strip()
    one_line = "\n" not in t
    if one_line and _URL.match(t):
        return "Link", "web-browser-symbolic"
    if one_line and _EMAIL.match(t):
        return "Email", "mail-unread-symbolic"
    if one_line and _COLOR.match(t):
        return "Colour", "applications-graphics-symbolic"
    if one_line and t.startswith(("/", "~/")) and " " not in t.strip("/"):
        return "Path", "folder-symbolic"
    lines = t.count("\n") + 1
    if lines > 1:
        return f"{lines} lines", "accessories-text-editor-symbolic"
    return f"{len(t)} chars", "edit-paste-symbolic"


def ago(ts: float, now: float | None = None) -> str:
    s = max(0, int((now or time.time()) - ts))
    if s < 60:
        return "just now"
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if s >= n:
            return f"{s // n} {unit} ago"
    return "just now"


def preview(text: str, width: int = 90) -> str:
    one = " ".join(text.split())
    return one if len(one) <= width else one[: width - 1] + "…"


class ClipboardProvider(Provider):
    name = "clipboard"
    prefixes = (PREFIX,)
    exclusive = True

    @property
    def store(self):
        return self.ctx.clips

    def query(self, text: str) -> list[Result]:
        if self.store is None:
            return [Result(id="clip:off", title="Clipboard history is turned off",
                           subtitle="Set enabled = true under [clipboard] in config.toml",
                           icon="edit-paste-symbolic", category="Clipboard", learn=False, score=1000)]
        term = text[len(PREFIX):].strip()
        clips = self.store.all()
        if not clips:
            return [Result(id="clip:empty", title="Nothing copied yet",
                           subtitle="Copy something and it will show up here, even after a restart",
                           icon="edit-paste-symbolic", category="Clipboard", learn=False, score=1000)]
        now = time.time()
        out = []
        for i, c in enumerate(clips):
            if term:
                low = term.lower()
                if low in c.text.lower():
                    s = 800.0 - i * 0.01
                else:
                    s = fuzzy.score(term, c.text[:400])
                    if s is None or s < 500:
                        continue
            else:
                s = 1000.0 - i * 0.01           # keep newest-first order
            out.append(self._result(c, s, now))
        return out[:200]

    def _result(self, c, score: float, now: float) -> Result:
        label, icon = kind_of(c.text)
        store, cid = self.store, c.id
        pinned = c.pinned

        def toggle_pin():
            store.set_pinned(cid, not pinned)
            return ("refresh", "Unpinned" if pinned else "Pinned to the top")

        def delete():
            store.delete(cid)
            return ("refresh", "Deleted from history")

        def clear():
            n = store.clear(keep_pinned=True)
            return ("refresh", f"Cleared {n} {'entry' if n == 1 else 'entries'} (pinned ones kept)")

        actions = [
            Action("Copy to Clipboard", "copy", c.text),
            Action("Show Full Text", "detail", c.text, shortcut="Ctrl+↵"),
        ]
        if label == "Link":
            actions.append(Action("Open Link", "open_uri", c.text.strip()))
        elif label == "Path":
            actions.append(Action("Open", "open_uri", c.text.strip()))
        actions += [
            Action("Unpin" if pinned else "Pin to Top", "callback", toggle_pin, close=False),
            Action("Delete Entry", "callback", delete, close=False),
            Action("Clear Clipboard History", "callback", clear, confirm=True, close=False),
        ]
        sub = f"{label}  ·  {ago(c.last_used, now)}" + (f"  ·  copied {c.uses}×" if c.uses > 1 else "")
        return Result(id=f"clip:{cid}", title=preview(c.text), subtitle=sub, icon=icon,
                      category="Pinned" if pinned else "Clipboard", actions=actions, score=score, learn=False)
