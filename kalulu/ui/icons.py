"""Icon resolution that matches what GNOME Shell shows.

GTK4's icon theme misses several things .desktop files do in the wild:
  * Icon=foo.png / foo.svg     (a file name, not an icon name)
  * Icon=/opt/app/icon         (absolute path without extension, common in AppImages/JetBrains)
  * icons living in /usr/share/pixmaps (GTK4 dropped that fallback; Shell still uses it)
  * Flatpak/Snap icon folders when Kalulu wasn't started with the full session XDG_DATA_DIRS
"""
from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

_EXTS = (".svg", ".png", ".xpm", ".svgz", ".jpg", ".ico")
FALLBACK_APP = "application-x-executable"
SYMBOLIC_FALLBACKS = ("system-run-symbolic", "emblem-system-symbolic", "applications-system-symbolic",
                      "preferences-system-symbolic", "starred-symbolic")


def _data_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    dirs = [home] + [Path(d) for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if d]
    for extra in (home / "flatpak/exports/share", Path("/var/lib/flatpak/exports/share"),
                  Path("/var/lib/snapd/desktop"), Path("/usr/share")):
        if extra not in dirs:
            dirs.append(extra)
    return dirs


def pixmap_dirs() -> list[Path]:
    return [d / "pixmaps" for d in _data_dirs() if (d / "pixmaps").is_dir()]


def find_file(stem: str, dirs: list[Path]) -> Path | None:
    for d in dirs:
        for ext in _EXTS:
            p = d / f"{stem}{ext}"
            if p.is_file():
                return p
    return None


class IconResolver:
    def __init__(self):
        self.theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        self.cache: dict[str, tuple[Gio.Icon, str]] = {}
        self.pixmaps = pixmap_dirs()
        existing = set(self.theme.get_search_path() or [])
        for d in _data_dirs():
            icons = d / "icons"
            if icons.is_dir() and str(icons) not in existing:
                self.theme.add_search_path(str(icons))
        for p in self.pixmaps:
            if str(p) not in existing:
                self.theme.add_search_path(str(p))   # unthemed icons in pixmaps become findable
        self.theme.connect("changed", lambda *_: self.cache.clear())

    def resolve(self, icon: str) -> tuple[Gio.Icon, str]:
        """Return (gicon, how) where how is 'file', 'theme', 'pixmap' or 'missing'."""
        icon = (icon or "").strip()
        hit = self.cache.get(icon)
        if hit:
            return hit
        out = self._resolve(icon)
        self.cache[icon] = out
        return out

    def _first(self, names) -> Gio.Icon:
        for n in names:
            if self.theme.has_icon(n):
                return Gio.ThemedIcon.new(n)
        return Gio.ThemedIcon.new(FALLBACK_APP)

    def _themed(self, name: str) -> Gio.Icon | None:
        for n in (name, name.lower()):
            if self.theme.has_icon(n):
                return Gio.ThemedIcon.new(n)
        return None

    def _resolve(self, icon: str) -> tuple[Gio.Icon, str]:
        if not icon:
            return Gio.ThemedIcon.new(FALLBACK_APP), "missing"

        if icon.startswith("mime:"):
            # The theme's own icon for the file type (spreadsheet, PDF, archive…), with the same
            # fallback chain Files uses, instead of one blank page for everything.
            mime = icon[5:]
            ctype = Gio.content_type_from_mime_type(mime) or mime
            names = list(Gio.content_type_get_icon(ctype).get_names())
            names += [Gio.content_type_get_generic_icon_name(ctype) or "", "text-x-generic"]
            for n in names:
                if n and not n.endswith("-symbolic") and self.theme.has_icon(n):
                    return Gio.ThemedIcon.new(n), "theme"
            return Gio.ThemedIcon.new("text-x-generic"), "missing"

        if icon.endswith("-symbolic"):
            g = self._themed(icon)
            if g:
                return g, "theme"
            base = self._themed(icon.removesuffix("-symbolic"))
            if base:
                return base, "theme"
            return self._first(SYMBOLIC_FALLBACKS), "missing"

        if os.path.isabs(icon) or icon.startswith("~"):
            p = Path(os.path.expanduser(icon))
            if p.is_file():
                return Gio.FileIcon.new(Gio.File.new_for_path(str(p))), "file"
            f = find_file(p.name, [p.parent])
            if f:
                return Gio.FileIcon.new(Gio.File.new_for_path(str(f))), "file"
            icon = p.stem                    # last resort: try it as a name

        stem = icon
        for ext in _EXTS:
            if stem.lower().endswith(ext):
                stem = stem[: -len(ext)]
                break

        g = self._themed(stem)
        if g:
            return g, "theme"
        f = find_file(stem, self.pixmaps) or find_file(icon, self.pixmaps)
        if f:
            return Gio.FileIcon.new(Gio.File.new_for_path(str(f))), "pixmap"
        # Reverse-DNS ids (org.foo.Bar) sometimes ship icons only under the last component
        if "." in stem:
            g = self._themed(stem.rsplit(".", 1)[-1])
            if g:
                return g, "theme"
        # Modern Adwaita ships many generic names only as -symbolic; better than a blank icon
        g = self._themed(stem + "-symbolic")
        if g:
            return g, "symbolic"
        return Gio.ThemedIcon.new(FALLBACK_APP), "missing"


_resolver: IconResolver | None = None


def resolver() -> IconResolver:
    global _resolver
    if _resolver is None:
        _resolver = IconResolver()
    return _resolver
