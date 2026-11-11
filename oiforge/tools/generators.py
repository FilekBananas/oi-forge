"""Generatory testów: mini-język (DSL) i losowe struktury.

HTTP:
    POST /api/gendsl/run {source, seed?, tests?, preview?, size?, include_output?=true}
         → {output, tests_count, declared_tests, preview: [test...], stats, warnings, seed, output_truncated}
         błąd DSL → {ok: false, error, errors: [{line, col, message, test}], line, col, test, preview}
    POST /api/gendsl/check {source}           → {valid, errors, warnings, info}
    POST /api/gendsl/export {source, lang}    → {code, lang, filename}       (lang: cpp | python)
    GET  /api/gendsl/examples                 → {examples: [{id, title, category, description, source}]}
    GET  /api/gendsl/reference                → {functions: [...], keywords: [...]}
    POST /api/randstruct/generate {kind, n, m, shape?, connected?, directed?, weights?: [lo, hi], seed?, base?}
         → {n, m, edges, directed, kind, rows?}   (dla edytora grafów i innych narzędzi)
"""
from __future__ import annotations

import json
import threading

from oiforge import config
from oiforge.core import gendsl, randstruct
from oiforge.server_api import ApiError

EXAMPLES_FILE = config.DATA_DIR / "gendsl_examples.json"
MAX_RETURN_OUTPUT = 8_000_000          # znaków wyjścia odsyłanych w JSON
_ex_cache = {"mtime": None, "data": []}
_ex_lock = threading.Lock()


def _source(body: dict) -> str:
    src = body.get("source")
    if src is None:
        src = body.get("code")
    if not isinstance(src, str) or not src.strip():
        raise ApiError("Wpisz program generatora (np. „n = int(1, 10)” i „print n”).")
    if len(src) > 200_000:
        raise ApiError("Program generatora jest za długi.")
    return src


def _dsl_error(e: gendsl.DslError, extra: dict | None = None) -> ApiError:
    errs = getattr(e, "all", None) or [e]
    payload = {"errors": [x.to_dict() for x in errs[:20]], "line": e.line, "col": e.col, "test": e.test,
               "preview": getattr(e, "partial", None) or []}
    if extra:
        payload.update(extra)
    return ApiError(e.text(), 400, **payload)


def _int_or_none(v, name, lo=None, hi=None):
    if v is None or v == "":
        return None
    try:
        x = int(float(v)) if isinstance(v, (int, float, str)) else int(v)
    except (TypeError, ValueError):
        raise ApiError(f"Pole „{name}” musi być liczbą całkowitą.") from None
    if lo is not None and x < lo:
        x = lo
    if hi is not None and x > hi:
        x = hi
    return x


# ---------------------------------------------------------------------------------------------- DSL
def _time_limit(v) -> float:
    try:
        t = float(v) if v not in (None, "") else 30.0
    except (TypeError, ValueError):
        t = 30.0
    return max(0.5, min(t, 60.0))


def handle_run(body: dict) -> dict:
    src = _source(body)
    seed = _int_or_none(body.get("seed"), "seed", 0)
    tests = _int_or_none(body.get("tests"), "tests", 0, 200_000)
    preview = _int_or_none(body.get("preview"), "preview", 0, 50)
    preview = gendsl.PREVIEW_DEFAULT if preview is None else preview
    size = _int_or_none(body.get("size"), "size", -10 ** 18, 10 ** 18)
    vars_ = {"size": size} if size is not None else {}
    include = body.get("include_output", True)
    include = include not in (False, 0, "0", "false")
    max_out = _int_or_none(body.get("max_output"), "max_output", 1000, 128_000_000) or 64_000_000
    try:
        res = gendsl.run(src, seed=seed, tests=tests, vars=vars_, preview=preview, keep_output=include,
                         max_output=max_out, time_limit=_time_limit(body.get("time_limit")), max_steps=80_000_000)
    except gendsl.DslError as e:
        raise _dsl_error(e) from None
    out = res.get("output")
    truncated = False
    if out is not None and len(out) > MAX_RETURN_OUTPUT:
        out = out[:MAX_RETURN_OUTPUT]
        truncated = True
    return {"output": out, "output_truncated": truncated, "tests_count": res["tests_count"],
            "declared_tests": res["declared_tests"], "preview": res["preview"], "stats": res["stats"],
            "warnings": res["warnings"], "seed": res["seed"]}


def handle_check(body: dict) -> dict:
    src = body.get("source") or body.get("code") or ""
    if not src.strip():
        return {"valid": False, "errors": [{"line": None, "col": None, "message": "Pusty program.", "severity": "error"}],
                "warnings": [], "info": {}}
    res = gendsl.check(src)
    res.pop("ok", None)
    return res


def handle_export(body: dict) -> dict:
    src = _source(body)
    lang = (body.get("lang") or "cpp").lower()
    try:
        code = gendsl.export(src, lang)
    except gendsl.DslError as e:
        raise _dsl_error(e) from None
    is_py = lang.startswith("py")
    return {"code": code, "lang": "python" if is_py else "cpp", "filename": "gen.py" if is_py else "gen.cpp",
            "usage": ("python3 gen.py <seed> [liczba_testów] [size]" if is_py
                      else "g++ -O2 -std=c++17 gen.cpp -o gen && ./gen <seed> [liczba_testów] [size]")}


def load_examples() -> list[dict]:
    with _ex_lock:
        try:
            mt = EXAMPLES_FILE.stat().st_mtime
        except OSError:
            return []
        if _ex_cache["mtime"] != mt:
            try:
                data = json.loads(EXAMPLES_FILE.read_text(encoding="utf-8"))
                _ex_cache["data"] = data.get("examples", []) if isinstance(data, dict) else list(data)
            except (OSError, ValueError):
                _ex_cache["data"] = []
            _ex_cache["mtime"] = mt
        return [dict(x) for x in _ex_cache["data"]]


def handle_examples(_q: dict) -> dict:
    return {"examples": load_examples()}


def handle_reference(_q: dict) -> dict:
    return {"functions": gendsl.reference(), "keywords": list(gendsl.KEYWORDS), "builtins": list(gendsl.FUNCTIONS),
            "predefined": list(gendsl.PREDEFINED), "tree_shapes": list(randstruct.TREE_SHAPES),
            "tree_shape_names": randstruct.TREE_SHAPE_NAMES, "seq_kinds": list(randstruct.SEQ_KINDS),
            "seq_kind_names": randstruct.SEQ_KIND_NAMES, "query_kinds": list(randstruct.QUERY_KINDS),
            "alphabets": randstruct.ALPHABETS}


# ---------------------------------------------------------------------------------------------- losowe struktury
def _flag(v, default=False) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, str):
        return v.lower() not in ("0", "false", "nie", "no", "")
    return bool(v)


def handle_struct(body: dict) -> dict:
    """Losowa struktura do bezpośredniego użycia (edytor grafów, inne narzędzia)."""
    kind = str(body.get("kind") or "tree").lower()
    seed = _int_or_none(body.get("seed"), "seed", 0)
    rng = randstruct.Rng(seed)
    base = _int_or_none(body.get("base"), "base", 0, 1)
    base = 1 if base is None else base
    weights = body.get("weights")
    if isinstance(weights, (list, tuple)) and len(weights) == 2:
        weights = (_int_or_none(weights[0], "weights"), _int_or_none(weights[1], "weights"))
    elif not weights:
        weights = None
    limit = 5000

    def num(name, default, lo=0, hi=limit):
        v = _int_or_none(body.get(name), name)
        v = default if v is None else v
        if v < lo or v > hi:
            raise ApiError(f"Pole „{name}” musi być z zakresu {lo}..{hi}.")
        return v

    try:
        if kind == "tree":
            n = num("n", 8, 1)
            edges = randstruct.random_tree(n, body.get("shape") or "random", rng, base=base, weights=weights)
            return {"kind": kind, "n": n, "m": len(edges), "edges": [list(e) for e in edges], "directed": False}
        if kind == "forest":
            n = num("n", 10, 1)
            k = num("k", 2, 1)
            edges = randstruct.random_forest(n, k, rng, base=base, weights=weights)
            return {"kind": kind, "n": n, "m": len(edges), "edges": [list(e) for e in edges], "directed": False}
        if kind == "graph":
            n = num("n", 8, 1)
            m = num("m", n, 0, 50_000)
            directed = _flag(body.get("directed"))
            edges = randstruct.random_graph(n, m, rng, connected=_flag(body.get("connected"), True), directed=directed,
                                            loops=_flag(body.get("loops")), multi=_flag(body.get("multi")), base=base,
                                            weights=weights)
            return {"kind": kind, "n": n, "m": len(edges), "edges": [list(e) for e in edges], "directed": directed}
        if kind == "dag":
            n = num("n", 8, 1)
            m = num("m", n, 0, 50_000)
            edges = randstruct.random_dag(n, m, rng, connected=_flag(body.get("connected")), base=base, weights=weights)
            return {"kind": kind, "n": n, "m": len(edges), "edges": [list(e) for e in edges], "directed": True}
        if kind == "bipartite":
            n1 = num("n1", 4, 1)
            n2 = num("n2", 4, 1)
            m = num("m", min(n1 * n2, n1 + n2), 0, 50_000)
            edges = randstruct.random_bipartite(n1, n2, m, rng, base=base, shift=True, weights=weights)
            return {"kind": kind, "n": n1 + n2, "n1": n1, "n2": n2, "m": len(edges), "edges": [list(e) for e in edges],
                    "directed": False}
        if kind in ("grid_graph", "gridgraph", "siatka"):
            r = num("rows", 4, 1, 100)
            c = num("cols", 5, 1, 100)
            edges = randstruct.grid_graph(r, c, base=base)
            if weights:
                lo, hi = randstruct.weight_range(weights)
                edges = [(u, v, rng.randint(lo, hi)) for u, v in edges]
            return {"kind": "grid_graph", "n": r * c, "m": len(edges), "rows": r, "cols": c,
                    "edges": [list(e) for e in edges], "directed": False}
        if kind == "grid":
            r = num("rows", 5, 1, 500)
            c = num("cols", 8, 1, 500)
            p = body.get("p")
            g = randstruct.random_grid(r, c, rng, alphabet=body.get("alphabet") or "#.",
                                       p=None if p in (None, "") else float(p), path=_flag(body.get("path")))
            return {"kind": "grid", "rows": r, "cols": c, "grid": g}
    except randstruct.StructError as e:
        raise ApiError(str(e)) from None
    raise ApiError(f"Nieznany rodzaj struktury „{kind}” (tree, forest, graph, dag, bipartite, grid_graph, grid).")


ROUTES = {
    "POST /api/gendsl/run": handle_run,
    "POST /api/gendsl/check": handle_check,
    "POST /api/gendsl/export": handle_export,
    "GET /api/gendsl/examples": handle_examples,
    "GET /api/gendsl/reference": handle_reference,
    "POST /api/randstruct/generate": handle_struct,
}
