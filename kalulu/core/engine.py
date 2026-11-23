"""Search engine: runs providers, ranks, dedupes. GTK-free so it can be unit tested."""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass

from . import config as config_mod
from . import fuzzy, paths
from .frecency import Frecency
from .models import Action, Result
from .system import notify, run_capture, spawn, terminal_argv

log = logging.getLogger("kalulu")

MIN_FUZZY = 260.0   # weaker scattered matches are dropped


@dataclass
class Context:
    config: dict
    frecency: Frecency
    config_error: str | None = None


class Engine:
    def __init__(self):
        self.ctx: Context | None = None
        self.providers = []
        self.reload()

    # ------------------------------------------------------------------
    def reload(self) -> None:
        from ..providers.apps import AppsProvider
        from ..providers.calculator import CalculatorProvider
        from ..providers.commands import KaluluCommandsProvider, SystemCommandsProvider
        from ..providers.files import FilesProvider
        from ..providers.misc import EmojiProvider, ShellProvider
        from ..providers.plugins import load_plugins
        from ..providers.scripts import ScriptsProvider
        from ..providers.web import QuicklinksProvider, WebProvider

        cfg, err = config_mod.load()
        if self.ctx is None:
            self.ctx = Context(cfg, Frecency(paths.FRECENCY_FILE), err)
        else:
            self.ctx.config, self.ctx.config_error = cfg, err
        if err:
            log.warning(err)
        c = self.ctx
        self.providers = [
            CalculatorProvider(c), AppsProvider(c), ScriptsProvider(c), SystemCommandsProvider(c),
            KaluluCommandsProvider(c), QuicklinksProvider(c), FilesProvider(c), ShellProvider(c),
            EmojiProvider(c), *load_plugins(c), WebProvider(c),
        ]

    def refresh(self) -> None:
        for p in self.providers:
            try:
                p.refresh()
            except Exception:
                log.exception("refresh failed for %s", p.name)

    @property
    def max_results(self) -> int:
        return int(self.ctx.config.get("general", {}).get("max_results", 9))

    # ------------------------------------------------------------------
    def _active(self, text: str):
        """Symbol prefixes (> : / ~) are exclusive modes; word prefixes ("kill ", "f ") blend in."""
        hit = [(p, x) for p in self.providers for x in p.prefixes if text.startswith(x)]
        exclusive = [p for p, x in hit if not x[0].isalnum()]
        if exclusive:
            return list(dict.fromkeys(exclusive))
        word = list(dict.fromkeys(p for p, _ in hit))
        return [p for p in self.providers if not p.prefixes] + word

    def _rank_static(self, p, text: str) -> list[Result]:
        out = []
        for r in p.items():
            s = fuzzy.best(text, r.title, *r.keywords, r.subtitle,
                           weights=(1.0,) + (0.85,) * len(r.keywords) + (0.4,))
            if s is None or s < MIN_FUZZY:
                continue
            out.append(_scored(r, s + p.weight + self.ctx.frecency.boost(r.id, text)))
        return out

    def _run_dynamic(self, p, text: str) -> list[Result]:
        try:
            res = p.query(text)
        except Exception:
            log.exception("provider %s failed", p.name)
            return []
        return [_scored(r, r.score + p.weight + (self.ctx.frecency.boost(r.id, text) if r.learn else 0))
                for r in res]

    def search(self, text: str, slow: bool = False) -> list[Result]:
        """Fast providers when slow=False; only slow providers when slow=True."""
        if not text.strip():
            return [] if slow else self.home()
        results: list[Result] = []
        for p in self._active(text):
            if p.slow != slow:
                continue
            if not p.prefixes:
                results += self._rank_static(p, text)
            results += self._run_dynamic(p, text)
        return dedupe_sort(results)

    def home(self) -> list[Result]:
        """Shown for an empty query: recently used, then a few handy commands."""
        index = {}
        for p in self.providers:
            if not p.prefixes:
                for r in p.items():
                    index.setdefault(r.id, r)
        out = [index[i] for i in self.ctx.frecency.recent(8) if i in index]
        for fallback in ("cmd:settings", "kalulu:config", "cmd:dark", "cmd:lock"):
            if len(out) >= self.max_results:
                break
            if fallback in index and index[fallback] not in out:
                out.append(index[fallback])
        return out

    # ------------------------------------------------------------------
    def record(self, result: Result, query: str) -> None:
        if result.learn:
            self.ctx.frecency.record(result.id, query)

    def terminal(self) -> str:
        return self.ctx.config.get("general", {}).get("terminal", "auto")

    def run_script(self, payload: dict) -> tuple[int, str]:
        """Blocking. Call from a worker thread."""
        if "shell" in payload:
            argv, cwd = ["sh", "-c", payload["shell"]], None
        else:
            argv, cwd = [payload["path"], *payload.get("args", [])], os.path.dirname(payload["path"])
        return run_capture(argv, timeout=120, cwd=cwd)

    def execute(self, action: Action) -> tuple[str, str] | None:
        """Run non-UI actions. Returns ("toast"|"detail", text) or None. May block for scripts."""
        k, pl = action.kind, action.payload
        if k == "run":
            spawn(pl)
        elif k == "terminal":
            spawn(terminal_argv(pl, self.terminal()))
        elif k == "terminal_here":
            spawn(terminal_argv("exec ${SHELL:-bash}", self.terminal(), hold=False), cwd=pl)
        elif k == "callback":
            msg = pl()
            return ("toast", msg) if msg else None
        elif k == "script":
            mode = pl.get("mode", "compact")
            if mode == "terminal":
                cmd = pl.get("shell") or shlex.join([pl["path"], *pl.get("args", [])])
                spawn(terminal_argv(cmd, self.terminal()))
                return None
            code, out = self.run_script(pl)
            title = pl.get("title") or pl.get("shell", "Command")
            last = out.strip().splitlines()[-1] if out.strip() else ("Done" if code == 0 else f"Exit {code}")
            if mode == "fullOutput":
                return ("detail", out or "(no output)")
            if mode == "silent":
                notify(title, last if code == 0 else f"Failed ({code}): {last}")
                return None
            return ("toast", last if code == 0 else f"Failed ({code}): {last}")
        elif k == "open_uri":
            spawn(["xdg-open", pl])
        else:
            log.warning("unhandled action kind %s", k)
        return None


def _scored(r: Result, s: float) -> Result:
    r = Result(**{**r.__dict__})   # shallow copy so cached items keep their base score
    r.score = s
    return r


def dedupe_sort(results: list[Result]) -> list[Result]:
    best: dict[str, Result] = {}
    for r in results:
        cur = best.get(r.id)
        if cur is None or r.score > cur.score:
            best[r.id] = r
    return sorted(best.values(), key=lambda r: -r.score)
