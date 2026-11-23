"""Web search fallback, quicklinks and URL detection."""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ..core import fuzzy
from ..core.models import Action, Result
from .base import Provider

_URLISH = re.compile(r"^(https?://\S+|(?:[\w-]+\.)+[a-z]{2,}(?:[/:?#]\S*)?)$", re.I)


class QuicklinksProvider(Provider):
    name = "quicklinks"
    weight = 30.0

    def query(self, text: str) -> list[Result]:
        links = self.ctx.config.get("quicklinks", [])
        t = text.strip()
        out = []
        head, _, rest = t.partition(" ")
        for ql in links:
            kw = str(ql.get("keyword", "")).lower()
            name = ql.get("name", kw)
            url = ql.get("url", "")
            icon = ql.get("icon", "web-browser-symbolic")
            if not kw or not url:
                continue
            if head.lower() == kw and rest:
                out.append(Result(
                    id=f"ql:{kw}", title=f"{name}: {rest}", subtitle=url.split("/")[2] if "//" in url else url,
                    icon=icon, category="Quicklink", score=1500,
                    actions=[Action("Open", "open_uri", url.replace("{query}", quote_plus(rest))),
                             Action("Copy URL", "copy", url.replace("{query}", quote_plus(rest)))]))
            else:
                s = fuzzy.best(t, name, kw, weights=(1.0, 1.1))
                if s is not None and t:
                    out.append(Result(
                        id=f"ql:{kw}", title=name, subtitle=f"Type “{kw} ” followed by your search",
                        icon=icon, category="Quicklink", score=s, learn=False,
                        actions=[Action("Use Quicklink", "complete", f"{kw} ", close=False)]))
        return out


class WebProvider(Provider):
    name = "web"
    weight = 0.0

    def query(self, text: str) -> list[Result]:
        t = text.strip()
        if not t or t.startswith((">", ":", "/", "~")):
            return []
        out = []
        if _URLISH.match(t) and " " not in t:
            url = t if "://" in t else f"https://{t}"
            out.append(Result(id="web:url", title=f"Open {url}", subtitle="Open in browser",
                              icon="web-browser-symbolic", category="Web", score=1400, learn=False,
                              actions=[Action("Open URL", "open_uri", url), Action("Copy URL", "copy", url)]))
        w = self.ctx.config.get("web", {})
        name = w.get("search_name", "Google")
        url = w.get("search_url", "https://www.google.com/search?q={query}").replace("{query}", quote_plus(t))
        out.append(Result(id="web:search", title=f"Search {name} for “{t}”", subtitle="Web search",
                          icon="system-search-symbolic", category="Web", score=-1000, learn=False,
                          actions=[Action("Search", "open_uri", url), Action("Copy Search URL", "copy", url)]))
        return out
