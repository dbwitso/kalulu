"""System and Kalulu commands for GNOME."""
from __future__ import annotations

import shutil

from ..core import paths
from ..core.models import Action, Result
from ..core.system import run_capture
from .base import Provider


def _gs_get(schema: str, key: str) -> str:
    _, out = run_capture(["gsettings", "get", schema, key], timeout=3)
    return out.strip().strip("'")


def _gs_set(schema: str, key: str, value: str) -> None:
    run_capture(["gsettings", "set", schema, key, value], timeout=3)


def toggle_dark() -> str:
    cur = _gs_get("org.gnome.desktop.interface", "color-scheme")
    new = "default" if cur == "prefer-dark" else "prefer-dark"
    _gs_set("org.gnome.desktop.interface", "color-scheme", new)
    return "Dark style on" if new == "prefer-dark" else "Dark style off"


def toggle_dnd() -> str:
    cur = _gs_get("org.gnome.desktop.notifications", "show-banners")
    new = "false" if cur == "true" else "true"
    _gs_set("org.gnome.desktop.notifications", "show-banners", new)
    return "Do Not Disturb on" if new == "false" else "Do Not Disturb off"


def toggle_night_light() -> str:
    schema = "org.gnome.settings-daemon.plugins.color"
    cur = _gs_get(schema, "night-light-enabled")
    new = "false" if cur == "true" else "true"
    _gs_set(schema, "night-light-enabled", new)
    return "Night Light on" if new == "true" else "Night Light off"


def toggle_wifi() -> str:
    _, cur = run_capture(["nmcli", "radio", "wifi"], timeout=3)
    new = "off" if cur.strip() == "enabled" else "on"
    run_capture(["nmcli", "radio", "wifi", new], timeout=5)
    return f"Wi-Fi {new}"


def toggle_bluetooth() -> str:
    _, out = run_capture(["bluetoothctl", "show"], timeout=3)
    on = "Powered: yes" in out
    run_capture(["bluetoothctl", "power", "off" if on else "on"], timeout=5)
    return f"Bluetooth {'off' if on else 'on'}"


def empty_trash() -> str:
    code, out = run_capture(["gio", "trash", "--empty"], timeout=60)
    return "Trash emptied" if code == 0 else f"Could not empty trash: {out}"


def _cmd(id_, title, subtitle, icon, actions, keywords=()):
    return Result(id=f"cmd:{id_}", title=title, subtitle=subtitle, icon=icon,
                  category="Command", actions=actions, keywords=list(keywords))


def _have(b: str) -> bool:
    return shutil.which(b) is not None


class SystemCommandsProvider(Provider):
    name = "system"
    weight = 20.0

    def items(self) -> list[Result]:
        r = [
            _cmd("lock", "Lock Screen", "System", "system-lock-screen-symbolic",
                 [Action("Lock", "run", ["loginctl", "lock-session"])], ["lock"]),
            _cmd("sleep", "Sleep", "Suspend the computer", "weather-clear-night-symbolic",
                 [Action("Sleep", "run", ["systemctl", "suspend"])], ["suspend"]),
            _cmd("logout", "Log Out", "End your session", "system-log-out-symbolic",
                 [Action("Log Out", "run", ["gnome-session-quit", "--logout", "--no-prompt"], confirm=True)],
                 ["sign out", "logoff"]),
            _cmd("restart", "Restart", "Reboot the computer", "system-reboot-symbolic",
                 [Action("Restart", "run", ["systemctl", "reboot"], confirm=True)], ["reboot"]),
            _cmd("shutdown", "Shut Down", "Power off the computer", "system-shutdown-symbolic",
                 [Action("Shut Down", "run", ["systemctl", "poweroff"], confirm=True)], ["power off", "turn off"]),
            _cmd("dark", "Toggle Dark Mode", "Appearance", "weather-clear-night-symbolic",
                 [Action("Toggle", "callback", toggle_dark, close=False)], ["theme", "light mode", "appearance"]),
            _cmd("dnd", "Toggle Do Not Disturb", "Notifications", "notifications-disabled-symbolic",
                 [Action("Toggle", "callback", toggle_dnd, close=False)], ["notifications", "focus"]),
            _cmd("night", "Toggle Night Light", "Display", "night-light-symbolic",
                 [Action("Toggle", "callback", toggle_night_light, close=False)], ["blue light", "warm"]),
            _cmd("trash", "Empty Trash", "Files", "user-trash-full-symbolic",
                 [Action("Empty Trash", "callback", empty_trash, confirm=True)], ["bin", "recycle"]),
            _cmd("settings", "Settings", "GNOME Settings", "preferences-system-symbolic",
                 [Action("Open", "run", ["gnome-control-center"])], ["preferences", "control center"]),
            _cmd("screenshot", "Take Screenshot", "Interactive screenshot", "applets-screenshooter-symbolic",
                 [Action("Screenshot", "run", ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                                               "--object-path", "/org/gnome/Shell",
                                               "--method", "org.gnome.Shell.Eval", "Main.screenshotUI.open()"])],
                 ["capture", "screen"]),
            _cmd("sysmon", "System Monitor", "Processes & resources", "utilities-system-monitor-symbolic",
                 [Action("Open", "run", ["gnome-system-monitor"])], ["task manager", "processes", "htop"]),
        ]
        upd = []
        if _have("apt"):
            upd.append("sudo apt update && sudo apt full-upgrade")
        if _have("snap"):
            upd.append("sudo snap refresh")
        if _have("flatpak"):
            upd.append("flatpak update")
        if upd:
            r.append(_cmd("update", "Update All Software", " · ".join(x.split()[1] if x.startswith("sudo") else x.split()[0] for x in upd),
                          "software-update-available-symbolic",
                          [Action("Update in Terminal", "terminal", " ; ".join(upd))],
                          ["upgrade", "apt upgrade", "snap refresh", "updates"]))
        if _have("nmcli"):
            r.append(_cmd("wifi", "Toggle Wi-Fi", "Network", "network-wireless-symbolic",
                          [Action("Toggle", "callback", toggle_wifi, close=False)], ["wireless", "network"]))
        if _have("bluetoothctl"):
            r.append(_cmd("bt", "Toggle Bluetooth", "Bluetooth", "bluetooth-active-symbolic",
                          [Action("Toggle", "callback", toggle_bluetooth, close=False)], ["bt"]))
        return r


class KaluluCommandsProvider(Provider):
    name = "kalulu"
    weight = 10.0

    def items(self) -> list[Result]:
        cfg = str(paths.CONFIG_FILE)
        return [
            Result(id="kalulu:config", title="Kalulu Settings", subtitle="Edit config.toml",
                   icon="preferences-other-symbolic", category="Kalulu", keywords=["preferences", "config"],
                   actions=[Action("Open config.toml", "open_uri", cfg),
                            Action("Copy Path", "copy", cfg)]),
            Result(id="kalulu:clipboard", title="Clipboard History", subtitle="Everything you've copied, kept after restarts",
                   icon="edit-paste-symbolic", category="Kalulu",
                   keywords=["clip", "clipboard", "paste", "copied", "history"],
                   actions=[Action("Open Clipboard History", "complete", "clip ", close=False)]),
            Result(id="kalulu:reload", title="Reload Kalulu", subtitle="Re-read config, apps, scripts and plugins",
                   icon="view-refresh-symbolic", category="Kalulu", keywords=["refresh"],
                   actions=[Action("Reload", "internal", "reload", close=False)]),
            Result(id="kalulu:scripts", title="Open Script Commands Folder", subtitle=str(paths.SCRIPTS_DIR),
                   icon="folder-symbolic", category="Kalulu", keywords=["scripts", "extensions"],
                   actions=[Action("Open Folder", "open_uri", str(paths.SCRIPTS_DIR))]),
            Result(id="kalulu:plugins", title="Open Plugins Folder", subtitle=str(paths.PLUGINS_DIR),
                   icon="folder-symbolic", category="Kalulu", keywords=["extensions"],
                   actions=[Action("Open Folder", "open_uri", str(paths.PLUGINS_DIR))]),
            Result(id="kalulu:quit", title="Quit Kalulu", subtitle="Stop the background launcher",
                   icon="application-exit-symbolic", category="Kalulu",
                   actions=[Action("Quit", "internal", "quit")]),
        ]


_PANELS = [
    ("wifi", "Wi-Fi", "network-wireless-symbolic", "wireless internet hotspot"),
    ("network", "Network", "network-wired-symbolic", "ethernet vpn proxy"),
    ("bluetooth", "Bluetooth", "bluetooth-active-symbolic", "pair devices headphones"),
    ("display", "Displays", "video-display-symbolic", "monitor resolution scale refresh rate"),
    ("sound", "Sound", "audio-speakers-symbolic", "volume microphone output input"),
    ("power", "Power", "battery-symbolic", "battery suspend performance"),
    ("multitasking", "Multitasking", "view-grid-symbolic", "workspaces hot corner"),
    ("background", "Appearance", "preferences-desktop-wallpaper-symbolic", "wallpaper background dark style"),
    ("ubuntu", "Ubuntu Desktop", "start-here-symbolic", "dock accent color"),
    ("notifications", "Notifications", "preferences-system-notifications-symbolic", "alerts banners"),
    ("search", "Search", "system-search-symbolic", "search providers"),
    ("applications", "Apps", "view-app-grid-symbolic", "default apps permissions"),
    ("privacy", "Privacy & Security", "preferences-system-privacy-symbolic", "location camera screen lock"),
    ("online-accounts", "Online Accounts", "goa-panel-symbolic", "google microsoft nextcloud"),
    ("sharing", "Sharing", "preferences-system-sharing-symbolic", "remote desktop file sharing"),
    ("mouse", "Mouse & Touchpad", "input-mouse-symbolic", "pointer scroll speed tap"),
    ("keyboard", "Keyboard", "input-keyboard-symbolic", "shortcuts layout input source"),
    ("color", "Color", "preferences-color-symbolic", "color profile calibration"),
    ("printers", "Printers", "printer-symbolic", "print scanner"),
    ("system", "System", "preferences-system-symbolic", "about users date time region language updates"),
]


class SettingsPanelsProvider(Provider):
    name = "settings-panels"
    weight = 15.0

    def items(self) -> list[Result]:
        if not _have("gnome-control-center"):
            return []
        return [Result(id=f"panel:{pid}", title=f"{title} Settings", subtitle="Settings",
                       icon=icon, category="Settings", keywords=kw.split() + [title.lower()],
                       actions=[Action("Open", "run", ["gnome-control-center", pid])])
                for pid, title, icon, kw in _PANELS]
