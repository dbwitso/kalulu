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


# ---------------------------------------------------------------------------- background helpers
def _lower_priority() -> None:          # runs in the child, before exec
    try:
        os.nice(15)
    except OSError:
        pass


def low_priority_argv(argv: list[str]) -> list[str]:
    """Run argv at idle I/O priority (when ionice exists): background scans must never slow
    down whatever the user is doing."""
    ionice = shutil.which("ionice")
    return [ionice, "-c", "3", *argv] if ionice else list(argv)


def _kill_group(p: subprocess.Popen) -> None:
    """Kill the helper and anything it started (so nothing keeps its output pipe open)."""
    import signal
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            p.kill()
        except ProcessLookupError:
            pass


class CancellableRunner:
    """One background helper per key (e.g. "files"): starting a new run kills the previous one, so
    fast typing never stacks up overlapping scans. Low CPU and I/O priority."""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}

    def run(self, key: str, argv: list[str], timeout: float = 3.0) -> str | None:
        """stdout, or None if cancelled by a newer run, timed out or failed."""
        try:
            p = subprocess.Popen(low_priority_argv(argv), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, text=True, preexec_fn=_lower_priority,
                                 cwd=os.path.expanduser("~"), start_new_session=True)
        except OSError:
            return None
        with self._lock:
            old = self._procs.get(key)
            self._procs[key] = p
        if old and old.poll() is None:
            _kill_group(old)
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(p)
            p.communicate()
            return None
        finally:
            with self._lock:
                if self._procs.get(key) is p:
                    del self._procs[key]
        return None if p.returncode and p.returncode < 0 else out


runner = CancellableRunner()
