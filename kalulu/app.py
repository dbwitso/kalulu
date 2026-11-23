"""Single-instance application. Running `kalulu` again talks to the resident instance over D-Bus."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from .core import paths  # noqa: E402
from .core.engine import Engine  # noqa: E402

USAGE = """\
Usage: kalulu [OPTION]
  (no option)     toggle the launcher
  --show          show the launcher
  --hide          hide the launcher
  --query TEXT    show with TEXT pre-filled (e.g. --query ':' for emoji)
  --background    start resident without showing (used by autostart)
  --reload        reload config, apps, scripts and plugins
  --doctor        list apps whose icons can't be found
  --quit          stop the resident instance
  --version
"""


class KaluluApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.engine: Engine | None = None
        self.win = None
        self._held = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        paths.ensure_dirs()
        self.engine = Engine()
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).parent / "ui" / "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            str(Path(__file__).parent / "ui"))
        from .ui.window import LauncherWindow
        self.win = LauncherWindow(self, self.engine)
        # D-Bus entry points (org.gtk.Actions on /dev/kalulu/Launcher):
        #   clip-add   — the GNOME Shell extension reports each copy here (Wayland hides it from apps)
        #   show-query — kalulu-toggle 'clip ' opens Kalulu prefilled without starting Python
        for name, handler in (("clip-add", lambda _a, v: self.win._remember_clip(v.get_string())),
                              ("show-query", lambda _a, v: self.win.show_launcher(v.get_string()))):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", handler)
            self.add_action(act)
        if not self._held:
            self.hold()          # stay resident after the window hides
            self._held = True

    def do_activate(self):
        self.win.toggle()

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        a = args[0] if args else ""
        if a in ("-h", "--help"):
            cmdline.print_literal(USAGE)
        elif a == "--version":
            cmdline.print_literal(f"kalulu {__version__}\n")
        elif a == "--background":
            pass
        elif a == "--quit":
            self.quit()
        elif a == "--hide":
            self.win.hide_launcher()
        elif a == "--show":
            self.win.show_launcher()
        elif a == "--doctor":
            cmdline.print_literal(self._doctor())
        elif a == "--reload":
            self.engine.reload()
        elif a == "--query":
            self.win.show_launcher(" ".join(args[1:]))
        elif a == "--screenshot":            # dev aid: --screenshot out.png [query]
            self.win.show_launcher(" ".join(args[2:]))
            GLib.timeout_add(900, self._screenshot, args[1])
        else:
            self.win.toggle()
        return 0

    def _doctor(self) -> str:
        from .providers.apps import AppsProvider
        from .ui.icons import resolver
        apps = next(p for p in self.engine.providers if isinstance(p, AppsProvider))
        apps.refresh()
        rows, counts = [], {}
        for r in apps.items():
            _, how = resolver().resolve(r.icon)
            counts[how] = counts.get(how, 0) + 1
            if how == "missing":
                path = r.actions[0].payload["path"]
                rows.append(f"  {r.title:<32} Icon={r.icon}\n    {path}")
        head = (f"{len(apps.items())} apps · " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
                + f"\nicon theme: {Gtk.Settings.get_default().props.gtk_icon_theme_name}\n")
        if not rows:
            return head + "All app icons resolved.\n"
        return head + "Missing icons:\n" + "\n".join(rows) + "\n"

    def _screenshot(self, out):
        from gi.repository import Graphene
        w = self.win
        width, height = w.get_width(), w.get_height()
        paintable = Gtk.WidgetPaintable.new(w)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, width, height)
        node = snap.to_node()
        tex = w.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
        tex.save_to_png(out)
        print("saved", out, flush=True)
        self.quit()
        return False


def run() -> int:
    paths.ensure_dirs()
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("KALULU_DEBUG") else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(paths.LOG_FILE), logging.StreamHandler()],
    )
    return KaluluApp().run(sys.argv)
