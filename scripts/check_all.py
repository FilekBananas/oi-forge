#!/usr/bin/env python3
"""Pełna kontrola jakości OI Forge (wieloplatformowo):
  1) testy jednostkowe (unittest discover),
  2) walidacja bazy wiedzy i snippetów (z kompilacją kodu, chyba że --fast),
  3) składnia wszystkich plików JS (node --check, jeśli node jest dostępny),
  4) start serwera na wolnym porcie i sprawdzenie /api/health + każdego widoku.
Użycie:  python3 scripts/check_all.py [--fast] [--no-tests]
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAST = "--fast" in sys.argv
NO_TESTS = "--no-tests" in sys.argv
results: list[tuple[str, bool, str]] = []


def step(name, ok, info=""):
    results.append((name, ok, info))
    print(f"{'✅' if ok else '❌'} {name}" + (f" — {info}" if info else ""), flush=True)


def run(cmd, timeout=1800):
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    if not NO_TESTS:
        code, out = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."])
        tail = [ln for ln in out.strip().splitlines() if ln.startswith(("Ran ", "OK", "FAILED"))]
        step("Testy jednostkowe", code == 0, " | ".join(tail[-2:]))
        if code != 0:
            print("\n".join(out.splitlines()[-60:]))
    code, out = run([sys.executable, "scripts/validate_kb.py"] + (["--no-compile"] if FAST else []))
    step("Baza wiedzy i snippety", code == 0, out.strip().splitlines()[-1] if out.strip() else "")
    if code != 0:
        print("\n".join([ln for ln in out.splitlines() if "BŁĄD" in ln][:30]))
    node = shutil.which("node")
    if node:
        bad = []
        files = sorted((ROOT / "web" / "js").rglob("*.js"))
        for f in files:
            c, o = run([node, "--check", str(f)])
            if c != 0:
                bad.append(f"{f.relative_to(ROOT)}: {o.strip().splitlines()[0] if o.strip() else ''}")
        step(f"Składnia JS ({len(files)} plików)", not bad, "; ".join(bad[:5]))
    else:
        step("Składnia JS", True, "pominięto (brak node)")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([sys.executable, "-m", "oiforge", "--port", str(port), "--no-browser", "--no-warmup"], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        health = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as r:
                    health = json.loads(r.read())
                break
            except Exception:
                time.sleep(0.5)
        if not health:
            step("Serwer", False, "nie odpowiada")
            return 1
        errs = health.get("tool_errors") or {}
        step(f"Serwer: {len(health['tools'])} modułów, {health['routes']} tras, {health['kb_articles']} artykułów", not errs,
             ("błędy: " + ", ".join(f"{k}: {v}" for k, v in errs.items())) if errs else "")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/views", timeout=5) as r:
            views = json.loads(r.read())["views"]
        missing = []
        for v in views:
            for key in ("js", "css"):
                if key in v:
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}{v[key]}", timeout=5).read()
                    except Exception as e:  # noqa: BLE001
                        missing.append(f"{v[key]} ({e})")
        step(f"Widoki ({len(views)})", not missing, "; ".join(missing[:5]))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    failed = [r for r in results if not r[1]]
    print(f"\n{'WSZYSTKO OK' if not failed else f'{len(failed)} problem(ów)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
