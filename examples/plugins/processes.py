"""Kalulu plugin: find and kill processes.   Type:  kill <name>"""
import os
import signal

NAME = "Processes"
PREFIX = "kill "


def _procs():
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                comm = f.read().strip()
            with open(f"/proc/{pid}/status") as f:
                rss = next((int(l.split()[1]) for l in f if l.startswith("VmRSS:")), 0)
            if os.stat(f"/proc/{pid}").st_uid != os.getuid():
                continue
            yield int(pid), comm, rss
        except (OSError, ValueError, StopIteration):
            continue


def _killer(pid, sig):
    def run():
        try:
            os.kill(pid, sig)
            return f"Sent {signal.Signals(sig).name} to {pid}"
        except OSError as e:
            return f"Could not kill {pid}: {e}"
    return run


def search(query, api):
    q = query.strip()
    out = []
    for pid, comm, rss in _procs():
        s = api.fuzzy.score(q, comm) if q else 100
        if s is None:
            continue
        out.append(api.Result(
            id=f"proc:{comm}", title=comm, subtitle=f"PID {pid} · {rss // 1024} MB",
            icon="utilities-system-monitor-symbolic", category="Process", score=s + rss / 1e6, learn=False,
            actions=[api.Action("Quit (SIGTERM)", "callback", _killer(pid, signal.SIGTERM), confirm=True, close=False),
                     api.Action("Force Kill (SIGKILL)", "callback", _killer(pid, signal.SIGKILL), confirm=True, close=False),
                     api.Action("Copy PID", "copy", str(pid))]))
    out.sort(key=lambda r: -r.score)
    return out[:30]
