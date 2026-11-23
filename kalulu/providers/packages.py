"""Install and remove software without leaving the launcher.

  install <name>     searches APT, Snap and Flatpak in parallel
  uninstall <name>   (or "remove <name>") finds an installed app and removes it from wherever it came from

↵ installs in the background — GNOME asks for your password (polkit) and a notification
tells you when it's done. Ctrl+↵ runs it in a terminal instead so you can watch progress.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core import fuzzy
from ..core.models import Action, Result
from ..core.system import run_capture
from .base import Provider

LONG = 1800  # seconds: installs can be slow on bad links


def _run(argv: list[str], timeout: float = 8) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env={**os.environ, "LANG": "C.UTF-8"}).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


# ---------------------------------------------------------------- search backends
_NOISE = re.compile(r"^lib|-(dev|dbg|doc|common|data|plugins?.*|l10n.*|locale.*|dbgsym)$")


def _apt_rank(term: str, r: dict) -> float:
    s = fuzzy.score(term, r["name"]) or 0
    if _NOISE.search(r["name"]):
        s -= 400
    return s


def search_apt(term: str, limit: int = 6) -> list[dict]:
    if not shutil.which("apt-cache"):
        return []
    rows = []
    for line in _run(["apt-cache", "search", "--names-only", term]).splitlines():
        name, _, desc = line.partition(" - ")
        if name:
            rows.append({"name": name.strip(), "desc": desc.strip(), "source": "apt", "id": name.strip()})
    rows.sort(key=lambda r: -_apt_rank(term, r))
    rows = rows[:limit]
    if rows:
        out = _run(["dpkg-query", "-W", "-f", "${Package}\t${db:Status-Abbrev}\n", *[r["name"] for r in rows]])
        installed = {l.split("\t")[0] for l in out.splitlines() if "\tii" in l}
        for r in rows:
            r["installed"] = r["name"] in installed
    return rows


def search_snap(term: str, limit: int = 6) -> list[dict]:
    if not shutil.which("snap"):
        return []
    lines = _run(["snap", "find", term], timeout=10).splitlines()
    if len(lines) < 2:
        return []
    installed = {l.split()[0] for l in _run(["snap", "list"]).splitlines()[1:] if l.strip()}
    rows = []
    for line in lines[1:]:
        cols = re.split(r"\s{2,}", line.strip())
        if len(cols) < 4:
            continue
        name, version, publisher = cols[0], cols[1], cols[2].rstrip("✓*✪ ")
        notes = cols[3] if len(cols) >= 5 else "-"
        summary = cols[-1]
        rows.append({"name": name, "id": name, "desc": summary, "publisher": publisher, "version": version,
                     "classic": "classic" in notes, "source": "snap", "installed": name in installed})
    return rows[:limit]


def search_flatpak(term: str, limit: int = 6) -> list[dict]:
    if not shutil.which("flatpak"):
        return []
    out = _run(["flatpak", "search", "--columns=name,application,version,remotes,description", term], timeout=10)
    installed = set(_run(["flatpak", "list", "--app", "--columns=application"]).split())
    rows = []
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 5 or "." not in cols[1]:
            continue
        rows.append({"name": cols[0], "id": cols[1], "version": cols[2], "remote": cols[3].split(",")[0] or "flathub",
                     "desc": cols[4], "source": "flatpak", "installed": cols[1] in installed})
    return rows[:limit]


# ---------------------------------------------------------------- commands
def install_cmd(pkg: dict) -> tuple[list[str], str]:
    """(background argv using polkit, terminal command)"""
    s, n = pkg["source"], pkg["id"]
    if s == "apt":
        return (["pkexec", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", n],
                f"sudo apt install {n}")
    if s == "snap":
        extra = ["--classic"] if pkg.get("classic") else []
        return (["snap", "install", n, *extra], f"sudo snap install {n} {' '.join(extra)}".strip())
    remote = pkg.get("remote", "flathub")
    return (["flatpak", "install", "-y", "--noninteractive", remote, n], f"flatpak install {remote} {n}")


def remove_cmd(source: str, n: str) -> tuple[list[str], str]:
    if source == "apt":
        return (["pkexec", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "remove", "-y", n],
                f"sudo apt remove {n}")
    if source == "snap":
        return (["snap", "remove", n], f"sudo snap remove {n}")
    return (["flatpak", "uninstall", "-y", "--noninteractive", n], f"flatpak uninstall {n}")


def details_cmd(pkg: dict) -> str:
    s, n = pkg["source"], pkg["id"]
    if s == "apt":
        return f"apt-cache show {n} | sed -n '1,/^$/p'; apt-cache policy {n}"
    if s == "snap":
        return f"snap info {n}"
    return f"flatpak remote-info {pkg.get('remote', 'flathub')} {n} 2>/dev/null || flatpak info {n}"


def source_of_desktop_file(path: str) -> tuple[str, str] | None:
    """Work out which package manager owns an installed app's .desktop file."""
    p = os.path.realpath(path)
    base = os.path.basename(path)
    if "/snapd/desktop/applications/" in path or p.startswith("/snap/"):
        return "snap", base.split("_")[0]
    if "/flatpak/exports/" in path or "/flatpak/" in p:
        return "flatpak", base.removesuffix(".desktop")
    if p.startswith(str(Path.home())):
        return None          # locally created launcher; nothing to uninstall
    out = _run(["dpkg-query", "-S", p]) or _run(["dpkg-query", "-S", path])
    if out:
        pkg = out.split(":")[0].split(",")[0].strip()
        if pkg:
            return "apt", pkg
    return None


def uninstall_app(title: str, desktop_path: str):
    src = source_of_desktop_file(desktop_path)
    if not src:
        return "Not installed by apt, snap or flatpak — remove it manually"
    source, name = src
    argv, _ = remove_cmd(source, name)
    code, out = run_capture(argv, timeout=LONG)
    if code == 0:
        return f"Removed {title} ({source}: {name})"
    if code in (126, 127) and source == "apt":
        return "Authentication cancelled"
    return f"Couldn't remove {title}: {(out.splitlines() or ['error'])[-1]}"


_LABEL = {"apt": "APT", "snap": "Snap", "flatpak": "Flatpak"}


def package_result(pkg: dict, term: str) -> Result:
    src, name = pkg["source"], pkg["name"]
    bits = [_LABEL[src]]
    if pkg.get("publisher"):
        bits.append(pkg["publisher"])
    if pkg.get("version"):
        bits.append(pkg["version"])
    if pkg.get("desc"):
        bits.append(pkg["desc"])
    bg, term_cmd = install_cmd(pkg)
    details = Action("Show Details", "script", {"shell": details_cmd(pkg), "mode": "fullOutput",
                                                 "title": name}, close=False)
    if pkg.get("installed"):
        rbg, rterm = remove_cmd(src, pkg["id"])
        actions = [
            details,
            Action(f"Remove {name}", "script", {"argv": rbg, "mode": "silent", "timeout": LONG,
                                                 "title": f"Removing {name}", "start_note": "Removing…",
                                                 "done_title": f"Removed {name}"}, confirm=True, close=False),
            Action("Remove in Terminal", "terminal", rterm),
        ]
        category = f"{_LABEL[src]} · Installed"
    else:
        actions = [
            Action(f"Install {name}", "script", {"argv": bg, "mode": "silent", "timeout": LONG,
                                                  "title": f"Installing {name}",
                                                  "start_note": f"Installing from {_LABEL[src]}…",
                                                  "done_title": f"{name} installed"}, close=False),
            Action("Install in Terminal", "terminal", term_cmd, shortcut="Ctrl+↵"),
            details,
            Action("Copy Install Command", "copy", term_cmd),
        ]
        category = _LABEL[src]
    score = fuzzy.best(term, name, pkg["id"], pkg.get("desc", ""), weights=(1.0, 0.9, 0.75)) or 100
    score += {"snap": 140, "flatpak": 140, "apt": 0}[src]      # store apps first, then debs
    if src == "apt" and _NOISE.search(pkg["id"]):
        score -= 400
    return Result(id=f"pkg:{src}:{pkg['id']}", title=name, subtitle=" · ".join(bits[1:]) or bits[0],
                  icon={"apt": "package-x-generic-symbolic", "snap": "system-software-install-symbolic",
                        "flatpak": "system-software-install-symbolic"}[src],
                  category=category, actions=actions, score=1000 + score, learn=False)


class PackagesProvider(Provider):
    name = "packages"
    slow = True
    delay_ms = 350
    prefixes = ("install ", "uninstall ", "remove ")

    def _sources(self) -> list[str]:
        return list(self.ctx.config.get("packages", {}).get("sources", ["apt", "snap", "flatpak"]))

    def query(self, text: str) -> list[Result]:
        if text.startswith("install "):
            term = text[len("install "):].strip()
            if len(term) < 2:
                return []
            fns = {"apt": search_apt, "snap": search_snap, "flatpak": search_flatpak}
            with ThreadPoolExecutor(3) as ex:
                futs = [ex.submit(fns[s], term) for s in self._sources() if s in fns]
                pkgs = [p for f in futs for p in f.result()]
            if not pkgs:
                return [Result(id="pkg:none", title=f"No packages found for “{term}”",
                               subtitle="Tried " + ", ".join(_LABEL[s] for s in self._sources() if s in _LABEL),
                               icon="package-x-generic-symbolic", category="Install", learn=False, score=900)]
            return [package_result(p, term) for p in pkgs]
        # uninstall / remove: match installed apps
        term = text.split(" ", 1)[1].strip() if " " in text else ""
        engine = self.ctx.engine
        apps = next((p for p in engine.providers if p.name == "apps"), None) if engine else None
        if not apps or len(term) < 2:
            return []
        out = []
        for r in apps.items():
            s = fuzzy.best(term, r.title, *r.keywords, weights=(1.0,) + (0.8,) * len(r.keywords))
            if s is None or s < 400:
                continue
            path = r.actions[0].payload["path"]
            out.append(Result(
                id=f"uninstall:{r.id}", title=f"Uninstall {r.title}", subtitle=path, icon=r.icon,
                category="Uninstall", score=1000 + s, learn=False,
                actions=[Action(f"Uninstall {r.title}", "callback",
                                lambda t=r.title, p=path: uninstall_app(t, p), confirm=True, close=False),
                         Action("Show Desktop File", "reveal", path)]))
        out.sort(key=lambda r: -r.score)
        return out[:10]
