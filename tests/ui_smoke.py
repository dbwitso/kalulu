"""Drive the real window headlessly and save screenshots of each state.
Run under Xvfb + dbus-run-session with the test env (see /tmp/shot.sh-style env)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi  # noqa: E402
gi.require_version("Graphene", "1.0")

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

from kalulu.app import KaluluApp  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots")
OUT.mkdir(exist_ok=True)
app = KaluluApp()
checks = []


def shot(name):
    w = app.win
    wd, ht = w.get_width(), w.get_height()
    p = Gtk.WidgetPaintable.new(w)
    s = Gtk.Snapshot()
    p.snapshot(s, wd, ht)
    tex = w.get_native().get_renderer().render_texture(s.to_node(), Graphene.Rect().init(0, 0, wd, ht))
    tex.save_to_png(str(OUT / f"ui_{name}.png"))


def steps():
    w = app.win
    yield lambda: w.show_launcher()
    yield lambda: shot("home")
    yield lambda: w.entry.set_text("fire")
    yield lambda: w.open_actions()
    yield lambda: (checks.append(("actions", [r.title for r in w.results])), shot("actions"))
    yield lambda: w.close_actions()
    yield lambda: checks.append(("restored query", w.entry.get_text()))
    yield lambda: w.entry.set_text(":fire")
    yield lambda: (checks.append(("emoji", w.results[0].title)), shot("emoji"))
    yield lambda: w.entry.set_text("kill port")
    yield lambda: checks.append(("script hint", w.results[0].subtitle))
    yield lambda: w.activate_selected()
    yield lambda: checks.append(("arg mode", w.entry.get_text()))
    yield lambda: w.entry.set_text("Top Memory Users")
    yield lambda: w.activate_selected()
    yield None  # let the script thread finish
    yield None
    yield lambda: (checks.append(("detail mode", w.mode)), shot("detail"))
    yield lambda: w.reset("lock")
    yield lambda: shot("lock")
    yield lambda: w.reset("restart")
    yield lambda: w.activate_selected()
    yield lambda: (checks.append(("confirm toast", w.toast.get_label())), shot("confirm"))
    yield lambda: w.reset("~/")
    yield None
    yield lambda: (checks.append(("browse", [r.title for r in w.results][:5])), shot("browse"))
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


def on_startup(_a):
    GLib.timeout_add(450, tick)


app.connect("startup", on_startup)
app.run(["kalulu", "--background"])
for k, v in checks:
    print(f"{k:16} {v}")
