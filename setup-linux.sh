#!/bin/bash
# Sets up crate_doctor on Linux. Rekordbox itself does not run on Linux, so this is for
# people working on a copied library. Point it at master.db with:  ./setup-linux.sh --db /path
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. On Debian/Ubuntu:  sudo apt install python3 python3-pip python3-venv"
  exit 1
fi
python3 crate_doctor.py setup "$@"
