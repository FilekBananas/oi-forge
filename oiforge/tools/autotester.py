"""Auto-testerka: sama buduje testerkę z przykładów — wnioskuje format wejścia, generuje testy różnymi
strategiami, porównuje z brute’em (jeśli jest) albo robi „crash-test” (RE/TLE/UB z sanitizerami) i sprawdza
zgodność z przykładowymi wyjściami. Przy błędzie zmniejsza kontrprzykład (shrinking).

HTTP:
    POST /api/autotester/infer {samples: [{input, output}] | [str], statement?}  →  {spec, description, example, …}
    POST /api/autotester/extract {statement}  →  {samples}
    POST /api/autotester/generate {spec, strategy, size, seed}  →  {input, valid, error, description}
    GET  /api/autotester/strategies  →  {strategies, sizes}
    POST /api/autotester/start {solution, brute?, spec, strategies, count, timeout, samples?, sanitize?, max_size?,
                                seed?, langs?, stop_on_first?}  →  {job}
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import random
import re
import time

from oiforge import config
from oiforge.core import inputgen, jobs, runner
from oiforge.server_api import ApiError
from oiforge.tools import diff as D

MAX_EVENT_TESTS = 200
BIG_SAMPLE = 100_000          # przykład dłuższy niż tyle znaków (np. test „ocen” z liczbą o milionie cyfr)
SIZE_ORDER = ["tiny", "small", "medium", "large", "max"]
SIZE_NAMES = {"tiny": "malutkie (n ≤ 4)", "small": "małe (n ≤ 10)", "medium": "średnie (n ≤ 100)",
              "large": "duże (n ≤ 3000)", "max": "maksymalne (z ograniczeń)"}

# Sanitizery → wyjaśnienie po polsku
_SAN_PATTERNS = [
    (r"heap-buffer-overflow|stack-buffer-overflow|global-buffer-overflow",
     "Wyjście poza tablicę (odczyt/zapis za końcem bufora)."),
    (r"index \d+ out of bounds|out of bounds for type", "Indeks poza zakresem tablicy."),
    (r"signed integer overflow", "Przepełnienie liczby całkowitej ze znakiem (np. int za mały — użyj long long)."),
    (r"heap-use-after-free", "Użycie pamięci po jej zwolnieniu (np. iterator/wskaźnik po erase/realloc)."),
    (r"stack-overflow|stack overflow", "Przepełnienie stosu — zbyt głęboka rekurencja."),
    (r"division by zero|integer divide by zero|SIGFPE", "Dzielenie przez zero."),
    (r"null pointer|SEGV on unknown address 0x0+\b", "Odwołanie przez pusty wskaźnik (nullptr)."),
    (r"shift exponent|left shift of", "Niepoprawne przesunięcie bitowe (za duże lub ujemne)."),
    (r"vector::_M_range_check|out_of_range", "std::out_of_range — .at() poza zakresem."),
    (r"Assertion .* failed|assertion", "Niespełniona asercja (assert)."),
    (r"attempt to (?:dereference|subscript|access)|_GLIBCXX_DEBUG|past-the-end|singular iterator",
     "Tryb debug STL wykrył błąd (np. indeks poza vectorem, zły iterator)."),
    (r"SEGV|Segmentation fault|SIGSEGV", "Naruszenie ochrony pamięci (segfault)."),
    (r"bad_alloc|out of memory|allocation-size-too-big", "Brak pamięci / za duża alokacja."),
]


def explain_runtime(stderr: str, signal: str | None = None) -> str | None:
    text = (stderr or "") + " " + (signal or "")
    for pat, msg in _SAN_PATTERNS:
        if re.search(pat, text, re.I):
            return msg
    return None


_SRC_PATH_RE = re.compile(r"(?:[A-Za-z]:)?[^\s:]*[\\/](?:cache[\\/]src[\\/])?[0-9a-f]{24}\.(?:cpp|py)|[0-9a-f]{24}\.(?:cpp|py)")


def clean_paths(text: str) -> str:
    """Ścieżki plików z cache (…/user_data/cache/src/<sha>.cpp) → main.cpp."""
    return _SRC_PATH_RE.sub(lambda m: "main.py" if m.group(0).endswith(".py") else "main.cpp", text or "")


def _sanitizer_report(stderr: str) -> str:
    lines = clean_paths(stderr or "").splitlines()
    keep = [ln for ln in lines if re.search(r"runtime error|ERROR: AddressSanitizer|SUMMARY|#\d+ 0x|main\.cpp:\d+", ln)]
    return "\n".join(keep[:30]) or "\n".join(lines[-20:])


def _float(v, default=None):
    if v in (None, ""):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ApiError("Oczekiwano liczby.")


def _norm_samples(raw) -> list[dict]:
    out = []
    for i, s in enumerate(raw or []):
        if isinstance(s, str):
            if s.strip():
                out.append({"input": inputgen.normalize_sample(s), "output": None, "name": f"przykład {i + 1}"})
        elif isinstance(s, dict) and (s.get("input") or "").strip():
            o = s.get("output")
            out.append({"input": inputgen.normalize_sample(s["input"]),
                        "output": inputgen.normalize_sample(o) if isinstance(o, str) and o.strip() else None,
                        "name": s.get("name") or f"przykład {i + 1}"})
            if s.get("source"):
                out[-1]["source"] = s["source"]
    return out


# ============================================================================================== infer / extract / generate
def handle_infer(body: dict) -> dict:
    statement = body.get("statement") or ""
    samples = _norm_samples(body.get("samples"))
    extracted = False
    if not samples and statement.strip():
        samples = _norm_samples(inputgen.extract_samples(statement))
        extracted = bool(samples)
    if not samples:
        raise ApiError("Podaj co najmniej jeden przykład wejścia (albo wklej treść zadania z sekcją „Przykład”).")
    try:
        spec = inputgen.infer([s["input"] for s in samples], statement or None)
    except ValueError as e:
        raise ApiError(str(e))
    examples = []
    rng = random.Random(int(body.get("seed") or 1))
    for st in ("random", "min", "max"):
        try:
            t = inputgen.generate(spec, st, "small" if st != "max" else "tiny", rng)
            ok, msg = inputgen.validate(spec, t)
            examples.append({"strategy": st, "input": t, "valid": ok, "error": msg})
        except Exception as e:  # noqa: BLE001
            examples.append({"strategy": st, "input": "", "valid": False, "error": str(e)})
    hints = {}
    if statement.strip():
        h = inputgen.statement_hints(statement)
        hints = {"constraints": h.get("constraints"), "flags": {k: v for k, v in (h.get("flags") or {}).items() if v}}
    return {"spec": inputgen.clean_spec(spec), "description": inputgen.describe(spec),
            "warnings": spec.get("warnings") or [], "confidence": spec.get("confidence"),
            "sample_parse": spec.get("sample_parse") or [], "samples": samples, "extracted": extracted,
            "example": examples[0]["input"] if examples else "", "examples": examples, "hints": hints,
            "suggested_strategies": suggest_strategies(spec)}


def suggest_strategies(spec: dict) -> list[str]:
    kinds = {it.get("kind") for it in inputgen._iter_items(spec.get("items") or [])}
    out = ["random", "random_full", "min", "max", "extreme"]
    if "array" in kinds or "matrix" in kinds:
        out += ["equal", "sorted", "reversed", "alternating"]
    if "graph" in kinds or "parents" in kinds:
        out += ["path", "star", "random_tree"]
        if any(it.get("kind") == "graph" and not it.get("tree") for it in inputgen._iter_items(spec["items"])):
            out += ["connected"]
        if any(it.get("kind") == "graph" and it.get("directed") for it in inputgen._iter_items(spec["items"])):
            out += ["dag"]
    if "string" in kinds or "grid" in kinds:
        out += ["equal", "alternating"]
    seen, res = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            res.append(s)
    return res


def handle_extract(body: dict) -> dict:
    st = body.get("statement") or ""
    if not st.strip():
        raise ApiError("Wklej treść zadania.")
    samples = inputgen.extract_samples(st)
    return {"samples": samples, "count": len(samples)}


def _spec_from(body) -> dict:
    spec = body.get("spec")
    if isinstance(spec, str):
        import json
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError as e:
            raise ApiError(f"Specyfikacja to niepoprawny JSON: {e.msg} (linia {e.lineno}).")
    try:
        return inputgen.clean_spec(spec)
    except inputgen.FormatError as e:
        raise ApiError(str(e))


def handle_generate(body: dict) -> dict:
    spec = _spec_from(body)
    st = body.get("strategy") or "random"
    size = body.get("size") or "small"
    seed = body.get("seed")
    try:
        t = inputgen.generate(spec, st, size, seed=int(seed) if seed not in (None, "") else None)
    except Exception as e:  # noqa: BLE001
        raise ApiError(f"Nie udało się wygenerować testu: {e}")
    ok, msg = inputgen.validate(spec, t)
    return {"input": t, "valid": ok, "error": msg, "description": inputgen.describe(spec)}


def handle_validate(body: dict) -> dict:
    spec = _spec_from(body)
    res = []
    for s in _norm_samples(body.get("samples")):
        ok, msg = inputgen.validate(spec, s["input"])
        res.append({"name": s["name"], "valid": ok, "error": msg})
    return {"results": res, "description": inputgen.describe(spec)}


def handle_strategies(_body: dict) -> dict:
    return {"strategies": inputgen.STRATEGIES, "sizes": [{"id": k, "name": SIZE_NAMES[k]} for k in SIZE_ORDER]}


# ============================================================================================== zadanie
def handle_start(body: dict) -> dict:
    sol = body.get("solution") or ""
    if not sol.strip():
        raise ApiError("Wklej swoje rozwiązanie.")
    spec = _spec_from(body)
    strategies = [s for s in (body.get("strategies") or []) if s in inputgen.STRATEGY_IDS] or suggest_strategies(spec)
    count = int(_float(body.get("count"), 200))
    count = max(1, min(count, 20000))
    try:
        from oiforge.core import store
        st = store.settings()
    except Exception:  # noqa: BLE001
        st = {}
    timeout = max(0.05, min(_float(body.get("timeout"), float(st.get("time_limit") or 2.0)), 60.0))
    max_size = body.get("max_size") or "large"
    if max_size not in SIZE_ORDER:
        max_size = "large"
    cfg = {
        "solution": sol, "brute": body.get("brute") or "", "spec": spec, "strategies": strategies,
        "count": count, "timeout": timeout, "brute_timeout": max(timeout * 5, 5.0),
        "samples": _norm_samples(body.get("samples")), "langs": body.get("langs") or {},
        "sanitize": body.get("sanitize") is not False, "max_size": max_size,
        "seed": int(_float(body.get("seed"), random.randint(1, 10 ** 6))),
        "stop_on_first": body.get("stop_on_first") is not False,
        "compare": body.get("compare") or "tokens", "float_eps": _float(body.get("float_eps")),
        "workers": max(1, min(int(_float(body.get("workers"), os.cpu_count() or 4)), 64)),
        "shrink": body.get("shrink") is not False,
    }
    # wstępna weryfikacja specyfikacji (żeby błąd był od razu, a nie w tle)
    try:
        inputgen.generate(spec, strategies[0], "tiny", seed=1)
    except Exception as e:  # noqa: BLE001
        raise ApiError(f"Specyfikacja jest niepoprawna: {e}")
    return {"job": jobs.submit(_work, cfg, title="Auto-testerka"), "seed": cfg["seed"]}


def _lang(cfg, role, src):
    v = (cfg["langs"] or {}).get(role)
    v = {"c++": "cpp", "cpp": "cpp", "py": "python", "python": "python"}.get(str(v).lower()) if v else None
    return v or runner.detect_lang(src)


def _compile(job, role, src, lang, mode="release"):
    prog = runner.prepare(src, lang=lang, mode=mode)
    name = {"solution": "Rozwiązanie", "brute": "Brute", "sanitize": "Rozwiązanie (sanitizery)"}.get(role, role)
    if prog.ok:
        job.push({"type": "compiled", "which": role, "name": name, "lang": prog.lang, "mode": mode,
                  "cached": bool(prog.compile and prog.compile.cached),
                  "compiler": prog.compile.compiler if prog.compile else None})
    else:
        stderr = prog.compile.stderr if prog.compile else prog.error
        expl = []
        try:
            from oiforge.tools import errors  # type: ignore
            expl = errors.translate(stderr, src) or []
        except Exception:  # noqa: BLE001
            pass
        job.push({"type": "compile_error", "which": role, "name": name, "mode": mode, "stderr": stderr[-60000:],
                  "explanations": expl})
    return prog


def _sizes_for(i: int, max_size: str, strategy: str) -> str:
    allowed = SIZE_ORDER[: SIZE_ORDER.index(max_size) + 1]
    if strategy == "max":
        return max_size
    if strategy == "min":
        return "tiny"
    cyc = ["tiny", "small", "small", "tiny", "medium", "small"]
    if "large" in allowed and i % 23 == 22:
        return "large"
    s = cyc[i % len(cyc)]
    return s if s in allowed else allowed[-1]


def _compare(cfg, out, exp):
    if cfg["compare"] == "exact":
        return D.first_difference(out, exp, mode="lines", ignore_whitespace=False)
    if cfg["compare"] == "float":
        return D.first_difference(out, exp, mode="tokens", float_eps=cfg["float_eps"] or 1e-6)
    return D.first_difference(out, exp)


def _check(cfg, progs, inp, size):
    """Ocena jednego testu. Zwraca rekord {verdict, …}."""
    sol, san, brute = progs["solution"], progs.get("sanitize"), progs.get("brute")
    rs = runner.run(sol.cmd, inp, timeout=cfg["timeout"])
    if rs.verdict == "TLE":      # potwierdzenie — chwilowe obciążenie komputera potrafi dać fałszywe TLE
        rs2 = runner.run(sol.cmd, inp, timeout=cfg["timeout"])
        if rs2.verdict != "TLE":
            rs = rs2
    rec = {"verdict": "OK", "time": rs.time, "size": len(inp), "output": rs.stdout}
    if rs.verdict != "OK":
        rec.update(verdict=rs.verdict, stderr=rs.stderr[-6000:], signal=rs.signal, exit_code=rs.exit_code,
                   explanation=explain_runtime(rs.stderr, rs.signal))
        # sanitizery często mówią więcej — uruchom wersję z sanitizerami dla RE
        if rs.verdict == "RE" and san is not None and size not in ("large", "max"):
            r2 = runner.run(san.cmd, inp, timeout=cfg["timeout"] * 6 + 2)
            if r2.stderr:
                rec["sanitizer"] = _sanitizer_report(r2.stderr)
                rec["explanation"] = explain_runtime(r2.stderr, r2.signal) or rec.get("explanation")
        return rec
    if brute is not None:
        rb = runner.run(brute.cmd, inp, timeout=cfg["brute_timeout"])
        rec["brute_time"] = rb.time
        if rb.verdict != "OK":
            rec.update(verdict="SKIP", brute_verdict=rb.verdict)
            return rec
        rec["expected"] = rb.stdout
        first = _compare(cfg, rs.stdout, rb.stdout)
        if first is not None:
            rec.update(verdict="WA", first=first)
            return rec
    # crash-test: niezdefiniowane zachowanie wykryte sanitizerami (tylko małe/średnie testy)
    if san is not None and size not in ("large", "max"):
        r2 = runner.run(san.cmd, inp, timeout=cfg["timeout"] * 6 + 2)
        if r2.verdict == "RE" or re.search(r"runtime error:|ERROR: AddressSanitizer", r2.stderr or ""):
            rec.update(verdict="UB", stderr=r2.stderr[-6000:], sanitizer=_sanitizer_report(r2.stderr),
                       explanation=explain_runtime(r2.stderr, r2.signal) or "Niezdefiniowane zachowanie wykryte "
                       "przez sanitizery (w wersji -O2 program akurat przeszedł).")
    return rec


def _failing(rec):
    return rec["verdict"] not in ("OK", "SKIP")


def _payload(cfg, kind, idx, strategy, size, inp, rec):
    d = None
    if rec.get("expected") is not None:
        d = D.compare(rec.get("output") or "", rec["expected"], mode="tokens", max_rows=400)
    return {"type": kind, "index": idx, "strategy": strategy, "size_class": size, "verdict": rec["verdict"],
            "input": inp[:2_000_000], "output": (rec.get("output") or "")[:200_000],
            "expected": (rec.get("expected") or "")[:200_000] if rec.get("expected") is not None else None,
            "stderr": clean_paths(rec.get("stderr") or "")[-8000:], "sanitizer": rec.get("sanitizer"),
            "explanation": rec.get("explanation"), "time": rec.get("time"), "size": len(inp), "diff": d,
            "first": rec.get("first"), "message": _message(rec)}


def _message(rec):
    v = rec["verdict"]
    if v == "WA":
        return "Wynik różni się od brute’a. " + ((rec.get("first") or {}).get("message") or "")
    if v == "TLE":
        return f"Przekroczony limit czasu ({rec.get('time', 0):.2f} s)."
    if v == "UB":
        return "Niezdefiniowane zachowanie: " + (rec.get("explanation") or "")
    if v in ("RE", "MLE"):
        return ("Błąd wykonania" if v == "RE" else "Za dużo pamięci") + \
            (f": {rec['explanation']}" if rec.get("explanation") else (f" ({rec.get('signal')})" if rec.get("signal") else "."))
    return v


def _work(job, cfg):
    t0 = time.perf_counter()
    spec = cfg["spec"]
    job.progress(0.01, "Kompilacja…")
    progs = {}
    progs["solution"] = _compile(job, "solution", cfg["solution"], _lang(cfg, "solution", cfg["solution"]))
    if not progs["solution"].ok:
        return {"status": "CE", "message": "Błąd kompilacji rozwiązania."}
    if cfg["brute"].strip():
        progs["brute"] = _compile(job, "brute", cfg["brute"], _lang(cfg, "brute", cfg["brute"]))
        if not progs["brute"].ok:
            return {"status": "CE", "message": "Błąd kompilacji brute’a."}
    if cfg["sanitize"] and progs["solution"].lang == "cpp":
        san = _compile(job, "sanitize", cfg["solution"], "cpp", mode="sanitize")
        if san.ok:
            progs["sanitize"] = san
        else:
            job.push({"type": "warning", "message": "Nie udało się skompilować wersji z sanitizerami — "
                                                    "crash-test bez wykrywania UB."})
    # ---- przykłady z treści
    samples_res = []
    for i, s in enumerate(cfg["samples"]):
        ok_fmt, msg = inputgen.validate(spec, s["input"], strict=False)
        rs = runner.run(progs["solution"].cmd, s["input"] + "\n", timeout=cfg["timeout"])
        item = {"index": i, "name": s["name"], "format_ok": ok_fmt, "format_error": msg, "verdict": rs.verdict,
                "time": rs.time, "output": rs.stdout[:20000], "source": s.get("source")}
        if rs.verdict == "OK" and s.get("output") is not None:
            first = _compare(cfg, rs.stdout, s["output"])
            if first is not None:
                item["verdict"] = "WA"
                item["first"] = first
                item["expected"] = s["output"]
                item["diff"] = D.compare(rs.stdout, s["output"], max_rows=200)
        elif rs.verdict != "OK":
            item["stderr"] = rs.stderr[-4000:]
            item["explanation"] = explain_runtime(rs.stderr, rs.signal)
        if "brute" in progs and s.get("output") is not None and len(s["input"]) <= BIG_SAMPLE:
            # (brute jest wolny — na bardzo dużych przykładach, np. teście „ocen” z milionem cyfr, go nie puszczamy)
            rb = runner.run(progs["brute"].cmd, s["input"] + "\n", timeout=cfg["brute_timeout"])
            if rb.verdict != "OK" or _compare(cfg, rb.stdout, s["output"]) is not None:
                item["brute_wrong"] = True
                job.push({"type": "hint", "message": f"Brute daje zły wynik na przykładzie „{s['name']}” — "
                                                     "porównanie z nim może wprowadzać w błąd."})
        samples_res.append(item)
        job.push(dict(item, type="sample"))
    # ---- testy generowane
    counts = {"OK": 0, "WA": 0, "TLE": 0, "RE": 0, "MLE": 0, "UB": 0, "SKIP": 0}
    per_strategy: dict[str, dict] = {s: {"OK": 0, "FAIL": 0, "SKIP": 0} for s in cfg["strategies"]}
    first_fail = None
    stop = {"flag": False}
    pushed = 0
    sol_max = 0.0
    gen_errors = 0
    n = cfg["count"]

    def task(i):
        if stop["flag"] or job.cancelled:
            return None
        st = cfg["strategies"][i % len(cfg["strategies"])]
        size = _sizes_for(i // len(cfg["strategies"]), cfg["max_size"], st)
        rng = random.Random(cfg["seed"] * 1_000_003 + i)
        try:
            inp = inputgen.generate(spec, st, size, rng)
        except Exception as e:  # noqa: BLE001
            return (i, st, size, None, {"verdict": "GENERR", "error": str(e)})
        return (i, st, size, inp, _check(cfg, progs, inp, size))
    done = 0
    last = 0.0
    with cf.ThreadPoolExecutor(max_workers=cfg["workers"]) as ex:
        futs = [ex.submit(task, i) for i in range(n)]
        for f in cf.as_completed(futs):
            r = f.result()
            if r is None:
                continue
            i, st, size, inp, rec = r
            done += 1
            if rec["verdict"] == "GENERR":
                gen_errors += 1
                if gen_errors == 1:
                    job.push({"type": "warning", "message": f"Błąd generowania testu ({st}): {rec['error']}"})
                continue
            counts[rec["verdict"]] = counts.get(rec["verdict"], 0) + 1
            ps = per_strategy.setdefault(st, {"OK": 0, "FAIL": 0, "SKIP": 0})
            ps["OK" if rec["verdict"] == "OK" else ("SKIP" if rec["verdict"] == "SKIP" else "FAIL")] += 1
            sol_max = max(sol_max, rec.get("time") or 0)
            if pushed < MAX_EVENT_TESTS:
                pushed += 1
                job.push({"type": "test", "index": i, "strategy": st, "size_class": size, "verdict": rec["verdict"],
                          "time": rec.get("time"), "brute_time": rec.get("brute_time"), "size": len(inp)})
            if _failing(rec) and first_fail is None:
                first_fail = (i, st, size, inp, rec)
                job.push(_payload(cfg, "failure", i, st, size, inp, rec))
                if cfg["stop_on_first"]:
                    stop["flag"] = True
            now = time.perf_counter()
            job.progress(0.05 + 0.85 * done / n, f"Test {done}/{n} ({st})")
            if now - last > 0.3:
                last = now
                job.push({"type": "stats", "done": done, "total": n, "counts": dict(counts), "sol_max": sol_max,
                          "per_strategy": per_strategy})
            if job.cancelled:
                stop["flag"] = True
    job.push({"type": "stats", "done": done, "total": n, "counts": dict(counts), "sol_max": sol_max,
              "per_strategy": per_strategy})
    # ---- shrinking
    shrunk = None
    if first_fail is not None and cfg["shrink"] and not job.cancelled:
        i, st, size, inp, rec = first_fail
        if rec["verdict"] == "TLE":
            shrunk = {"type": "shrink_skipped", "message": "Przy przekroczeniu czasu nie zmniejszamy testu "
                                                         "(mniejsze testy liczą się szybciej)."}
        else:
            job.push({"type": "shrink_start", "message": "Zmniejszam kontrprzykład…"})
            kind = rec["verdict"]

            def predicate(text):
                if job.cancelled:
                    return False
                r2 = _check(cfg, progs, text, "small")
                if kind == "WA":
                    return r2["verdict"] == "WA"
                if kind in ("RE", "UB", "MLE"):
                    return r2["verdict"] in ("RE", "UB", "MLE")
                return _failing(r2)

            def prog_cb(sz, ev):
                job.progress(0.92 + 0.07 * min(1.0, ev / 300), f"Zmniejszanie: {sz} znaków (próba {ev})")
            res = inputgen.shrink(spec, inp, predicate, max_evals=300, time_budget=40.0, on_progress=prog_cb)
            if res.get("improved") and res["input"] != inp:
                r2 = _check(cfg, progs, res["input"], "small")
                shrunk = _payload(cfg, "shrunk", i, st, size, res["input"], r2)
                shrunk.update({"original_size": len(inp), "evals": res["evals"], "steps": res["steps"],
                               "message": f"Minimalny kontrprzykład: {len(inp)} → {len(res['input'])} znaków. "
                                          + _message(r2)})
            else:
                shrunk = {"type": "shrink_skipped", "message": res.get("error") or
                          "Kontrprzykład jest już minimalny (każda zmiana usuwa błąd)."}
        job.push(shrunk)
    sample_bad = [s for s in samples_res if s["verdict"] != "OK"]
    if first_fail is not None:
        status = first_fail[4]["verdict"]
    elif sample_bad:
        status = "SAMPLE_" + sample_bad[0]["verdict"]
    elif job.cancelled:
        status = "CANCELLED"
    else:
        status = "OK"
    tested = sum(counts.values())
    mode = "porównanie z brute’em" if "brute" in progs else ("crash-test z sanitizerami" if "sanitize" in progs
                                                             else "crash-test")
    if status == "OK":
        msg = f"Brak błędów: {counts['OK']} testów ({mode})" + \
              (f", przykłady {len(samples_res)}/{len(samples_res)} OK." if samples_res else ".")
    elif status.startswith("SAMPLE_"):
        msg = f"Testy losowe przeszły, ale rozwiązanie nie zgadza się z przykładem „{sample_bad[0]['name']}”."
    elif status == "CANCELLED":
        msg = f"Anulowano po {tested} testach."
    else:
        msg = f"Znaleziono błąd ({status}) — {_message(first_fail[4])}"
    if not os.environ.get("OIFORGE_NO_JOURNAL"):
        try:
            from oiforge.tools import journal  # type: ignore
            journal.log_event("autotester_result", {"status": status, "tests": tested, "counts": counts, "mode": mode,
                                                    "strategies": cfg["strategies"], "seed": cfg["seed"]})
        except Exception:  # noqa: BLE001
            pass
    return {"status": status, "message": msg, "mode": mode, "counts": counts, "per_strategy": per_strategy,
            "tested": tested, "samples": samples_res, "failure": _payload(cfg, "failure", *first_fail[:4], first_fail[4])
            if first_fail else None, "shrunk": shrunk if shrunk and shrunk.get("type") == "shrunk" else None,
            "shrink_info": shrunk if shrunk and shrunk.get("type") != "shrunk" else None,
            "seed": cfg["seed"], "elapsed": round(time.perf_counter() - t0, 3), "sol_max": sol_max}


ROUTES = {
    "POST /api/autotester/infer": handle_infer,
    "POST /api/autotester/extract": handle_extract,
    "POST /api/autotester/generate": handle_generate,
    "POST /api/autotester/validate": handle_validate,
    "GET /api/autotester/strategies": handle_strategies,
    "POST /api/autotester/start": handle_start,
}
