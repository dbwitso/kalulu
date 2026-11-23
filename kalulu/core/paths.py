from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "kalulu"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "kalulu"
DATA_HOME = _xdg("XDG_DATA_HOME", ".local/share")
CONFIG_FILE = CONFIG_DIR / "config.toml"
SCRIPTS_DIR = CONFIG_DIR / "scripts"
PLUGINS_DIR = CONFIG_DIR / "plugins"
FRECENCY_FILE = STATE_DIR / "frecency.json"
LOG_FILE = STATE_DIR / "kalulu.log"
CLIPBOARD_DB = STATE_DIR / "clipboard.db"   # not under ~/.local/share/kalulu: install.sh replaces that folder


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, STATE_DIR, SCRIPTS_DIR, PLUGINS_DIR):
        d.mkdir(parents=True, exist_ok=True)
