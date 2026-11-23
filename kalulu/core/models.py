"""Data types shared by the engine, providers and UI. No GTK imports here."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Action:
    """Something a result can do.

    kind:
      launch_app   payload = {"path": desktop_file, "action": optional desktop action id}
      open_uri     payload = uri / path
      copy         payload = text
      run          payload = argv list (background, detached)
      terminal     payload = shell command string (opens in terminal)
      script       payload = {"path": script, "args": [...], "mode": "silent|compact|fullOutput"}
      callback     payload = callable() -> Optional[str]  (str is shown as a toast)
      detail       payload = text to show inside the launcher
    """

    title: str
    kind: str
    payload: Any = None
    shortcut: str = ""          # display hint, e.g. "Ctrl+↵"
    confirm: bool = False       # require a second ↵
    close: bool = True          # hide launcher after running


@dataclass
class Result:
    id: str                      # stable key used for frecency
    title: str
    subtitle: str = ""
    icon: str = "system-search-symbolic"   # icon name or absolute path
    category: str = ""
    actions: list[Action] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    score: float = 0.0
    learn: bool = True           # record in frecency when used

    @property
    def primary(self) -> Optional[Action]:
        return self.actions[0] if self.actions else None


Callback = Callable[[], Optional[str]]
