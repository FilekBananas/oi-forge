#!/bin/bash
# OI Forge — start na macOS (dwuklik w Finderze).
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Nie znaleziono Pythona 3. Zainstaluj go: https://www.python.org/downloads/ albo 'brew install python'."
  read -r -p "Naciśnij Enter, aby zamknąć..."
  exit 1
fi
exec "$PY" -m oiforge "$@"
