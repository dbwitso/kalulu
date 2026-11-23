#!/usr/bin/env bash
# Kalulu installer for Ubuntu (GNOME, Wayland or X11). Installs per-user, no root except for apt deps.
#
#   ./install.sh                       # default hotkey Super+Space
#   KALULU_HOTKEY='<Alt>space' ./install.sh
#   KALULU_HOTKEY=none ./install.sh    # skip hotkey setup
#   KALULU_CLIP_HOTKEY='<Super><Shift>v' (default) opens clipboard history; 'none' to skip
#   KALULU_CLIPBOARD=off ./install.sh  # don't install the GNOME Shell clipboard extension
#   KALULU_LOCKSCREEN=off ./install.sh # don't install the iOS-style lock screen
set -euo pipefail

APP_ID="dev.kalulu.Launcher"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
LIB="$PREFIX/kalulu"
BIN="$HOME/.local/bin"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/kalulu"
HOTKEY="${KALULU_HOTKEY:-<Super>space}"
CLIP_HOTKEY="${KALULU_CLIP_HOTKEY:-<Super><Shift>v}"
EXT_UUID="kalulu-clipboard@kalulu.dev"
LOCK_UUID="kalulu-lockscreen@kalulu.dev"
KB_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kalulu/"
KB_CLIP_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kalulu-clipboard/"
KB_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"

say()  { printf '\033[1;33m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------- dependencies
PY="${KALULU_PYTHON:-/usr/bin/python3}"
if ! "$PY" -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1"); from gi.repository import Gtk, Adw' 2>/dev/null; then
  say "Installing GTK4 + libadwaita Python bindings (needs sudo)"
  sudo apt-get install -y python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
fi
"$PY" -c 'import sys; assert sys.version_info >= (3, 11)' || { warn "Python 3.11+ required"; exit 1; }
ok "GTK4 / libadwaita available"

if [ "${KALULU_LOCKSCREEN:-on}" != "off" ] && ! "$PY" -c 'import cv2, numpy, venv, ensurepip' 2>/dev/null; then
  say "Installing OpenCV + numpy for the lock screen's wallpaper cut-out (needs sudo)"
  sudo apt-get install -y python3-opencv python3-numpy python3-venv || warn "Skipped; the lock screen will work without the depth effect"
fi

missing=()
command -v fdfind >/dev/null || command -v fd >/dev/null || missing+=(fd-find)
command -v notify-send >/dev/null || missing+=(libnotify-bin)
if ((${#missing[@]})); then
  say "Optional: 'sudo apt install ${missing[*]}' for faster file search / notifications"
fi

# ---------------------------------------------------------------- files
say "Installing to $LIB"
rm -rf "$LIB"
mkdir -p "$LIB" "$BIN" "$CFG/scripts" "$CFG/plugins"
cp -r "$SRC/kalulu" "$LIB/"
find "$LIB" -name '__pycache__' -prune -exec rm -rf {} +

cat > "$BIN/kalulu" <<EOF
#!/bin/sh
PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}" exec $PY -m kalulu "\$@"
EOF
chmod +x "$BIN/kalulu"

# Fast path for the hotkey: poke the resident instance over D-Bus (~10 ms) instead of
# starting Python. Falls back to the full client if Kalulu is not running yet.
#   kalulu-toggle           toggle the launcher
#   kalulu-toggle 'clip '   open it prefilled (used by the clipboard hotkey)
cat > "$BIN/kalulu-toggle" <<EOF
#!/bin/sh
tok="\${XDG_ACTIVATION_TOKEN:-\$DESKTOP_STARTUP_ID}"
pd="{'activation-token': <'\$tok'>, 'desktop-startup-id': <'\$tok'>}"
if [ -n "\$1" ]; then
  gdbus call --session --dest $APP_ID --object-path /dev/kalulu/Launcher \\
    --method org.gtk.Actions.Activate show-query "[<'\$1'>]" "\$pd" >/dev/null 2>&1 \\
    || exec "$BIN/kalulu" --query "\$1"
else
  gdbus call --session --dest $APP_ID --object-path /dev/kalulu/Launcher \\
    --method org.gtk.Application.Activate "\$pd" >/dev/null 2>&1 \\
    || exec "$BIN/kalulu"
fi
EOF
chmod +x "$BIN/kalulu-toggle"

install -Dm644 "$SRC/data/$APP_ID.svg" "$PREFIX/icons/hicolor/scalable/apps/$APP_ID.svg"
gtk-update-icon-cache -q -t "$PREFIX/icons/hicolor" 2>/dev/null || true

DESKTOP="[Desktop Entry]
Type=Application
Name=Kalulu
GenericName=Launcher
Comment=Keyboard launcher for apps, files, commands and scripts
Exec=$BIN/kalulu --show
Icon=$APP_ID
Terminal=false
Categories=Utility;
Keywords=launcher;raycast;spotlight;search;
StartupNotify=false
X-GNOME-UsesNotifications=true"
mkdir -p "$PREFIX/applications" "$HOME/.config/autostart"
printf '%s\n' "$DESKTOP" > "$PREFIX/applications/$APP_ID.desktop"
printf '%s\nExec=%s --background\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n' \
  "$(printf '%s\n' "$DESKTOP" | grep -v '^Exec=')" "$BIN/kalulu" > "$HOME/.config/autostart/$APP_ID.desktop"
update-desktop-database -q "$PREFIX/applications" 2>/dev/null || true
ok "Launcher, icon and autostart entry installed"

# example script commands + plugins (never overwrite your own)
for f in "$SRC"/examples/scripts/*; do
  [ -e "$CFG/scripts/$(basename "$f")" ] || install -m755 "$f" "$CFG/scripts/"
done
for f in "$SRC"/examples/plugins/*.py; do
  [ -e "$CFG/plugins/$(basename "$f")" ] || install -m644 "$f" "$CFG/plugins/"
done
ok "Example scripts → $CFG/scripts, plugins → $CFG/plugins"

# ---------------------------------------------------------------- GNOME Shell extensions
# Clipboard: GNOME on Wayland only lets the focused window see clipboard changes, so a tiny Shell
# extension forwards each copy to Kalulu. Lock screen: only a Shell extension can draw on it.
EXT_NEW=0
install_ext() {   # uuid
  local dir="$PREFIX/gnome-shell/extensions/$1" enabled newlist
  [ -d "$dir" ] || EXT_NEW=1
  rm -rf "$dir"
  mkdir -p "$dir"
  cp "$SRC/data/gnome-shell/$1"/* "$dir/"
  enabled=$(gsettings get org.gnome.shell enabled-extensions)
  newlist=$("$PY" -c "import ast,sys; v=ast.literal_eval(sys.argv[1].replace('@as ','')); u=sys.argv[2]; print(v if u in v else v+[u])" "$enabled" "$1")
  gsettings set org.gnome.shell enabled-extensions "$newlist"
  gnome-extensions enable "$1" >/dev/null 2>&1 || true
}
if [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]] && command -v gsettings >/dev/null; then
  if [ "${KALULU_CLIPBOARD:-on}" != "off" ]; then
    install_ext "$EXT_UUID"
    ok "Clipboard extension installed ($EXT_UUID)"
  fi
  if [ "${KALULU_LOCKSCREEN:-on}" != "off" ]; then
    install_ext "$LOCK_UUID"
    ok "Lock screen extension installed ($LOCK_UUID)"
    say "Preparing the lock screen's depth effect (one-off download, about 60 MB)"
    "$BIN/kalulu" --lockscreen-setup || warn "Depth effect unavailable for now; the lock screen still works"
  fi
  if [ "$(gsettings get org.gnome.shell disable-user-extensions)" = "true" ]; then
    warn "User extensions are switched off in GNOME; turn them on in the Extensions app"
  fi
fi

# ---------------------------------------------------------------- hotkey
add_binding() {   # path name command binding
  local list newlist
  list=$(gsettings get $KB_SCHEMA custom-keybindings)
  newlist=$("$PY" -c "import ast,sys; v=ast.literal_eval(sys.argv[1].replace('@as ','')); p=sys.argv[2]; v=v if p in v else v+[p]; print(v)" "$list" "$1")
  gsettings set $KB_SCHEMA custom-keybindings "$newlist"
  gsettings set $KB_SCHEMA.custom-keybinding:$1 name "$2"
  gsettings set $KB_SCHEMA.custom-keybinding:$1 command "$3"
  gsettings set $KB_SCHEMA.custom-keybinding:$1 binding "$4"
}

if [ "$HOTKEY" != "none" ] && command -v gsettings >/dev/null && gsettings list-schemas 2>/dev/null | grep -qx "$KB_SCHEMA"; then
  if [ "$HOTKEY" = "<Super>space" ]; then
    # Super+Space is GNOME's "switch input source" by default. Free it (backed up for uninstall).
    cur=$(gsettings get org.gnome.desktop.wm.keybindings switch-input-source)
    if [[ "$cur" == *"<Super>space"* ]]; then
      [ -f "$CFG/.input-source-backup" ] || echo "$cur" > "$CFG/.input-source-backup"
      new=$("$PY" -c "import ast,sys; v=ast.literal_eval(sys.argv[1].replace('@as ','')); print([k for k in v if k!='<Super>space'])" "$cur")
      gsettings set org.gnome.desktop.wm.keybindings switch-input-source "$new"
      say "Moved input-source switching off Super+Space (it stays on XF86Keyboard; restore with uninstall.sh)"
    fi
  fi
  add_binding "$KB_PATH" "Kalulu" "$BIN/kalulu-toggle" "$HOTKEY"
  ok "Hotkey $HOTKEY → Kalulu (change it in Settings › Keyboard › Custom Shortcuts)"
fi
if [ "$CLIP_HOTKEY" != "none" ] && command -v gsettings >/dev/null && gsettings list-schemas 2>/dev/null | grep -qx "$KB_SCHEMA"; then
  add_binding "$KB_CLIP_PATH" "Kalulu Clipboard History" "$BIN/kalulu-toggle 'clip '" "$CLIP_HOTKEY"
  ok "Hotkey $CLIP_HOTKEY → Clipboard History"
fi

# ---------------------------------------------------------------- start
"$BIN/kalulu" --quit >/dev/null 2>&1 || true
sleep 0.3
setsid -f "$BIN/kalulu" --background >/dev/null 2>&1 < /dev/null
ok "Kalulu is running. Press ${HOTKEY/none/your hotkey} to open it."
if [ "$EXT_NEW" = 1 ]; then
  say "Log out and back in once so GNOME loads Kalulu's Shell extensions (Wayland can't reload the Shell live)."
fi
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not on your PATH; add it to use 'kalulu' from a terminal";; esac
