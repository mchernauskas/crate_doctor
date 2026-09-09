#!/bin/bash
# Double-click me. Sets up crate_doctor on a Mac.
cd "$(dirname "$0")"
echo
echo "crate_doctor setup for macOS"
echo "======================="
echo
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed."
  echo
  echo "Get it from  https://www.python.org/downloads/macos/  (the big yellow button),"
  echo "run the installer, then double-click this file again."
  echo
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi
# macOS without the developer tools has a python3 stub that only prints an install prompt
if ! python3 -c "import sys" >/dev/null 2>&1; then
  echo "macOS wants to install its command line tools first. Say yes to the dialog,"
  echo "wait for it to finish, then double-click this file again."
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi
python3 crate_doctor.py setup
echo
read -n 1 -s -r -p "Done. Press any key to close."
echo
