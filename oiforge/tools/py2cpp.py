"""Tłumacz Python → C++ („wysyłam w Pythonie, zmienia na C++”).

Trasy:
  POST /api/py2cpp/translate     {code, options:{int_type, use_unordered, fast_io}} -> {cpp, warnings, line_map, helpers}
  POST /api/py2cpp/verify        {code, input | inputs[], options?, timeout?}        -> wynik porównania (synchronicznie)
  POST /api/py2cpp/verify_start  jak wyżej, ale jako zadanie w tle (jobs) z postępem i zdarzeniami
  GET  /api/py2cpp/examples      -> {examples:[{id, title, description, code, input}]}

Silnik: pakiet `oiforge.tools._py2cpp` (analiza AST, inferencja typów, emiter C++). Zero AI.
"""
from __future__ import annotations

from oiforge.core import jobs, runner, store
from oiforge.server_api import ApiError

from ._py2cpp import translate
from ._py2cpp.examples import EXAMPLES

MAX_CODE = 300_000
MAX_INPUT = 20_000_000
MAX_TESTS = 20


def _options(body: dict) -> dict:
    o = body.get("options") or {}
    if not isinstance(o, dict):
        o = {}
    it = str(o.get("int_type") or "long long").strip()
    return {"int_type": "int" if it == "int" else "long long",
            "use_unordered": bool(o.get("use_unordered", False)),
            "fast_io": o.get("fast_io", True) is not False}


def _code(body: dict) -> str:
    code = body.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ApiError("Wklej program w Pythonie do przetłumaczenia.")
    if len(code) > MAX_CODE:
        raise ApiError("Program jest za długi (limit 300 000 znaków).")
    return code


def handle_translate(body: dict) -> dict:
    code = _code(body)
    res = translate(code, _options(body))
    res.pop("trace", None) if not body.get("debug") else None
    return {"ok": True, "cpp": res.get("cpp", ""), "warnings": res.get("warnings", []),
            "line_map": res.get("line_map", []), "helpers": res.get("helpers", []),
            "translated": bool(res.get("ok")), "error": res.get("error"), "time_ms": res.get("time_ms"),
            "options": res.get("options")}


def _inputs(body: dict) -> list[str]:
    inputs = body.get("inputs")
    if isinstance(inputs, list) and inputs:
        out = [str(x) for x in inputs[:MAX_TESTS]]
    else:
        out = [str(body.get("input") or "")]
    if sum(len(x) for x in out) > MAX_INPUT:
        raise ApiError("Wejście jest za duże.")
    return out


def _timeouts(body: dict) -> tuple[float, float]:
    s = store.settings() or {}
    try:
        base = float(body.get("timeout") or s.get("time_limit") or 2.0)
    except (TypeError, ValueError):
        base = 2.0
    base = max(0.2, min(base, 30.0))
    return max(base * 5, 10.0), base * 2 + 1.0     # Python bywa ~10-100× wolniejszy


def _verify(body: dict, job=None) -> dict:
    code = _code(body)
    inputs = _inputs(body)
    opts = _options(body)
    py_timeout, cpp_timeout = _timeouts(body)

    def progress(f, msg):
        if job is not None:
            job.progress(f, msg)

    progress(0.02, "Tłumaczenie…")
    tr = translate(code, opts)
    result = {"translation": {"cpp": tr.get("cpp", ""), "warnings": tr.get("warnings", []),
                              "line_map": tr.get("line_map", []), "translated": bool(tr.get("ok"))},
              "tests": [], "equal": False, "compile": None}
    if not tr.get("ok"):
        result["error"] = tr.get("error") or "Nie udało się przetłumaczyć programu."
        return result
    py_ok, py_msg, py_line = runner.python_syntax(code)
    if not py_ok:
        result["error"] = f"Błąd składni Pythona: {py_msg}"
        return result
    progress(0.08, "Kompilacja C++…")
    cr = runner.compile_cpp(tr["cpp"], mode="release")
    result["compile"] = {"ok": cr.ok, "stderr": cr.stderr[-20000:], "time": cr.time, "cached": cr.cached,
                         "compiler": cr.compiler, "warnings": cr.warnings, "errors": cr.errors}
    if not cr.ok:
        result["error"] = "Przetłumaczony kod C++ się nie kompiluje (zobacz błędy kompilacji)."
        return result
    prog_py = runner.prepare(code, lang="python")
    all_equal = True
    py_total = cpp_total = 0.0
    n = len(inputs)
    for i, inp in enumerate(inputs):
        if job is not None and job.cancelled:
            break
        progress(0.15 + 0.85 * i / n, f"Test {i + 1}/{n}: Python…")
        rp = runner.run_program(prog_py, inp, timeout=py_timeout)
        progress(0.15 + 0.85 * (i + 0.5) / n, f"Test {i + 1}/{n}: C++…")
        rc = runner.run([cr.exe], inp, timeout=cpp_timeout)
        eq_exact = runner.normalize_output(rp.stdout) == runner.normalize_output(rc.stdout)
        eq_tokens = runner.outputs_equal(rp.stdout, rc.stdout)
        eq_float = eq_tokens or runner.outputs_equal(rp.stdout, rc.stdout, float_eps=1e-6)
        ok_runs = rp.verdict == "OK" and rc.verdict == "OK"
        equal = eq_tokens and (ok_runs or (rp.verdict == rc.verdict))
        all_equal = all_equal and equal
        py_total += rp.time
        cpp_total += rc.time
        diff_line = None
        if not eq_tokens:
            pl = runner.normalize_output(rp.stdout).split("\n")
            cl = runner.normalize_output(rc.stdout).split("\n")
            for k in range(max(len(pl), len(cl))):
                a = pl[k] if k < len(pl) else None
                b = cl[k] if k < len(cl) else None
                if a != b:
                    diff_line = {"line": k + 1, "python": a, "cpp": b}
                    break
        test = {"index": i, "equal": equal, "exact": eq_exact, "float_close": eq_float and not eq_tokens,
                "python": _run_dict(rp), "cpp": _run_dict(rc), "first_diff": diff_line,
                "speedup": _speedup(rp.time, rc.time)}
        result["tests"].append(test)
        if job is not None:
            job.push({"type": "test", **{k: test[k] for k in ("index", "equal", "speedup")},
                      "python_time": rp.time, "cpp_time": rc.time})
    result["equal"] = all_equal and bool(result["tests"])
    result["python_time"] = round(py_total, 4)
    result["cpp_time"] = round(cpp_total, 4)
    result["speedup"] = _speedup(py_total, cpp_total)
    progress(1.0, "Gotowe")
    return result


def _run_dict(r) -> dict:
    return {"stdout": r.stdout[-200_000:], "stderr": r.stderr[-20_000:], "time": r.time, "wall": r.wall,
            "mem_kb": r.mem_kb, "verdict": r.verdict, "exit_code": r.exit_code, "truncated": r.truncated or
            len(r.stdout) > 200_000}


def _speedup(py_t: float, cpp_t: float) -> float | None:
    if py_t <= 0:
        return None
    return round(py_t / max(cpp_t, 0.0005), 1)


def handle_verify(body: dict) -> dict:
    return {"ok": True, **_verify(body)}


def handle_verify_start(body: dict) -> dict:
    _code(body)
    return {"ok": True, "job": jobs.submit(lambda job, b: _verify(b, job), body, title="Równoważność Python ↔ C++")}


def handle_examples(_q: dict) -> dict:
    return {"ok": True, "examples": EXAMPLES}


ROUTES = {
    "POST /api/py2cpp/translate": handle_translate,
    "POST /api/py2cpp/verify": handle_verify,
    "POST /api/py2cpp/verify_start": handle_verify_start,
    "GET /api/py2cpp/examples": handle_examples,
}
