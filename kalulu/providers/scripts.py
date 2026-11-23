"""Script Commands: drop an executable into ~/.config/kalulu/scripts and it becomes a command.

Metadata lives in comments at the top of the file:

    #!/usr/bin/env bash
    # @kalulu.title Restart NetBird
    # @kalulu.mode compact            # silent | compact | fullOutput | terminal
    # @kalulu.icon network-vpn-symbolic   (icon name, file path, or emoji)
    # @kalulu.subtitle Networking
    # @kalulu.keywords vpn wireguard
    # @kalulu.argument1 {"placeholder": "hostname"}
    # @kalulu.needsConfirmation true

The `@raycast.` prefix is accepted too, so many existing script commands run unchanged
(macOS-only ones obviously won't).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..core import paths
from ..core.models import Action, Result
from .base import Provider

_META = re.compile(r"^\s*(?:#|//|--|;)\s*@(?:kalulu|raycast)\.(\w+)\s+(.*?)\s*$")


def parse_script(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = [next(f, "") for _ in range(40)]
    except OSError:
        return None
    meta: dict = {"args": []}
    for line in head:
        m = _META.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if key.startswith("argument"):
            try:
                meta["args"].append(json.loads(val))
            except ValueError:
                meta["args"].append({"placeholder": val})
        else:
            meta[key] = val
    if "title" not in meta:
        return None
    meta["path"] = str(path)
    return meta


def _is_emoji(s: str) -> bool:
    return bool(s) and len(s) <= 4 and any(ord(c) > 0x2100 for c in s)


class ScriptsProvider(Provider):
    name = "scripts"
    weight = 35.0

    def __init__(self, ctx):
        super().__init__(ctx)
        self.scripts: list[dict] = []
        self.refresh()

    def refresh(self) -> None:
        out = []
        d = paths.SCRIPTS_DIR
        if d.is_dir():
            for p in sorted(d.rglob("*")):
                if p.is_file() and os.access(p, os.X_OK) and not p.name.startswith("."):
                    m = parse_script(p)
                    if m:
                        out.append(m)
        self.scripts = out

    def _icon(self, m: dict) -> tuple[str, str]:
        icon = m.get("icon", "")
        if _is_emoji(icon):
            return "utilities-terminal-symbolic", icon + "  "
        if icon and not icon.startswith(("http", "/")) and "." in icon and not icon.endswith("-symbolic"):
            p = Path(m["path"]).parent / icon
            return (str(p) if p.exists() else "utilities-terminal-symbolic"), ""
        return icon or "utilities-terminal-symbolic", ""

    def _result(self, m: dict, args: list[str] | None, score: float = 0.0) -> Result:
        icon, prefix = self._icon(m)
        mode = m.get("mode", "compact")
        confirm = str(m.get("needsConfirmation", "false")).lower() == "true"
        needs_args = bool(m["args"]) and args is None
        sid = f"script:{m['path']}"
        if needs_args:
            hint = " ".join(f"‹{a.get('placeholder', 'arg')}›" for a in m["args"])
            return Result(id=sid, title=f"{prefix}{m['title']}", subtitle=f"{m.get('subtitle') or m.get('packageName') or 'Script'}  ·  {hint}",
                          icon=icon, category="Script", score=score, keywords=m.get("keywords", "").split(),
                          actions=[Action("Enter Arguments", "complete", m["title"] + " ", close=False),
                                   Action("Edit Script", "open_uri", m["path"]),
                                   Action("Show in Files", "reveal", m["path"])])
        run = Action("Run Script", "script", {"path": m["path"], "args": args or [], "mode": mode,
                                              "title": m["title"]},
                     confirm=confirm, close=mode != "fullOutput")
        title = f"{prefix}{m['title']}" + (f"  {' '.join(args)}" if args else "")
        return Result(id=sid, title=title, subtitle=m.get("subtitle") or m.get("packageName") or m.get("description") or "Script",
                      icon=icon, category="Script", score=score, keywords=m.get("keywords", "").split(),
                      actions=[run,
                               Action("Run in Terminal", "script", {**run.payload, "mode": "terminal"}),
                               Action("Edit Script", "open_uri", m["path"]),
                               Action("Show in Files", "reveal", m["path"])])

    def items(self) -> list[Result]:
        return [self._result(m, None) for m in self.scripts]

    def query(self, text: str) -> list[Result]:
        """Argument mode: '<script title> <args...>'."""
        low = text.lower()
        out = []
        for m in self.scripts:
            if not m["args"]:
                continue
            t = m["title"].lower() + " "
            if low.startswith(t):
                rest = text[len(t):]
                n = len(m["args"])
                parts = [p.strip() for p in rest.split("|")] if n > 1 else [rest.strip()]
                parts = (parts + [""] * n)[:n]
                out.append(self._result(m, parts, score=1800))
        return out
