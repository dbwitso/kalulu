"""Query GNOME's file index (LocalSearch / Tracker) — the same index Activities search uses.

Matches file names *and* document contents. Instant even on huge home folders.
Works with LocalSearch 3.8+ (GNOME 47+, Ubuntu 25.04+) and Tracker 3.x (Ubuntu 24.04).
Returns None when no index is reachable, so callers can fall back to fd/plocate.
"""
from __future__ import annotations

import logging
import re
import threading
from urllib.parse import unquote, urlparse

log = logging.getLogger("kalulu.index")

_BUS_NAMES = ("org.freedesktop.LocalSearch3", "org.freedesktop.Tracker3.Miner.Files")

_QUERY = """
SELECT DISTINCT ?url ?inContent WHERE {
  ?file a nfo:FileDataObject ; nie:url ?url .
  {
    ?file fts:match ~match .
    BIND(false AS ?inContent)
  } UNION {
    ?doc nie:isStoredAs ?file ; fts:match ~match .
    BIND(true AS ?inContent)
  }
}
LIMIT 80
"""

_lock = threading.Lock()
_conn = None
_unavailable = False


def _connect():
    global _conn, _unavailable
    if _conn or _unavailable:
        return _conn
    import gi
    mods = []
    for ns in ("Tsparql", "Tracker"):
        try:
            gi.require_version(ns, "3.0")
            mods.append(__import__("gi.repository", fromlist=[ns]).__dict__[ns])
        except (ValueError, ImportError, KeyError):
            continue
    for mod in mods:
        for bus in _BUS_NAMES:
            try:
                conn = mod.SparqlConnection.bus_new(bus, None, None)
                cur = conn.query("ASK { ?s a nfo:FileDataObject }", None)
                cur.next(None)
                _conn = conn
                log.info("file index: %s via %s", bus, mod.__name__)
                return _conn
            except Exception as e:  # GLib.Error, not activatable, etc.
                log.debug("index %s/%s unavailable: %s", mod.__name__, bus, e)
    _unavailable = True
    if not mods:
        log.warning("GNOME file index unreachable: the Python binding is missing "
                    "(sudo apt install gir1.2-tsparql-3.0); falling back to fd/plocate")
    else:
        log.info("no GNOME file index available; falling back to fd/plocate")
    return None


def _fts_term(term: str) -> str:
    words = re.findall(r"\w+", term)
    return " ".join(w + "*" for w in words)


def search(term: str, limit: int = 40) -> list[tuple[str, bool]] | None:
    """[(path, matched_in_content)], or None if the index isn't available."""
    fts = _fts_term(term)
    if not fts:
        return []
    with _lock:
        conn = _connect()
        if conn is None:
            return None
        try:
            stmt = conn.query_statement(_QUERY, None)
            stmt.bind_string("match", fts)
            cur = stmt.execute(None)
            out, seen = [], set()
            while cur.next(None) and len(out) < limit:
                url = cur.get_string(0)[0]
                in_content = (cur.get_string(1)[0] or "").lower() in ("true", "1")
                if not url or not url.startswith("file://"):
                    continue
                path = unquote(urlparse(url).path)
                if path in seen:
                    continue
                seen.add(path)
                out.append((path, in_content))
            return out
        except Exception as e:
            log.warning("index query failed: %s", e)
            return None
