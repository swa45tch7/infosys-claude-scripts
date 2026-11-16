#!/bin/bash
# Double-click to make a LogicMonitor health check report on macOS.
# Finds Python 3.9+, sets up a private .venv in this folder, then runs the guided start.
cd "$(dirname "$0")" || exit 1
clear

finish() {
  echo
  echo "$1"
  echo
  read -r -p "Press Enter to close this window." _
  exit "${2:-0}"
}

python_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

find_python() {
  local candidate
  for candidate in \
      /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
      /Library/Frameworks/Python.framework/Versions/3.*/bin/python3 \
      /opt/homebrew/bin/python3 \
      /usr/local/bin/python3; do
    if [ -x "$candidate" ] && python_ok "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done
  # Apple's /usr/bin/python3 pops up an install dialog unless the developer tools exist.
  if [ "$(uname)" = "Darwin" ]; then
    if xcode-select -p >/dev/null 2>&1 && python_ok /usr/bin/python3; then
      echo /usr/bin/python3
      return 0
    fi
  elif command -v python3 >/dev/null 2>&1 && python_ok python3; then
    command -v python3
    return 0
  fi
  return 1
}

PY="$(find_python)"
if [ -z "$PY" ]; then
  echo "Python is needed. The download page will open now. Install it, then double-click this file again."
  open "https://www.python.org/downloads/" 2>/dev/null || xdg-open "https://www.python.org/downloads/" 2>/dev/null
  finish "Not finished: Python is not installed yet." 1
fi

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  echo "Getting ready. This is only slow the first time..."
  rm -rf .venv
  "$PY" -m venv .venv >/dev/null 2>&1 || finish "Not finished: could not set up Python in this folder." 1
fi

if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Installing what the report needs. Please wait..."
  .venv/bin/python -m pip install -q --disable-pip-version-check --upgrade pip >/dev/null 2>&1
  .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt \
    || finish "Not finished: the install failed. Check your internet connection and try again." 1
  cp requirements.txt .venv/requirements.installed
fi

clear
.venv/bin/python app/easy_start.py
finish "" $?
