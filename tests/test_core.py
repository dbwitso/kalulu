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


# ---------------------------------------------------------------- v0.2: inline answers, packages, settings
def _fake_bin(name, body):
    d = TMP / "fakebin"
    d.mkdir(exist_ok=True)
    (d / name).write_text("#!/bin/sh\n" + body)
    (d / name).chmod(0o755)
    os.environ["PATH"] = f"{d}:{os.environ['PATH']}"


def test_knowledge_inline():
    from kalulu.providers import knowledge
    canned = {
        "search/title": {"pages": [{"key": "Lusaka", "title": "Lusaka", "description": "Capital city of Zambia"},
                                   {"key": "Lusaka_Province", "title": "Lusaka Province", "description": "Province"}]},
        "page/summary": {"title": "Lusaka", "description": "Capital city of Zambia",
                         "extract": "Lusaka is the capital and largest city of Zambia."},
        "duckduckgo": {"Heading": "Lusaka", "AbstractText": "Lusaka is the capital of Zambia.",
                       "AbstractSource": "Wikipedia", "AbstractURL": "https://en.wikipedia.org/wiki/Lusaka"},
        "dictionaryapi": [{"word": "kalulu", "phonetic": "/ka'lu:lu/", "meanings": [
            {"partOfSpeech": "noun", "definitions": [{"definition": "A hare, the clever trickster of folk tales."}]}]}],
    }
    knowledge.http_json = lambda url, timeout=3.5: next((v for k, v in canned.items() if k in url), None)
    blended = E.search("lusaka", slow=True)
    wiki = [r for r in blended if r.category == "Wikipedia"]
    assert [r.title for r in wiki] == ["Lusaka", "Lusaka Province"], blended
    kind, text = E.execute(wiki[0].primary)
    assert kind == "detail" and "largest city of Zambia" in text["body"] and "Wikipedia" in text["source"]
    ask = E.search("? lusaka", slow=True)
    assert ask[0].category == "Answer" and "capital of Zambia" in ask[0].title
    assert all(r.category in ("Answer", "Wikipedia") for r in ask)
    d = E.search("define kalulu", slow=True)
    assert d[0].category == "Define" and "hare" in d[0].title
    # apps still beat Wikipedia when both match
    mixed = E.search("files", slow=False) + E.search("files", slow=True)
    from kalulu.core.engine import dedupe_sort
    assert dedupe_sort(mixed)[0].title == "Files"


def test_install_search():
    _fake_bin("snap", """
case "$1" in
  find) printf 'Name     Version  Publisher     Notes    Summary\\nhtop     3.3.0    maxiberta     -        Interactive processes viewer\\ncode     1.95     vscode**      classic  Code editing. Redefined.\\n';;
  list) printf 'Name Version Rev\\ncore22 1 1\\n';;
esac""")
    _fake_bin("flatpak", """
case "$1" in
  search) printf 'htop\\tio.htop.Htop\\t3.3.0\\tflathub\\tProcess viewer\\n';;
  list) printf 'org.gnome.Calculator\\n';;
esac""")
    res = E.search("install htop", slow=True)
    srcs = {r.category.split(" ")[0] for r in res}
    assert {"APT", "Snap", "Flatpak"} <= srcs, [(r.title, r.category) for r in res]
    snap_code = next(r for r in E.search("install code", slow=True) if r.id == "pkg:snap:code")
    assert snap_code.primary.payload["argv"] == ["snap", "install", "code", "--classic"]
    apt = next(r for r in res if r.category.startswith("APT") and r.title == "htop")
    assert apt.primary.payload["argv"][:2] == ["pkexec", "env"]
    assert apt.actions[1].payload == "sudo apt install htop"
    # an installed apt package offers removal, not install
    installed = next((r for r in E.search("install coreutils", slow=True) if r.id == "pkg:apt:coreutils"), None)
    assert installed and "Installed" in installed.category and installed.actions[1].confirm


def test_uninstall_and_sources():
    from kalulu.providers.packages import source_of_desktop_file
    res = E.search("uninstall firefox", slow=True)
    assert res and res[0].title == "Uninstall Firefox Web Browser" and res[0].primary.confirm
    assert source_of_desktop_file("/var/lib/snapd/desktop/applications/firefox_firefox.desktop") == ("snap", "firefox")
    assert source_of_desktop_file("/var/lib/flatpak/exports/share/applications/org.gimp.GIMP.desktop") == ("flatpak", "org.gimp.GIMP")
    real = next(Path("/usr/share/applications").glob("*.desktop"), None)
    if real:
        src = source_of_desktop_file(str(real))
        assert src and src[0] == "apt", src


def test_settings_panels():
    _fake_bin("gnome-control-center", "true")
    E.reload()
    assert titles("bluetooth")[0] in ("Bluetooth Settings", "Toggle Bluetooth")
    assert titles("resolution")[0] == "Displays Settings"
    assert "Update All Software" in titles("upgrade")



def test_clipboard_history_persists():
    from kalulu.core.clipstore import ClipStore
    db = TMP / "clip-test.db"
    s1 = ClipStore(db, max_items=3)
    assert s1.add("first") and s1.add("second") and s1.add("https://github.com/dbwitso/kalulu")
    assert not s1.add("   ")                      # blank copies are ignored
    s1.add("first")                               # re-copy moves it back to the top
    assert [c.text for c in s1.all()][0] == "first"
    assert (db.stat().st_mode & 0o777) == 0o600   # history can hold secrets
    pin = next(c for c in s1.all() if c.text == "second")
    s1.set_pinned(pin.id, True)
    for i in range(5):
        s1.add(f"filler {i}")
    s1.close()

    s2 = ClipStore(db, max_items=3)               # "after a reboot": new process, same file
    texts = [c.text for c in s2.all()]
    assert texts[0] == "second"                   # pinned first, and never pruned
    assert len(texts) == 4 and "first" not in texts
    assert s2.clear(keep_pinned=True) == 3 and [c.text for c in s2.all()] == ["second"]


def test_clipboard_provider():
    store = E.ctx.clips
    assert store is not None
    store.add("ssh dabs@10.0.0.5 -p 2222")
    store.add("https://usetupane.com")
    store.add("line one\nline two")
    res = E.search("clip ")
    assert res[0].title == "line one line two" and res[0].category == "Clipboard"
    assert all(r.id.startswith("clip:") for r in res)          # "clip " hides apps, web search etc.
    hit = E.search("clip tupane")
    assert hit[0].title == "https://usetupane.com" and "Link" in hit[0].subtitle
    assert any(a.title == "Open Link" for a in hit[0].actions)
    delete = next(a for a in hit[0].actions if a.title == "Delete Entry")
    kind, _msg = E.execute(delete)
    assert kind == "refresh" and not [r for r in E.search("clip tupane") if "usetupane" in r.title]
    assert "Clipboard History" in titles("clipboard")


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
