#!/usr/bin/env bash
# Linux / macOS：转发到 start.py（例：./scripts/start.sh stop）
cd "$(dirname "$0")/.." || exit 1
exec python start.py "$@"
