"""Installed applications, read straight from .desktop files (apt, snap, flatpak, local)."""
from __future__ import annotations

import configparser
import locale
import os
import shutil
from pathlib import Path

from ..core.models import Action, Result
from ..core.paths import DATA_HOME
from .base import Provider


def _app_dirs() -> list[Path]:
    dirs = [DATA_HOME / "applications"]
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for d in data_dirs.split(":"):
        if d:
            dirs.append(Path(d) / "applications")
    for extra in (
        DATA_HOME / "flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
    ):
        if extra not in dirs:
            dirs.append(extra)
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _locale_keys() -> list[str]:
    lang = os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    if not lang:
        try:
            lang = locale.getlocale()[0] or ""
        except ValueError:
            lang = ""
    lang = lang.split(".")[0].split("@")[0]
    keys = []
    if lang:
        keys.append(lang)
        if "_" in lang:
            keys.append(lang.split("_")[0])
    return keys


def _current_desktops() -> set[str]:
    return {d.lower() for d in (os.environ.get("XDG_CURRENT_DESKTOP") or "GNOME").split(":") if d}


def _split(v: str) -> list[str]:
    return [x for x in v.split(";") if x]


class DesktopEntry:
    __slots__ = ("id", "path", "name", "generic", "comment", "icon", "exec", "keywords",
                 "categories", "terminal", "actions")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def parse_desktop_file(path: Path, desktop_id: str, loc: list[str], desktops: set[str]) -> DesktopEntry | None:
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return None
    if not cp.has_section("Desktop Entry"):
        return None
    s = cp["Desktop Entry"]

    def get(key: str, default: str = "") -> str:
        for l in loc:
            v = s.get(f"{key}[{l}]")
            if v:
                return v
        return s.get(key, default)

    if s.get("Type", "Application") != "Application":
        return None
    if s.get("NoDisplay", "false").lower() == "true" or s.get("Hidden", "false").lower() == "true":
        return None
    only = {d.lower() for d in _split(s.get("OnlyShowIn", ""))}
    if only and not (only & desktops):
        return None
    nots = {d.lower() for d in _split(s.get("NotShowIn", ""))}
    if nots & desktops:
        return None
    try_exec = s.get("TryExec")
    if try_exec and not (os.path.isabs(try_exec) and os.access(try_exec, os.X_OK)) and not shutil.which(try_exec):
        return None
    name = get("Name")
    if not name or not s.get("Exec"):
        return None

    actions = []
    for act in _split(s.get("Actions", "")):
        sec = f"Desktop Action {act}"
        if cp.has_section(sec):
            a = cp[sec]
            aname = next((a.get(f"Name[{l}]") for l in loc if a.get(f"Name[{l}]")), a.get("Name"))
            if aname and a.get("Exec"):
                actions.append((act, aname, a.get("Exec")))

    return DesktopEntry(
        id=desktop_id, path=str(path), name=name, generic=get("GenericName"),
        comment=get("Comment"), icon=s.get("Icon", "application-x-executable"),
        exec=s.get("Exec"), keywords=_split(get("Keywords")),
        categories=_split(s.get("Categories", "")),
        terminal=s.get("Terminal", "false").lower() == "true", actions=actions,
    )


def scan() -> list[DesktopEntry]:
    loc, desktops = _locale_keys(), _current_desktops()
    seen: set[str] = set()
    out: list[DesktopEntry] = []
    for base in _app_dirs():
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.desktop")):
            desktop_id = str(path.relative_to(base)).replace(os.sep, "-")
            if desktop_id in seen:
                continue
            seen.add(desktop_id)   # first dir wins, even if hidden (that's how overrides work)
            e = parse_desktop_file(path, desktop_id, loc, desktops)
            if e:
                out.append(e)
    return out


class AppsProvider(Provider):
    name = "apps"
    weight = 40.0

    def __init__(self, ctx):
        super().__init__(ctx)
        self._items: list[Result] = []
        self._stamp: tuple = ()
        self.refresh()

    def _dir_stamp(self) -> tuple:
        st = []
        for d in _app_dirs():
            try:
                st.append(d.stat().st_mtime_ns)
            except OSError:
                st.append(0)
        return tuple(st)

    def refresh(self) -> None:
        stamp = self._dir_stamp()
        if stamp == self._stamp and self._items:
            return
        self._stamp = stamp
        items = []
        for e in scan():
            actions = [Action("Open Application", "launch_app", {"path": e.path, "exec": e.exec,
                                                                  "terminal": e.terminal})]
            for act_id, act_name, act_exec in e.actions:
                actions.append(Action(act_name, "launch_app",
                                      {"path": e.path, "action": act_id, "exec": act_exec,
                                       "terminal": e.terminal}))
            actions.append(Action("Uninstall…", "callback",
                                  (lambda t=e.name, p=e.path: __import__("kalulu.providers.packages", fromlist=["x"])
                                   .uninstall_app(t, p)), confirm=True, close=False))
            actions.append(Action("Copy Desktop File Path", "copy", e.path))
            actions.append(Action("Show Desktop File", "reveal", e.path))
            kws = list(e.keywords)
            if e.generic:
                kws.append(e.generic)
            kws.append(e.id.removesuffix(".desktop").split(".")[-1])
            items.append(Result(
                id=f"app:{e.id}", title=e.name, subtitle=e.comment or e.generic or "",
                icon=e.icon, category="Application", actions=actions, keywords=kws,
            ))
        self._items = items

    def items(self) -> list[Result]:
        return self._items
