"""File search ("f name") and path browsing ("/" or "~/")."""
from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
import time
from pathlib import Path

from ..core import fuzzy
from ..core.models import Action, Result
from .base import Provider

_SKIP = {"node_modules", "__pycache__", ".git", ".cache", "venv", ".venv", "snap", ".local", ".npm",
         ".cargo", ".rustup", "site-packages", ".var"}


def _icon_for(p: Path) -> str:
    if p.is_dir():
        return "folder"
    mt, _ = mimetypes.guess_type(p.name)
    if not mt:
        return "text-x-generic"
    major = mt.split("/")[0]
    return {
        "image": "image-x-generic", "video": "video-x-generic", "audio": "audio-x-generic",
        "text": "text-x-generic",
    }.get(major, "application-x-generic") if mt != "application/pdf" else "application-pdf"


def _pretty(p: Path) -> str:
    home = str(Path.home())
    s = str(p)
    return "~" + s[len(home):] if s.startswith(home) else s


def file_result(p: Path, score: float) -> Result:
    path = str(p)
    parent = str(p.parent)
    actions = [
        Action("Open", "open_uri", path),
        Action("Show in Files", "reveal", path, shortcut="Ctrl+↵"),
        Action("Copy Path", "copy", path),
        Action("Open Terminal Here", "terminal_here", path if p.is_dir() else parent),
    ]
    if p.is_dir():
        actions.insert(1, Action("Browse in Kalulu", "complete", _pretty(p).rstrip("/") + "/", close=False))
    return Result(id=f"file:{path}", title=p.name or path, subtitle=_pretty(p.parent),
                  icon=_icon_for(p), category="Folder" if p.is_dir() else "File",
                  actions=actions, score=score)


class FilesProvider(Provider):
    name = "files"
    slow = True
    prefixes = ("f ", "/", "~")

    def query(self, text: str) -> list[Result]:
        if text.startswith(("/", "~")):
            return self._browse(text)
        term = text[2:].strip()
        if len(term) < 2:
            return []
        return [file_result(p, fuzzy.score(term, p.name) or 1) for p in self._search(term)][:40]

    # -- path browsing -------------------------------------------------
    def _browse(self, text: str) -> list[Result]:
        expanded = os.path.expanduser(text)
        if expanded.endswith("/"):
            base, partial = Path(expanded), ""
        else:
            base, partial = Path(expanded).parent, Path(expanded).name
        if not base.is_dir():
            return []
        out = []
        try:
            entries = list(os.scandir(base))
        except OSError:
            return []
        for e in entries:
            if e.name.startswith(".") and not partial.startswith("."):
                continue
            s = fuzzy.score(partial, e.name) if partial else 100.0
            if s is None:
                continue
            p = Path(e.path)
            r = file_result(p, s + (20 if e.is_dir() else 0))
            if e.is_dir():
                # ↵ on a folder in browse mode drills into it; Ctrl+↵ opens it
                r.actions.insert(0, Action("Browse", "complete", _pretty(p) + "/", close=False))
            out.append(r)
        out.sort(key=lambda r: (-r.score, r.title.lower()))
        return out[:60]

    # -- search ----------------------------------------------------------
    def _roots(self) -> list[str]:
        roots = self.ctx.config.get("files", {}).get("roots", ["~"])
        return [os.path.expanduser(r) for r in roots]

    def _search(self, term: str) -> list[Path]:
        roots = self._roots()
        fd = shutil.which("fd") or shutil.which("fdfind")
        try:
            if fd:
                out = subprocess.run([fd, "-i", "--max-results", "60", "--", term, *roots],
                                     capture_output=True, text=True, timeout=3).stdout
                return [Path(l) for l in out.splitlines() if l]
            if shutil.which("plocate") or shutil.which("locate"):
                loc = shutil.which("plocate") or shutil.which("locate")
                out = subprocess.run([loc, "-i", "-l", "200", "--basename", term],
                                     capture_output=True, text=True, timeout=3).stdout
                paths = [Path(l) for l in out.splitlines() if l and any(l.startswith(r) for r in roots)]
                if paths:
                    return paths[:60]
        except (subprocess.SubprocessError, OSError):
            pass
        return self._walk(term, roots)

    def _walk(self, term: str, roots: list[str], budget: float = 0.6) -> list[Path]:
        deadline = time.monotonic() + budget
        max_depth = int(self.ctx.config.get("files", {}).get("max_depth", 6))
        t = term.lower()
        found: list[Path] = []
        for root in roots:
            base_depth = root.rstrip("/").count("/")
            for dirpath, dirnames, filenames in os.walk(root):
                if time.monotonic() > deadline or len(found) >= 60:
                    return found
                depth = dirpath.count("/") - base_depth
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP] \
                    if depth < max_depth else []
                for n in dirnames + filenames:
                    if t in n.lower():
                        found.append(Path(dirpath) / n)
        return found
