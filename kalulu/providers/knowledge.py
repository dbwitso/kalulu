"""Answers inside the launcher — no browser.

  * blended: top Wikipedia matches appear under your local results as you type
  * "? <anything>": focused mode — instant answer (DuckDuckGo) + Wikipedia results
  * "define <word>": dictionary definitions

↵ reads the summary inside Kalulu; Ctrl+↵ opens the page in the browser if you want more.
Turn off with [web] inline_answers = false.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from urllib.parse import quote, urlencode

from .. import __version__
from ..core import fuzzy
from ..core.models import Action, Result
from .base import Provider

log = logging.getLogger("kalulu.knowledge")
UA = f"Kalulu/{__version__} (https://github.com/dbwitso/kalulu; desktop launcher)"


class _Cache:
    def __init__(self, size=256):
        self.d: OrderedDict = OrderedDict()
        self.size = size
        self.lock = threading.Lock()

    def get(self, k):
        with self.lock:
            if k in self.d:
                self.d.move_to_end(k)
                return self.d[k]
        return None

    def put(self, k, v):
        with self.lock:
            self.d[k] = v
            self.d.move_to_end(k)
            while len(self.d) > self.size:
                self.d.popitem(last=False)


_cache = _Cache()


def http_json(url: str, timeout: float = 3.5):
    hit = _cache.get(url)
    if hit is not None:
        return hit
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        log.debug("fetch failed %s: %s", url, e)
        return None
    _cache.put(url, data)
    return data


def _strip(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html or "").replace("&quot;", '"').replace("&amp;", "&").strip()


# ---------------------------------------------------------------- wikipedia
def wiki_search(lang: str, q: str, limit: int = 5) -> list[dict]:
    data = http_json(f"https://{lang}.wikipedia.org/w/rest.php/v1/search/title?"
                     + urlencode({"q": q, "limit": limit}))
    return (data or {}).get("pages", []) if isinstance(data, dict) else []


def wiki_summary(lang: str, key: str) -> str:
    data = http_json(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(key, safe='')}")
    if not data:
        return {"title": key.replace("_", " "), "body": "Couldn't load the article (offline?)."}
    return {"title": data.get("title", key), "subtitle": data.get("description", ""),
            "body": data.get("extract", ""), "source": f"Source: Wikipedia ({lang}) · CC BY-SA"}


def _wiki_results(lang: str, q: str, limit: int, base: float, ctx_weight: float) -> list[Result]:
    out = []
    for i, page in enumerate(wiki_search(lang, q, limit)):
        key, title = page.get("key"), page.get("title", "")
        if not key:
            continue
        desc = page.get("description") or _strip(page.get("excerpt", ""))
        url = f"https://{lang}.wikipedia.org/wiki/{quote(key)}"
        out.append(Result(
            id=f"wiki:{lang}:{key}", title=title, subtitle=desc or "Wikipedia",
            icon="accessories-dictionary-symbolic", category="Wikipedia",
            score=base - i * 5 + (fuzzy.score(q, title) or 0) * ctx_weight, learn=False,
            actions=[
                Action("Read Here", "callback", lambda l=lang, k=key: ("detail", wiki_summary(l, k)), close=False),
                Action("Open in Browser", "open_uri", url, shortcut="Ctrl+↵"),
                Action("Copy Link", "copy", url),
            ]))
    return out


# ---------------------------------------------------------------- duckduckgo instant answers
def instant_answer(q: str) -> Result | None:
    data = http_json("https://api.duckduckgo.com/?" + urlencode(
        {"q": q, "format": "json", "no_html": 1, "skip_disambig": 1, "t": "kalulu"}))
    if not isinstance(data, dict):
        return None
    answer = _strip(data.get("Answer") or "")
    abstract = data.get("AbstractText") or ""
    definition = data.get("Definition") or ""
    body = answer or abstract or definition
    if not body:
        return None
    source = data.get("AbstractSource") or data.get("DefinitionSource") or data.get("AnswerType") or "DuckDuckGo"
    heading = data.get("Heading") or q
    url = data.get("AbstractURL") or data.get("DefinitionURL") or f"https://duckduckgo.com/?q={quote(q)}"
    related = [t.get("Text") for t in data.get("RelatedTopics", []) if isinstance(t, dict) and t.get("Text")][:6]
    text = body + ("\n\nRelated:\n" + "\n".join(f"• {r}" for r in related) if related else "")
    detail = {"title": heading, "subtitle": data.get("AnswerType") or "", "body": text, "source": f"Source: {source}"}
    return Result(
        id=f"ddg:{q}", title=body if len(body) < 90 else body[:87] + "…", subtitle=f"{heading} · {source}",
        icon="dialog-information-symbolic", category="Answer", score=1700, learn=False,
        actions=[Action("Read Here", "detail", detail, close=False),
                 Action("Open Source", "open_uri", url, shortcut="Ctrl+↵"),
                 Action("Copy Answer", "copy", body)])


class KnowledgeProvider(Provider):
    name = "knowledge"
    slow = True
    delay_ms = 380          # wait for a typing pause before going to the network
    min_len = 3
    weight = -200.0         # below apps, commands and files in blended mode

    def _enabled(self) -> bool:
        return bool(self.ctx.config.get("web", {}).get("inline_answers", True))

    def _lang(self) -> str:
        return self.ctx.config.get("web", {}).get("language", "en")

    def query(self, text: str) -> list[Result]:
        t = text.strip()
        if not self._enabled():
            return []
        if t.startswith("?"):
            q = t[1:].strip()
            if len(q) < 2:
                return [Result(id="ask:hint", title="Ask anything", subtitle="Answers and Wikipedia, shown right here",
                               icon="dialog-question-symbolic", category="Ask", learn=False, score=1500)]
            out = _wiki_results(self._lang(), q, 8, 1200, 0.2)
            ia = instant_answer(q)
            return ([ia] if ia else []) + out
        if t.startswith(("/", "~", ">", ":", "f ", "define ", "install ", "uninstall ", "remove ")):
            return []
        return _wiki_results(self._lang(), t, 2, 300, 0.3)


# ---------------------------------------------------------------- dictionary
class DefineProvider(Provider):
    name = "define"
    slow = True
    delay_ms = 250
    prefixes = ("define ",)

    def query(self, text: str) -> list[Result]:
        word = text[len("define "):].strip()
        if len(word) < 2 or " " in word:
            return []
        data = http_json(f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(word)}")
        if not isinstance(data, list) or not data:
            return [Result(id=f"define:{word}", title=f"No definition found for “{word}”", subtitle="Dictionary",
                           icon="accessories-dictionary-symbolic", category="Define", learn=False, score=1500)]
        entry = data[0]
        phon = entry.get("phonetic") or next((p.get("text") for p in entry.get("phonetics", []) if p.get("text")), "")
        lines = []
        out = []
        for m in entry.get("meanings", []):
            pos = m.get("partOfSpeech", "")
            lines.append(pos.upper())
            for i, d in enumerate(m.get("definitions", [])[:5], 1):
                lines.append(f"  {i}. {d.get('definition', '')}")
                if d.get("example"):
                    lines.append(f"     “{d['example']}”")
            syn = m.get("synonyms", [])[:8]
            if syn:
                lines.append(f"  synonyms: {', '.join(syn)}")
            lines.append("")
        detail = {"title": entry.get("word", word), "subtitle": phon, "body": "\n".join(lines).strip(),
                  "source": "Source: dictionaryapi.dev (Wiktionary)"}
        for m in entry.get("meanings", [])[:4]:
            d = (m.get("definitions") or [{}])[0].get("definition", "")
            out.append(Result(
                id=f"define:{word}:{m.get('partOfSpeech')}", title=d, subtitle=f"{word} · {m.get('partOfSpeech', '')}",
                icon="accessories-dictionary-symbolic", category="Define", learn=False, score=1500 - len(out),
                actions=[Action("Read All Definitions", "detail", detail, close=False),
                         Action("Copy Definition", "copy", d)]))
        return out
