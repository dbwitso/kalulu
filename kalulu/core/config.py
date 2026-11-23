from __future__ import annotations

import tomllib
from copy import deepcopy

from . import paths

DEFAULT_TOML = """\
# Kalulu configuration. Reload with the "Reload Kalulu" command.

[general]
# Max results shown at once
max_results = 9
# Hide the launcher when it loses focus
hide_on_focus_loss = true
# Terminal used for shell commands and scripts ("auto" picks ptyxis, gnome-terminal, kgx, foot...)
terminal = "auto"

[web]
# {query} is replaced with the URL-encoded search text
search_url = "https://www.google.com/search?q={query}"
search_name = "Google"
# Show Wikipedia matches under local results as you type, and enable "? question" / "define word"
inline_answers = true
language = "en"

[packages]
# Sources searched by "install <name>"
sources = ["apt", "snap", "flatpak"]

[clipboard]
# Keep a history of what you copy, saved to disk so it survives reboots. Open it with "clip ".
# On GNOME Wayland this needs the small "Kalulu Clipboard" Shell extension that install.sh adds.
enabled = true
# Oldest unpinned entries are dropped past this many. Pinned entries are always kept.
max_items = 1000
# Also drop unpinned entries older than this many days (0 = keep until max_items pushes them out)
keep_days = 0

[lockscreen]
# iOS-style lock screen, drawn by the "Kalulu Lock Screen" GNOME Shell extension (install.sh adds it).
enabled = true
# Cut the subject out of the wallpaper so the clock sits behind it (on-device model, downloaded once)
depth_effect = true
# Leave empty to use your desktop wallpaper, or point at a picture just for the lock screen
wallpaper = ""
clock_font = "Ubuntu Sans"
clock_weight = 700
# Glass widgets under the clock, in order. Any of: "weather", "battery", "calendar"
widgets = ["weather", "battery", "calendar"]
# Show the title of your next calendar event (false shows just the time)
show_event_titles = true
# Weather location, e.g. "Lusaka". Empty uses the location set in GNOME Weather / the top-bar clock.
weather_city = ""
fahrenheit = false

[files]
# Folders searched by "f <name>" (when fd/plocate are not installed)
roots = ["~"]
max_depth = 6

# Quicklinks: type the keyword, a space, then your text. Delete or add freely.
[[quicklinks]]
keyword = "gh"
name = "Search GitHub"
url = "https://github.com/search?q={query}"
icon = "web-browser-symbolic"

[[quicklinks]]
keyword = "yt"
name = "Search YouTube"
url = "https://www.youtube.com/results?search_query={query}"
icon = "video-x-generic-symbolic"

[[quicklinks]]
keyword = "maps"
name = "Search Google Maps"
url = "https://www.google.com/maps/search/{query}"
icon = "mark-location-symbolic"

[[quicklinks]]
keyword = "pkg"
name = "Search Ubuntu Packages"
url = "https://packages.ubuntu.com/search?keywords={query}"
icon = "package-x-generic-symbolic"
"""

_DEFAULTS = tomllib.loads(DEFAULT_TOML)


def _merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> tuple[dict, str | None]:
    """Return (config, error). Writes the default file on first run."""
    paths.ensure_dirs()
    if not paths.CONFIG_FILE.exists():
        paths.CONFIG_FILE.write_text(DEFAULT_TOML)
    try:
        user = tomllib.loads(paths.CONFIG_FILE.read_text())
        return _merge(_DEFAULTS, user), None
    except (OSError, tomllib.TOMLDecodeError) as e:
        return deepcopy(_DEFAULTS), f"config.toml: {e}"
