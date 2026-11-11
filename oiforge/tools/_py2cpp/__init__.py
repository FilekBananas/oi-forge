"""Silnik tłumacza Python → C++ (bez AI: analiza AST, inferencja typów, reguły).

Użycie:
    from oiforge.tools._py2cpp import translate
    res = translate(code, {"int_type": "long long", "use_unordered": False, "fast_io": True})
    res["cpp"], res["warnings"], res["line_map"]
"""
from __future__ import annotations

import ast
import sys
import time
import traceback

from .analysis import Analysis
from .emit import Emitter
from .infer import Infer
from .signs import Signs

DEFAULT_OPTIONS = {"int_type": "long long", "use_unordered": False, "fast_io": True}


def _repair(tree: ast.AST) -> list[dict]:
    """Naprawia typowe usterki, zanim tłumacz je zobaczy (zamiast zostawiać kawałki nieprzetłumaczone):
    `for i in range():` (bez argumentów — w Pythonie TypeError) → `range(len(tab))`, gdy w pętli jest `tab[i]`,
    albo `range(n)`, gdy w programie jest zmienna n/m/k/q. Każda naprawa trafia do uwag z numerem linii."""
    notes: list[dict] = []
    assigned = {t.id for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign))
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
                for t in ([t] if isinstance(t, ast.Name) else [e for e in getattr(t, "elts", []) if isinstance(e, ast.Name)])}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.For) and isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name)
                and node.iter.func.id == "range" and not node.iter.args and not node.iter.keywords
                and isinstance(node.target, ast.Name)):
            continue
        var = node.target.id
        seq = None
        for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) and isinstance(sub.slice, ast.Name) \
                    and sub.slice.id == var:
                seq = sub.value.id
                break
        if seq:
            arg = ast.Call(func=ast.Name(id="len", ctx=ast.Load()), args=[ast.Name(id=seq, ctx=ast.Load())], keywords=[])
            what = f"range(len({seq}))"
            why = f"bo w pętli jest {seq}[{var}]"
        else:
            bound = next((b for b in ("n", "m", "k", "q") if b in assigned), None)
            if not bound:
                continue
            arg = ast.Name(id=bound, ctx=ast.Load())
            what, why = f"range({bound})", f"bo w programie jest zmienna {bound}"
        node.iter.args = [ast.copy_location(arg, node.iter)]
        ast.fix_missing_locations(node.iter)
        notes.append({"line": node.lineno, "severity": "warn", "code": "range-empty",
                      "message": f"`range()` bez argumentów to błąd w Pythonie (TypeError) — przyjąłem {what}, {why}. "
                                 f"Popraw też plik .py."})
    return notes


def translate(code: str, options: dict | None = None) -> dict:
    """Tłumaczy program Pythona na C++. Nigdy nie rzuca wyjątku — błędy trafiają do `warnings`."""
    opts = dict(DEFAULT_OPTIONS)
    opts.update({k: v for k, v in (options or {}).items() if v is not None})
    code = (code or "").replace("\r\n", "\n").replace("\r", "\n").expandtabs(8)
    t0 = time.perf_counter()
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"ok": False, "cpp": "", "line_map": [], "helpers": [],
                "warnings": [{"line": e.lineno or 0, "severity": "error", "code": "syntax",
                              "message": f"Błąd składni Pythona: {e.msg} (linia {e.lineno}, kolumna {e.offset or 0})."}],
                "error": "Błąd składni Pythona"}
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 20000))
    repairs = _repair(tree)
    try:
        an = Analysis(tree)
        inf = Infer(an, opts["int_type"])
        inf.run()
        signs = Signs(an, inf)
        signs.run()
        em = Emitter(code, tree, an, inf, signs, opts)
        res = em.translate()
        res["ok"] = True
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        res = {"ok": False, "cpp": "// Tłumaczenie nie powiodło się (błąd wewnętrzny tłumacza).\n", "line_map": [],
               "helpers": [], "error": f"{e.__class__.__name__}: {e}", "trace": tb,
               "warnings": [{"line": 0, "severity": "error", "code": "internal",
                             "message": f"Błąd wewnętrzny tłumacza: {e.__class__.__name__}: {e}"}]}
    finally:
        sys.setrecursionlimit(old_limit)
    if repairs:
        res["warnings"] = repairs + list(res.get("warnings") or [])
    res["time_ms"] = round(1000 * (time.perf_counter() - t0), 1)
    res["options"] = opts
    return res


__all__ = ["translate", "DEFAULT_OPTIONS"]
