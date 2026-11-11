#!/usr/bin/env python3
"""Walidator bazy wiedzy i snippetów OI Forge.

Użycie (z katalogu głównego):
    python3 scripts/validate_kb.py                 # wszystkie data/kb/*.json i data/snippets/*.json
    python3 scripts/validate_kb.py data/kb/stl.json # wybrane pliki
    python3 scripts/validate_kb.py --no-compile     # tylko schemat

Sprawdza: poprawność JSON, schemat artykułów, unikalność id, odwołania `related`, kompilację kodu C++
(g++-16 -std=c++20 -fsyntax-only) oraz składnię kodu Pythona. Wyniki kompilacji są cache'owane.
"""
from __future__ import annotations

import ast
import concurrent.futures as cf
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "data" / "kb"
SNIP_DIR = ROOT / "data" / "snippets"
CACHE_FILE = ROOT / "user_data" / "cache" / "kbcheck.json"

CATEGORIES = {
    "C++ język", "STL kontenery", "STL algorytmy", "Wejście/wyjście", "Liczby i typy", "Pułapki",
    "OI praktyka", "Grafy", "Drzewa", "Struktury danych", "Techniki", "Programowanie dynamiczne",
    "Teksty", "Matematyka", "Geometria", "Teoria gier", "Narzędzia",
}
BODY_TYPES = {"text", "code", "table", "tip", "warn"}
HEADER = "#include <bits/stdc++.h>\nusing namespace std;\n"


def find_compiler() -> str | None:
    for c in ("g++-16", "g++-15", "g++-14", "g++-13"):
        if shutil.which(c):
            return c
    for c in ("/opt/homebrew/bin/g++-16",):
        if os.path.exists(c):
            return c
    return shutil.which("g++")


CXX = find_compiler()


def load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def save_cache(c: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(c))


def wrap(code: str, scope: str) -> str:
    if "main(" in code:
        return code if "#include" in code else HEADER + code
    if scope == "global":
        return HEADER + code + "\nint main() {}\n"
    return HEADER + "int main() {\n" + code + "\n}\n"


def compile_check(code: str, scope: str | None) -> tuple[bool, str]:
    """Zwraca (ok, stderr). scope None => próbuje local, potem global."""
    scopes = [scope] if scope in ("global", "local") else ["local", "global"]
    if "main(" in code:
        scopes = ["full"]
    last_err = ""
    for sc in scopes:
        src = wrap(code, "global" if sc == "global" else "local")
        with tempfile.NamedTemporaryFile("w", suffix=".cpp", delete=False) as f:
            f.write(src)
            path = f.name
        try:
            p = subprocess.run([CXX, "-std=c++20", "-fsyntax-only", "-w", path],
                               capture_output=True, text=True, timeout=240)
            if p.returncode == 0:
                return True, ""
            last_err = p.stderr.replace(path, "<kod>")
        except subprocess.TimeoutExpired:
            last_err = "timeout kompilacji"
        finally:
            os.unlink(path)
    return False, last_err


def py_check(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, where: str, msg: str):
        self.errors.append(f"[BŁĄD] {where}: {msg}")

    def warn(self, where: str, msg: str):
        self.warnings.append(f"[uwaga] {where}: {msg}")


def is_str_list(x) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def check_article(a: dict, where: str, rep: Report, code_jobs: list):
    req = ["id", "title", "category", "tags", "aliases", "summary", "body", "qa"]
    for k in req:
        if k not in a:
            rep.err(where, f"brak pola '{k}'")
    if not isinstance(a.get("id", ""), str) or not a.get("id"):
        return
    aid = a["id"]
    w = f"{where}#{aid}"
    if a.get("category") not in CATEGORIES:
        rep.err(w, f"nieznana kategoria {a.get('category')!r}")
    for k in ("tags", "aliases", "pitfalls", "related"):
        if k in a and not is_str_list(a[k]):
            rep.err(w, f"'{k}' musi być listą stringów")
    if len(a.get("aliases", [])) < 3:
        rep.warn(w, "mniej niż 3 aliasy")
    lvl = a.get("level", 1)
    if not isinstance(lvl, int) or not 1 <= lvl <= 5:
        rep.err(w, "level musi być int 1..5")
    body = a.get("body", [])
    if not isinstance(body, list) or not body:
        rep.err(w, "body musi być niepustą listą")
        body = []
    for i, b in enumerate(body):
        bw = f"{w}.body[{i}]"
        if not isinstance(b, dict) or b.get("type") not in BODY_TYPES:
            rep.err(bw, f"zły blok (type ∈ {sorted(BODY_TYPES)})")
            continue
        t = b["type"]
        if t in ("text", "tip", "warn") and not isinstance(b.get("md"), str):
            rep.err(bw, "brak 'md'")
        if t == "table":
            hdr, rows = b.get("headers"), b.get("rows")
            if not is_str_list(hdr) or not isinstance(rows, list):
                rep.err(bw, "tabela wymaga headers[] i rows[][]")
            else:
                for r in rows:
                    if not isinstance(r, list) or len(r) != len(hdr):
                        rep.err(bw, f"wiersz {r!r} ma inną liczbę kolumn niż nagłówek")
                        break
        if t == "code":
            if not isinstance(b.get("code"), str) or not b["code"].strip():
                rep.err(bw, "pusty kod")
            elif not b.get("nocompile"):
                code_jobs.append((bw, b["code"], b.get("lang", "cpp"), b.get("scope")))
    for i, f in enumerate(a.get("functions", []) or []):
        if not isinstance(f, dict) or "sig" not in f or "desc" not in f:
            rep.err(f"{w}.functions[{i}]", "wymaga sig i desc")
    qa = a.get("qa", [])
    if not isinstance(qa, list):
        rep.err(w, "qa musi być listą")
        qa = []
    if len(qa) < 5:
        rep.warn(w, f"tylko {len(qa)} QA (zalecane ≥ 5)")
    for i, q in enumerate(qa):
        qw = f"{w}.qa[{i}]"
        if not isinstance(q, dict):
            rep.err(qw, "QA musi być obiektem")
            continue
        qs = q.get("q")
        if isinstance(qs, str):
            qs = [qs]
        if not is_str_list(qs) or not qs:
            rep.err(qw, "q musi być listą wariantów pytania")
        if not isinstance(q.get("a"), str) or not q["a"].strip():
            rep.err(qw, "brak odpowiedzi 'a'")
        if q.get("code") and not q.get("nocompile"):
            code_jobs.append((qw, q["code"], q.get("lang", "cpp"), q.get("code_scope")))


def check_snippets(data: dict, where: str, rep: Report, code_jobs: list, triggers: dict):
    sn = data.get("snippets")
    if not isinstance(sn, list):
        rep.err(where, "brak listy 'snippets'")
        return
    for i, s in enumerate(sn):
        w = f"{where}.snippets[{i}]"
        for k in ("trigger", "name", "code"):
            if not isinstance(s.get(k), str) or not s.get(k):
                rep.err(w, f"brak '{k}'")
        t = s.get("trigger")
        if t:
            if t in triggers:
                rep.err(w, f"zduplikowany trigger '{t}' (też w {triggers[t]})")
            triggers[t] = w
        if s.get("code") and not s.get("nocompile"):
            import re
            code = re.sub(r"\$\{\d+:([^}]*)\}", r"\1", s["code"])
            code = re.sub(r"\$\d+", "", code)
            code_jobs.append((f"{w}({t})", code, s.get("lang", "cpp"), s.get("scope", "global")))


def main(argv: list[str]) -> int:
    do_compile = "--no-compile" not in argv
    files = [Path(a) for a in argv if not a.startswith("--")]
    all_kb = sorted(KB_DIR.glob("*.json"))
    all_snip = sorted(SNIP_DIR.glob("*.json"))
    targets = [f.resolve() for f in files] if files else all_kb + all_snip
    rep = Report()
    code_jobs: list = []
    ids: dict[str, str] = {}
    related: list[tuple[str, str]] = []
    triggers: dict[str, str] = {}
    n_articles = n_qa = 0

    # id-y ze WSZYSTKICH plików KB (żeby `related` mogło wskazywać na inne pliki)
    global_ids: set[str] = set()
    for f in all_kb:
        try:
            for a in json.loads(f.read_text()).get("articles", []):
                if isinstance(a, dict) and isinstance(a.get("id"), str):
                    global_ids.add(a["id"])
        except Exception:
            pass

    for f in targets:
        where = f.name
        try:
            data = json.loads(f.read_text())
        except Exception as e:
            rep.err(where, f"niepoprawny JSON: {e}")
            continue
        if f.parent.name == "snippets":
            check_snippets(data, where, rep, code_jobs, triggers)
            continue
        arts = data.get("articles")
        if not isinstance(arts, list):
            rep.err(where, "brak listy 'articles'")
            continue
        for a in arts:
            if not isinstance(a, dict):
                rep.err(where, "artykuł nie jest obiektem")
                continue
            aid = a.get("id")
            if aid in ids:
                rep.err(where, f"zduplikowane id '{aid}' (też w {ids[aid]})")
            ids[aid] = where
            n_articles += 1
            n_qa += len(a.get("qa", []) or [])
            check_article(a, where, rep, code_jobs)
            for r in a.get("related", []) or []:
                related.append((f"{where}#{aid}", r))

    for w, r in related:
        if r not in global_ids:
            rep.warn(w, f"related '{r}' nie istnieje (jeszcze?)")

    if do_compile and code_jobs:
        if not CXX:
            rep.warn("kompilacja", "nie znaleziono g++ — pomijam")
        else:
            cache = load_cache()

            def job(item):
                w, code, lang, scope = item
                key = hashlib.sha1(f"{lang}|{scope}|{code}".encode()).hexdigest()
                if cache.get(key) is True:
                    return w, True, "", key
                if lang in ("python", "py"):
                    ok, err = py_check(code)
                elif lang in ("cpp", "c++", "cc"):
                    ok, err = compile_check(code, scope)
                else:
                    return w, True, "", key
                return w, ok, err, key

            with cf.ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 4))) as ex:
                for w, ok, err, key in ex.map(job, code_jobs):
                    if ok:
                        cache[key] = True
                    else:
                        lines = [ln for ln in err.splitlines() if "error" in ln][:3] or err.splitlines()[:3]
                        rep.err(w, "kod się nie kompiluje:\n      " + "\n      ".join(lines))
            save_cache(cache)

    for x in rep.warnings:
        print(x)
    for x in rep.errors:
        print(x)
    print(f"\nPliki: {len(targets)} | artykuły: {n_articles} | QA: {n_qa} | bloki kodu: {len(code_jobs)} | "
          f"błędy: {len(rep.errors)} | uwagi: {len(rep.warnings)}")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
