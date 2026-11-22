#!/bin/bash
# Double-click this file to start the Just Shoot Me Receipt Maker.
# It opens the app in your web browser. Keep this window open while you use it.
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed. Run:  xcode-select --install   then try again."
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi
exec python3 server.py
