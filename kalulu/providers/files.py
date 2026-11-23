"""File search ("f name") and path browsing ("/" or "~/")."""
from __future__ import annotations

import mimetypes
import os
import shutil
import time
from pathlib import Path

from ..core import fuzzy
from ..core.models import Action, Result
from ..core.system import runner
from .base import Provider

_RECENT_TTL = 15.0
_recent: dict[tuple, tuple[float, list]] = {}

_SKIP = {"node_modules", "__pycache__", ".git", ".cache", "venv", ".venv", "snap", ".local", ".npm",
         ".cargo", ".rustup", "site-packages", ".var"}


def _icon_for(p: Path) -> str:
    if p.is_dir():
        return "folder"
    mt, _ = mimetypes.guess_type(p.name)
    return f"mime:{mt}" if mt else "text-x-generic"    # resolved to the theme's file-type icon in the UI


def _pretty(p: Path) -> str:
    home = str(Path.home())
    s = str(p)
    return "~" + s[len(home):] if s.startswith(home) else s


def file_result(p: Path, score: float, in_content: bool = False) -> Result:
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
    sub = _pretty(p.parent) + ("  ·  matches inside" if in_content else "")
    return Result(id=f"file:{path}", title=p.name or path, subtitle=sub,
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
        out = []
        for p, in_content in self._search(term):
            s = fuzzy.score(term, p.name)
            out.append(file_result(p, (s or 150) + (0 if in_content else 50), in_content))
        return out[:40]

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

    def _search(self, term: str, walk: bool = True, budget: float = 0.6) -> list[tuple[Path, bool]]:
        from . import localindex
        roots = self._roots()
        hits = [(Path(p), c) for p, c in (localindex.search(term) or []) if Path(p).exists()]
        if len(hits) >= 15:
            return hits
        # the index only covers what GNOME indexes (XDG folders by default): top up from fd/plocate
        seen = {p for p, _ in hits}
        hits += [(p, False) for p in self._search_tools(term, roots, walk, budget) if p not in seen]
        return hits

    def _search_tools(self, term: str, roots: list[str], walk: bool, budget: float = 0.6) -> list[Path]:
        key = (term.lower(), tuple(roots))
        hit = _recent.get(key)
        if hit and time.monotonic() - hit[0] < _RECENT_TTL:
            return hit[1]                      # backspacing / retyping: no new scan
        fd = shutil.which("fd") or shutil.which("fdfind")
        found: list[Path] | None = None
        if fd:
            # 2 threads, bounded depth, the usual junk excluded, idle priority, and a newer keystroke
            # cancels this scan instead of piling another one on top.
            depth = str(int(self.ctx.config.get("files", {}).get("max_depth", 6)) + 2)
            excludes = [x for d in sorted(_SKIP) for x in ("--exclude", d)]
            out = runner.run(f"fd:{self.name}", [fd, "-i", "--threads", "2", "--max-depth", depth,
                                                 "--max-results", "60", *excludes, "--", term, *roots])
            if out is None:
                return []                      # superseded by a newer query
            found = [Path(l) for l in out.splitlines() if l]
        elif shutil.which("plocate") or shutil.which("locate"):
            loc = shutil.which("plocate") or shutil.which("locate")
            out = runner.run(f"locate:{self.name}", [loc, "-i", "-l", "200", "--basename", term])
            if out is None:
                return []
            found = [Path(l) for l in out.splitlines() if l and any(l.startswith(r) for r in roots)][:60] or None
        if found is None:
            found = self._walk(term, roots, budget) if walk else []
        _recent[key] = (time.monotonic(), found)
        if len(_recent) > 64:
            for k in sorted(_recent, key=lambda k: _recent[k][0])[:32]:
                _recent.pop(k, None)
        return found

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


class BlendedFilesProvider(FilesProvider):
    """Top file matches mixed into normal search (no prefix needed): index, then fd/plocate, then a short walk."""

    name = "files-blended"
    prefixes = ()
    min_len = 3
    delay_ms = 180
    weight = -120.0       # apps and commands stay above files

    def items(self):
        return []

    def query(self, text: str) -> list[Result]:
        t = text.strip()
        if t.startswith(("/", "~", ">", ":", "?", "f ")) or len(t) < 3:
            return []
        out = []
        for p, in_content in self._search(t, walk=True, budget=0.25)[:30]:
            s = fuzzy.score(t, p.name)
            if s is None and not in_content:
                continue
            out.append(file_result(p, (s or 300) - (80 if in_content else 0), in_content))
        out.sort(key=lambda r: -r.score)
        return out[:6]
