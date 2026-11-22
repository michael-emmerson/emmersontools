#!/bin/bash
# Double-click on a Mac to start RRR Coffee Inventory. Keep this window open while you use the app.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed. Install it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Return to close." _; exit 1
fi
if [ ! -d .venv ]; then
  echo "First run: setting up (takes a few seconds)…"
  python3 -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt || { read -r -p "Setup failed. Press Return to close." _; exit 1; }
fi
.venv/bin/python app.py "$@"
