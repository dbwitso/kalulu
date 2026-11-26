"""Window switching and tiling, backed by the "Kalulu Windows" Shell extension.

  w <name>        switch to an open window (all workspaces, most recent first)
  just type       open windows also appear among normal results; apps that are already running
                  switch to their window instead of starting a second copy
  left half, …    tiling commands act on the window you were using before you opened Kalulu
"""
from __future__ import annotations

from ..core import fuzzy
from ..core.models import Action, Result
from .base import Provider

PREFIX = "w "

# layout, title, keywords, icon, default shortcut label
TILES = [
    ("left-half", "Left Half", "tile snap split side", "view-dual-symbolic", "Ctrl Super ←"),
    ("right-half", "Right Half", "tile snap split side", "view-dual-symbolic", "Ctrl Super →"),
    ("top-half", "Top Half", "tile snap split", "view-paged-symbolic", ""),
    ("bottom-half", "Bottom Half", "tile snap split", "view-paged-symbolic", ""),
    ("maximize", "Maximize Window", "fill full zoom", "window-maximize-symbolic", "Ctrl Super ↑"),
    ("almost-maximize", "Almost Maximize", "fill nearly", "window-maximize-symbolic", "Ctrl Super ↵"),
    ("center", "Centre Window", "center middle", "focus-windows-symbolic", "Ctrl Super C"),
    ("restore", "Restore Window Size", "unmaximize original undo", "window-restore-symbolic", "Ctrl Super ↓"),
    ("top-left", "Top Left Quarter", "tile corner", "view-grid-symbolic", "Ctrl Super U"),
    ("top-right", "Top Right Quarter", "tile corner", "view-grid-symbolic", "Ctrl Super I"),
    ("bottom-left", "Bottom Left Quarter", "tile corner", "view-grid-symbolic", "Ctrl Super J"),
    ("bottom-right", "Bottom Right Quarter", "tile corner", "view-grid-symbolic", "Ctrl Super K"),
    ("first-third", "First Third", "tile column left", "view-continuous-symbolic", "Ctrl Super D"),
    ("center-third", "Centre Third", "tile column middle", "view-continuous-symbolic", "Ctrl Super F"),
    ("last-third", "Last Third", "tile column right", "view-continuous-symbolic", "Ctrl Super G"),
    ("first-two-thirds", "First Two Thirds", "tile wide left", "view-continuous-symbolic", "Ctrl Super E"),
    ("last-two-thirds", "Last Two Thirds", "tile wide right", "view-continuous-symbolic", "Ctrl Super T"),
    ("larger", "Make Window Larger", "grow bigger resize", "zoom-in-symbolic", "Ctrl Super ="),
    ("smaller", "Make Window Smaller", "shrink resize", "zoom-out-symbolic", "Ctrl Super −"),
    ("next-display", "Move to Next Display", "monitor screen external", "video-display-symbolic", "Ctrl Super N"),
]


def _icon(w: dict) -> str:
    if w.get("icon"):
        return w["icon"]
    if w.get("app_id"):
        return w["app_id"].removesuffix(".desktop")
    return "focus-windows-symbolic"


class WindowsProvider(Provider):
    name = "windows"
    prefixes = ()
    weight = 0.0

    @property
    def bridge(self):
        return self.ctx.windows

    def _tile_action(self, layout: str, title: str, wid: int = 0) -> Action:
        bridge = self.bridge

        def run(layout=layout, wid=wid):
            return bridge.tile(layout, wid) or None
        return Action(title, "callback", run)

    # -------------------------------------------------------------- tiling commands (static, fuzzy-matched)
    def items(self) -> list[Result]:
        if not self.bridge or not self.bridge.available():
            return []
        show_keys = self.ctx.config.get("windows", {}).get("hotkeys", True)
        out = []
        for layout, title, kw, icon, keys in TILES:
            out.append(Result(
                id=f"tile:{layout}", title=title,
                subtitle="Arrange the window you were using" + (f"  ·  {keys}" if keys and show_keys else ""),
                icon=icon, category="Window", keywords=kw.split() + ["window", "tile", "snap", "rectangle"],
                actions=[self._tile_action(layout, title)]))
        return out

    # -------------------------------------------------------------- open windows
    def query(self, text: str) -> list[Result]:
        exclusive = bool(self.prefixes) and text.startswith(PREFIX)
        term = text[len(PREFIX):].strip() if exclusive else text.strip()
        if not exclusive and len(term) < 2:
            return []
        data = self.bridge.list() if self.bridge else None
        if data is None:
            if not exclusive:
                return []
            return [Result(id="win:missing", title="Window switching needs the Kalulu Windows extension",
                           subtitle="Run ./install.sh, then log out and back in once",
                           icon="focus-windows-symbolic", category="Windows", learn=False, score=1000)]
        cur_ws = data.get("workspace", 0)
        out = []
        for i, w in enumerate(data.get("windows", [])):
            if term:
                s = fuzzy.best(term, w.get("title", ""), w.get("app_name", ""), weights=(1.0, 0.95))
                if s is None or (not exclusive and s < 600):
                    continue
            else:
                s = 1000.0
            s -= i * 0.5                                  # most recent first among equals
            out.append(self._result(w, s - (0 if exclusive else 260), cur_ws))
        return out[: (60 if exclusive else 4)]

    def _result(self, w: dict, score: float, cur_ws: int) -> Result:
        bridge, wid = self.bridge, int(w["id"])
        bits = [w.get("app_name") or w.get("wm_class") or "Window"]
        if w.get("workspace", cur_ws) not in (cur_ws, -1):
            bits.append(f"Workspace {w['workspace'] + 1}")
        if w.get("minimized"):
            bits.append("minimised")

        def closer():
            return ("refresh", "Closed" if bridge.close(wid) else "Couldn't close that window")

        def minimizer():
            return "Minimised" if bridge.minimize(wid) else "That window can't be minimised"

        def quitter():
            return ("refresh", "Asked the app to quit" if bridge.quit_app(wid) else "Couldn't quit that app")

        actions = [
            Action("Switch to Window", "callback", lambda: None if bridge.activate(wid) else "That window is gone"),
            self._tile_action("left-half", "Move to Left Half", wid),
            self._tile_action("right-half", "Move to Right Half", wid),
            self._tile_action("maximize", "Maximize", wid),
            Action("Minimise", "callback", minimizer),
            Action("Close Window", "callback", closer, close=False),
            Action(f"Quit {bits[0]}", "callback", quitter, confirm=True, close=False),
            Action("Copy Window Title", "copy", w.get("title", "")),
        ]
        return Result(id=f"win:{wid}", title=w.get("title") or bits[0], subtitle="  ·  ".join(bits),
                      icon=_icon(w), category="Window", actions=actions, score=score, learn=False)


def mark_running(results: list[Result], bridge) -> list[Result]:
    """App results whose app already has a window switch to it (↵); 'Open New Window' stays available."""
    apps = [r for r in results if r.id.startswith("app:")]
    if not apps or not bridge:
        return results
    data = bridge.list()
    if not data:
        return results
    first: dict[str, dict] = {}
    for w in data.get("windows", []):                 # MRU order: first hit is the most recent window
        if w.get("app_id"):
            first.setdefault(w["app_id"], w)
    if not first:
        return results
    out = []
    for r in results:
        w = first.get(r.id[4:]) if r.id.startswith("app:") else None
        if w is None:
            out.append(r)
            continue
        wid = int(w["id"])
        r2 = Result(**{**r.__dict__})
        launch = [Action("Open New Window" if a.title == "Open Application" else a.title, a.kind, a.payload,
                         a.shortcut, a.confirm, a.close) for a in r.actions]
        r2.actions = [Action("Switch to Window", "callback",
                             lambda wid=wid: None if bridge.activate(wid) else "That window is gone")] + launch
        r2.category = "Running"
        out.append(r2)
    return out


class WindowSwitcherProvider(WindowsProvider):
    """'w <name>': only windows, all of them, most recent first."""

    name = "window-switcher"
    prefixes = (PREFIX,)
    exclusive = True

    def items(self) -> list[Result]:
        return []
