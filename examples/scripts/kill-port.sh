#!/usr/bin/env bash
# @kalulu.title Kill Port
# @kalulu.subtitle Dev
# @kalulu.mode compact
# @kalulu.icon process-stop-symbolic
# @kalulu.argument1 {"placeholder": "port"}
# @kalulu.needsConfirmation true
port="${1//[^0-9]/}"
[ -n "$port" ] || { echo "give a port number"; exit 1; }
pids=$(ss -ltnpH "sport = :$port" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)
[ -n "$pids" ] || { echo "nothing listening on :$port"; exit 0; }
kill $pids && echo "killed $(echo $pids | wc -w) process(es) on :$port"
