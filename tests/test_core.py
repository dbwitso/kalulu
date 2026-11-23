"""Run: python3 -m pytest tests  (or python3 tests/test_core.py)"""
import os
import sys
import tempfile
import textwrap
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ.update(HOME=str(TMP), XDG_CONFIG_HOME=str(TMP / "cfg"), XDG_STATE_HOME=str(TMP / "state"),
                  XDG_DATA_HOME=str(TMP / "data"), XDG_DATA_DIRS=str(TMP / "sys"),
                  XDG_CURRENT_DESKTOP="ubuntu:GNOME", LANG="en_US.UTF-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

apps = TMP / "sys/applications"
apps.mkdir(parents=True)
for fid, body in {
    "firefox_firefox.desktop": "Name=Firefox Web Browser\nExec=firefox %u\nIcon=firefox\nKeywords=Internet;WWW;\n"
                               "Actions=new-private;\n\n[Desktop Action new-private]\nName=New Private Window\nExec=firefox --private-window\n",
    "code.desktop": "Name=Visual Studio Code\nExec=code %F\nIcon=vscode\nKeywords=vscode;editor;\n",
    "org.gnome.Nautilus.desktop": "Name=Files\nExec=nautilus\nIcon=org.gnome.Nautilus\nKeywords=folder;explorer;\n",
    "hidden.desktop": "Name=Hidden Thing\nExec=x\nNoDisplay=true\n",
    "kde.desktop": "Name=KDE Only\nExec=x\nOnlyShowIn=KDE;\n",
}.items():
    (apps / fid).write_text("[Desktop Entry]\nType=Application\n" + body)

from kalulu.core import fuzzy, paths  # noqa: E402
from kalulu.core.engine import Engine  # noqa: E402
from kalulu.providers.calculator import evaluate, fmt  # noqa: E402

paths.ensure_dirs()
script = paths.SCRIPTS_DIR / "greet.sh"
script.write_text(textwrap.dedent("""\
    #!/bin/sh
    # @kalulu.title Greet Someone
    # @kalulu.mode fullOutput
    # @kalulu.argument1 {"placeholder": "name"}
    echo "hello $1"
"""))
script.chmod(0o755)
(paths.PLUGINS_DIR / "echo.py").write_text(textwrap.dedent("""\
    NAME = "Echo"
    PREFIX = "echo "
    def search(q, api):
        return [api.Result(id="echo", title=q.upper(), actions=[api.Action("Copy", "copy", q)])]
"""))

E = Engine()


def titles(q, slow=False):
    return [r.title for r in E.search(q, slow=slow)]


def test_fuzzy():
    assert fuzzy.score("vsc", "Visual Studio Code") > fuzzy.score("vsc", "Visual Scheduler Calendar thing") - 50
    assert fuzzy.score("code", "Visual Studio Code") > 700
    assert fuzzy.score("fire", "Firefox") > fuzzy.score("fire", "Campfire")
    assert fuzzy.score("zzz", "Firefox") is None


def test_apps():
    assert titles("fire")[0] == "Firefox Web Browser"
    assert titles("vsc")[0] == "Visual Studio Code"
    assert titles("explorer")[0] == "Files"
    assert "Hidden Thing" not in titles("hidden") and "KDE Only" not in titles("kde")
    ff = E.search("fire")[0]
    assert [a.title for a in ff.actions][:2] == ["Open Application", "New Private Window"]


def test_calc():
    assert evaluate("2+2*3") == 8
    assert fmt(evaluate("15% of 200")) == "30"
    assert fmt(evaluate("2^10")) == "1,024"
    assert abs(evaluate("sqrt(16)+pi") - 7.14159265) < 1e-6
    assert titles("12*12")[0] == "144"
    assert not any(t == "144" for t in titles("firefox"))


def test_calc_safety():
    for bad in ("__import__('os')", "9**9**9", "().__class__", "open('x')"):
        assert E.search(bad)[0].category != "Calculator", bad


def test_commands_and_web():
    assert titles("lock")[0] == "Lock Screen"
    assert titles("dark")[0] == "Toggle Dark Mode"
    assert titles("something random")[-1].startswith("Search Google")
    assert titles("github.com")[0] == "Open https://github.com"
    assert titles("gh kalulu")[0] == "Search GitHub: kalulu"


def test_scripts_and_args():
    r = E.search("greet")[0]
    assert r.title == "Greet Someone" and r.primary.kind == "complete"
    r = E.search("Greet Someone Dabs")[0]
    assert r.primary.kind == "script" and r.primary.payload["args"] == ["Dabs"]
    kind, out = E.execute(r.primary)
    assert (kind, out) == ("detail", "hello Dabs")


def test_plugins_and_prefixes():
    assert titles("echo hi")[0] == "HI"
    assert titles(":fire")[0].startswith("🔥")
    assert titles("> ls")[0] == "ls"
    (TMP / "Documents").mkdir(exist_ok=True)
    (TMP / "Documents/Budget 2026.xlsx").write_text("x")
    assert "Documents" in titles("~/Doc", slow=True)
    assert "Budget 2026.xlsx" in titles("f budget", slow=True)


def test_frecency_learning():
    files = next(r for r in E.search("f") if r.title == "Files")
    before = titles("f")
    for _ in range(3):
        E.record(files, "f")
    assert titles("f")[0] == "Files", before
    assert E.home()[0].title == "Files"


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:
                fails += 1
                import traceback
                traceback.print_exc()
                print("FAIL", name, e)
    sys.exit(1 if fails else 0)
