#!/usr/bin/env bash
# Sets up a virtualenv on first run, then starts Sunbird.
#   ./run.sh            → connect to the SigenStor in config.json
#   ./run.sh --demo     → simulated SigenStor, no hardware needed
#   ./run.sh --check    → test the Modbus connection and find inverter IDs
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "First run: creating virtualenv and installing dependencies…"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

if [ "${1:-}" = "--check" ]; then
  shift
  exec .venv/bin/python tools/check_connection.py "$@"
fi
exec .venv/bin/python -m sunbird "$@"
