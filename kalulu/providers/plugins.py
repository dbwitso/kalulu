"""Python plugins: ~/.config/kalulu/plugins/<name>.py

A plugin is a module with:

    NAME = "My plugin"
    PREFIX = "np "          # optional: only run when the query starts with this
    SLOW = False            # optional: True runs off the UI thread (network calls etc.)

    def search(query: str, api) -> list:
        return [api.Result(id="x", title="Hello", actions=[api.Action("Copy", "copy", "hi")])]

`api` exposes Result, Action, fuzzy (matcher), config (dict), notify(title, body).
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import types

from ..core import fuzzy, paths
from ..core.models import Action, Result
from ..core.system import notify
from .base import Provider

log = logging.getLogger("kalulu.plugins")


class PluginProvider(Provider):
    def __init__(self, ctx, module):
        super().__init__(ctx)
        self.module = module
        self.name = f"plugin:{getattr(module, 'NAME', module.__name__)}"
        pre = getattr(module, "PREFIX", None)
        self.prefixes = (pre,) if pre else ()
        self.slow = bool(getattr(module, "SLOW", False))
        self.weight = float(getattr(module, "WEIGHT", 25.0))
        self.api = types.SimpleNamespace(Result=Result, Action=Action, fuzzy=fuzzy,
                                         config=ctx.config, notify=notify)

    def query(self, text: str) -> list[Result]:
        q = text[len(self.prefixes[0]):] if self.prefixes else text
        try:
            res = self.module.search(q, self.api) or []
            return [r for r in res if isinstance(r, Result)]
        except Exception:  # a broken plugin must never break the launcher
            log.exception("plugin %s failed", self.name)
            return []


def load_plugins(ctx) -> list[PluginProvider]:
    out = []
    if not paths.PLUGINS_DIR.is_dir():
        return out
    for p in sorted(paths.PLUGINS_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue
        mod_name = f"kalulu_plugin_{p.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, p)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            if hasattr(mod, "search"):
                out.append(PluginProvider(ctx, mod))
        except Exception:
            log.exception("could not load plugin %s", p)
    return out
