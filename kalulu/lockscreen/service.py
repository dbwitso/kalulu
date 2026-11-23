"""Keeps the lock screen data fresh while Kalulu runs: re-cuts the wallpaper when it changes and
refreshes the weather every half hour. All slow work happens on worker threads."""
from __future__ import annotations

import logging
import threading

from gi.repository import Gio, GLib

from . import WEATHER_MAX_AGE, current_wallpaper, prefs, prepare, read_meta, update_weather

log = logging.getLogger("kalulu.lockscreen")


def _fresh_json(url, timeout=8):
    from ..providers.knowledge import http_json
    return http_json(url, timeout, cache=False)      # weather must not come from the answer cache


def _settings(schema: str):
    src = Gio.SettingsSchemaSource.get_default()
    if src is None or src.lookup(schema, True) is None:
        return None
    return Gio.Settings.new(schema)


class LockscreenService:
    def __init__(self, engine):
        self.engine = engine
        self.bg = _settings("org.gnome.desktop.background")
        self.iface = _settings("org.gnome.desktop.interface")
        self.weather_settings = _settings("org.gnome.shell.weather")
        self._lock = threading.Lock()
        self._debounce = 0
        self.busy = False
        for s, keys in ((self.bg, ("picture-uri", "picture-uri-dark")), (self.iface, ("color-scheme",))):
            if s is not None:
                for k in keys:
                    s.connect(f"changed::{k}", lambda *_: self.schedule())
        GLib.timeout_add_seconds(WEATHER_MAX_AGE, self._weather_tick)
        self.schedule(delay_ms=4000)          # let login settle first

    @property
    def prefs(self) -> dict:
        return prefs(self.engine.ctx.config)

    def schedule(self, delay_ms: int = 1500, force: bool = False):
        if self._debounce:
            GLib.source_remove(self._debounce)
        self._debounce = GLib.timeout_add(delay_ms, self._kick, force)

    def _kick(self, force):
        self._debounce = 0
        self.refresh(force=force)
        return False

    def refresh(self, force: bool = False, done=None):
        """Re-prepare wallpaper + weather in the background. done(meta) runs on the main loop."""
        p = self.prefs

        def work():
            if not self._lock.acquire(blocking=False):
                return
            self.busy = True
            meta = {}
            try:
                wall = current_wallpaper(p, self.bg, self.iface)
                meta = prepare(p, wall, force=force)
                w = meta.get("weather") or {}
                if force or not w or GLib.get_real_time() / 1e6 - w.get("time", 0) > WEATHER_MAX_AGE:
                    update_weather(p, _fresh_json, self.weather_settings)
                meta = read_meta()
            except Exception:
                log.exception("lock screen refresh failed")
            finally:
                self.busy = False
                self._lock.release()
            if done:
                GLib.idle_add(lambda: (done(meta), False)[1])

        threading.Thread(target=work, daemon=True).start()

    def _weather_tick(self):
        p = self.prefs

        def work():
            try:
                update_weather(p, _fresh_json, self.weather_settings)
            except Exception:
                log.exception("weather refresh failed")

        threading.Thread(target=work, daemon=True).start()
        return True
