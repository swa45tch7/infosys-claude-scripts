#!/usr/bin/env bash
# macOS / Linux launcher. Double-click in Finder, or run from Terminal with extra options:
#   ./run_health_check.command --customer "Contoso" --contract-devices 1500
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.9 or newer was not found. Install it from https://www.python.org/downloads/ (README.md, step 1)."
  read -r -p "Press Enter to close." _
  exit 1
fi

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  echo "Creating a private Python environment in .venv ..."
  rm -rf .venv
  if ! python3 -m venv .venv; then
    rm -rf .venv
    echo "Could not create the Python environment. On Debian/Ubuntu run: sudo apt install python3-venv"
    if [ -t 0 ]; then read -r -p "Press Enter to close." _; fi
    exit 1
  fi
fi

.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
.venv/bin/python lm_health_report.py "$@" || true
echo
if [ -t 0 ]; then read -r -p "Press Enter to close." _; fi
