#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 scripts/configure.py
exec docker compose up --build -d --wait "$@"
