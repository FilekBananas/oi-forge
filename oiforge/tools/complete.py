"""Spersonalizowane autouzupełnianie: model n-gramowy uczony na kodzie ucznia (rozwiązania z SIO2 + pliki w edytorze).

Bez AI — zwykła statystyka:
  * words   — identyfikatory ucznia z częstościami (jego nazwy zmiennych, funkcji, makr),
  * members — co uczeń wpisuje po `.` / `->` (push_back, size, first…),
  * next    — bigramy tokenów: co zwykle pisze po danym tokenie (np. po `vector<` → `int`, `long`),
  * lines   — jego typowe całe linie (np. własna pętla `for`, szybkie I/O, `#define`), do podpowiadania całej linii,
  * funcs   — funkcje, które uczeń wywołuje (identyfikator tuż przed `(`, bez słów kluczowych i rzutowań),
  * types   — jego typy i aliasy (typedef/using/struct/class, np. `ll`, `pii`) oraz makra z #define.

GET  /api/complete/model    → model (cache'owany w config.CACHE_DIR, przebudowa gdy zmienią się źródła)
POST /api/complete/rebuild  → wymuś przebudowę
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from oiforge import config
from oiforge.core import cpp_lexer

MODEL_PATH = config.CACHE_DIR / "complete_model.json"
_lock = threading.Lock()
_mem: dict = {"sig": None, "model": None}
MODEL_VERSION = 4
MAX_WORDS, MAX_MEMBERS, MAX_NEXT_KEYS, MAX_LINES = 4000, 400, 2500, 2500
_SKIP_LINE = re.compile(r"^\s*(//|/\*|\*|$)")


def _sources() -> list[Path]:
    """Pliki kodu ucznia: programy z SIO2 (index.json) + pliki w edytorze projektów."""
    files: list[Path] = []
    idx = config.PROGRAMS_DIR / "index.json"
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
        for p in data.get("programs", []):
            fp = config.PROGRAMS_DIR / p.get("path", "")
            if fp.suffix in (".cpp", ".cc", ".cxx", ".h", ".hpp", ".py") and fp.is_file():
                files.append(fp)
    except (OSError, ValueError):
        pass
    ws = config.USER_DIR / "workspace"
    if ws.is_dir():
        for fp in ws.rglob("*"):
            if ".trash" in fp.parts:
                continue
            if fp.suffix in (".cpp", ".cc", ".cxx", ".h", ".hpp", ".py") and fp.is_file():
                try:
                    if fp.stat().st_size < 300_000:
                        files.append(fp)
                except OSError:
                    pass
    return files


def _signature(files: list[Path]) -> str:
    h = 0
    newest = 0.0
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        h = (h * 1_000_003 + int(st.st_mtime_ns) + st.st_size) % (1 << 61)
        newest = max(newest, st.st_mtime)
    return f"v{MODEL_VERSION}:{len(files)}:{h}"


def _norm_line(line: str) -> str:
    s = line.strip()
    s = re.sub(r"\s*//.*$", "", s) if "//" in s and '"' not in s else s
    return s.rstrip()


_TYPE_DEF_RE = re.compile(r"\btypedef\b[^;{}]*?\b([A-Za-z_]\w*)\s*;|\busing\s+([A-Za-z_]\w*)\s*=|\b(?:struct|class)\s+([A-Za-z_]\w*)")
# słowa, po których „(” nie oznacza wywołania funkcji (sterowanie, rzutowania, typy)
_NOT_FUNC = set("""if for while switch return sizeof catch do else case new delete throw typeid decltype alignof static_assert
int long short unsigned signed double float char bool void auto const ll ull ld string main""".split())


def build(files: list[Path] | None = None) -> dict:
    files = files if files is not None else _sources()
    words: Counter = Counter()
    members: Counter = Counter()
    nxt: dict[str, Counter] = defaultdict(Counter)
    nxt2: dict[str, Counter] = defaultdict(Counter)
    lines: Counter = Counter()
    funcs: Counter = Counter()
    types: Counter = Counter()
    for f in files:
        try:
            code = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(code) > 300_000:
            continue
        # linie (z kodu bez komentarzy — baner OnlineGDB itp. nie może trafić do podpowiedzi)
        seen_here = set()
        plain = cpp_lexer.strip_comments(code) if f.suffix != ".py" else re.sub(r"#.*", "", code)
        for ln in plain.split("\n"):
            if _SKIP_LINE.match(ln):
                continue
            s = _norm_line(ln)
            if 6 <= len(s) <= 140 and s not in seen_here:
                seen_here.add(s)
                lines[s] += 1
        if f.suffix == ".py":
            for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code):
                words[w] += 1
            continue
        toks = cpp_lexer.significant(cpp_lexer.tokenize(code, keep_comments=False))
        for m in _TYPE_DEF_RE.finditer(plain):
            types[m.group(1) or m.group(2) or m.group(3)] += 1
        for m in re.finditer(r"^\s*#\s*define\s+([A-Za-z_]\w*)", plain, re.M):
            types[m.group(1)] += 1
        prev = None
        prev2 = None
        for i, t in enumerate(toks):
            # wywołanie: „x = f(”, „return f(”, „<< f(” — ale nie deklaracja „int f(” ani „vector<int> a(n)”
            if (t.kind == "id" and i + 1 < len(toks) and toks[i + 1].text == "("
                    and (prev is None or (prev.text not in (".", "->", "::", ">", "*", "&") and prev.kind != "id"))
                    and t.text not in _NOT_FUNC):
                funcs[t.text] += 1
            if t.kind == "id":
                words[t.text] += 1
                if prev is not None and prev.text in (".", "->"):
                    members[t.text] += 1
            elif t.kind == "kw":
                words[t.text] += 1
            if prev is not None and t.kind in ("id", "kw", "num", "op", "punct") and len(t.text) <= 24:
                nxt[prev.text][t.text] += 1
                if prev2 is not None:
                    nxt2[f"{prev2.text} {prev.text}"][t.text] += 1
            prev2, prev = prev, t
    # przycinanie
    top_words = [[w, c] for w, c in words.most_common(MAX_WORDS) if len(w) >= 2]
    top_members = [[w, c] for w, c in members.most_common(MAX_MEMBERS)]
    # funkcje: wywoływane w ≥ 2 programach albo ≥ 3 razy (pojedyncze literówki odpadają); typy/makra jak wyżej
    top_funcs = [[w, c] for w, c in funcs.most_common(600) if c >= 3 and len(w) >= 2]
    top_types = [[w, c] for w, c in types.most_common(300) if c >= 2]
    keys = sorted(nxt, key=lambda k: -sum(nxt[k].values()))[:MAX_NEXT_KEYS]
    next_map = {k: [[n, c] for n, c in nxt[k].most_common(8)] for k in keys if sum(nxt[k].values()) >= 3}
    keys2 = sorted((k for k in nxt2 if sum(nxt2[k].values()) >= 3), key=lambda k: -sum(nxt2[k].values()))[:MAX_NEXT_KEYS]
    next2_map = {k: [[n, c] for n, c in nxt2[k].most_common(6)] for k in keys2}
    # linie: powtarzane (≥2) albo bardzo „szablonowe” (include/define/using/typedef/fast IO)
    tmpl = re.compile(r"^(#\s*include|#\s*define|using |typedef |ios(_base)?::|cin\.tie|cout\.tie|int main|signed main)")
    top_lines = [[l, c] for l, c in lines.most_common() if c >= 2 or tmpl.match(l)][:MAX_LINES]
    return {"version": MODEL_VERSION, "built_at": time.strftime("%Y-%m-%d %H:%M"), "files": len(files),
            "words": top_words, "members": top_members, "next": next_map, "next2": next2_map, "lines": top_lines,
            "funcs": top_funcs, "types": top_types}


def model(force: bool = False) -> dict:
    files = _sources()
    sig = _signature(files)
    with _lock:
        if not force and _mem["sig"] == sig and _mem["model"] is not None:
            return _mem["model"]
        if not force and MODEL_PATH.exists():
            try:
                d = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
                if d.get("sig") == sig:
                    _mem.update(sig=sig, model=d["model"])
                    return d["model"]
            except (OSError, ValueError, KeyError):
                pass
        m = build(files)
        try:
            MODEL_PATH.write_text(json.dumps({"sig": sig, "model": m}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        _mem.update(sig=sig, model=m)
        return m


def handle_model(_q: dict) -> dict:
    return {"model": model()}


def handle_rebuild(_b: dict) -> dict:
    m = model(force=True)
    return {"files": m["files"], "words": len(m["words"]), "lines": len(m["lines"])}


ROUTES = {
    "GET /api/complete/model": handle_model,
    "POST /api/complete/rebuild": handle_rebuild,
}
