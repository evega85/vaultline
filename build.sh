#!/usr/bin/env bash
# Build Vaultline and vaultline-cli into ./dist (or the folder given as the first argument).
# macOS / Linux builds are EXPERIMENTAL and untested: Vaultline is developed and tested on Windows.
set -euo pipefail
cd "$(dirname "$0")"
OUT="${1:-dist}"

if [ ! -x .venv/bin/python ]; then
  echo "Creating a private Python environment in .venv ..."
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

.venv/bin/pyinstaller --noconfirm --onefile --windowed --name Vaultline \
  --distpath "$OUT" --workpath build/pyinstaller \
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQml \
  --exclude-module PySide6.QtQuick --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtPdf \
  --collect-data pykeepass \
  run.py
.venv/bin/pyinstaller --noconfirm --onefile --console --name vaultline-cli \
  --distpath "$OUT" --workpath build/pyinstaller \
  --exclude-module PySide6 --exclude-module pykeepass --exclude-module lxml \
  run_cli.py

echo
echo "Done: $OUT/Vaultline and $OUT/vaultline-cli"
echo "Note: macOS/Linux builds are experimental. The command line (vaultline-cli) currently works on Windows only."
