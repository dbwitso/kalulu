#!/usr/bin/env bash
# @kalulu.title Docker: Running Containers
# @kalulu.subtitle Docker
# @kalulu.mode fullOutput
# @kalulu.icon 🐳
# @kalulu.keywords containers compose
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1
