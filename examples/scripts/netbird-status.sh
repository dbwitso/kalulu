#!/usr/bin/env bash
# @kalulu.title NetBird Status
# @kalulu.subtitle Networking
# @kalulu.mode fullOutput
# @kalulu.icon network-vpn-symbolic
# @kalulu.keywords vpn wireguard peers
netbird status -d 2>&1 || echo "netbird not installed or not running"
