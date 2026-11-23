"""Glass theme settings. Kalulu owns the config ([glass] in config.toml); the "Kalulu Glass" GNOME Shell
extension reads the mirrored ~/.local/state/kalulu/glass.json and applies changes live.
GTK-free so it can be unit tested."""
from __future__ import annotations

import json
import os

from .core import paths

STATE = paths.STATE_DIR / "glass.json"
EXTENSION_UUID = "kalulu-glass@kalulu.dev"
PARTS = ("panel", "dock", "menus", "notifications", "popups", "overview", "kalulu")


def settings(cfg: dict) -> dict:
    c = cfg.get("glass", {})
    out = {"enabled": bool(c.get("enabled", True))}
    for part in PARTS:
        out[part] = bool(c.get(part, True))
    out["blur"] = max(4, min(120, int(c.get("blur", 36))))
    out["opacity"] = max(0.1, min(0.9, float(c.get("opacity", 0.42))))
    tint = str(c.get("tint", "auto")).lower()
    out["tint"] = tint if tint in ("auto", "light", "dark") else "auto"
    return out


def write_state(cfg: dict) -> dict:
    s = settings(cfg)
    try:
        if STATE.exists() and json.loads(STATE.read_text()) == s:
            return s                       # unchanged: don't make the extension rebuild
    except (OSError, ValueError):
        pass
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    os.replace(tmp, STATE)
    return s
