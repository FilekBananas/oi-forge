#!/usr/bin/env python3
"""Eksport publicznej wersji OI Forge (bez czyichkolwiek danych) do osobnego folderu.

Użycie (z katalogu głównego):
    python3 scripts/export_public.py ../oi-forge            # kopiuje i sprawdza
    python3 scripts/export_public.py ../oi-forge --check    # tylko sprawdza istniejący eksport

Do eksportu NIE trafia:
  * user_data/            — zaimportowane programy, treści zadań, ustawienia, sesja SIO2, projekty, dziennik,
  * *_private.py          — dane i testy autora (zapisane strony jego SIO2, kalibracja na jego programach),
  * cache, __pycache__, pliki systemowe i robocze (.DS_Store, .playwright-mcp, .git).
Potem skrypt przeszukuje kopię pod kątem śladów prywatnych danych (ścieżki z dysku, zgłoszenia, sesje, adresy e-mail)
i kończy się błędem, jeśli coś znajdzie.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {"user_data", "__pycache__", ".git", ".playwright-mcp", ".idea", ".vscode", "node_modules", ".venv",
                "venv"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}
EXCLUDE_GLOBS = ["*_private.py", "*.pyc", "*.pyo", "*.log"]          # *_private.py = dane/testy autora

# ślady, których nie może być w publicznym kodzie (poza wskazanymi plikami)
LEAKS = [
    (re.compile(r"/Users/[A-Za-z0-9._-]+/|/home/[a-z0-9._-]+/|C:\\\\Users\\\\[A-Za-z0-9._-]+\\\\", re.I),
     "ścieżka z czyjegoś dysku"),
    (re.compile(r"sessionid=[A-Za-z0-9]{20,}"), "ciasteczko sesji SIO2"),
    (re.compile(r"\b[\w.+-]+@(gmail|wp|onet|o2|interia|outlook|icloud|yahoo)\.[a-z.]+\b", re.I), "adres e-mail"),
    (re.compile(r"/c/[a-z0-9_-]+/s/\d{5,}/"), "adres prawdziwego zgłoszenia SIO2"),
    (re.compile(r"__\d{5,}\.(cpp|py)\b"), "plik prawdziwego zgłoszenia"),
]
LEAK_ALLOW = {"scripts/export_public.py"}
SAMPLE_PATHS = re.compile(r"/home/linuxbrew/|Users\\+(Jan|Ja|Uczen|Ania)\\+", re.I)   # zmyślone przykłady w testach/UI
TEXT_EXT = {".py", ".js", ".mjs", ".json", ".md", ".txt", ".css", ".html", ".sh", ".bat", ".command", ".cpp", ".h", ""}


def _excluded(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts) or rel.name in EXCLUDE_FILES:
        return True
    return any(rel.match(g) for g in EXCLUDE_GLOBS)


def export(dest: Path) -> int:
    if dest.resolve() == ROOT or ROOT in dest.resolve().parents:
        raise SystemExit("Folder docelowy nie może być wewnątrz projektu.")
    if dest.exists():
        keep_git = dest / ".git"
        for child in dest.iterdir():
            if child == keep_git:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT)
        dirnames[:] = [d for d in dirnames if not _excluded(rel_dir / d)]
        for f in filenames:
            rel = rel_dir / f
            if _excluded(rel):
                continue
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dest / rel)
            n += 1
    return n


def check(dest: Path) -> list[str]:
    problems = []
    for p in dest.rglob("*"):
        rel = p.relative_to(dest)
        if ".git" in rel.parts:
            continue
        if any(part == "user_data" for part in rel.parts):
            problems.append(f"{rel}: folder user_data nie może być w eksporcie")
            continue
        if p.is_file() and rel.match("*_private.py"):
            problems.append(f"{rel}: prywatny plik w eksporcie")
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT or str(rel) in LEAK_ALLOW or "vendor" in rel.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for rx, what in LEAKS:
            m = next((m for m in rx.finditer(text) if not SAMPLE_PATHS.search(m.group(0))), None)
            if m:
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{rel}:{line}: {what}: {m.group(0)[:60]}")
    return problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    dest = Path(os.path.expanduser(args[0])).resolve()
    if "--check" not in argv:
        n = export(dest)
        print(f"Skopiowano {n} plików do {dest}")
    problems = check(dest)
    if problems:
        print(f"UWAGA — {len(problems)} śladów prywatnych danych:")
        for x in problems[:100]:
            print("  -", x)
        return 1
    print("Sprawdzone: brak user_data/, prywatnych testów, ścieżek z dysku, sesji, e-maili i prawdziwych zgłoszeń.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
