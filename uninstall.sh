#!/usr/bin/env bash
# Removes Kalulu. Keeps ~/.config/kalulu (your scripts/plugins/config) unless --purge.
set -uo pipefail
APP_ID="dev.kalulu.Launcher"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/kalulu"
KB_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kalulu/"
KB_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"

"$HOME/.local/bin/kalulu" --quit >/dev/null 2>&1 || true

if command -v gsettings >/dev/null; then
  list=$(gsettings get $KB_SCHEMA custom-keybindings)
  newlist=$(python3 -c "import ast,sys; v=ast.literal_eval(sys.argv[1].replace('@as ','')); print([x for x in v if x!=sys.argv[2]])" "$list" "$KB_PATH")
  gsettings set $KB_SCHEMA custom-keybindings "$newlist"
  gsettings reset-recursively $KB_SCHEMA.custom-keybinding:$KB_PATH 2>/dev/null || true
  if [ -f "$CFG/.input-source-backup" ]; then
    gsettings set org.gnome.desktop.wm.keybindings switch-input-source "$(cat "$CFG/.input-source-backup")"
    rm -f "$CFG/.input-source-backup"
    echo "Restored Super+Space for input-source switching"
  fi
fi

rm -rf "$PREFIX/kalulu" "$HOME/.local/bin/kalulu" "$HOME/.local/bin/kalulu-toggle" \
       "$PREFIX/applications/$APP_ID.desktop" "$HOME/.config/autostart/$APP_ID.desktop" \
       "$PREFIX/icons/hicolor/scalable/apps/$APP_ID.svg"
rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/kalulu"
[ "${1:-}" = "--purge" ] && rm -rf "$CFG" && echo "Removed $CFG"
echo "Kalulu uninstalled."
