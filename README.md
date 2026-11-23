# Kalulu

A Raycast-style launcher for GNOME on Ubuntu 26.04 LTS. Native GTK4 + libadwaita, pure Python, no Electron.
It stays resident in the background, so the hotkey opens it instantly.

## Install

```bash
tar xzf kalulu-0.1.0.tar.gz && cd kalulu
./install.sh                              # hotkey: Super+Space
KALULU_HOTKEY='<Control>space' ./install.sh   # or any other binding
```

What the installer does (all per-user, `sudo` only for apt deps if missing):

- installs `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1` if they're not already there
- copies the app to `~/.local/share/kalulu`, adds `kalulu` and `kalulu-toggle` to `~/.local/bin`
- adds an app entry, an icon, and an autostart entry (`kalulu --background` at login)
- registers a GNOME custom shortcut that runs `kalulu-toggle`
- **Super+Space:** GNOME uses this by default to switch keyboard layouts. The installer removes just that one
  binding (layout switching stays on the XF86Keyboard key), saves the old value, and `uninstall.sh` restores it.
  If you switch keyboard layouts often, install with another hotkey instead.

Optional extras: `sudo apt install fd-find libnotify-bin wl-clipboard` for faster file search, notifications,
and the public-IP example script.

Uninstall: `./uninstall.sh` (keeps `~/.config/kalulu`) or `./uninstall.sh --purge`.

## Using it

| Type | Gets you |
|---|---|
| `fire`, `vsc`, `term` | Apps (fuzzy matching and acronyms; learns your picks, so `f` → Firefox after a few uses) |
| `12*12+sqrt(81)`, `15% of 2400`, `2^32`, `0xff + 1` | Calculator, ↵ copies the answer |
| `lock`, `sleep`, `dark`, `dnd`, `wifi`, `night`, `trash`, `restart` | System commands (destructive ones ask for a second ↵) |
| `f budget` | File search (fd → plocate → walking your home folder) |
| `~/` or `/etc/` | Browse folders: ↵ drills into a folder, Ctrl+↵ opens it |
| `> docker ps` | Shell: ↵ runs it in a terminal, Ctrl+↵ shows the output inside Kalulu |
| `:fire` | Emoji picker (copies) |
| `gh kalulu`, `yt …`, `maps …`, `pkg …` | Quicklinks (edit them in config) |
| `github.com/dbwitso` | Opens the URL |
| `kill chrome` | Process killer (example plugin) |
| anything else | Web search fallback |

**Keys:** ↑↓ / Ctrl+N/P move · ↵ run · Ctrl+↵ second action · **Ctrl+K action panel** · Alt+1…9 pick a row ·
Tab completes · Esc goes back / clears / hides · Ctrl+, settings · Ctrl+R reload.

CLI: `kalulu` (toggle) · `--show` · `--hide` · `--query ':'` (open prefilled, e.g. bind a second hotkey to
the emoji picker) · `--reload` · `--doctor` (lists apps whose icons can't be found) · `--quit`.

## Script Commands

Put any executable in `~/.config/kalulu/scripts/` and it becomes a command (run **Reload Kalulu** or press Ctrl+R):

```bash
#!/usr/bin/env bash
# @kalulu.title Restart NetBird
# @kalulu.subtitle Networking
# @kalulu.mode compact          # silent (notification) | compact (footer toast) | fullOutput (in launcher) | terminal
# @kalulu.icon network-vpn-symbolic   # icon name, image path next to the script, or an emoji
# @kalulu.keywords vpn wireguard
# @kalulu.argument1 {"placeholder": "peer"}   # optional; type "Restart NetBird <arg>". Separate multiple args with |
# @kalulu.needsConfirmation true
sudo -n systemctl restart netbird && echo "NetBird restarted"
```

Any language works (Python, Node, …). The `@raycast.` prefix is accepted too, so many existing Raycast script
commands run unchanged (the macOS-only ones won't, obviously). Examples installed: NetBird status, Docker containers,
top memory users, kill port, copy public IP.

## Plugins

For anything dynamic, drop a Python file in `~/.config/kalulu/plugins/`:

```python
NAME = "Hello"
PREFIX = "hi "        # optional: only runs when the query starts with this
SLOW = False          # True runs it off the UI thread (for network calls)

def search(query, api):
    return [api.Result(id="hello", title=f"Hello {query}", icon="face-smile-symbolic",
                       actions=[api.Action("Copy", "copy", f"Hello {query}"),
                                api.Action("Open site", "open_uri", "https://example.com")])]
```

Action kinds: `copy`, `open_uri`, `run` (argv list, detached), `terminal` (shell string), `callback`
(Python function; a returned string shows as a toast), `script`, `reveal`, `complete` (rewrites the query),
`detail` (shows text in the launcher). Set `confirm=True` for dangerous actions. See `examples/plugins/processes.py`.

## Config

`~/.config/kalulu/config.toml` (open it with **Kalulu Settings** or Ctrl+,): result count, hide on focus loss,
preferred terminal, web search engine, file search roots, quicklinks.

## How it fits GNOME/Wayland

- Wayland doesn't let apps grab global hotkeys, so the hotkey is a GNOME custom shortcut. It calls
  `kalulu-toggle`, which sends a single D-Bus `Activate` to the running instance (~30 ms) and forwards the
  activation token so Mutter gives the window focus.
- Single instance via `GApplication`. Apps launch through `Gio.DesktopAppInfo`, so you get proper startup
  notification, and desktop actions like "New Private Window" show up in the action panel.
- Reads `.desktop` files from apt, snap, flatpak and `~/.local` directly, refreshing when those folders change.
- Frecency lives in `~/.local/state/kalulu/frecency.json`, and logs in `kalulu.log` in the same folder.

## Not yet (good next steps)

Clipboard history (GNOME doesn't expose a background clipboard watcher to normal apps on Wayland; needs a small
Shell extension), window switching (same reason), snippets/text expansion, a settings GUI.

## Dev

```bash
python3 tests/test_core.py          # engine tests, no display needed
PYTHONPATH=. python3 -m kalulu --show
```
