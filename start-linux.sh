#!/usr/bin/env bash
# OI Forge — start na Linuksie (i macOS z terminala): ./start-linux.sh [--port 8777] [--browser] [--no-browser]
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else
  echo "Nie znaleziono Pythona 3. Zainstaluj: sudo apt install python3 (Debian/Ubuntu) lub sudo dnf install python3 (Fedora)."
  exit 1
fi
if ! command -v g++ >/dev/null 2>&1 && ! command -v clang++ >/dev/null 2>&1; then
  echo "Uwaga: brak kompilatora C++ (g++). Zainstaluj: sudo apt install g++ gdb"
fi
exec "$PY" -m oiforge "$@"
