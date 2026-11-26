"""Client for the "Kalulu Windows" GNOME Shell extension (dev.kalulu.Windows on the session bus).

On Wayland an app can't see or move other apps' windows; the Shell can, so the extension does the work
and Kalulu asks it over D-Bus. Everything here degrades to "not available" when the extension isn't
loaded, so the launcher keeps working without it. GTK-free.
"""
from __future__ import annotations

import json
import logging
import threading
import time

log = logging.getLogger("kalulu.windows")

BUS_NAME = "dev.kalulu.Windows"
OBJECT_PATH = "/dev/kalulu/Windows"
IFACE = "dev.kalulu.Windows"
EXTENSION_UUID = "kalulu-windows@kalulu.dev"
SCHEMA_ID = "org.gnome.shell.extensions.kalulu-windows"

LAYOUTS = ("left-half", "right-half", "top-half", "bottom-half", "maximize", "almost-maximize", "center",
           "restore", "top-left", "top-right", "bottom-left", "bottom-right", "first-third", "center-third",
           "last-third", "first-two-thirds", "last-two-thirds", "larger", "smaller", "next-display")


class WindowsBridge:
    LIST_TTL = 0.4          # one D-Bus round trip serves a whole keystroke's worth of lookups
    RETRY_AFTER = 8.0       # after a failure, don't keep asking a Shell that lacks the extension

    def __init__(self):
        self._conn = None
        self._lock = threading.Lock()
        self._down_until = 0.0
        self._cache: tuple[float, dict] | None = None

    # -------------------------------------------------------------- transport
    def _call(self, method: str, args=None, sig: str | None = None, timeout_ms: int = 600):
        if time.monotonic() < self._down_until:
            return None
        try:
            from gi.repository import Gio, GLib
            with self._lock:
                if self._conn is None:
                    self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            params = GLib.Variant(sig, args) if sig else None
            res = self._conn.call_sync(BUS_NAME, OBJECT_PATH, IFACE, method, params, None,
                                       Gio.DBusCallFlags.NO_AUTO_START, timeout_ms, None)
            return res.unpack()[0] if res is not None and res.n_children() else None
        except Exception as e:  # GLib.Error: extension missing, Shell restarting, no session bus…
            log.debug("windows bridge %s failed: %s", method, e)
            self._down_until = time.monotonic() + self.RETRY_AFTER
            self._cache = None
            return None

    def available(self) -> bool:
        return self.list() is not None

    # -------------------------------------------------------------- API
    def list(self) -> dict | None:
        now = time.monotonic()
        if self._cache and now - self._cache[0] < self.LIST_TTL:
            return self._cache[1]
        raw = self._call("ListWindows")
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        self._cache = (now, data)
        return data

    def windows(self) -> list[dict]:
        data = self.list()
        return data.get("windows", []) if data else []

    def _act(self, method: str, wid: int) -> bool:
        self._cache = None
        return bool(self._call(method, (int(wid),), "(t)"))

    def activate(self, wid: int) -> bool:
        return self._act("ActivateWindow", wid)

    def close(self, wid: int) -> bool:
        return self._act("CloseWindow", wid)

    def minimize(self, wid: int) -> bool:
        return self._act("MinimizeWindow", wid)

    def quit_app(self, wid: int) -> bool:
        return self._act("QuitApp", wid)

    def tile(self, layout: str, wid: int = 0) -> str:
        """'' on success, otherwise a message to show."""
        self._cache = None
        res = self._call("Tile", (layout, int(wid)), "(st)")
        if res is None:
            return "Window tiling needs the Kalulu Windows extension (log out and back in after installing)"
        return res


# ------------------------------------------------------------------ settings mirror
def settings_from_config(cfg: dict) -> dict:
    c = cfg.get("windows", {})
    keys = c.get("keys", {}) if isinstance(c.get("keys", {}), dict) else {}
    return {
        "hotkeys-enabled": bool(c.get("hotkeys", True)),
        "gap": max(0, min(64, int(c.get("gap", 0)))),
        "cycle-sizes": bool(c.get("cycle_sizes", True)),
        "keys": {k: str(v) for k, v in keys.items() if k in LAYOUTS},
    }


def apply_settings(cfg: dict, schema_dir) -> bool:
    """Mirror [windows] from config.toml into the extension's GSettings (applied live by the Shell)."""
    try:
        from gi.repository import Gio
    except ImportError:
        return False
    if not schema_dir.is_dir():
        return False
    try:
        src = Gio.SettingsSchemaSource.new_from_directory(str(schema_dir), Gio.SettingsSchemaSource.get_default(),
                                                          False)
        schema = src.lookup(SCHEMA_ID, False)
        if schema is None:
            return False
        gs = Gio.Settings.new_full(schema, None, None)
    except Exception as e:
        log.debug("windows settings unavailable: %s", e)
        return False
    want = settings_from_config(cfg)
    if gs.get_boolean("hotkeys-enabled") != want["hotkeys-enabled"]:
        gs.set_boolean("hotkeys-enabled", want["hotkeys-enabled"])
    if gs.get_int("gap") != want["gap"]:
        gs.set_int("gap", want["gap"])
    if gs.get_boolean("cycle-sizes") != want["cycle-sizes"]:
        gs.set_boolean("cycle-sizes", want["cycle-sizes"])
    for layout in LAYOUTS:
        key = f"tile-{layout}"
        if layout in want["keys"]:
            v = want["keys"][layout]
            value = [v] if v else []
            if list(gs.get_strv(key)) != value:
                gs.set_strv(key, value)
        elif gs.get_user_value(key) is not None:
            gs.reset(key)                       # removed from config: back to the default
    Gio.Settings.sync()
    return True
