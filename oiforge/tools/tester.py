"""Testerka uniwersalna: Twoje rozwiązanie vs brute na testach z TWOJEGO generatora.

Tryb "multi" (domyślny, tak jak pracuje uczeń): generator przy każdym uruchomieniu wypisuje ~1000 testów
oddzielonych PUSTYMI liniami (argv[1] = seed, argv[2] = liczba testów). Tryb "seed": klasyczny stress-test —
każde uruchomienie generatora z argv[1] = seed daje jeden test.

HTTP:
    POST /api/tester/start {solution, brute, generator, langs?, tests_per_run?, runs?, tests?, seed?, timeout?,
                            brute_timeout?, compare: tokens|exact|float, float_eps?, checker?, separator?,
                            stop_on_first?, mode: "multi"|"seed", shrink?}  →  {job}
    GET  /api/tester/templates  →  {generators: [...], checkers: [...]}
    POST /api/tester/split {text, separator?}  →  {tests, count, warnings}   (podgląd podziału wyjścia generatora)
Zdarzenia zadania (job.push): compiled, compile_error, generator, warning, hint, test, stats, failure,
minimized, shrink_start, shrunk, shrink_skipped.
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import random
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from oiforge import config
from oiforge.core import inputgen, jobs, runner
from oiforge.server_api import ApiError
from oiforge.tools import diff as D

CHECKERS_DIR = config.DATA_DIR / "checkers"
MAX_TESTS = 200_000
MAX_EVENT_TESTS = 200
EVENT_TEXT_LIMIT = 200_000
SAVE_TEXT_LIMIT = 2_000_000
COMPARE_MODES = ("tokens", "exact", "float")

# ============================================================================================== szablony generatorów
GEN_TEMPLATE_CPP = r"""// Generator testów dla Testerki OI Forge.
// Uruchamiany jako: ./gen <seed> <liczba_testów>
// Wypisuje N testów w formacie wejścia, a MIĘDZY testami — PUSTĄ LINIĘ.
#include <bits/stdc++.h>
using namespace std;

mt19937_64 rng;
long long rnd(long long a, long long b) { return uniform_int_distribution<long long>(a, b)(rng); }

int main(int argc, char** argv) {
    unsigned long long seed = argc > 1 ? atoll(argv[1]) : 12345;
    int T = argc > 2 ? atoi(argv[2]) : 1000;
    rng.seed(seed);
    for (int t = 0; t < T; t++) {
        if (t) cout << "\n";                 // pusta linia = separator testów
        int n = rnd(1, 8);                   // MAŁE testy — brute musi zdążyć!
        cout << n << "\n";
        for (int i = 0; i < n; i++) cout << rnd(1, 10) << " \n"[i == n - 1];
    }
}
"""

GEN_TEMPLATE_PY = r'''# Generator testów dla Testerki OI Forge.
# Uruchamiany jako: python3 gen.py <seed> <liczba_testów>
# Wypisuje N testów w formacie wejścia, a MIĘDZY testami — PUSTĄ LINIĘ.
import random
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 12345
T = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
random.seed(seed)
out = []
for t in range(T):
    n = random.randint(1, 8)              # MAŁE testy — brute musi zdążyć!
    a = [random.randint(1, 10) for _ in range(n)]
    out.append(f"{n}\n{' '.join(map(str, a))}")
print("\n\n".join(out))
'''

GEN_TEMPLATE_CPP_SEED = r"""// Generator jednego testu (tryb „seed”): ./gen <seed>
#include <bits/stdc++.h>
using namespace std;

int main(int argc, char** argv) {
    mt19937_64 rng(argc > 1 ? atoll(argv[1]) : 1);
    auto rnd = [&](long long a, long long b) { return uniform_int_distribution<long long>(a, b)(rng); };
    int n = rnd(1, 8);
    cout << n << "\n";
    for (int i = 0; i < n; i++) cout << rnd(1, 10) << " \n"[i == n - 1];
}
"""

GEN_TEMPLATE_GRAPH_CPP = r"""// Generator: N testów z drzewem (n wierzchołków, n-1 krawędzi), oddzielonych pustymi liniami.
#include <bits/stdc++.h>
using namespace std;

int main(int argc, char** argv) {
    mt19937 rng(argc > 1 ? atoi(argv[1]) : 1);
    int T = argc > 2 ? atoi(argv[2]) : 1000;
    auto rnd = [&](int a, int b) { return uniform_int_distribution<int>(a, b)(rng); };
    for (int t = 0; t < T; t++) {
        if (t) cout << "\n";
        int n = rnd(1, 8);
        vector<int> perm(n);
        iota(perm.begin(), perm.end(), 1);
        shuffle(perm.begin(), perm.end(), rng);
        cout << n << "\n";
        for (int i = 1; i < n; i++) cout << perm[rnd(0, i - 1)] << " " << perm[i] << "\n";
    }
}
"""

GENERATORS = [
    {"id": "cpp_multi", "lang": "cpp", "name": "C++: N testów z pustymi liniami", "code": GEN_TEMPLATE_CPP,
     "mode": "multi"},
    {"id": "py_multi", "lang": "python", "name": "Python: N testów z pustymi liniami", "code": GEN_TEMPLATE_PY,
     "mode": "multi"},
    {"id": "cpp_tree", "lang": "cpp", "name": "C++: N losowych drzew", "code": GEN_TEMPLATE_GRAPH_CPP, "mode": "multi"},
    {"id": "cpp_seed", "lang": "cpp", "name": "C++: jeden test na seed", "code": GEN_TEMPLATE_CPP_SEED, "mode": "seed"},
]


def checker_templates() -> list[dict]:
    out = []
    if CHECKERS_DIR.is_dir():
        for p in sorted(CHECKERS_DIR.glob("*.cpp")):
            code = p.read_text(encoding="utf-8", errors="replace")
            name = re.search(r"^//\s*name:\s*(.+)$", code, re.M)
            desc = re.search(r"^//\s*description:\s*(.+)$", code, re.M)
            out.append({"id": p.stem, "name": name.group(1).strip() if name else p.stem,
                        "description": desc.group(1).strip() if desc else "", "code": code})
    order = {"float": 0, "permutation": 1, "set": 2, "yesno": 3, "lines_any_order": 4, "custom_template": 9}
    out.sort(key=lambda c: order.get(c["id"], 5))
    return out


# ============================================================================================== dzielenie testów
_BLANK_RE = re.compile(r"\n[ \t\f\v]*\n")


def split_tests(text: str, separator: str | None = None) -> tuple[list[str], list[str]]:
    """Dzieli wyjście generatora na testy. Separator: pusta linia (domyślnie; odporne na \\r\\n, wiele pustych
    linii i spacje w pustych liniach) albo linia równa `separator` (np. "---"). Zwraca (testy, ostrzeżenia)."""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    warnings: list[str] = []
    sep = (separator or "").strip()
    if sep:
        tests, cur = [], []
        for ln in s.split("\n"):
            if ln.strip() == sep:
                tests.append("\n".join(cur))
                cur = []
            else:
                cur.append(ln)
        tests.append("\n".join(cur))
        tests = [_clean_test(t) for t in tests]
        tests = [t for t in tests if t.strip()]
        if len(tests) <= 1 and _BLANK_RE.search(s.strip()):
            warnings.append(f"Nie znaleziono separatora „{sep}”, a w wyjściu są puste linie — "
                            "może generator oddziela testy pustą linią?")
        return tests, warnings
    parts = _BLANK_RE.split("\n" + s.strip("\n") + "\n")
    tests = [_clean_test(p) for p in parts]
    tests = [t for t in tests if t.strip()]
    warnings.extend(_split_warnings(tests))
    return tests, warnings


def _clean_test(t: str) -> str:
    lines = [ln.rstrip() for ln in t.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def _shape(t: str) -> tuple[int, int]:
    lines = t.strip("\n").split("\n")
    return len(lines), len(lines[0].split()) if lines else 0


def _split_warnings(tests: list[str]) -> list[str]:
    """Heurystyka: test z pustą linią „w środku” (np. pusty wiersz w danych) rozpada się na naprzemienne kawałki
    o różnym kształcie — wtedy ostrzegamy i proponujemy własny separator."""
    w = []
    if len(tests) < 6:
        return w
    sh = [_shape(t) for t in tests[:400]]
    n = len(sh)
    alt = sum(1 for i in range(n - 2) if sh[i] == sh[i + 2] and sh[i] != sh[i + 1])
    if alt >= 0.8 * (n - 2) and len(set(sh[0::2])) <= 2 and len(set(sh[1::2])) <= 2:
        w.append("Testy mają na przemian dwa różne kształty — wygląda na to, że w środku każdego testu jest "
                 "pusta linia (np. generator wypisuje pustą linię między nagłówkiem a danymi). Usuń ją albo "
                 "oddzielaj testy własnym separatorem, np. linią „---”.")
        return w
    # pierwsza linia = jedna liczba k, test jednowierszowy, a następny ma k linii/tokenów
    sus = 0
    for i in range(min(n - 1, 300)):
        a = tests[i].strip().split("\n")
        if len(a) == 1 and re.fullmatch(r"\d+", a[0].strip()):
            k = int(a[0])
            b = tests[i + 1].strip().split("\n")
            if k >= 2 and (len(b) == k or len(b[0].split()) == k):
                sus += 1
    if sus >= max(3, (min(n - 1, 300)) // 4):
        w.append("Część testów składa się tylko z jednej liczby, a kolejny „test” ma dokładnie tyle elementów — "
                 "prawdopodobnie pusta linia jest wewnątrz testu. Rozważ własny separator (np. „---”).")
    return w


# ============================================================================================== porównanie / checker
def compare_outputs(out: str, ans: str, mode: str = "tokens", float_eps: float | None = None) -> dict | None:
    """None gdy zgodne; w przeciwnym razie pierwsza różnica (dict z diff.first_difference)."""
    if mode == "exact":
        return D.first_difference(out, ans, mode="lines", ignore_whitespace=False)
    if mode == "float":
        return D.first_difference(out, ans, mode="tokens", float_eps=float_eps if float_eps else 1e-6)
    return D.first_difference(out, ans, mode="tokens")


class Checker:
    """Zewnętrzny checker: `checker in out ans` → kod 0 = OK."""

    def __init__(self, prog: runner.Program, workdir: str, extra_args: list[str] | None = None):
        self.prog = prog
        self.workdir = workdir
        self.extra = extra_args or []
        self._n = 0
        self._lock = threading.Lock()

    def check(self, inp: str, out: str, ans: str, timeout: float = 10.0) -> tuple[bool, str, str | None]:
        with self._lock:
            self._n += 1
            k = self._n
        d = Path(self.workdir) / f"c{k}"
        d.mkdir(parents=True, exist_ok=True)
        pi, po, pa = d / "in.txt", d / "out.txt", d / "ans.txt"
        pi.write_text(inp, encoding="utf-8")
        po.write_text(out, encoding="utf-8")
        pa.write_text(ans, encoding="utf-8")
        rr = runner.run(self.prog.cmd, "", timeout=timeout, args=[str(pi), str(po), str(pa), *self.extra])
        try:
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
        msg = (rr.stdout.strip() or rr.stderr.strip())[:500]
        if rr.verdict == "TLE":
            return False, "Checker przekroczył limit czasu.", "checker_tle"
        if rr.exit_code == 0:
            return True, msg, None
        if rr.exit_code in (1,):
            return False, msg or "Checker: WA", None
        return False, msg or f"Checker zakończył się kodem {rr.exit_code}", "checker_error"


# ============================================================================================== pomocnicze
def _translate(stderr: str, source: str) -> list:
    try:
        from oiforge.tools import errors  # type: ignore
        return errors.translate(stderr, source) or []
    except Exception:  # noqa: BLE001
        return []


def _journal(kind: str, data: dict) -> None:
    if os.environ.get("OIFORGE_NO_JOURNAL"):
        return
    try:
        from oiforge.tools import journal  # type: ignore
        journal.log_event(kind, data)
    except Exception:  # noqa: BLE001
        pass


def _clip(s: str | None, limit: int = EVENT_TEXT_LIMIT) -> str:
    s = s or ""
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n… (obcięto, łącznie {len(s)} znaków)"


_LANG_ALIASES = {"c++": "cpp", "cpp": "cpp", "cxx": "cpp", "py": "python", "python": "python", "python3": "python"}


def _lang(langs, key, src) -> str:
    v = (langs or {}).get(key) if isinstance(langs, dict) else None
    v = _LANG_ALIASES.get(str(v).lower()) if v else None
    return v or runner.detect_lang(src)


ROLE_NAMES = {"solution": "Rozwiązanie", "brute": "Brute", "generator": "Generator", "checker": "Checker"}


def _prepare(job, role: str, source: str, lang: str, mode: str = "release") -> runner.Program:
    t0 = time.perf_counter()
    prog = runner.prepare(source, lang=lang, mode=mode)
    if prog.ok:
        job.push({"type": "compiled", "which": role, "name": ROLE_NAMES.get(role, role), "lang": prog.lang,
                  "cached": bool(prog.compile and prog.compile.cached), "time": round(time.perf_counter() - t0, 3),
                  "warnings": prog.compile.warnings if prog.compile else 0})
    else:
        stderr = prog.compile.stderr if prog.compile else prog.error
        job.push({"type": "compile_error", "which": role, "name": ROLE_NAMES.get(role, role), "lang": prog.lang,
                  "stderr": _clip(stderr, 60_000), "explanations": _translate(stderr, source)})
    return prog


def _float_opt(v, name):
    if v in (None, "", False):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ApiError(f"Pole „{name}” musi być liczbą.")


def _int_opt(v, name, lo, hi, default):
    if v in (None, ""):
        return default
    try:
        x = int(v)
    except (TypeError, ValueError):
        raise ApiError(f"Pole „{name}” musi być liczbą całkowitą.")
    return max(lo, min(hi, x))


def _config_from(body: dict) -> dict:
    sol = body.get("solution") or ""
    brute = body.get("brute") or ""
    gen = body.get("generator") or ""
    if not sol.strip():
        raise ApiError("Wklej swoje rozwiązanie.")
    if not brute.strip() and not (body.get("checker") or "").strip():
        raise ApiError("Wklej rozwiązanie wzorcowe (brute) — z nim porównujemy wyniki.")
    if not gen.strip():
        raise ApiError("Wklej generator testów (albo wstaw szablon przyciskiem „Wstaw szablon generatora”).")
    mode = body.get("mode") or "multi"
    if mode not in ("multi", "seed"):
        raise ApiError("Tryb musi być „multi” albo „seed”.")
    cmp_mode = body.get("compare") or "tokens"
    if cmp_mode not in COMPARE_MODES:
        raise ApiError("Porównanie: tokens, exact albo float.")
    try:
        from oiforge.core import store
        s = store.settings()
    except Exception:  # noqa: BLE001
        s = {}
    timeout = _float_opt(body.get("timeout"), "timeout") or float(s.get("time_limit") or 2.0)
    timeout = max(0.05, min(timeout, 60.0))
    brute_timeout = _float_opt(body.get("brute_timeout"), "brute_timeout") or max(timeout * 5, 5.0)
    seed = body.get("seed")
    if seed in (None, ""):
        seed = random.randint(1, 10 ** 6)
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        raise ApiError("Seed musi być liczbą całkowitą.")
    ts = s.get("tester") if isinstance(s.get("tester"), dict) else {}
    tests_per_run = _int_opt(body.get("tests_per_run"), "tests_per_run", 1, 100_000, int(ts.get("tests") or 1000))
    runs = _int_opt(body.get("runs"), "runs", 1, 1000, 1)
    n_seed_tests = _int_opt(body.get("tests") or (body.get("runs") if mode == "seed" else None),
                            "tests", 1, 100_000, 500)
    checker = (body.get("checker") or "").strip()
    return {
        "solution": sol, "brute": brute, "generator": gen, "langs": body.get("langs") or {},
        "mode": mode, "compare": cmp_mode, "float_eps": _float_opt(body.get("float_eps"), "float_eps") or _float_opt(ts.get("float_eps"), "float_eps"),
        "timeout": timeout, "brute_timeout": max(timeout, min(brute_timeout, 120.0)), "seed": seed,
        "tests_per_run": tests_per_run, "runs": runs, "seed_tests": n_seed_tests,
        "checker": checker, "checker_args": [str(a) for a in (body.get("checker_args") or [])][:4],
        "separator": (body.get("separator") or "").strip() or None,
        "stop_on_first": (body.get("stop_on_first") is not False) if "stop_on_first" in body
        else ts.get("stop_on_first", True) is not False,
        "shrink": body.get("shrink") is not False,
        "workers": _int_opt(body.get("workers") or ts.get("workers") or None, "workers", 1, 64, os.cpu_count() or 4),
        "gen_timeout": _float_opt(body.get("gen_timeout"), "gen_timeout") or 30.0,
        "mem_limit_mb": _int_opt(body.get("memory_limit") or s.get("memory_limit"), "memory_limit", 1, 1 << 20, None),
        "minimize_budget": _float_opt(body.get("minimize_budget"), "minimize_budget") or 60.0,
        "shrink_budget": _float_opt(body.get("shrink_budget"), "shrink_budget") or 40.0,
    }


def _checker_source(spec: str) -> str | None:
    """Id szablonu („float”, „builtin:float”) → kod; w przeciwnym razie traktujemy jako kod źródłowy."""
    s = spec.strip()
    tid = s[8:] if s.startswith("builtin:") else s
    if re.fullmatch(r"[a-z_]{2,40}", tid):
        p = CHECKERS_DIR / f"{tid}.cpp"
        if p.is_file():
            return p.read_text(encoding="utf-8")
        raise ApiError(f"Nie ma szablonu checkera „{tid}”.")
    return s


# ============================================================================================== jeden test
class _Ctx:
    def __init__(self, cfg, sol, brute, checker):
        self.cfg = cfg
        self.sol = sol
        self.brute = brute
        self.checker = checker
        self.stop = threading.Event()


def run_one(ctx: _Ctx, inp: str) -> dict:
    """Uruchamia rozwiązanie i brute na jednym teście i ocenia. Zwraca rekord testu (bez indeksu)."""
    cfg = ctx.cfg
    rs = runner.run(ctx.sol.cmd, inp, timeout=cfg["timeout"], mem_limit_mb=cfg["mem_limit_mb"])
    if rs.verdict == "TLE":
        # potwierdzenie (chwilowe obciążenie komputera potrafi dać fałszywe TLE)
        rs2 = runner.run(ctx.sol.cmd, inp, timeout=cfg["timeout"], mem_limit_mb=cfg["mem_limit_mb"])
        if rs2.verdict != "TLE":
            rs = rs2
    rec = {"verdict": "OK", "time": rs.time, "mem_kb": rs.mem_kb, "size": len(inp)}
    rb = None
    if ctx.brute is not None:
        rb = runner.run(ctx.brute.cmd, inp, timeout=cfg["brute_timeout"])
        rec["brute_time"] = rb.time
        rec["brute_slow"] = rb.verdict == "OK" and rb.time > cfg["timeout"]
    if rs.verdict != "OK":
        rec["verdict"] = rs.verdict
        rec["stderr"] = rs.stderr[-4000:]
        rec["signal"] = rs.signal
        rec["exit_code"] = rs.exit_code
        if rb is not None and rb.verdict == "OK":
            rec["expected"] = rb.stdout
        rec["output"] = rs.stdout
        return rec
    rec["output"] = rs.stdout
    if rb is not None:
        if rb.verdict != "OK":
            rec["verdict"] = "SKIP"
            rec["brute_verdict"] = rb.verdict
            rec["brute_stderr"] = rb.stderr[-2000:]
            return rec
        rec["expected"] = rb.stdout
    if ctx.checker is not None:
        ok, msg, err = ctx.checker.check(inp, rs.stdout, rb.stdout if rb is not None else "")
        rec["checker_message"] = msg
        if err:
            rec["verdict"] = "SKIP"
            rec["checker_error"] = err
        elif not ok:
            rec["verdict"] = "WA"
        return rec
    first = compare_outputs(rs.stdout, rb.stdout, cfg["compare"], cfg["float_eps"])
    if first is not None:
        rec["verdict"] = "WA"
        rec["first"] = first
    return rec


def _failing(rec: dict) -> bool:
    return rec.get("verdict") not in ("OK", "SKIP")


def _failure_payload(ctx, idx, run_no, inp, rec, kind="failure") -> dict:
    cfg = ctx.cfg
    out, exp = rec.get("output") or "", rec.get("expected") or ""
    d = None
    if rec.get("expected") is not None:
        mode = "lines" if cfg["compare"] == "exact" else "tokens"
        eps = (cfg["float_eps"] or 1e-6) if cfg["compare"] == "float" else None
        d = D.compare(_clip(out, 300_000), _clip(exp, 300_000), mode=mode, ignore_whitespace=cfg["compare"] != "exact",
                      float_eps=eps, max_rows=400)
    return {"type": kind, "index": idx, "run": run_no, "verdict": rec.get("verdict"), "input": _clip(inp, SAVE_TEXT_LIMIT),
            "output": _clip(out), "expected": _clip(exp), "stderr": _clip(rec.get("stderr"), 8000),
            "time": rec.get("time"), "brute_time": rec.get("brute_time"), "size": len(inp),
            "diff": d, "first": rec.get("first") or (d or {}).get("first"),
            "checker_message": rec.get("checker_message"), "signal": rec.get("signal"),
            "message": _failure_message(rec)}


def _failure_message(rec: dict) -> str:
    v = rec.get("verdict")
    if v == "WA":
        f = rec.get("first")
        if f:
            return "Złe wyjście. " + f.get("message", "")
        return "Złe wyjście." + (f" Checker: {rec['checker_message']}" if rec.get("checker_message") else "")
    if v == "TLE":
        return f"Przekroczony limit czasu ({rec.get('time', 0):.2f} s)."
    if v == "MLE":
        return "Przekroczony limit pamięci."
    if v == "RE":
        sig = rec.get("signal")
        return "Błąd wykonania" + (f" ({sig})" if sig else f" (kod wyjścia {rec.get('exit_code')})") + "."
    return str(v)


# ============================================================================================== statystyki
class _Stats:
    def __init__(self):
        self.counts = {"OK": 0, "WA": 0, "TLE": 0, "RE": 0, "MLE": 0, "SKIP": 0}
        self.done = 0
        self.sol_max = 0.0
        self.sol_sum = 0.0
        self.brute_max = 0.0
        self.brute_sum = 0.0
        self.brute_n = 0
        self.brute_slow = 0
        self.brute_fail = 0
        self.max_size = 0

    def add(self, rec):
        self.done += 1
        v = rec.get("verdict", "OK")
        self.counts[v] = self.counts.get(v, 0) + 1
        t = rec.get("time") or 0.0
        self.sol_max = max(self.sol_max, t)
        self.sol_sum += t
        if rec.get("brute_time") is not None:
            self.brute_n += 1
            self.brute_max = max(self.brute_max, rec["brute_time"])
            self.brute_sum += rec["brute_time"]
        if rec.get("brute_slow"):
            self.brute_slow += 1
        if rec.get("brute_verdict"):
            self.brute_fail += 1
        self.max_size = max(self.max_size, rec.get("size") or 0)

    def to_dict(self, total=None):
        return {"done": self.done, "total": total, "counts": dict(self.counts),
                "sol_max": round(self.sol_max, 4), "sol_avg": round(self.sol_sum / self.done, 4) if self.done else 0,
                "brute_max": round(self.brute_max, 4),
                "brute_avg": round(self.brute_sum / self.brute_n, 4) if self.brute_n else 0,
                "brute_slow": self.brute_slow, "brute_fail": self.brute_fail, "max_size": self.max_size}


# ============================================================================================== zadanie
def handle_start(body: dict) -> dict:
    cfg = _config_from(body)
    if cfg["checker"]:
        cfg["checker_source"] = _checker_source(cfg["checker"])
    title = "Testerka" + (" (seed)" if cfg["mode"] == "seed" else "")
    return {"job": jobs.submit(_work, cfg, title=title), "seed": cfg["seed"]}


def _work(job, cfg):
    t_start = time.perf_counter()
    workdir = tempfile.mkdtemp(prefix="tester_", dir=str(config.TMP_DIR) if config.TMP_DIR.exists() else None)
    try:
        return _work_inner(job, cfg, workdir, t_start)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _work_inner(job, cfg, workdir, t_start):
    job.progress(0.01, "Kompilacja…")
    srcs = [("solution", cfg["solution"]), ("generator", cfg["generator"])]
    if cfg["brute"].strip():
        srcs.append(("brute", cfg["brute"]))
    if cfg.get("checker_source"):
        srcs.append(("checker", cfg["checker_source"]))
    progs: dict[str, runner.Program] = {}
    with cf.ThreadPoolExecutor(max_workers=len(srcs)) as ex:
        futs = {role: ex.submit(_prepare, job, role, src, _lang(cfg["langs"], role, src) if role != "checker" else "cpp")
                for role, src in srcs}
        for role, f in futs.items():
            progs[role] = f.result()
    bad = [r for r, p in progs.items() if not p.ok]
    if bad:
        names = ", ".join(ROLE_NAMES.get(r, r) for r in bad)
        res = {"status": "CE", "message": f"Błąd kompilacji: {names}.", "failed": bad, "seed": cfg["seed"]}
        _journal("tester_result", {"status": "CE", "failed": bad})
        return res
    checker = Checker(progs["checker"], workdir, cfg["checker_args"]) if "checker" in progs else None
    ctx = _Ctx(cfg, progs["solution"], progs.get("brute"), checker)
    stats = _Stats()
    all_tests: dict[int, tuple[int, str]] = {}         # globalny indeks → (uruchomienie, test) — do minimalizacji
    executed: dict[int, dict] = {}                     # globalny indeks → rekord
    failures: list[tuple[int, int, str, dict]] = []    # (gidx, run, inp, rec)
    state = {"first": None, "last_push": 0.0, "hints": set(), "pushed_tests": 0}
    warnings: list[str] = []
    workers = max(1, cfg["workers"])

    def emit_stats(force=False, total=None):
        now = time.perf_counter()
        if force or now - state["last_push"] > 0.3:
            state["last_push"] = now
            job.push(dict(stats.to_dict(total), type="stats"))

    def record(gidx, run_no, inp, rec):
        executed[gidx] = rec
        stats.add(rec)
        if state["pushed_tests"] < MAX_EVENT_TESTS:
            state["pushed_tests"] += 1
            job.push({"type": "test", "index": gidx, "run": run_no, "verdict": rec["verdict"],
                      "time": rec.get("time"), "brute_time": rec.get("brute_time"), "size": len(inp),
                      "brute_verdict": rec.get("brute_verdict")})
        if rec.get("brute_slow") and "brute_slow" not in state["hints"]:
            state["hints"].add("brute_slow")
            job.push({"type": "hint", "code": "brute_slow",
                      "message": f"Brute jest wolniejszy niż limit czasu ({rec['brute_time']:.2f} s > {cfg['timeout']:.2f} s) "
                                 "— testy są za duże dla brute’a. Zmniejsz rozmiar testów w generatorze."})
        if rec.get("brute_verdict") and "brute_fail" not in state["hints"]:
            state["hints"].add("brute_fail")
            msg = ("Brute przekroczył limit czasu" if rec["brute_verdict"] == "TLE" else "Brute zakończył się błędem") + \
                  f" na teście #{gidx + 1} — test pominięto. " + \
                  ("Zmniejsz testy w generatorze." if rec["brute_verdict"] == "TLE" else "Sprawdź brute’a i generator.")
            job.push({"type": "hint", "code": "brute_fail", "message": msg})
        if rec.get("checker_error") and "checker" not in state["hints"]:
            state["hints"].add("checker")
            job.push({"type": "hint", "code": "checker_error", "message": "Checker zakończył się błędem: " +
                      (rec.get("checker_message") or "")})
        if _failing(rec):
            failures.append((gidx, run_no, inp, rec))
            if state["first"] is None:
                state["first"] = _failure_payload(ctx, gidx, run_no, inp, rec)
                job.push(state["first"])
                if cfg["stop_on_first"]:
                    ctx.stop.set()

    def task(inp):
        if ctx.stop.is_set() or job.cancelled:
            return None
        return run_one(ctx, inp)

    gen = progs["generator"]
    gidx_base = 0
    cancelled = False
    if cfg["mode"] == "multi":
        total_runs = cfg["runs"]
        for r in range(total_runs):
            if ctx.stop.is_set() or job.cancelled:
                break
            seed = cfg["seed"] + r
            job.progress(0.02 + 0.9 * r / total_runs, f"Generator (uruchomienie {r + 1}/{total_runs}, seed {seed})…")
            rg = runner.run(gen.cmd, "", timeout=cfg["gen_timeout"], args=[seed, cfg["tests_per_run"]],
                            max_output=256_000_000)
            if rg.verdict != "OK":
                job.push({"type": "generator_error", "run": r, "seed": seed, "verdict": rg.verdict,
                          "stderr": _clip(rg.stderr, 8000),
                          "message": "Generator przekroczył limit czasu." if rg.verdict == "TLE" else
                          f"Generator zakończył się błędem ({rg.signal or rg.exit_code})."})
                if not all_tests:
                    return {"status": "ERROR", "message": "Generator nie zadziałał.", "seed": cfg["seed"]}
                break
            if rg.truncated:
                warnings.append("Wyjście generatora było za duże i zostało obcięte.")
            h_out = hash(rg.stdout)
            if r > 0 and h_out == state.get("prev_gen"):
                msg = ("Generator wypisał dokładnie to samo co poprzednio — chyba ignoruje seed (argv[1]). "
                       "Użyj go do inicjalizacji losowania, np. mt19937 rng(atoi(argv[1])).")
                if msg not in warnings:
                    warnings.append(msg)
                    job.push({"type": "warning", "message": msg})
                continue
            state["prev_gen"] = h_out
            tests, w = split_tests(rg.stdout, cfg["separator"])
            for x in w:
                if x not in warnings:
                    warnings.append(x)
                    job.push({"type": "warning", "message": x})
            if len(all_tests) + len(tests) > MAX_TESTS:
                tests = tests[: max(0, MAX_TESTS - len(all_tests))]
            job.push({"type": "generator", "run": r, "seed": seed, "tests": len(tests), "time": rg.time,
                      "bytes": len(rg.stdout)})
            if not tests:
                job.push({"type": "warning", "message": f"Generator (seed {seed}) nic nie wypisał."})
                continue
            for i, t in enumerate(tests):
                all_tests[gidx_base + i] = (r, t)
            total = len(tests)
            with cf.ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(task, t): (gidx_base + i, t) for i, t in enumerate(tests)}
                done_n = 0
                for f in cf.as_completed(futs):
                    gidx, t = futs[f]
                    rec = f.result()
                    if rec is None:
                        continue
                    record(gidx, r, t, rec)
                    done_n += 1
                    job.progress(0.02 + 0.9 * (r + done_n / total) / total_runs,
                                 f"Test {stats.done} (uruchomienie {r + 1}/{total_runs})")
                    emit_stats(total=len(all_tests) if total_runs == 1 else None)
                    if job.cancelled:
                        ctx.stop.set()
                        cancelled = True
            gidx_base += len(tests)
    else:
        n = cfg["seed_tests"]

        def seed_task(i):
            if ctx.stop.is_set() or job.cancelled:
                return None
            sd = cfg["seed"] + i
            rg = runner.run(gen.cmd, "", timeout=cfg["gen_timeout"], args=[sd])
            if rg.verdict != "OK":
                return ("generr", sd, rg)
            t = _clean_test(rg.stdout.replace("\r\n", "\n"))
            return ("ok", sd, t, run_one(ctx, t) if t.strip() else None)
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(seed_task, i): i for i in range(n)}
            for f in cf.as_completed(futs):
                i = futs[f]
                res = f.result()
                if res is None:
                    continue
                if res[0] == "generr":
                    if "generr" not in state["hints"]:
                        state["hints"].add("generr")
                        rg = res[2]
                        job.push({"type": "generator_error", "seed": res[1], "verdict": rg.verdict,
                                  "stderr": _clip(rg.stderr, 8000),
                                  "message": f"Generator z seedem {res[1]} zakończył się błędem ({rg.verdict})."})
                    ctx.stop.set()
                    continue
                _, sd, t, rec = res
                if rec is None:
                    continue
                seen_h = state.setdefault("seen", set())
                seen_h.add(hash(t))
                if len(all_tests) == 30 and len(seen_h) == 1 and "same_seed" not in state["hints"]:
                    state["hints"].add("same_seed")
                    job.push({"type": "warning", "message": "Każdy seed daje ten sam test — generator ignoruje argv[1]. "
                                                           "Zainicjuj losowanie seedem, np. mt19937 rng(atoi(argv[1]))."})
                all_tests[i] = (0, t)
                record(i, 0, t, rec)
                job.progress(0.02 + 0.9 * stats.done / n, f"Test {stats.done}/{n} (seed {sd})")
                emit_stats(total=n)
                if job.cancelled:
                    ctx.stop.set()
                    cancelled = True
    emit_stats(force=True, total=len(all_tests))
    cancelled = cancelled or job.cancelled

    # ---------------------------------------------------------------- minimalizacja: najmniejszy błędny test
    minimized = None
    shrunk = None
    shrink_info = None
    if failures and not cancelled:
        minimized = _minimize(job, ctx, cfg, all_tests, executed, failures, workers)
        if minimized is not None:
            job.push(minimized)
        base = minimized or state["first"]
        if cfg["shrink"] and base is not None and not job.cancelled:
            sh = _shrink(job, ctx, cfg, base, all_tests)
            if sh is not None:
                job.push(sh)
                if sh.get("type") == "shrunk":
                    shrunk = sh
                else:
                    shrink_info = sh

    status = "CANCELLED" if cancelled else ("OK" if not failures else (state["first"] or {}).get("verdict", "WA"))
    if not failures and stats.done == 0 and not cancelled:
        status = "EMPTY"
    elapsed = round(time.perf_counter() - t_start, 3)
    summary = {"status": status, "seed": cfg["seed"], "mode": cfg["mode"], "elapsed": elapsed,
               "stats": stats.to_dict(len(all_tests)), "total_generated": len(all_tests),
               "failures": len(failures), "first_failure": state["first"], "minimized": minimized, "shrunk": shrunk,
               "shrink_info": shrink_info,
               "warnings": warnings, "hints": sorted(state["hints"]),
               "message": _summary_message(status, stats, len(failures), elapsed)}
    _journal("tester_result", {"status": status, "tests": stats.done, "failures": len(failures),
                               "counts": stats.counts, "seed": cfg["seed"], "mode": cfg["mode"], "time": elapsed,
                               "verdict": (state["first"] or {}).get("verdict"),
                               "min_size": (minimized or state["first"] or {}).get("size")})
    return summary


def _summary_message(status, stats, nfail, elapsed) -> str:
    if status == "OK":
        return f"Wszystkie {stats.counts['OK']} testy zgodne z brute’em ({elapsed:.1f} s)." if stats.counts.get("SKIP", 0) == 0 \
            else f"{stats.counts['OK']} testów zgodnych, {stats.counts['SKIP']} pominiętych (brute nie dał rady)."
    if status == "CANCELLED":
        return f"Anulowano po {stats.done} testach."
    if status == "EMPTY":
        return "Nie wykonano żadnego testu — sprawdź generator."
    return f"Znaleziono kontrprzykład ({status}). Błędnych testów: {nfail} z {stats.done}."


def _minimize(job, ctx, cfg, all_tests, executed, failures, workers):
    """Szuka NAJMNIEJSZEGO (w znakach) błędnego testu wśród wszystkich wygenerowanych."""
    t0 = time.perf_counter()
    best_g, best_r, best_inp, best_rec = min(failures, key=lambda x: (len(x[2]), x[0]))
    cands = sorted((gi for gi, (_, t) in all_tests.items() if gi not in executed and len(t) < len(best_inp)),
                   key=lambda gi: (len(all_tests[gi][1]), gi))
    checked = 0
    if cands:
        job.progress(0.93, f"Szukam najmniejszego błędnego testu ({len(cands)} kandydatów)…")
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        while cands and not job.cancelled and time.perf_counter() - t0 < cfg["minimize_budget"]:
            chunk, cands = cands[: workers * 2], cands[workers * 2:]
            futs = {ex.submit(run_one, ctx, all_tests[gi][1]): gi for gi in chunk}
            for f in cf.as_completed(futs):
                gi = futs[f]
                rec = f.result()
                checked += 1
                executed[gi] = rec
                if _failing(rec) and len(all_tests[gi][1]) < len(best_inp):
                    best_g, best_r, best_inp, best_rec = gi, all_tests[gi][0], all_tests[gi][1], rec
            cands = [gi for gi in cands if len(all_tests[gi][1]) < len(best_inp)]
    payload = _failure_payload(ctx, best_g, best_r, best_inp, best_rec, kind="minimized")
    payload["same_as_first"] = best_g == failures[0][0]
    payload["checked"] = checked
    payload["complete"] = not cands
    payload["message"] = f"Najmniejszy błędny test: #{best_g + 1} ({len(best_inp)} znaków). " + payload["message"]
    return payload


def _shrink(job, ctx, cfg, base, all_tests):
    """Wnioskuje format z wygenerowanych testów i zmniejsza kontrprzykład (delta debugging z inputgen)."""
    inp = base["input"]
    if len(inp) > 200_000:
        return {"type": "shrink_skipped", "message": "Test jest za duży do automatycznego zmniejszania."}
    job.push({"type": "shrink_start", "message": "Rozpoznaję format wejścia i zmniejszam kontrprzykład…"})
    job.progress(0.96, "Zmniejszanie kontrprzykładu…")
    rnd = random.Random(cfg["seed"])
    pool = [t for _, t in all_tests.values() if len(t) < 20_000]
    sample = rnd.sample(pool, min(len(pool), 30)) if pool else []
    sample.sort(key=len)
    samples = [inp] + [t for t in sample if t != inp][:30]
    try:
        spec = inputgen.infer(samples, time_budget=6.0)
    except Exception as e:  # noqa: BLE001
        return {"type": "shrink_skipped", "message": f"Nie udało się rozpoznać formatu wejścia: {e}"}
    ok, msg = inputgen.validate(spec, inp, strict=False)
    if not ok or not all(spec.get("sample_parse") or [False]):
        return {"type": "shrink_skipped", "message": "Format wejścia nie jest jednolity — pomijam zmniejszanie.",
                "detail": msg}

    def predicate(text):
        if job.cancelled:
            return False
        return _failing(run_one(ctx, text))

    def prog(size, evals):
        job.progress(0.96 + 0.03 * min(1.0, evals / 300), f"Zmniejszanie: {size} znaków (próba {evals})")
    res = inputgen.shrink(spec, inp, predicate, max_evals=300, time_budget=cfg["shrink_budget"], on_progress=prog)
    if not res.get("improved") or res["input"] == inp:
        return {"type": "shrink_skipped", "message": "Nie udało się zmniejszyć testu — jest już minimalny "
                "(albo błąd znika po każdej zmianie).", "spec": inputgen.describe(spec)}
    rec = run_one(ctx, res["input"])
    payload = _failure_payload(ctx, -1, -1, res["input"], rec, kind="shrunk")
    payload.update({"original_size": len(inp), "size": len(res["input"]), "evals": res["evals"],
                    "steps": res["steps"], "spec": inputgen.describe(spec),
                    "message": f"Zmniejszony kontrprzykład: {len(inp)} → {len(res['input'])} znaków "
                               f"({res['evals']} prób). " + _failure_message(rec)})
    return payload


# ============================================================================================== inne endpointy
def handle_templates(_body: dict) -> dict:
    return {"generators": GENERATORS, "checkers": checker_templates()}


def handle_split(body: dict) -> dict:
    text = body.get("text") or ""
    tests, warnings = split_tests(text, body.get("separator"))
    return {"count": len(tests), "tests": tests[:50], "warnings": warnings,
            "sizes": [len(t) for t in tests[:2000]]}


def handle_run_one(body: dict) -> dict:
    """Szybkie sprawdzenie jednego testu (np. zapisany kontrprzykład): {solution, brute, input, ...}."""
    cfg = _config_from({**body, "generator": body.get("generator") or "x"})
    inp = body.get("input") or ""

    class _J:
        cancelled = False

        def push(self, _e):
            pass
    j = _J()
    sol = _prepare(j, "solution", cfg["solution"], _lang(cfg["langs"], "solution", cfg["solution"]))
    brute = _prepare(j, "brute", cfg["brute"], _lang(cfg["langs"], "brute", cfg["brute"])) if cfg["brute"].strip() else None
    if not sol.ok or (brute is not None and not brute.ok):
        raise ApiError("Błąd kompilacji.")
    ctx = _Ctx(cfg, sol, brute, None)
    rec = run_one(ctx, inp)
    return {"result": _failure_payload(ctx, 0, 0, inp, rec, kind="single")}


ROUTES = {
    "POST /api/tester/start": handle_start,
    "GET /api/tester/templates": handle_templates,
    "POST /api/tester/split": handle_split,
    "POST /api/tester/run_one": handle_run_one,
}
