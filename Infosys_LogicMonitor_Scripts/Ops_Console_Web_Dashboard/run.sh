#!/usr/bin/env bash
# LogicMonitor Ops Console — macOS and Linux
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  echo "First run: setting up a local environment..."
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi
exec ./.venv/bin/python -m lm_console "$@"
