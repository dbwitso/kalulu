"""Regenerate docs/screenshots/*.png with canned data (no network needed).

    xvfb-run -a dbus-run-session python3 tests/screenshots.py docs/screenshots

Uses a throwaway HOME so your real config/frecency are untouched.
"""
import os
import sys
import tempfile
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots").resolve()
REPO = Path(__file__).resolve().parents[1]
H = Path(tempfile.mkdtemp(prefix="kalulu-shots-"))
os.environ.update(HOME=str(H), XDG_CONFIG_HOME=str(H / "cfg"), XDG_STATE_HOME=str(H / "state"),
                  XDG_DATA_HOME=str(H / "data"), XDG_DATA_DIRS=f"{H}/sys:/usr/share",
                  XDG_CURRENT_DESKTOP="ubuntu:GNOME", LANG="en_US.UTF-8")
if os.environ.get("KALULU_DARK"):
    os.environ["ADW_DEBUG_COLOR_SCHEME"] = "prefer-dark"
sys.path.insert(0, str(REPO))

# --- demo apps (generic / GNOME icons only) ----------------------------------
apps = H / "sys/applications"
apps.mkdir(parents=True)
DEMO = [
    ("org.gnome.Calculator", "Calculator", "Perform arithmetic, scientific or financial calculations", "org.gnome.Calculator"),
    ("org.gnome.Calendar", "Calendar", "Calendar for GNOME", "org.gnome.Calendar"),
    ("libreoffice-calc", "LibreOffice Calc", "Perform calculations, analyze information and manage lists", "libreoffice-calc"),
    ("libreoffice-writer", "LibreOffice Writer", "Create and edit text and graphics in letters and reports", "libreoffice-writer"),
    ("org.gnome.Nautilus", "Files", "Access and organize files", "org.gnome.Nautilus"),
    ("org.gnome.Terminal", "Terminal", "Use the command line", "org.gnome.Terminal"),
    ("org.gnome.TextEditor", "Text Editor", "Edit text files", "org.gnome.TextEditor"),
    ("org.gnome.SystemMonitor", "System Monitor", "View current processes and monitor system state", "org.gnome.SystemMonitor"),
    ("org.gnome.clocks", "Clocks", "Clocks for world times, plus alarms, stopwatch and timer", "org.gnome.clocks"),
    ("browser", "Web Browser", "Browse the web", "web-browser"),
    ("snap-store", "App Center", "Find, install and manage apps", "snap-store"),
]
for fid, name, comment, icon in DEMO:
    (apps / f"{fid}.desktop").write_text(
        f"[Desktop Entry]\nType=Application\nName={name}\nComment={comment}\nExec=true\nIcon={icon}\n"
        + ("Actions=new-window;\n\n[Desktop Action new-window]\nName=New Window\nExec=true\n" if fid == "org.gnome.Nautilus" else ""))
(H / "cfg/gtk-4.0").mkdir(parents=True)
(H / "cfg/gtk-4.0/settings.ini").write_text("[Settings]\ngtk-icon-theme-name=Yaru\n")
for d, files in {"Documents": ["Budget 2026.xlsx", "Budget review notes.md"],
                 "Documents/Tupane": ["budget-forecast-q1.csv"], "Downloads": ["budget-template.ods"]}.items():
    (H / d).mkdir(parents=True, exist_ok=True)
    for f in files:
        (H / d / f).write_text("x")

# --- fake package managers ------------------------------------------------------
fb = H / "fakebin"
fb.mkdir()
for name, body in {
    "snap": """case "$1" in find) printf 'Name    Version  Publisher   Notes  Summary\\nbtop    1.4.0    kz6fittycent  -   Resource monitor with CPU, memory, disks and network\\n';; list) printf 'Name\\n';; esac""",
    "flatpak": """case "$1" in search) printf 'Mission Center\\tio.missioncenter.MissionCenter\\t1.0\\tflathub\\tMonitor system resource usage\\n';; list) :;; esac""",
    "gnome-control-center": "true",
}.items():
    (fb / name).write_text("#!/bin/sh\n" + body + "\n")
    (fb / name).chmod(0o755)
os.environ["PATH"] = f"{fb}:{os.environ['PATH']}"

# --- canned answers (text written for this demo) ----------------------------------------
from kalulu.providers import knowledge  # noqa: E402

CANNED = {
    "search/title": {"pages": [
        {"key": "Lusaka", "title": "Lusaka", "description": "Capital and largest city of Zambia"},
        {"key": "Lusaka_Province", "title": "Lusaka Province", "description": "Province of Zambia"},
        {"key": "University_of_Zambia", "title": "University of Zambia", "description": "Public university in Lusaka"}]},
    "page/summary": {"title": "Lusaka", "description": "Capital and largest city of Zambia",
                     "extract": "(Demo text) Lusaka is Zambia's capital and its largest city, sitting on a high "
                                "plateau in the south-central part of the country. It is the country's main centre "
                                "for government, commerce and finance."},
    "duckduckgo": {"Heading": "Lusaka", "AbstractText": "Lusaka is the capital and largest city of Zambia.",
                   "AbstractSource": "Wikipedia", "AbstractURL": "https://en.wikipedia.org/wiki/Lusaka"},
}
def _fake_http(url, timeout=3.5):
    if "search/title" in url and "lusaka" not in url.lower():
        return {"pages": []}
    return next((v for k, v in CANNED.items() if k in url), None)


knowledge.http_json = _fake_http

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

from kalulu.app import KaluluApp  # noqa: E402

OUT.mkdir(parents=True, exist_ok=True)
app = KaluluApp()
suffix = "-dark" if os.environ.get("KALULU_DARK") else ""


def shot(name):
    w = app.win
    wd, ht = w.get_width(), w.get_height()
    p = Gtk.WidgetPaintable.new(w)
    s = Gtk.Snapshot()
    p.snapshot(s, wd, ht)
    tex = w.get_native().get_renderer().render_texture(s.to_node(), Graphene.Rect().init(0, 0, wd, ht))
    tex.save_to_png(str(OUT / f"{name}{suffix}.png"))
    print("saved", name + suffix)


def use(title, n=3):
    r = next(x for x in app.engine.search(title) if x.title == title)
    for _ in range(n):
        app.engine.record(r, "")


def steps():
    w = app.win
    yield lambda: [use(t, n) for t, n in (("Terminal", 2), ("Files", 3), ("Web Browser", 4))]
    yield lambda: w.show_launcher()
    yield lambda: shot("home")
    yield lambda: w.entry.set_text("cal")
    yield None
    yield lambda: shot("apps")
    if suffix:
        yield lambda: app.quit()
        return
    yield lambda: w.entry.set_text("2400 * 1.16 + 15% of 300")
    yield lambda: shot("calculator")
    yield lambda: w.entry.set_text("files")
    yield lambda: w.open_actions()
    yield lambda: shot("actions")
    yield lambda: w.close_actions()
    yield lambda: w.entry.set_text("budget")
    yield None
    yield None
    yield lambda: shot("files")
    yield lambda: w.entry.set_text("? lusaka")
    yield None
    yield None
    yield lambda: shot("ask")
    yield lambda: w.select(1)
    yield lambda: w.activate_selected()
    yield None
    yield None
    yield lambda: shot("answer")
    yield lambda: w.reset("install monitor")
    yield None
    yield None
    yield None
    yield lambda: shot("install")
    yield lambda: w.reset(":party")
    yield lambda: shot("emoji")
    yield lambda: w.reset("display")
    yield lambda: shot("settings")
    yield lambda: [app.engine.ctx.clips.add(t) for t in (
        "#e8590c", "~/Documents/Tupane/budget-forecast-q1.csv",
        "def handler(event):\n    return {\"ok\": True}\n",
        "netbird status --detail", "https://github.com/dbwitso/kalulu")]
    yield lambda: w.reset("clip ")
    yield lambda: shot("clipboard")
    yield lambda: app.quit()


it = steps()


def tick():
    try:
        step = next(it)
    except StopIteration:
        return False
    if step:
        step()
    return True


app.connect("startup", lambda _a: GLib.timeout_add(500, tick))
app.run(["kalulu", "--background"])
