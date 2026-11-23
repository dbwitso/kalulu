#!/usr/bin/env bash
# @kalulu.title Top Memory Users
# @kalulu.subtitle System
# @kalulu.mode fullOutput
# @kalulu.icon utilities-system-monitor-symbolic
# @kalulu.keywords ram processes zram
free -h; echo; zramctl 2>/dev/null; echo
ps -eo pid,rss,comm --sort=-rss | head -15 | awk 'NR==1{printf "%-8s %-9s %s\n","PID","RSS","COMMAND";next}{printf "%-8s %-9s %s\n",$1,int($2/1024)"M",$3}'
