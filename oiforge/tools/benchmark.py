"""Porównanie dwóch rozwiązań (benchmark): czasy i pamięć na rosnących testach, zgodność wyników, szacowana
złożoność (dopasowanie modeli c·f(n) + t0 i nachylenie w skali log-log) oraz werdykt po polsku.

HTTP:
    POST /api/benchmark/start {a, b?, lang_a?, lang_b?, generator, gen_kind?: auto|dsl|code, gen_lang?,
                               sizes: [..] | "1e3, 1e4, 1e5, 2e5", repeats?=3, timeout?=5, seed?=1, mem_limit?}
         → {job}; zdarzenia: compiled, compile_error, generator, size, warning; wynik: {status, rows, series,
           verdict: {headline, messages}, complexity: {a, b}, sizes}
    POST /api/benchmark/sizes {text} → {sizes}
    GET  /api/benchmark/templates    → {templates: [...]}

Generator: źródło mini-języka (zmienna `size` = bieżący rozmiar) albo program C++/Python, który dostaje rozmiar
w argv[1] (i ziarno w argv[2]) i wypisuje JEDEN test.
"""
from __future__ import annotations

import math
import re
import statistics
import time

from oiforge.core import gendsl, jobs, runner
from oiforge.server_api import ApiError

MAX_SIZES = 30
MAX_SIZE = 1_000_000_000
GEN_TIMEOUT = 60.0

# ============================================================================================== rozmiary
_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def _parse_one(tok: str) -> int:
    t = tok.strip().lower().replace(" ", "").replace("_", "").replace("·", "*").replace("×", "*").translate(_SUP)
    t = t.replace("10**", "10^")
    if not t:
        raise ValueError
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)e\+?(\d+)", t)
    if m:
        return round(float(m.group(1).replace(",", ".")) * 10 ** int(m.group(2)))
    m = re.fullmatch(r"(?:(\d+(?:[.,]\d+)?)\*?)?10\^(\d+)", t)
    if m:
        mant = float(m.group(1).replace(",", ".")) if m.group(1) else 1.0
        return round(mant * 10 ** int(m.group(2)))
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)(k|tys|m|mln)", t)
    if m:
        mult = 1000 if m.group(2) in ("k", "tys") else 1_000_000
        return round(float(m.group(1).replace(",", ".")) * mult)
    m = re.fullmatch(r"\d+", t)
    if m:
        return int(t)
    m = re.fullmatch(r"\d+[.,]\d+", t)
    if m:
        return round(float(t.replace(",", ".")))
    raise ValueError


def parse_sizes(spec) -> list[int]:
    """„1e3, 1e4, 1e5, 2e5”, „10^5”, „2·10^5”, „100k”, „1M”, lista liczb → posortowana lista bez powtórzeń."""
    if spec is None or spec == "":
        raise ApiError("Podaj rozmiary testów, np. „1e3, 1e4, 1e5, 2e5”.")
    if isinstance(spec, (list, tuple)):
        toks = [str(x) for x in spec]
    else:
        s = str(spec).strip()
        if ";" in s:
            toks = re.split(r"[;\n]+", s)          # „1,5e5; 2e5” — przecinek może być dziesiętny
        elif re.search(r"[,\n]", s):
            toks = re.split(r"[,\n]+", s)
        else:
            toks = s.split()
    out = []
    for tok in toks:
        if not tok.strip():
            continue
        try:
            v = _parse_one(tok)
        except (ValueError, OverflowError):
            raise ApiError(f"Nie rozumiem rozmiaru „{tok.strip()}” — wpisz np. 1000, 1e5, 2·10^5 albo 100k.") from None
        if v < 1 or v > MAX_SIZE:
            raise ApiError(f"Rozmiar {v} poza zakresem 1..10^9.")
        out.append(v)
    out = sorted(set(out))
    if not out:
        raise ApiError("Podaj co najmniej jeden rozmiar testu.")
    if len(out) > MAX_SIZES:
        raise ApiError(f"Za dużo rozmiarów (najwyżej {MAX_SIZES}).")
    return out


def fmt_n(n: int) -> str:
    """100000 → 10^5, 200000 → 2·10^5, 1500 → 1500."""
    n = int(n)
    if n >= 1000:
        e = int(math.floor(math.log10(n)))
        mant = n / 10 ** e
        if abs(mant - round(mant)) < 1e-9:
            return f"10^{e}" if round(mant) == 1 else f"{int(round(mant))}·10^{e}"
        if abs(mant * 10 - round(mant * 10)) < 1e-9 and n >= 100_000:
            return f"{mant:.1f}".replace(".", ",") + f"·10^{e}"
    return str(n)


def fmt_time(t: float | None) -> str:
    if t is None:
        return "—"
    if t < 1:
        return f"{t * 1000:.0f} ms" if t >= 0.01 else f"{t * 1000:.1f} ms".replace(".", ",")
    return f"{t:.2f} s".replace(".", ",")


def _fmt_factor(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") if x < 10 else f"{x:.0f}"


# ============================================================================================== złożoność
def _lg(n):
    return math.log2(max(n, 2))


COMPLEXITY_CLASSES = [
    ("O(1)", lambda n: 0.0),
    ("O(log n)", lambda n: _lg(n)),
    ("O(√n)", lambda n: math.sqrt(n)),
    ("O(n)", lambda n: float(n)),
    ("O(n log n)", lambda n: n * _lg(n)),
    ("O(n log² n)", lambda n: n * _lg(n) ** 2),
    ("O(n√n)", lambda n: n ** 1.5),
    ("O(n²)", lambda n: float(n) ** 2),
    ("O(n² log n)", lambda n: float(n) ** 2 * _lg(n)),
    ("O(n³)", lambda n: float(n) ** 3),
]


def _fit(points, f):
    """Ważone MNK dla t ≈ c·f(n) + t0 (c, t0 ≥ 0), błąd względny. Zwraca (c, t0, rms_rel)."""
    w = [1.0 / max(t, 1e-6) ** 2 for _, t in points]
    fs = [f(n) for n, _ in points]
    ts = [t for _, t in points]
    sw = sum(w)
    swf = sum(wi * fi for wi, fi in zip(w, fs))
    swff = sum(wi * fi * fi for wi, fi in zip(w, fs))
    swt = sum(wi * ti for wi, ti in zip(w, ts))
    swft = sum(wi * fi * ti for wi, fi, ti in zip(w, fs, ts))
    det = swff * sw - swf * swf
    c = t0 = None
    if det > 1e-300 * max(1.0, swff * sw):
        c = (swft * sw - swf * swt) / det
        t0 = (swff * swt - swf * swft) / det
    if c is None or t0 is None or t0 < 0 or c < 0:
        cands = []
        if swff > 0:
            c1 = max(0.0, swft / swff)
            cands.append((c1, 0.0))
        cands.append((0.0, swt / sw if sw else 0.0))
        best = None
        for cc, tt in cands:
            e = _rel_err(points, f, cc, tt)
            if best is None or e < best[2]:
                best = (cc, tt, e)
        return best
    return c, t0, _rel_err(points, f, c, t0)


def _rel_err(points, f, c, t0):
    errs = [((c * f(n) + t0) - t) / max(t, 1e-6) for n, t in points]
    return math.sqrt(sum(e * e for e in errs) / len(errs))


def estimate_complexity(points) -> dict:
    """points: [(n, czas_s)] → {label, slope, error, confidence, note, fit: [[n, t]], ranking}."""
    pts = sorted((int(n), float(t)) for n, t in points if n and n > 0 and t is not None and t >= 0)
    pts = [(n, max(t, 2e-5)) for n, t in pts]
    if len(pts) < 2:
        return {"label": None, "slope": None, "confidence": "none",
                "note": "Za mało poprawnych pomiarów, żeby oszacować złożoność (potrzeba co najmniej 2 rozmiarów)."}
    ranking = []
    for label, f in COMPLEXITY_CLASSES:
        c, t0, err = _fit(pts, f)
        ranking.append({"label": label, "c": c, "t0": t0, "error": round(err, 4)})
    order = {label: i for i, (label, _) in enumerate(COMPLEXITY_CLASSES)}
    ranking.sort(key=lambda r: r["error"])
    # przy (prawie) równym dopasowaniu wybierz prostszą klasę
    tol = ranking[0]["error"] * 1.1 + 0.01
    best = min((r for r in ranking if r["error"] <= tol), key=lambda r: order[r["label"]])
    ranking.sort(key=lambda r: (r is not best, r["error"]))
    # nachylenie log-log liczone na czasie „ponad start programu” (t − t0)
    tmin = min(t for _, t in pts)
    t0 = min(best["t0"] if best["label"] != "O(1)" else 0.0, 0.9 * tmin)
    info = [(n, t - t0) for n, t in pts if t - t0 > max(0.25 * t, 0.002)]
    flat = max(t for _, t in pts) < 1.5 * tmin
    use = info if len(info) >= 2 else pts[-2:]
    xs = [math.log(n) for n, _ in use]
    ys = [math.log(max(t, 1e-6)) for _, t in use]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 0 else 0.0
    tmax = pts[-1][1]
    span = pts[-1][0] / pts[0][0]
    if flat or len(info) < 2 or span < 4 or len(pts) < 3:
        conf = "low"
    elif len(info) < 3 or best["error"] > 0.25 or (len(ranking) > 1 and ranking[1]["error"] <= best["error"] * 1.15):
        conf = "medium"
    else:
        conf = "high"
    note = ""
    if flat or len(info) < 2:
        note = "Czasy są prawie stałe (dominuje start programu) — zwiększ rozmiary, żeby ocena była wiarygodna."
    elif span < 4:
        note = "Rozmiary są zbyt podobne — rozciągnij zakres (np. 1e3 … 1e6)."
    elif conf == "medium" and len(ranking) > 1:
        note = f"Podobnie pasuje {ranking[1]['label']}."
    fit = [[n, best["c"] * dict(COMPLEXITY_CLASSES)[best["label"]](n) + best["t0"]] for n, _ in pts]
    return {"label": best["label"], "slope": round(slope, 2), "error": best["error"], "confidence": conf,
            "note": note, "fit": fit, "ranking": ranking[:4]}


# ============================================================================================== werdykt
def make_verdict(rows: list[dict], has_b: bool, timeout: float, cx: dict | None = None) -> dict:
    msgs = []
    names = {"a": "A", "b": "B"}
    diff = [r["n"] for r in rows if r.get("same") is False]
    if diff:
        msgs.append({"tone": "danger", "text": "Wyniki A i B RÓŻNIĄ SIĘ dla n = " + ", ".join(fmt_n(n) for n in diff[:6]) +
                     ". Co najmniej jedno rozwiązanie jest błędne — sprawdź je w Testerce (brute vs wzorcówka)."})
    failed_at = {}
    for key in ("a", "b") if has_b else ("a",):
        for verdict, text, tone in (("TLE", "przekracza limit czasu ({t}) od n = {n}", "warn"),
                                    ("MLE", "przekracza limit pamięci od n = {n}", "warn"),
                                    ("RE", "kończy się błędem wykonania dla n = {n}", "danger")):
            bad = [r["n"] for r in rows if (r.get(key) or {}).get("verdict") == verdict]
            if bad:
                failed_at.setdefault(key, bad[0])
                msgs.append({"tone": tone, "text": f"{names[key]} " + text.format(t=fmt_time(timeout), n=fmt_n(bad[0])) +
                             (f" (i dla {len(bad) - 1} większych)" if verdict == "RE" and len(bad) > 1 else "") + "."})
    headline = ""
    if not rows:
        headline = "Brak pomiarów."
    elif has_b:
        common = [r for r in rows if (r.get("a") or {}).get("verdict") == "OK" and (r.get("b") or {}).get("verdict") == "OK"]
        ratios = [(r["n"], max(r["a"]["time"], 1e-5) / max(r["b"]["time"], 1e-5)) for r in common]
        if "a" in failed_at and "b" not in failed_at:
            headline = f"B jest szybsze — A nie mieści się w limicie od n = {fmt_n(failed_at['a'])}, a B przechodzi wszystkie rozmiary."
        elif "b" in failed_at and "a" not in failed_at:
            headline = f"A jest szybsze — B nie mieści się w limicie od n = {fmt_n(failed_at['b'])}, a A przechodzi wszystkie rozmiary."
        elif ratios:
            last_n, last_r = ratios[-1]
            if all(0.8 <= r <= 1.25 for _, r in ratios):
                headline = "A i B są podobnie szybkie (różnica do ~25%)."
            else:
                faster = "B" if last_r > 1 else "A"
                factor = last_r if last_r > 1 else 1 / last_r
                thr = last_n
                for i in range(len(ratios)):
                    if all((r > 1.25) if faster == "B" else (r < 0.8) for _, r in ratios[i:]):
                        thr = ratios[i][0]
                        break
                if factor < 1.25:
                    headline = f"Dla największego n = {fmt_n(last_n)} A i B są podobnie szybkie."
                elif thr == ratios[0][0] and len(ratios) > 1:
                    headline = f"{faster} jest szybsze na wszystkich rozmiarach — ~{_fmt_factor(factor)}× dla n = {fmt_n(last_n)}."
                else:
                    headline = f"{faster} jest ~{_fmt_factor(factor)}× szybsze dla n ≥ {fmt_n(thr)}."
        else:
            headline = "Żadne rozmiary nie dały poprawnych pomiarów obu rozwiązań."
        if cx:
            parts = []
            for k in ("a", "b"):
                c = cx.get(k) or {}
                if not c.get("label"):
                    continue
                if c.get("confidence") == "low":
                    parts.append(f"{names[k]}: za mało danych do oceny (dominuje start programu)")
                else:
                    parts.append(f"{names[k]} ≈ {c['label']} (nachylenie {str(c['slope']).replace('.', ',')})")
            if parts:
                msgs.append({"tone": "info", "text": "Szacowana złożoność: " + "; ".join(parts) + "."})
    else:
        ok = [r for r in rows if (r.get("a") or {}).get("verdict") == "OK"]
        if ok:
            r = ok[-1]
            headline = f"Największy przechodzący test: n = {fmt_n(r['n'])} w {fmt_time(r['a']['time'])}."
            if cx and cx.get("a", {}).get("label"):
                headline += f" Szacowana złożoność: {cx['a']['label']}."
        else:
            headline = "Rozwiązanie nie przeszło żadnego rozmiaru."
    return {"headline": headline, "messages": msgs}


# ============================================================================================== zadanie
def _detect_gen_kind(src: str, kind: str | None) -> str:
    kind = (kind or "auto").lower()
    if kind in ("dsl", "code"):
        return kind
    if re.search(r"#\s*include|\bint\s+main\s*\(|^\s*(import|from)\s+\w|^\s*def\s+\w", src, re.M):
        return "code"
    try:
        gendsl.parse(src)
        return "dsl"
    except gendsl.DslError:
        if re.search(r"\bsys\.argv|\bprint\(|\bcout\b|\bprintf\s*\(", src):
            return "code"
        return "dsl"


def _config(body: dict) -> dict:
    a = body.get("a") or body.get("solution") or ""
    b = body.get("b") or body.get("brute") or ""
    gen = body.get("generator") or ""
    if not a.strip():
        raise ApiError("Wklej rozwiązanie A.")
    if not gen.strip():
        raise ApiError("Podaj generator testu (mini-język z `size` albo program z rozmiarem w argv[1]).")
    sizes = parse_sizes(body.get("sizes") or "1e3, 1e4, 1e5, 2e5")
    try:
        repeats = int(body.get("repeats") or 3)
        timeout = float(body.get("timeout") or 5.0)
        seed = int(body.get("seed") or 1)
    except (TypeError, ValueError):
        raise ApiError("Powtórzenia, limit czasu i ziarno muszą być liczbami.") from None
    mem = body.get("mem_limit") or body.get("memory_limit")
    try:
        mem = int(mem) if mem else None
    except (TypeError, ValueError):
        mem = None
    kind = _detect_gen_kind(gen, body.get("gen_kind"))
    if kind == "dsl":
        try:
            gendsl.parse(gen)
        except gendsl.DslError as e:
            errs = getattr(e, "all", None) or [e]
            raise ApiError("Generator (mini-język): " + e.text(), 400, errors=[x.to_dict() for x in errs[:10]]) from None
    return {"a": a, "b": b if b.strip() else "", "lang_a": body.get("lang_a"), "lang_b": body.get("lang_b"),
            "generator": gen, "gen_kind": kind, "gen_lang": body.get("gen_lang"), "sizes": sizes,
            "repeats": max(1, min(repeats, 15)), "timeout": max(0.1, min(timeout, 60.0)), "seed": max(0, seed),
            "mem_limit": mem}


def handle_start(body: dict) -> dict:
    cfg = _config(body)
    return {"job": jobs.submit(_work, cfg, title="Porównanie rozwiązań"), "sizes": cfg["sizes"], "gen_kind": cfg["gen_kind"]}


def handle_sizes(body: dict) -> dict:
    sizes = parse_sizes(body.get("text") if body.get("text") is not None else body.get("sizes"))
    return {"sizes": sizes, "labels": [fmt_n(s) for s in sizes]}


def _summ(runs: list) -> dict:
    if not runs:
        return {"verdict": "SKIP", "time": None, "mem_kb": None, "times": []}
    bad = next((r for r in runs if r.verdict != "OK"), None)
    times = [r.time for r in runs]
    mems = [r.mem_kb for r in runs if r.mem_kb]
    d = {"verdict": bad.verdict if bad else "OK", "time": round(statistics.median(times), 5) if not bad else
         (round(bad.time, 5) if bad.verdict != "TLE" else None), "times": [round(t, 5) for t in times],
         "mem_kb": max(mems) if mems else None, "wall": round(statistics.median([r.wall for r in runs]), 5)}
    if bad:
        d["signal"] = bad.signal
        d["exit_code"] = bad.exit_code
        if bad.stderr:
            d["stderr"] = bad.stderr[:2000]
    if runs[0].truncated:
        d["truncated"] = True
    return d


def _first_diff(a: str, b: str) -> dict:
    ta, tb = a.split(), b.split()
    for i, (x, y) in enumerate(zip(ta, tb)):
        if x != y:
            return {"token": i + 1, "a": x[:60], "b": y[:60]}
    return {"token": min(len(ta), len(tb)) + 1, "a": f"(koniec po {len(ta)} tokenach)" if len(ta) < len(tb) else ta[len(tb)][:60],
            "b": f"(koniec po {len(tb)} tokenach)" if len(tb) < len(ta) else tb[len(ta)][:60]}


def _prepare(job, which: str, source: str, lang: str | None):
    job.progress(0.0, f"Kompilacja {which.upper()}…")
    prog = runner.prepare(source, lang=lang or None, mode="release")
    if not prog.ok:
        job.push({"type": "compile_error", "which": which, "compile": prog.compile.to_dict() if prog.compile else None,
                  "message": prog.error, "source": source})
        return None
    c = prog.compile
    job.push({"type": "compiled", "which": which, "lang": prog.lang, "cached": bool(c and c.cached),
              "time": c.time if c else 0, "warnings": c.warnings if c else 0})
    return prog


def _work(job, cfg):
    progs = {}
    for which, src, lang in (("a", cfg["a"], cfg["lang_a"]), ("b", cfg["b"], cfg["lang_b"])):
        if not src:
            continue
        p = _prepare(job, which, src, lang)
        if p is None:
            return {"status": "compile_error", "which": which, "rows": [], "sizes": cfg["sizes"]}
        progs[which] = p
    has_b = "b" in progs
    gen_prog = None
    if cfg["gen_kind"] == "code":
        gen_prog = _prepare(job, "generator", cfg["generator"], cfg["gen_lang"])
        if gen_prog is None:
            return {"status": "compile_error", "which": "generator", "rows": [], "sizes": cfg["sizes"]}
    else:
        prog = gendsl.parse(cfg["generator"])
        if "size" not in prog.used_names:
            job.push({"type": "warning", "message": "Generator w mini-języku nie używa zmiennej size — wszystkie rozmiary "
                                                    "dadzą testy tej samej wielkości. Napisz np. n = size."})
    sizes, reps, timeout = cfg["sizes"], cfg["repeats"], cfg["timeout"]
    rows = []
    dead = {}
    status = "done"
    total_steps = len(sizes) * (1 + reps * len(progs))
    step = 0
    t_start = time.perf_counter()
    for idx, n in enumerate(sizes):
        if job.cancelled:
            status = "cancelled"
            break
        job.progress(step / total_steps, f"n = {fmt_n(n)}: generuję test…")
        seed = cfg["seed"] + idx
        g0 = time.perf_counter()
        try:
            if gen_prog is None:
                res = gendsl.run(cfg["generator"], seed=seed, tests=1, vars={"size": n}, preview=0,
                                 max_output=512_000_000, max_steps=2_000_000_000, time_limit=GEN_TIMEOUT)
                inp = res["output"]
            else:
                rg = runner.run(gen_prog.cmd, "", timeout=GEN_TIMEOUT, args=[n, seed], max_output=512_000_000)
                if rg.verdict != "OK":
                    job.push({"type": "generator_error", "n": n, "verdict": rg.verdict, "stderr": (rg.stderr or "")[:4000],
                              "message": "Generator przekroczył limit czasu." if rg.verdict == "TLE" else
                              f"Generator zakończył się błędem ({rg.signal or rg.exit_code})."})
                    status = "error"
                    break
                if rg.truncated:
                    job.push({"type": "warning", "message": f"Test dla n = {fmt_n(n)} był za duży i został obcięty."})
                inp = rg.stdout
        except gendsl.DslError as e:
            job.push({"type": "generator_error", "n": n, "message": "Generator (mini-język): " + e.text(),
                      "line": e.line, "col": e.col})
            status = "error"
            break
        gen_time = time.perf_counter() - g0
        step += 1
        job.push({"type": "generator", "n": n, "bytes": len(inp), "time": round(gen_time, 4)})
        runs = {w: [] for w in progs}
        for r in range(reps):
            for w, p in progs.items():
                if dead.get(w) or (runs[w] and runs[w][-1].verdict != "OK"):
                    step += 1
                    continue
                if job.cancelled:
                    break
                job.progress(step / total_steps, f"n = {fmt_n(n)}: uruchamiam {w.upper()} ({r + 1}/{reps})…")
                runs[w].append(runner.run(p.cmd, inp, timeout=timeout, mem_limit_mb=cfg["mem_limit"]))
                step += 1
        row = {"n": n, "label": fmt_n(n), "input_bytes": len(inp), "gen_time": round(gen_time, 4)}
        for w in progs:
            if dead.get(w):
                row[w] = {"verdict": "SKIP", "time": None, "mem_kb": None, "times": [],
                          "note": "pominięto (przekroczony limit dla mniejszego n)"}
            else:
                row[w] = _summ(runs[w])
                if row[w]["verdict"] in ("TLE", "MLE"):
                    dead[w] = True
        same = None
        if has_b and row["a"]["verdict"] == "OK" and row["b"]["verdict"] == "OK":
            oa, ob = runs["a"][0].stdout, runs["b"][0].stdout
            same = runner.outputs_equal(oa, ob)
            if not same:
                row["diff"] = _first_diff(oa, ob)
        row["same"] = same
        if has_b and row["a"].get("time") and row["b"].get("time") and row["a"]["verdict"] == "OK" and row["b"]["verdict"] == "OK":
            row["ratio"] = round(max(row["a"]["time"], 1e-5) / max(row["b"]["time"], 1e-5), 3)
        rows.append(row)
        job.push({"type": "size", "row": row, "index": idx})
        if dead and len(dead) == len(progs):
            job.push({"type": "warning", "message": "Oba rozwiązania przekroczyły limit — pomijam większe rozmiary."
                      if has_b else "Rozwiązanie przekroczyło limit — pomijam większe rozmiary."})
            break
    cx = {}
    series = []
    for w in progs:
        pts = [(r["n"], r[w]["time"]) for r in rows if r[w]["verdict"] == "OK" and r[w]["time"] is not None]
        cx[w] = estimate_complexity(pts)
        series.append({"name": w.upper(), "points": [[n, round(max(t, 1e-5) * 1000, 3)] for n, t in pts]})
        if cx[w].get("fit") and len(pts) >= 3:
            series.append({"name": f"{w.upper()} ≈ {cx[w]['label']}", "dashed": True,
                           "points": [[n, round(max(t, 1e-5) * 1000, 3)] for n, t in cx[w]["fit"]]})
    verdict = make_verdict(rows, has_b, timeout, cx)
    return {"status": status, "rows": rows, "series": series, "verdict": verdict, "complexity": cx,
            "sizes": sizes, "has_b": has_b, "langs": {w: p.lang for w, p in progs.items()},
            "gen_kind": cfg["gen_kind"], "elapsed": round(time.perf_counter() - t_start, 2),
            "settings": {"repeats": reps, "timeout": timeout, "seed": cfg["seed"]}}


# ============================================================================================== szablony
_GEN_ARRAY = "# size = bieżący rozmiar testu\nn = size\nprint n\nprint array(n, int(1, 1e9))\n"
_GEN_QUERIES = "n = size\nq = size\nprint n, q\nprint array(n, int(1, 1e9))\nprint pairs(q, 1, n)\n"

TEMPLATES = [
    {"id": "sort", "name": "Sortowanie: bąbelkowe O(n²) vs std::sort O(n log n)", "sizes": "1e3, 2e3, 5e3, 1e4, 2e4",
     "gen_kind": "dsl", "generator": _GEN_ARRAY,
     "a": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios_base::sync_with_stdio(false);\n    cin.tie(nullptr);\n    int n;\n    cin >> n;\n    vector<long long> a(n);\n    for (auto& x : a) cin >> x;\n    // sortowanie bąbelkowe: O(n²)\n    for (int i = 0; i < n; i++)\n        for (int j = 0; j + 1 < n - i; j++)\n            if (a[j] > a[j + 1]) swap(a[j], a[j + 1]);\n    long long s = 0;\n    for (int i = 0; i < n; i++) s += a[i] % 1000 * (i + 1);\n    cout << s << '\\n';\n}\n",
     "b": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios_base::sync_with_stdio(false);\n    cin.tie(nullptr);\n    int n;\n    cin >> n;\n    vector<long long> a(n);\n    for (auto& x : a) cin >> x;\n    sort(a.begin(), a.end());   // O(n log n)\n    long long s = 0;\n    for (int i = 0; i < n; i++) s += a[i] % 1000 * (i + 1);\n    cout << s << '\\n';\n}\n"},
    {"id": "prefix", "name": "Sumy na przedziałach: naiwnie O(n·q) vs sumy prefiksowe O(n + q)",
     "sizes": "1e3, 3e3, 1e4, 3e4", "gen_kind": "dsl", "generator": _GEN_QUERIES,
     "a": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios_base::sync_with_stdio(false);\n    cin.tie(nullptr);\n    int n, q;\n    cin >> n >> q;\n    vector<long long> a(n + 1);\n    for (int i = 1; i <= n; i++) cin >> a[i];\n    while (q--) {\n        int l, r;\n        cin >> l >> r;\n        long long s = 0;\n        for (int i = l; i <= r; i++) s += a[i];   // O(n) na zapytanie\n        cout << s << '\\n';\n    }\n}\n",
     "b": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios_base::sync_with_stdio(false);\n    cin.tie(nullptr);\n    int n, q;\n    cin >> n >> q;\n    vector<long long> p(n + 1, 0);\n    for (int i = 1; i <= n; i++) { long long x; cin >> x; p[i] = p[i - 1] + x; }\n    while (q--) {\n        int l, r;\n        cin >> l >> r;\n        cout << p[r] - p[l - 1] << '\\n';   // O(1) na zapytanie\n    }\n}\n"},
    {"id": "python-cpp", "name": "To samo rozwiązanie: Python vs C++", "sizes": "1e4, 1e5, 3e5, 1e6", "gen_kind": "dsl",
     "generator": _GEN_ARRAY,
     "a": "import sys\n\ndata = sys.stdin.buffer.read().split()\nn = int(data[0])\na = sorted(map(int, data[1:1 + n]))\nprint(sum(x % 1000 * (i + 1) for i, x in enumerate(a)))\n",
     "b": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios_base::sync_with_stdio(false);\n    cin.tie(nullptr);\n    int n;\n    cin >> n;\n    vector<long long> a(n);\n    for (auto& x : a) cin >> x;\n    sort(a.begin(), a.end());\n    long long s = 0;\n    for (int i = 0; i < n; i++) s += a[i] % 1000 * (i + 1);\n    cout << s << '\\n';\n}\n"},
    {"id": "primes", "name": "Liczby pierwsze ≤ n: dzielenie próbne O(n√n) vs sito O(n log log n)",
     "sizes": "1e4, 1e5, 1e6, 3e6", "gen_kind": "dsl", "generator": "print size\n",
     "a": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    int n;\n    cin >> n;\n    int cnt = 0;\n    for (int x = 2; x <= n; x++) {\n        bool prime = true;\n        for (int d = 2; (long long)d * d <= x; d++)\n            if (x % d == 0) { prime = false; break; }\n        cnt += prime;\n    }\n    cout << cnt << '\\n';\n}\n",
     "b": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    int n;\n    cin >> n;\n    vector<bool> comp(n + 1, false);\n    int cnt = 0;\n    for (int x = 2; x <= n; x++) {\n        if (comp[x]) continue;\n        cnt++;\n        for (long long y = (long long)x * x; y <= n; y += x) comp[y] = true;\n    }\n    cout << cnt << '\\n';\n}\n"},
]


def handle_templates(_q: dict) -> dict:
    return {"templates": TEMPLATES}


ROUTES = {
    "POST /api/benchmark/start": handle_start,
    "POST /api/benchmark/sizes": handle_sizes,
    "GET /api/benchmark/templates": handle_templates,
}
