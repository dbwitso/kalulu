"""Process helpers: detached spawning, terminal detection, notifications."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess

_TERMINALS = [
    # (binary, argv prefix that precedes "sh -c <cmd>")
    ("ptyxis", ["ptyxis", "--new-window", "--"]),
    ("gnome-terminal", ["gnome-terminal", "--"]),
    ("kgx", ["kgx", "-e"]),
    ("foot", ["foot"]),
    ("kitty", ["kitty"]),
    ("alacritty", ["alacritty", "-e"]),
    ("wezterm", ["wezterm", "start", "--"]),
    ("konsole", ["konsole", "-e"]),
    ("xterm", ["xterm", "-e"]),
]


def spawn(argv: list[str], cwd: str | None = None) -> None:
    """Start a process fully detached from Kalulu."""
    env = dict(os.environ)
    env.pop("DESKTOP_STARTUP_ID", None)
    env.pop("XDG_ACTIVATION_TOKEN", None)
    subprocess.Popen(
        argv,
        cwd=cwd or os.path.expanduser("~"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )


def terminal_argv(command: str, preferred: str = "auto", hold: bool = True) -> list[str]:
    shell_cmd = command
    if hold:
        shell_cmd = f'{command}; echo; read -r -p "[exit $?] press Enter to close" _'
    choices = _TERMINALS
    if preferred and preferred != "auto":
        choices = [t for t in _TERMINALS if t[0] == preferred] + _TERMINALS
    for binary, prefix in choices:
        if shutil.which(binary):
            return prefix + ["sh", "-c", shell_cmd]
    return ["x-terminal-emulator", "-e", "sh", "-c", shell_cmd]


def notify(title: str, body: str = "", icon: str = "dev.kalulu.Launcher") -> None:
    if shutil.which("notify-send"):
        spawn(["notify-send", "-a", "Kalulu", "-i", icon, title, body[:500]])


def run_capture(argv: list[str], timeout: float = 30.0, cwd: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(
            argv, cwd=cwd or os.path.expanduser("~"), capture_output=True,
            text=True, timeout=timeout, stdin=subprocess.DEVNULL,
        )
        out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
        return p.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout:.0f}s"
    except OSError as e:
        return 127, str(e)


_FIELD_CODES = {"%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%i", "%c", "%k", "%v", "%m"}


def exec_to_argv(exec_line: str) -> list[str]:
    """Turn a .desktop Exec= line into argv (field codes dropped)."""
    try:
        parts = shlex.split(exec_line)
    except ValueError:
        parts = exec_line.split()
    return [p.replace("%%", "%") for p in parts if p not in _FIELD_CODES]
