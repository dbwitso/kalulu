#!/usr/bin/env bash
# @kalulu.title Copy Public IP
# @kalulu.subtitle Networking
# @kalulu.mode compact
# @kalulu.icon 🌍
ip=$(curl -fsS --max-time 5 https://api.ipify.org) || { echo "offline?"; exit 1; }
printf '%s' "$ip" | wl-copy 2>/dev/null || printf '%s' "$ip" | xclip -selection clipboard 2>/dev/null
echo "Public IP $ip (copied)"
