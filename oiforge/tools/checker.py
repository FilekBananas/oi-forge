"""Sprawdzarka kodu — „wysyłam kod, a on go debuguje”.

1. Analiza statyczna (szybka, synchroniczna): ponad 60 reguł na tokenach (`_checker_rules`).
2. Analiza dynamiczna (zadanie w tle): kompilacja w kilku wariantach i uruchomienie na podanych wejściach:
   - release (-O2 + rozszerzone ostrzeżenia, tłumaczone przez `errors.translate`),
   - sanitize (ASan + UBSan + hardening libc++ / _GLIBCXX_DEBUG) — raporty z LINIĄ w kodzie użytkownika,
   - -O0 — różny wynik niż -O2 ⇒ prawie na pewno UB,
   - -ftrivial-auto-var-init=pattern vs =zero — różny wynik ⇒ zależność od niezainicjalizowanej zmiennej,
   - tryb debug STL GCC (_GLIBCXX_DEBUG) — indeks i rozmiar przy wyjściu poza vector,
   - mały stos (8 MB) — wykrywa rekurencję, która działa tylko z powiększonym stosem,
   - TLE: odróżnia „za wolno” od „nieskończona pętla” (dłuższy limit).
   Wynik: posortowana lista problemów z pewnością + werdykt zdrowia kodu.

HTTP:
    POST /api/checker/analyze {code}                 -> {issues, stats}
    POST /api/checker/run {code, inputs, timeout?}   -> {job}   (wynik: GET /api/jobs/<id>)
    GET  /api/checker/rules                          -> {rules, categories}
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import shutil
import time

from oiforge import config
from oiforge.core import jobs, runner
from oiforge.server_api import ApiError

from . import _checker_rules as rules
from . import errors

MAX_CODE = 400_000
MAX_INPUTS = 12
MAX_INPUT_LEN = 8_000_000

SEV_ORDER = rules.SEVERITY_ORDER


# ================================================================================================ pomocnicze
def _kb_ids() -> set | None:
    try:
        from oiforge.core import kb
        return {a["id"] for a in kb.articles()}
    except Exception:  # noqa: BLE001
        return None


def _log_journal(issues: list[dict], source: str):
    """Zapis do dziennika błędów (moduł innego agenta — opcjonalny). OIFORGE_NO_JOURNAL=1 wyłącza (testy)."""
    import os
    if os.environ.get("OIFORGE_NO_JOURNAL"):
        return
    try:
        from oiforge.tools import journal  # type: ignore
    except Exception:  # noqa: BLE001
        return
    seen = set()
    for it in issues:
        key = (it.get("rule"), it.get("severity"))
        if key in seen or it.get("severity") not in ("error", "warning"):
            continue
        seen.add(key)
        try:
            journal.log_event("checker_issue", {"rule": it.get("rule"), "severity": it.get("severity"),
                                                "title": it.get("title"), "source": source})
        except Exception:  # noqa: BLE001
            return


def _is_python(code: str) -> bool:
    try:
        return runner.detect_lang(code) == "python"
    except Exception:  # noqa: BLE001
        return False


def static_analysis(code: str) -> dict:
    res = rules.analyze(code, kb_ids=_kb_ids())
    return res


# ================================================================================================ HTTP: statyczna
def handle_analyze(body: dict) -> dict:
    code = body.get("code") or ""
    if not code.strip():
        raise ApiError("Wklej kod do analizy.")
    if len(code) > MAX_CODE:
        raise ApiError("Kod jest za długi do analizy (limit 400 tys. znaków).")
    if _is_python(code):
        return {"issues": [], "stats": {"lines": code.count("\n") + 1, "language": "python", "total": 0,
                                        "by_severity": {}, "health": {"score": 100, "label": "Python — analiza statyczna "
                                                                      "tylko dla C++", "tone": "info"}},
                "note": "Analiza statyczna obsługuje C++. Dla Pythona użyj „Uruchom z sanitizerami” — "
                        "program zostanie uruchomiony, a błędy wykonania wyjaśnione."}
    res = static_analysis(code)
    _log_journal(res["issues"], "static")
    out = {"issues": res["issues"], "stats": res["stats"]}
    if res.get("internal_errors"):
        out["internal_errors"] = res["internal_errors"]
    return out


def handle_rules(_q: dict) -> dict:
    return {"rules": rules.rules_catalog(), "categories": rules.CATEGORIES, "count": len(rules.RULES)}


# ================================================================================================ dynamiczna: warianty
WARN_FLAGS = ["-Wshadow", "-Wconversion", "-Wfloat-equal", "-Wformat=2", "-Wlogical-op", "-Wduplicated-cond",
              "-Wnull-dereference", "-Wuninitialized", "-Wmaybe-uninitialized", "-Wshift-overflow=2",
              "-Wsign-compare", "-Wtype-limits", "-Wreturn-type", "-Wsequence-point", "-Wparentheses",
              "-Wmisleading-indentation", "-Wempty-body", "-Wimplicit-fallthrough", "-Wsizeof-array-argument",
              "-Wclass-memaccess", "-Waggressive-loop-optimizations", "-Warray-bounds"]
CLANG_WARN_FLAGS = ["-Wshadow", "-Wconversion", "-Wfloat-equal", "-Wformat=2", "-Wnull-dereference",
                    "-Wuninitialized", "-Wsometimes-uninitialized", "-Wsign-compare", "-Wtautological-compare",
                    "-Wreturn-type", "-Wunsequenced", "-Wparentheses", "-Wmisleading-indentation", "-Wempty-body",
                    "-Wimplicit-fallthrough", "-Warray-bounds", "-Wshift-overflow", "-Wsizeof-array-argument"]

VARIANT_INFO = {
    "release": "Kompilacja wydaniowa (-O2), jak na sprawdzarce",
    "sanitize": "Sanitizery: AddressSanitizer + UndefinedBehaviorSanitizer + kontrola zakresów STL",
    "o0": "Bez optymalizacji (-O0) — porównanie z -O2 wykrywa UB",
    "init_pattern": "Zmienne lokalne wypełnione wzorcem 0xAA… — wykrywa niezainicjalizowane zmienne",
    "init_zero": "Zmienne lokalne wyzerowane — porównanie z wzorcem",
    "stl_debug": "Tryb debug biblioteki STL GCC (_GLIBCXX_DEBUG) — indeks i rozmiar przy wyjściu poza zakres",
    "small_stack": "Stos 8 MB (jak domyślnie w Linuksie) — wykrywa zbyt głęboką rekurencję",
}


def _small_stack_flags() -> list[str] | None:
    if config.IS_MAC:
        return ["-Wl,-stack_size,0x800000"]
    if config.IS_WINDOWS:
        return ["-Wl,--stack,8388608"]
    return None


def _variant_specs(caps: dict, want: set | None) -> dict:
    gcc = config.GCC
    cxx_is_clang = runner._is_clang(config.CXX)
    specs = {
        "release": {"mode": "release", "extra": CLANG_WARN_FLAGS if cxx_is_clang else WARN_FLAGS},
        "o0": {"mode": "release", "extra": ["-O0"]},
        "init_pattern": {"mode": "release", "extra": ["-ftrivial-auto-var-init=pattern"]},
        "init_zero": {"mode": "release", "extra": ["-ftrivial-auto-var-init=zero"]},
    }
    if caps.get("sanitizers"):
        specs["sanitize"] = {"mode": "sanitize", "extra": []}
    if gcc:
        specs["stl_debug"] = {"mode": "release", "compiler": gcc,
                              "extra": ["-O1", "-g", "-D_GLIBCXX_DEBUG", "-D_GLIBCXX_DEBUG_PEDANTIC",
                                        "-D_GLIBCXX_ASSERTIONS"]}
    ss = _small_stack_flags()
    if ss:
        specs["small_stack"] = {"mode": "release", "extra": ss}
    elif config.IS_LINUX and (shutil.which("prlimit") or shutil.which("sh")):
        specs["small_stack"] = {"mode": "release", "extra": [], "linux_ulimit": True}
    if want:
        specs = {k: v for k, v in specs.items() if k in want or k == "release"}
    return specs


def _compile_variant(code: str, name: str, spec: dict, std: str):
    t0 = time.perf_counter()
    try:
        cr = runner.compile_cpp(code, mode=spec["mode"], std=std, extra_flags=spec.get("extra") or None,
                                compiler=spec.get("compiler"))
    except Exception as e:  # noqa: BLE001
        cr = runner.CompileResult(ok=False, stderr=f"Błąd kompilacji wariantu {name}: {e}")
    return name, cr, time.perf_counter() - t0


def _cmd_for(name: str, spec: dict, cr) -> list:
    if spec.get("linux_ulimit"):
        pl = shutil.which("prlimit")
        if pl:
            return [pl, "--stack=8388608", cr.exe]
        return [shutil.which("sh") or "sh", "-c", 'ulimit -s 8192 2>/dev/null; exec "$0"', cr.exe]
    return [cr.exe]


_SAN_ENV = {
    "ASAN_OPTIONS": "detect_leaks=0:abort_on_error=0:symbolize=1:print_summary=1:handle_abort=1:handle_segv=1:"
                    "handle_sigbus=1:handle_sigfpe=1:handle_sigill=1:detect_stack_use_after_return=1:"
                    "check_initialization_order=1:strict_init_order=1:allocator_may_return_null=0:"
                    "detect_container_overflow=1:print_scariness=0",
    "UBSAN_OPTIONS": "print_stacktrace=1:halt_on_error=0:report_error_type=1",
}


# ================================================================================================ parsowanie raportów
_FRAME_RE = re.compile(r"^\s*#(?P<n>\d+)\s+0x[0-9a-fA-F]+\s+in\s+(?P<fn>.+?)\s+(?P<file>[^\s()]+?):(?P<line>\d+)(?::(?P<col>\d+))?\s*$")
_FRAME_NOSRC_RE = re.compile(r"^\s*#(?P<n>\d+)\s+0x[0-9a-fA-F]+\s+in\s+(?P<fn>.+?)\s+\(")
_UBSAN_RE = re.compile(r"^(?P<file>[^\s:]+):(?P<line>\d+):(?P<col>\d+): runtime error: (?P<msg>.+)$", re.M)
_ASAN_RE = re.compile(r"==\d+==ERROR: AddressSanitizer: (?P<kind>[\w-]+)(?: on (?:unknown )?address (?P<addr>0x[0-9a-f]+))?(?P<rest>.*)")
_ACCESS_RE = re.compile(r"(?P<rw>READ|WRITE) of size (?P<size>\d+) at")
_REGION_RE = re.compile(r"is located (?P<off>\d+) bytes (?P<where>after|before|inside of) (?P<sz>\d+)-byte region")
_STACKVAR_RE = re.compile(r"\[(?P<a>\d+), (?P<b>\d+)\) '(?P<var>[^']+)'(?: \(line (?P<line>\d+)\))? <== Memory access at offset (?P<off>\d+) (?P<how>overflows|underflows|partially overflows|is inside) this variable")
_GLOBALVAR_RE = re.compile(r"is located (?P<off>\d+) bytes (?P<where>after|before) global variable '(?P<var>[^']+)'(?: defined in '[^']*:(?P<line>\d+)(?::\d+)?')? \(0x[0-9a-f]+\) of size (?P<sz>\d+)")
_HARD_RE = re.compile(r"libc\+\+ Hardening assertion (?P<cond>.+?) failed: (?P<msg>.+)")
_GLIBCXX_RE = re.compile(r"Error: (?P<msg>attempt to[^\n]*(?:\n(?!\n)[^\n]*)*)")
_EXC_RE = re.compile(r"(?:terminate called after throwing an instance of '(?P<a>[^']+)'(?:\s*\n\s*what\(\):\s*(?P<wa>[^\n]*))?|"
                     r"terminating (?:due to|with) uncaught exception of type (?P<b>[^:\n]+)(?::\s*(?P<wb>[^\n]*))?)")


_RAW_FRAME_RE = re.compile(r"^(?P<pre>\s*#(?P<n>\d+)\s+0x[0-9a-fA-F]+)\s+(?:in\s+\S+\s+)?\((?P<mod>[^()]+?):(?P<arch>[\w]+)\+(?P<off>0x[0-9a-fA-F]+)\)\s*$")
_ATOS_RE = re.compile(r"^(?P<fn>.+?) \(in [^)]+\) \((?P<file>[^:()]+):(?P<line>\d+)\)\s*$")


def _symbolize_mac(text: str, exe: str) -> str:
    """macOS: ASan z symbolize=1 woła atos dla każdej biblioteki (~5 s). Symbolizujemy sami tylko ramki programu."""
    if not text or not exe or "#" not in text:
        return text
    atos = shutil.which("atos")
    if not atos:
        return text
    lines = text.split("\n")
    todo = []
    for k, ln in enumerate(lines):
        m = _RAW_FRAME_RE.match(ln)
        if m and m.group("mod") == exe:
            off = int(m.group("off"), 16)
            if int(m.group("n")) > 0:
                off -= 1
            todo.append((k, m, off))
    if not todo:
        return text
    arch = todo[0][1].group("arch").replace("arm64e", "arm64")
    try:
        import subprocess
        p = subprocess.run([atos, "-o", exe, "-arch", arch] + [hex(o) for _, _, o in todo[:64]],
                           capture_output=True, text=True, timeout=45)
        outs = p.stdout.strip().split("\n")
    except Exception:  # noqa: BLE001
        return text
    for (k, m, _), res in zip(todo, outs):
        am = _ATOS_RE.match(res.strip())
        if am:
            lines[k] = f"{m.group('pre')} in {am.group('fn')} {am.group('file')}:{am.group('line')}"
    return "\n".join(lines)


_RAW_FRAME_LINUX_RE = re.compile(r"^(?P<pre>\s*#(?P<n>\d+)\s+0x[0-9a-fA-F]+)\s+(?:in\s+\S+\s+)?\((?P<mod>[^()+]+)\+(?P<off>0x[0-9a-fA-F]+)\)\s*$")


def _symbolize_linux(text: str, exe: str) -> str:
    """Linux bez llvm-symbolizer: ASan wypisuje surowe ramki `(exe+0x1234)` — tłumaczymy je przez addr2line."""
    if not text or not exe or "#" not in text or " in main " in text:
        return text
    tool = shutil.which("addr2line") or shutil.which("llvm-addr2line")
    if not tool:
        return text
    lines = text.split("\n")
    todo = []
    for k, ln in enumerate(lines):
        m = _RAW_FRAME_LINUX_RE.match(ln)
        if m and m.group("mod") == exe:
            off = int(m.group("off"), 16) - (1 if int(m.group("n")) > 0 else 0)
            todo.append((k, m, off))
    if not todo:
        return text
    try:
        import subprocess
        p = subprocess.run([tool, "-e", exe, "-f", "-C"] + [hex(o) for _, _, o in todo[:64]],
                           capture_output=True, text=True, timeout=45)
        outs = p.stdout.strip().split("\n")
    except Exception:  # noqa: BLE001
        return text
    for idx, (k, m, _) in enumerate(todo):
        if 2 * idx + 1 >= len(outs):
            break
        fn, loc = outs[2 * idx].strip(), outs[2 * idx + 1].strip()
        lm = re.match(r"(?P<file>[^:]+):(?P<line>\d+)", loc)
        if lm and fn != "??":
            lines[k] = f"{m.group('pre')} in {fn} {lm.group('file')}:{lm.group('line')}"
    return "\n".join(lines)


def _normalize_paths(text: str, cr) -> str:
    if not text:
        return text
    sp = getattr(cr, "source_path", None)
    if sp:
        text = text.replace(sp, "main.cpp")
        from pathlib import Path
        text = text.replace(Path(sp).name, "main.cpp")
    return text


def _user_frames(text: str) -> list[dict]:
    out = []
    for ln in (text or "").split("\n"):
        m = _FRAME_RE.match(ln)
        if m and m.group("file").replace("\\", "/").split("/")[-1] == "main.cpp":
            out.append({"fn": _short_fn(m.group("fn")), "line": int(m.group("line")), "col": int(m.group("col") or 0)})
    return out


def _short_fn(fn: str) -> str:
    fn = errors.simplify_types(fn)
    fn = re.sub(r"\(.*\)$", "", fn).strip()
    return fn[:80]


def _code_line(code: str, line: int) -> str:
    ls = code.split("\n")
    return ls[line - 1].strip()[:200] if 1 <= line <= len(ls) else ""


_UB_TRANSLATIONS = [
    (r"signed integer overflow: (?P<a>-?\d+) (?P<op>[-+*]) (?P<b>-?\d+) cannot be represented in type '(?:[^']+)'(?: \(aka '(?P<aka>[^']+)'\))?",
     "ub-overflow", "Przepełnienie liczby całkowitej",
     lambda m: f"Przepełnienie: {m['a']} {m['op']} {m['b']} nie mieści się w typie {_ub_type(m)} "
               f"(zakres int to ±2 147 483 647). W -O2 wynik jest „zawinięty” albo kompilator wytnie kod.",
     lambda m: "Licz w long long: zadeklaruj zmienne jako `long long` albo wymuś typ przy działaniu `1LL * a * b`."),
    (r"negation of (?P<a>-?\d+) cannot be represented in type '(?P<t>[^']+)'", "ub-overflow", "Przepełnienie przy negacji",
     lambda m: f"-({m['a']}) nie mieści się w {m['t']} (INT_MIN nie ma dodatniego odpowiednika).",
     lambda m: "Użyj long long."),
    (r"index (?P<i>-?\d+) out of bounds for type '(?P<t>[^']+)'", "ub-index", "Indeks poza tablicą",
     lambda m: f"Wyjście poza tablicę: indeks {m['i']}, a tablica ma typ {m['t']} "
               f"(poprawne indeksy 0…{_arr_len(m['t']) - 1 if _arr_len(m['t']) else 'rozmiar-1'}).",
     lambda m: "Powiększ tablicę albo popraw warunek pętli (`<` zamiast `<=`)."),
    (r"shift exponent (?P<e>-?\d+) is (?:too large for (?P<bits>\d+)-bit type|negative)(?: '(?P<t>[^']+)')?", "ub-shift",
     "Przesunięcie bitowe poza zakres",
     lambda m: f"Przesunięcie o {m['e']} bitów" + (f" w {m['bits']}-bitowym typie {m['t']}" if m['bits'] else "") + " — UB.",
     lambda m: "Użyj `1LL << k` dla k ≥ 31; sprawdź, czy k nie jest ujemne."),
    (r"left shift of (?P<a>-?\d+) by (?P<e>\d+) places cannot be represented in type '(?P<t>[^']+)'", "ub-shift",
     "Przesunięcie bitowe przepełnia typ",
     lambda m: f"{m['a']} << {m['e']} nie mieści się w {m['t']}.", lambda m: "Użyj `1LL << k`."),
    (r"left shift of negative value (?P<a>-?\d+)", "ub-shift", "Przesunięcie liczby ujemnej",
     lambda m: f"Przesuwasz w lewo liczbę ujemną ({m['a']}) — to UB.", lambda m: "Przesuwaj wartości nieujemne (unsigned/long long)."),
    (r"division by zero", "ub-div-zero", "Dzielenie przez zero", lambda m: "Dzielenie (albo modulo) przez zero.",
     lambda m: "Sprawdź dzielnik przed dzieleniem."),
    (r"division of (?P<a>-?\d+) by -1 cannot be represented", "ub-overflow", "Przepełnienie dzielenia",
     lambda m: f"{m['a']} / -1 nie mieści się w typie.", lambda m: "Użyj long long."),
    (r"load of value (?P<v>\d+), which is not a valid value for type '(?P<t>bool|_Bool)'", "ub-uninit-bool",
     "Niezainicjalizowana zmienna bool",
     lambda m: f"Zmienna/element typu bool ma wartość {m['v']} (bool może mieć tylko 0 lub 1) — to prawie zawsze "
               f"NIEZAINICJALIZOWANA pamięć (np. lokalna tablica visited bez wyzerowania).",
     lambda m: "Zainicjalizuj: `bool vis[N] = {};`, `vector<bool> vis(n, false);` albo memset(vis, 0, sizeof vis)."),
    (r"load of value (?P<v>-?\d+), which is not a valid value for type '(?P<t>[^']+)'", "ub-invalid-value",
     "Nieprawidłowa wartość enuma/typu",
     lambda m: f"Wartość {m['v']} nie jest poprawna dla typu {m['t']} (niezainicjalizowana pamięć?).",
     lambda m: "Zainicjalizuj zmienną."),
    (r"(?:load|store) of null pointer of type '(?P<t>[^']+)'", "ub-null", "Odwołanie przez pusty wskaźnik (nullptr)",
     lambda m: f"Odczyt/zapis przez wskaźnik nullptr (typ {m['t']}).",
     lambda m: "Sprawdź, czy wskaźnik/iterator wskazuje na istniejący element."),
    (r"member (?:access|call) within null pointer of type '(?P<t>[^']+)'", "ub-null", "Pole/metoda na nullptr",
     lambda m: f"Dostęp do pola/metody obiektu {m['t']} przez nullptr.", lambda m: "Sprawdź wskaźnik przed użyciem."),
    (r"reference binding to null pointer of type '(?P<t>[^']+)'", "ub-null", "Referencja do nieistniejącego elementu",
     lambda m: f"Referencja do pustego wskaźnika ({m['t']}) — zwykle `v[i]` na pustym vectorze albo `*it` dla end().",
     lambda m: "Upewnij się, że kontener nie jest pusty przed dostępem."),
    (r"(?:store to|load of) (?:misaligned )?address 0x[0-9a-f]+ with insufficient space for an object of type '(?P<t>[^']+)'",
     "ub-heap-overflow", "Zapis/odczyt poza zaalokowaną pamięcią",
     lambda m: f"Dostęp do obiektu {m['t']} poza końcem zaalokowanego bloku (wyjście poza vector/new[]).",
     lambda m: "Sprawdź indeksy względem rozmiaru."),
    (r"execution reached the end of a value-returning function without returning a value", "ub-missing-return",
     "Funkcja zakończyła się bez return",
     lambda m: "Wykonanie doszło do końca funkcji, która powinna zwrócić wartość — zwrócony wynik to śmieć.",
     lambda m: "Dodaj `return` na wszystkich ścieżkach."),
    (r"(?P<v>[-\w.+e]+) is outside the range of representable values of type '(?P<t>[^']+)'", "ub-float-cast",
     "Konwersja double → całkowita poza zakresem",
     lambda m: f"Wartość {m['v']} nie mieści się w {m['t']} przy konwersji z liczby rzeczywistej (np. (int)1e10, NaN).",
     lambda m: "Sprawdź zakres przed rzutowaniem; użyj long long."),
    (r"variable length array bound evaluates to non-positive value (?P<v>-?\d+)", "ub-vla", "Tablica o rozmiarze ≤ 0",
     lambda m: f"Rozmiar tablicy VLA wynosi {m['v']}.", lambda m: "Sprawdź n przed utworzeniem tablicy; użyj vector."),
    (r"pointer index expression with base 0x[0-9a-f]+ overflowed|applying (?:non-zero )?offset .* to null pointer|"
     r"addition of unsigned offset to 0x[0-9a-f]+ overflowed", "ub-pointer", "Arytmetyka wskaźników poza zakresem",
     lambda m: "Przesunięcie wskaźnika/iteratora poza tablicę (np. v.begin() - 1, ujemny indeks jako size_t).",
     lambda m: "Sprawdź indeksy (ujemne wartości zamienione na unsigned)."),
    (r"implicit conversion from type '(?P<f>[^']+)' of value (?P<v>-?\d+) .* to type '(?P<t>[^']+)' changed the value to (?P<n>-?\d+)",
     "ub-conversion", "Konwersja zmieniła wartość",
     lambda m: f"{m['v']} ({m['f']}) → {m['t']} dało {m['n']}.", lambda m: "Użyj szerszego typu."),
]


def _ub_type(m: dict) -> str:
    return errors.simplify_types(m.get("aka") or m.get("t") or "int") or "int"


def _arr_len(t: str) -> int | None:
    m = re.search(r"\[(\d+)\]$", t or "")
    return int(m.group(1)) if m else None


_ASAN_KINDS = {
    "heap-buffer-overflow": ("Wyjście poza vector/tablicę dynamiczną", "asan-heap-overflow"),
    "stack-buffer-overflow": ("Wyjście poza tablicę lokalną", "asan-stack-overflow-array"),
    "global-buffer-overflow": ("Wyjście poza tablicę globalną", "asan-global-overflow"),
    "dynamic-stack-buffer-overflow": ("Wyjście poza tablicę VLA", "asan-vla-overflow"),
    "container-overflow": ("Dostęp do elementu vectora poza size()", "asan-container-overflow"),
    "stack-overflow": ("Przepełnienie stosu (zbyt głęboka rekurencja)", "asan-stack-exhausted"),
    "heap-use-after-free": ("Użycie zwolnionej pamięci (np. referencja do vectora po push_back)", "asan-use-after-free"),
    "stack-use-after-return": ("Użycie zmiennej lokalnej po wyjściu z funkcji", "asan-use-after-return"),
    "stack-use-after-scope": ("Użycie zmiennej po wyjściu z jej bloku", "asan-use-after-scope"),
    "double-free": ("Podwójne zwolnienie pamięci", "asan-double-free"),
    "alloc-dealloc-mismatch": ("new[] zwalniane przez delete (bez [])", "asan-alloc-mismatch"),
    "SEGV": ("Odwołanie do nieprawidłowej pamięci (SEGV)", "asan-segv"),
    "BUS": ("Błąd szyny (BUS)", "asan-segv"),
    "FPE": ("Błąd arytmetyczny (dzielenie przez zero)", "asan-fpe"),
    "ILL": ("Niedozwolona instrukcja (brak return?)", "asan-ill"),
    "requested allocation size": ("Próba zaalokowania gigantycznej pamięci (ujemny rozmiar?)", "asan-alloc-size"),
    "allocation-size-too-big": ("Próba zaalokowania gigantycznej pamięci (ujemny rozmiar?)", "asan-alloc-size"),
    "out-of-memory": ("Brak pamięci", "asan-oom"),
    "calloc-overflow": ("Przepełnienie przy alokacji", "asan-alloc-size"),
    "negative-size-param": ("Ujemny rozmiar w memcpy/memset", "asan-negative-size"),
    "memcpy-param-overlap": ("memcpy na nakładających się obszarach", "asan-memcpy-overlap"),
    "ABRT": ("Program przerwany (abort)", "asan-abort"),
}


def parse_sanitizer(stderr: str, code: str) -> list[dict]:
    """Zamienia raport ASan/UBSan/hardening/wyjątków na listę problemów z linią w kodzie użytkownika."""
    out: list[dict] = []
    text = stderr or ""
    # UBSan (może być wiele)
    for m in _UBSAN_RE.finditer(text):
        fname = m.group("file").replace("\\", "/").split("/")[-1]
        line = int(m.group("line")) if fname == "main.cpp" else 0
        col = int(m.group("col")) if fname == "main.cpp" else 0
        msg = m.group("msg").strip()
        if not line:
            fr = _user_frames(text[m.end():m.end() + 2000])
            if fr:
                line, col = fr[0]["line"], fr[0]["col"]
        rule, title, expl, fix = "ub-other", "Niezdefiniowane zachowanie (UBSan)", f"UBSan: {msg}", \
            "Popraw wskazaną linię."
        for rx, rid, tt, fe, ff in _UB_TRANSLATIONS:
            mm = re.search(rx, msg)
            if mm:
                gd = {k: (v or "") for k, v in mm.groupdict().items()}
                rule, title, expl, fix = rid, tt, fe(gd), ff(gd)
                break
        out.append(_dyn_issue(rule, line, col, "error", title, expl + (f" (linia {line})" if line else ""), fix, 0.97,
                              "sanitizer", evidence=f"{m.group('file')}:{m.group('line')}:{m.group('col')}: runtime error: {msg}",
                              code=code))
    # ASan
    am = _ASAN_RE.search(text)
    if am:
        kind = am.group("kind")
        rest = am.group("rest") or ""
        if kind == "requested" or "requested allocation size" in text:
            kind = "requested allocation size"
        block = text[am.start():]
        frames = _user_frames(block)
        line = frames[0]["line"] if frames else 0
        col = frames[0]["col"] if frames else 0
        fn = frames[0]["fn"] if frames else ""
        title, rid = _ASAN_KINDS.get(kind, (f"AddressSanitizer: {kind}", "asan-other"))
        acc = _ACCESS_RE.search(block)
        rw = acc.group("rw") if acc else ""
        size = int(acc.group("size")) if acc else 0
        details = ""
        reg = _REGION_RE.search(block)
        sv = _STACKVAR_RE.search(block)
        gv = _GLOBALVAR_RE.search(block)
        hard = _HARD_RE.search(text)
        exc = _EXC_RE.search(text)
        if kind in ("heap-buffer-overflow", "container-overflow") and reg and size:
            regsz = int(reg.group("sz"))
            off = int(reg.group("off"))
            n_el = regsz // size if size else 0
            if reg.group("where") == "after":
                idx = (regsz + off) // size
                details = f" Odwołanie do elementu o indeksie ≈ {idx}, a obszar mieści {n_el} element(ów) po {size} B " \
                          f"(poprawne indeksy 0…{max(0, n_el - 1)})."
            elif reg.group("where") == "before":
                details = f" Odwołanie {off} bajtów PRZED początkiem tablicy — indeks ujemny (≈ -{max(1, off // size)})."
        elif sv:
            var = sv.group("var")
            a, b = int(sv.group("a")), int(sv.group("b"))
            off = int(sv.group("off"))
            el = size or 4
            n_el = (b - a) // el if el else 0
            idx = (off - a) // el if el else 0
            details = f" Tablica `{var}`" + (f" (linia {sv.group('line')})" if sv.group("line") else "") + \
                f" ma {n_el} element(ów), a odwołanie dotyczy indeksu ≈ {idx}."
        elif gv:
            var = gv.group("var")
            sz = int(gv.group("sz"))
            off = int(gv.group("off"))
            el = size or 4
            if gv.group("where") == "after":
                details = f" Globalna tablica `{var}` ma {sz // el if el else sz} element(ów), a odwołanie dotyczy indeksu ≈ {(sz + off) // el if el else '?'}."
            else:
                details = f" Odwołanie przed początkiem globalnej tablicy `{var}` (indeks ujemny)."
        if kind == "SEGV":
            if re.search(r"address 0x0+\b|zero page|address 0x0{6,}[0-9a-f]{0,4}\b", block):
                details += " Adres bliski zera — odwołanie przez nullptr albo do elementu pustego kontenera (v[i] gdy v jest pusty)."
            else:
                details += " Najczęściej: indeks daleko poza tablicą (np. ujemny albo śmieciowy) lub iterator end()."
        if kind == "ABRT":
            if hard:
                title = "Biblioteka STL wykryła błąd: " + errors._pl_lib_msg(hard.group("msg"))
                rid = "stl-hardening"
                details = f" Warunek `{hard.group('cond')}` nie był spełniony."
            elif exc:
                ex = errors.simplify_types(exc.group("a") or exc.group("b") or "")
                what = (exc.group("wa") or exc.group("wb") or "").strip()
                info = errors._exception_info(ex, what)
                title, rid = info[0], "exception"
                details = " " + info[1]
            elif re.search(r"Assertion .* failed", text):
                title, rid = "Nie powiodła się asercja assert", "assert"
        what_txt = {"READ": "odczyt", "WRITE": "zapis"}.get(rw, "dostęp")
        expl = f"{title}" + (f" ({what_txt} {size} B)" if size and kind not in ("stack-overflow", "SEGV") else "") + \
            (f" w funkcji `{fn}`" if fn and fn != "main" else "") + (f", linia {line}" if line else "") + "." + details
        fix = _asan_fix(kind, rid)
        if rid == "stl-hardening":
            fix = ("Sprawdź indeks względem size() w tej linii (pętla `i < n`, nie `i <= n`); jeśli to front()/back()/top() — "
                   "czy kontener nie jest pusty; jeśli komparator — używaj tylko `<`.")
        out.append(_dyn_issue(rid, line, col, "error", title, expl, fix, 0.98, "sanitizer",
                              evidence="\n".join(text[am.start():].split("\n")[:8]), code=code,
                              stack=[f"{f['fn']} (linia {f['line']})" for f in frames[:6]]))
    elif not out:
        hard = _HARD_RE.search(text)
        exc = _EXC_RE.search(text)
        frames = _user_frames(text)
        line = frames[0]["line"] if frames else 0
        if hard:
            out.append(_dyn_issue("stl-hardening", line, 0, "error",
                                  "Biblioteka STL wykryła błąd: " + errors._pl_lib_msg(hard.group("msg")),
                                  f"Warunek `{hard.group('cond')}` nie był spełniony" + (f" (linia {line})." if line else "."),
                                  "Sprawdź indeks/rozmiar kontenera w tej linii.", 0.95, "sanitizer", evidence=hard.group(0),
                                  code=code))
        elif exc:
            ex = errors.simplify_types(exc.group("a") or exc.group("b") or "")
            what = (exc.group("wa") or exc.group("wb") or "").strip()
            info = errors._exception_info(ex, what)
            out.append(_dyn_issue("exception", line, 0, "error", info[0], info[1], info[3], 0.95, "runtime",
                                  evidence=exc.group(0), code=code))
    return out


def _asan_fix(kind: str, rid: str) -> str:
    return {
        "heap-buffer-overflow": "Sprawdź indeksy względem size() (pętla `i < n`, nie `i <= n`; `v[i+1]` przy i = n-1). "
                                "Jeśli indeksujesz od 1, utwórz vector o rozmiarze n + 1.",
        "container-overflow": "Indeks jest ≥ size() (choć w obrębie zarezerwowanej pamięci) — sprawdź warunek pętli "
                              "i czy vector ma właściwy rozmiar (resize zamiast reserve).",
        "stack-buffer-overflow": "Powiększ tablicę lokalną albo popraw zakres pętli.",
        "global-buffer-overflow": "Powiększ tablicę globalną (np. MAXN = 1e6 + 5) albo popraw zakres pętli.",
        "dynamic-stack-buffer-overflow": "Tablica VLA jest za mała — użyj vector<…>(n + 1) i sprawdź indeksy.",
        "stack-overflow": "Rekurencja jest za głęboka albo nie ma warunku stopu. Sprawdź przypadek bazowy; dla głębokości "
                          "10⁵–10⁶ przepisz DFS iteracyjnie albo przenieś duże tablice poza funkcję.",
        "heap-use-after-free": "Nie trzymaj referencji/iteratorów/wskaźników do elementów vectora podczas push_back "
                               "(realokacja przenosi dane) ani po erase. Kopiuj wartość albo używaj indeksów.",
        "stack-use-after-return": "Nie zwracaj referencji/wskaźnika do zmiennej lokalnej — zwracaj przez wartość.",
        "stack-use-after-scope": "Zmienna przestała istnieć po wyjściu z bloku — przenieś jej deklarację wyżej.",
        "SEGV": "Sprawdź indeksy (szczególnie ujemne i niezainicjalizowane), puste kontenery (front/top/back) i iteratory end().",
        "requested allocation size": "Rozmiar kontenera jest ujemny lub gigantyczny — sprawdź, czy n jest wczytane i "
                                     "dodatnie przed `vector<…> v(n)`.",
        "allocation-size-too-big": "Rozmiar kontenera jest ujemny lub gigantyczny — sprawdź n.",
        "FPE": "Sprawdź dzielniki (/ i %) — czy mogą być zerem.",
    }.get(kind, "Popraw wskazaną linię — szczegóły w raporcie sanitizera.")


def _dyn_issue(rule, line, col, severity, title, message, fix, confidence, source, evidence="", code="", stack=None,
               kb=None) -> dict:
    kb_map = {"ub-overflow": ["pit-int-overflow", "num-overflow"], "ub-index": ["pit-out-of-bounds", "pit-off-by-one"],
              "asan-heap-overflow": ["pit-out-of-bounds", "stl-vector"], "asan-container-overflow": ["pit-out-of-bounds", "stl-vector"],
              "asan-stack-overflow-array": ["pit-out-of-bounds"], "asan-global-overflow": ["pit-out-of-bounds"],
              "asan-vla-overflow": ["pit-out-of-bounds", "cpp-global-vs-local"],
              "asan-stack-exhausted": ["pit-stack-overflow", "cpp-stack-overflow"], "asan-use-after-free": ["pit-iterator-erase", "stl-iterators"],
              "asan-segv": ["pit-out-of-bounds", "oi-debugging"], "asan-alloc-size": ["stl-vector", "oi-memory"],
              "uninit-diff": ["pit-uninitialized"], "ub-uninit-bool": ["pit-uninitialized"],
              "tle-slow": ["oi-complexity-limits", "io-fast-io"], "tle-infinite": ["oi-debugging", "pit-unsigned-size"],
              "stl-hardening": ["pit-out-of-bounds", "stl-vector"], "stl-debug": ["pit-out-of-bounds", "oi-compile-flags"],
              "stl-assert": ["pit-out-of-bounds", "oi-compile-flags"], "exception": ["stl-vector", "oi-debugging"],
              "ub-shift": ["pit-bit-ops"], "ub-div-zero": ["oi-debugging"], "ub-missing-return": ["oi-compile-flags"],
              "ub-null": ["pit-out-of-bounds", "oi-debugging"], "ub-float-cast": ["num-floating", "num-rounding"],
              "stack-depth": ["pit-stack-overflow", "cpp-stack-overflow"], "ub-differential": ["oi-compile-flags", "pit-int-overflow"],
              "wrong-answer": ["oi-stress-test"]}
    kbid = kb
    if kbid is None:
        cands = kb_map.get(rule, [])
        ids = _kb_ids()
        kbid = next((c for c in cands if ids is None or c in ids), None)
    d = {"line": int(line or 0), "col": int(col or 0), "severity": severity, "rule": rule, "title": title,
         "message": message, "fix": fix, "kb": kbid, "category": "dynamic", "confidence": round(confidence, 2),
         "source": source, "code": _code_line(code, int(line or 0)) if code else "", "evidence": (evidence or "")[:1500],
         "inputs": []}
    if stack:
        d["stack"] = stack
    return d


def _outputs_agree(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return True
    return runner.outputs_equal(a, b) or runner.normalize_output(a) == runner.normalize_output(b)


def _preview(s: str, n: int = 300) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"… (+{len(s) - n} znaków)"


def _first_diff(a: str, b: str) -> str:
    ta, tb = (a or "").split(), (b or "").split()
    for k, (x, y) in enumerate(zip(ta, tb)):
        if x != y:
            return f"token {k + 1}: `{x[:40]}` vs `{y[:40]}`"
    if len(ta) != len(tb):
        return f"różna liczba tokenów ({len(ta)} vs {len(tb)})"
    return ""


# ================================================================================================ zadanie w tle
def _norm_inputs(raw) -> list[dict]:
    out = []
    if raw is None:
        raw = []
    if isinstance(raw, str):
        raw = [raw]
    for x in raw[:MAX_INPUTS]:
        if isinstance(x, dict):
            inp = str(x.get("input") or x.get("in") or "")
            exp = x.get("expected") if x.get("expected") is not None else x.get("output")
            out.append({"input": inp[:MAX_INPUT_LEN], "expected": None if exp in (None, "") else str(exp),
                        "name": x.get("name")})
        else:
            out.append({"input": str(x)[:MAX_INPUT_LEN], "expected": None, "name": None})
    if not out:
        out.append({"input": "", "expected": None, "name": "puste wejście"})
    return out


def handle_run(body: dict) -> dict:
    code = body.get("code") or ""
    if not code.strip():
        raise ApiError("Wklej kod do sprawdzenia.")
    if len(code) > MAX_CODE:
        raise ApiError("Kod jest za długi (limit 400 tys. znaków).")
    return {"job": jobs.submit(_dynamic_job, body, title="Sprawdzarka: analiza dynamiczna")}


def _settings() -> dict:
    try:
        from oiforge.core import store
        return store.settings() or {}
    except Exception:  # noqa: BLE001
        return {}


def _dynamic_job(job, body: dict) -> dict:
    return run_dynamic(body.get("code") or "", body.get("inputs"), timeout=body.get("timeout"),
                       variants=body.get("variants"), std=body.get("std"), job=job,
                       include_static=body.get("static", True))


def run_dynamic(code: str, inputs=None, timeout=None, variants=None, std=None, job=None, include_static=True) -> dict:
    """Pełna analiza dynamiczna (synchronicznie). `job` — opcjonalny obiekt postępu z oiforge.core.jobs."""
    t_start = time.perf_counter()
    code = (code or "").replace("\r\n", "\n")
    st = _settings()
    try:
        tl = float(timeout or st.get("time_limit") or 2.0)
    except (TypeError, ValueError):
        tl = 2.0
    tl = max(0.2, min(tl, 20.0))
    std = std or st.get("cpp_std") or "c++20"
    ins = _norm_inputs(inputs)
    caps = config.capabilities()
    notes: list[str] = []

    def progress(f, msg):
        if job is not None:
            job.progress(f, msg)

    def push(ev):
        if job is not None:
            job.push(ev)

    if _is_python(code):
        return _run_python(code, ins, tl, job)

    # 1) statyczna
    static = {"issues": [], "stats": {}}
    if include_static:
        progress(0.02, "Analiza statyczna…")
        static = static_analysis(code)
        push({"type": "stage", "stage": "static", "message": f"Analiza statyczna: {len(static['issues'])} uwag"})

    # 2) kompilacje (równolegle)
    want = set(variants) if variants else None
    specs = _variant_specs(caps, want)
    if not caps.get("sanitizers"):
        notes.append("Sanitizery (ASan/UBSan) są niedostępne na tej platformie" +
                     (" (MinGW na Windows ich nie wspiera)" if config.IS_WINDOWS else "") +
                     " — używam trybu debug STL, porównania optymalizacji i inicjalizacji zmiennych.")
    progress(0.05, f"Kompilacja {len(specs)} wariantów…")
    push({"type": "stage", "stage": "compile", "message": f"Kompiluję {len(specs)} warianty programu…",
          "variants": list(specs)})
    compiled: dict = {}
    with cf.ThreadPoolExecutor(max_workers=min(4, len(specs))) as ex:
        futs = [ex.submit(_compile_variant, code, name, spec, std) for name, spec in specs.items()]
        done = 0
        for fut in cf.as_completed(futs):
            name, cr, dt = fut.result()
            done += 1
            compiled[name] = cr
            progress(0.05 + 0.35 * done / len(specs), f"Skompilowano: {name}")
            push({"type": "compile", "variant": name, "ok": cr.ok, "time": round(dt, 2), "cached": cr.cached,
                  "compiler": cr.compiler})
            if job is not None and job.cancelled:
                break
    rel = compiled.get("release")
    issues: list[dict] = []
    compile_info = {"ok": bool(rel and rel.ok), "compiler": rel.compiler if rel else None,
                    "stderr": rel.stderr if rel else "", "translated": []}
    if rel is None or not rel.ok:
        tr = errors.translate(rel.stderr if rel else "", code)
        compile_info["translated"] = tr
        for it in tr:
            if it["severity"] != "error":
                continue
            issues.append(_dyn_issue("compile-error", it["line"], it["col"], "error", it["title"],
                                     it["explanation"], it["fix"], 1.0, "compiler", evidence=it["original"], code=code,
                                     kb=it.get("kb")))
        if not issues:
            msg = (rel.stderr if rel else "") or "Kompilator nie zwrócił komunikatu."
            issues.append(_dyn_issue("compile-error", 0, 0, "error", "Kompilacja nie powiodła się", msg[:1200],
                                     "Sprawdź komunikat kompilatora (zakładka Tłumacz błędów wyjaśni go po polsku).",
                                     1.0, "compiler", evidence=msg, code=code))
        res = _finish(code, static, issues, [], compile_info, notes, specs, compiled, t_start, ce=True)
        return res
    # ostrzeżenia kompilatora → zgłoszenia
    warn_items = errors.translate(rel.stderr, code) if rel.stderr else []
    compile_info["translated"] = warn_items
    for it in warn_items:
        if it["severity"] != "warning" or not it["line"]:
            continue
        lvl = it.get("level") or "warning"
        if lvl not in SEV_ORDER:
            lvl = "warning"
        pid = it.get("pattern") or "warning"
        if pid in ("conversion", "sign-conversion") and re.search(r"(unsigned long|size_t|long unsigned)\W.*\bint\b",
                                                                  it.get("original", "")):
            lvl = "style"
        if pid == "unused-result":
            continue
        expl = it["explanation"] if it.get("pattern") else f"{it['explanation']} Komunikat: {it['original']}"
        issues.append(_dyn_issue("cc-" + pid, it["line"], it["col"], lvl, it["title"], expl, it["fix"],
                                 0.9 if lvl == "error" else 0.75, "compiler", evidence=it["original"], code=code,
                                 kb=it.get("kb")))
    unavailable = [n for n, cr in compiled.items() if not cr.ok and n != "release"]
    if unavailable:
        notes.append("Niektóre warianty nie skompilowały się na tej maszynie i zostały pominięte: " +
                     ", ".join(unavailable) + ".")

    # 3) uruchomienia
    per_input = []
    total_runs = max(1, len(ins) * sum(1 for c in compiled.values() if c.ok))
    runs_done = 0
    for idx, case in enumerate(ins):
        if job is not None and job.cancelled:
            break
        inp = case["input"]
        rec = {"index": idx, "name": case.get("name") or f"Test {idx + 1}", "input_preview": _preview(inp, 200),
               "input_len": len(inp), "variants": {}, "notes": [], "expected": case.get("expected") is not None}
        results = {}
        order = ["release", "sanitize", "o0", "init_pattern", "init_zero", "stl_debug", "small_stack"]
        for name in order:
            cr = compiled.get(name)
            if cr is None or not cr.ok:
                continue
            if name != "release" and name != "sanitize" and results.get("release") is not None and \
                    results["release"].verdict == "TLE":
                continue                     # program i tak się nie mieści w czasie — nie marnuj czasu
            t = tl * (6 if name == "sanitize" else (3 if name in ("o0", "stl_debug") else 1.5 if name != "release" else 1))
            t = min(t, 30.0)
            env = _SAN_ENV if name == "sanitize" else None
            mac_sym = name == "sanitize" and config.IS_MAC and shutil.which("atos")
            if mac_sym:
                env = {"ASAN_OPTIONS": _SAN_ENV["ASAN_OPTIONS"].replace("symbolize=1", "symbolize=0"),
                       "UBSAN_OPTIONS": _SAN_ENV["UBSAN_OPTIONS"] + ":symbolize=0"}
            progress(0.42 + 0.5 * runs_done / total_runs, f"Test {idx + 1}/{len(ins)}: {name}")
            try:
                rr = runner.run(_cmd_for(name, specs[name], cr), inp, timeout=t, env=env)
            except Exception as e:  # noqa: BLE001
                rr = runner.RunResult(verdict="RE", exit_code=None, error=str(e), stderr=str(e))
            if mac_sym:
                rr.stderr = _symbolize_mac(rr.stderr, cr.exe)
            elif name == "sanitize" and config.IS_LINUX:
                rr.stderr = _symbolize_linux(rr.stderr, cr.exe)
            rr.stderr = _normalize_paths(rr.stderr, cr)
            results[name] = rr
            runs_done += 1
            rec["variants"][name] = {"verdict": rr.verdict, "time": rr.time, "mem_kb": rr.mem_kb,
                                     "exit_code": rr.exit_code, "signal": rr.signal,
                                     "stdout": _preview(rr.stdout, 400), "stderr": _preview(rr.stderr, 1500)}
        found = _analyze_runs(code, idx, case, results, specs, tl, compiled)
        for it in found:
            it["inputs"] = [idx]
        issues.extend(found)
        rec["issues"] = len(found)
        outs = {n: r.stdout for n, r in results.items() if r.verdict == "OK"}
        rec["agree"] = len({runner.normalize_output(" ".join(o.split())) for o in outs.values()}) <= 1
        if case.get("expected") is not None and "release" in results:
            ok = results["release"].verdict == "OK" and runner.outputs_equal(results["release"].stdout, case["expected"])
            rec["expected_ok"] = ok
        per_input.append(rec)
        push({"type": "input", "index": idx, "summary": {k: v["verdict"] for k, v in rec["variants"].items()},
              "issues": len(found), "agree": rec["agree"]})
        for it in found:
            push({"type": "issue", "issue": it})
    return _finish(code, static, issues, per_input, compile_info, notes, specs, compiled, t_start)


def _analyze_runs(code: str, idx: int, case: dict, results: dict, specs: dict, tl: float, compiled: dict) -> list[dict]:
    out: list[dict] = []
    rel = results.get("release")
    san = results.get("sanitize")
    # sanitizery / tryb debug
    if san is not None:
        out.extend(parse_sanitizer(san.stderr, code))
        if san.verdict == "TLE" and rel is not None and rel.verdict != "TLE":
            pass
    dbg = results.get("stl_debug")
    if dbg is not None and dbg.verdict != "OK":
        m = _GLIBCXX_RE.search(dbg.stderr or "")
        if m:
            what = re.sub(r"\s+", " ", m.group("msg"))
            pl = errors._pl_lib_msg(what)
            merged = False
            mi = re.search(r"out-of-bounds index (\d+), but\s*container only holds (\d+) elements", what)
            for it in out:
                if it["rule"] in ("stl-hardening", "asan-container-overflow", "asan-heap-overflow") and mi:
                    it["message"] = (f"Wyjście poza zakres kontenera: indeks {mi.group(1)}, a rozmiar to {mi.group(2)} "
                                     f"(poprawne indeksy 0…{int(mi.group(2)) - 1})"
                                     + (f", linia {it['line']}" if it["line"] else "") + ". Wersja -O2 tego nie zgłasza, "
                                     "tylko po cichu czyta/zapisuje cudzą pamięć.")
                    it["title"] = f"Wyjście poza vector: indeks {mi.group(1)} przy rozmiarze {mi.group(2)}"
                    merged = True
            if not merged:
                line = 0
                title = f"Tryb debug STL: {pl}"
                if mi:
                    title = f"Wyjście poza vector: indeks {mi.group(1)} przy rozmiarze {mi.group(2)}"
                out.append(_dyn_issue("stl-debug", line, 0, "error", title,
                                      f"Biblioteka STL w trybie debug (_GLIBCXX_DEBUG) zatrzymała program: {pl}." +
                                      ("" if line else " (Ten tryb nie podaje linii — uruchom z sanitizerami lub szukaj "
                                                       "odwołań [] do tego kontenera.)"),
                                      "Sprawdź indeksy i rozmiary kontenerów; przy lower_bound/binary_search — czy dane "
                                      "są posortowane.", 0.95, "sanitizer", evidence=m.group(0), code=code))
        elif not out:
            exc = _EXC_RE.search(dbg.stderr or "")
            if exc:
                ex = errors.simplify_types(exc.group("a") or exc.group("b") or "")
                info = errors._exception_info(ex, (exc.group("wa") or exc.group("wb") or "").strip())
                out.append(_dyn_issue("exception", 0, 0, "error", info[0], info[1], info[3], 0.9, "runtime",
                                      evidence=exc.group(0), code=code))
    for vname in ("o0", "release"):
        rv = results.get(vname)
        if rv is None or rv.verdict == "OK":
            continue
        am = re.search(r"Assertion '(?P<cond>[^']+)' failed", rv.stderr or "")
        if am and not any(i["rule"] in ("stl-debug", "stl-hardening", "asan-heap-overflow", "asan-container-overflow")
                          for i in out):
            cond = am.group("cond")
            what = {"__n < this->size()": "indeks poza rozmiarem vectora/stringa (operator[])",
                    "!this->empty()": "front()/back()/pop_back()/top() na pustym kontenerze",
                    "__pos <= size()": "pozycja poza napisem"}.get(cond, f"warunek `{cond}`")
            frames = _user_frames(rv.stderr)
            out.append(_dyn_issue("stl-assert", frames[0]["line"] if frames else 0, 0, "error",
                                  "Asercja biblioteki STL: " + what,
                                  f"Wersja {'-O0' if vname == 'o0' else 'release'} (z asercjami biblioteki standardowej) "
                                  f"zatrzymała program: {what}. To błąd w programie, nawet jeśli wersja -O2 „działa”.",
                                  "Sprawdź indeksy względem size() i czy kontener nie jest pusty przed front/back/top.",
                                  0.95, "sanitizer", evidence=am.group(0), code=code))
            break
    # błąd wykonania w wersji release bez raportu sanitizera
    if rel is not None and rel.verdict in ("RE", "MLE") and not any(i["severity"] == "error" for i in out):
        ex = errors.explain_runtime(rel.to_dict(), code)
        line = ex.get("line") or 0
        out.append(_dyn_issue("runtime-" + (ex.get("kind") or "re"), line, 0, "error", ex.get("title", "Błąd wykonania"),
                              ex.get("explanation", "") + (" Możliwe przyczyny: " + "; ".join(ex["causes"][:4]) + "."
                                                           if ex.get("causes") else ""),
                              ex.get("fix", ""), 0.85, "runtime", evidence=_preview(rel.stderr, 600), code=code))
    # TLE
    if rel is not None and rel.verdict == "TLE":
        cr = compiled.get("release")
        longer = None
        if cr is not None and cr.ok:
            try:
                longer = runner.run([cr.exe], case["input"], timeout=min(tl * 4, 12.0))
            except Exception:  # noqa: BLE001
                longer = None
        small = len(case["input"]) < 200
        if longer is not None and longer.verdict == "OK":
            msg = (f"Program przekroczył limit {tl:g} s, ale z dłuższym limitem skończył się po {longer.time:.2f} s — "
                   f"algorytm jest poprawny, lecz ZA WOLNY dla tego testu (albo wolne wejście/wyjście).")
            fix = ("Zmniejsz złożoność (np. O(n²) → O(n log n)), dodaj `ios::sync_with_stdio(false); cin.tie(nullptr);`, "
                   "zamień endl na '\\n'. Pomiar złożoności pokaże, jak rośnie czas.")
            out.append(_dyn_issue("tle-slow", 0, 0, "error", "Za wolno (TLE)", msg, fix, 0.9, "runtime", code=code))
        else:
            msg = (f"Program nie skończył się nawet w {min(tl * 4, 12.0):g} s" +
                   (" na małym wejściu — to prawie na pewno NIESKOŃCZONA PĘTLA (zły warunek, brak i++, pętla po "
                    "unsigned w dół, while(true) bez break) albo program czeka na więcej danych, niż podano." if small else
                    " — nieskończona pętla albo bardzo wolny algorytm."))
            fix = ("Sprawdź warunki pętli i inkrementacje; czy wczytujesz tyle danych, ile jest w teście; "
                   "uruchom w Debuggerze i zatrzymaj program, żeby zobaczyć, gdzie stoi.")
            out.append(_dyn_issue("tle-infinite", 0, 0, "error", "Nieskończona pętla lub bardzo wolny program", msg, fix,
                                  0.8 if small else 0.6, "runtime", code=code))
    # różnice wyników między wariantami (UB)
    base = rel.stdout if rel is not None and rel.verdict == "OK" else None
    pat, zero = results.get("init_pattern"), results.get("init_zero")
    uninit_found = False
    if pat is not None and zero is not None and pat.verdict == "OK" and zero.verdict == "OK" and \
            not _outputs_agree(pat.stdout, zero.stdout):
        uninit_found = True
        out.append(_dyn_issue("uninit-diff", 0, 0, "error", "Wynik zależy od niezainicjalizowanej zmiennej",
                              "Ten sam program skompilowany tak, że zmienne lokalne startują od zera, i tak, że startują "
                              "od „śmieciowego” wzorca (0xAAAA…), daje RÓŻNE wyniki (" +
                              _first_diff(zero.stdout, pat.stdout) + "). To znaczy, że jakaś zmienna lokalna (albo "
                              "lokalna tablica) jest odczytywana, zanim dostanie wartość. U Ciebie może przypadkiem "
                              "działać, na sprawdzarce — nie.",
                              "Nadaj wartości początkowe wszystkim zmiennym lokalnym (`int s = 0;`, `bool vis[N] = {};`) "
                              "albo przenieś tablice poza funkcje.", 0.95, "differential", code=code))
    elif pat is not None and pat.verdict != "OK" and zero is not None and zero.verdict == "OK":
        uninit_found = True
        out.append(_dyn_issue("uninit-diff", 0, 0, "error", "Niezainicjalizowana zmienna powoduje błąd",
                              f"Gdy zmienne lokalne mają „śmieciowe” wartości, program kończy się błędem ({pat.verdict}"
                              f"{', ' + pat.signal if pat.signal else ''}), a gdy są wyzerowane — działa. Używasz "
                              f"zmiennej/tablicy lokalnej przed nadaniem jej wartości (np. jako indeksu albo rozmiaru).",
                              "Zainicjalizuj zmienne lokalne przy deklaracji.", 0.9, "differential", code=code))
    o0 = results.get("o0")
    variants_ok = {n: r for n, r in results.items() if r.verdict == "OK" and n in ("release", "o0", "sanitize")}
    if base is not None and len(variants_ok) >= 2:
        diffs = [n for n, r in variants_ok.items() if n != "release" and not _outputs_agree(base, r.stdout)]
        if diffs and not uninit_found:
            names = {"o0": "-O0", "sanitize": "wersją z sanitizerami"}
            other = results[diffs[0]].stdout
            out.append(_dyn_issue("ub-differential", 0, 0, "error",
                                  "Różne wyniki zależnie od optymalizacji — niezdefiniowane zachowanie",
                                  "Wersja -O2 daje inny wynik niż " + ", ".join(names.get(d, d) for d in diffs) +
                                  f" ({_first_diff(base, other)}). Poprawny program w C++ daje ten sam wynik niezależnie "
                                  f"od optymalizacji — różnica prawie na pewno oznacza UB: przepełnienie int, wyjście "
                                  f"poza tablicę, niezainicjalizowaną zmienną albo brak return w funkcji.",
                                  "Przejrzyj zgłoszenia sanitizera i analizy statycznej powyżej; najczęściej winne jest "
                                  "przepełnienie (użyj long long) albo indeks poza tablicą.", 0.9, "differential",
                                  code=code))
    if o0 is not None and rel is not None and rel.verdict == "TLE" and o0.verdict == "OK":
        pass
    # mały stos
    ss = results.get("small_stack")
    if ss is not None and rel is not None and rel.verdict == "OK" and ss.verdict == "RE":
        out.append(_dyn_issue("stack-depth", 0, 0, "warning", "Rekurencja potrzebuje więcej niż 8 MB stosu",
                              "Program działa z dużym stosem (tak uruchamia go OI Forge), ale wysypuje się przy stosie "
                              "8 MB, jaki jest domyślnie w Linuksie i na wielu sprawdzarkach. Na OI stos jest zwykle "
                              "równy limitowi pamięci, ale np. na Codeforces/AtCoder (i na Windowsie: 1 MB) to błąd.",
                              "Zmniejsz głębokość rekurencji (DFS iteracyjny z własnym stosem) albo rozmiar ramek "
                              "(duże tablice lokalne przenieś globalnie).", 0.75, "runtime", code=code))
    # zgodność z oczekiwanym wynikiem
    if case.get("expected") is not None and rel is not None and rel.verdict == "OK" and \
            not runner.outputs_equal(rel.stdout, case["expected"]):
        out.append(_dyn_issue("wrong-answer", 0, 0, "warning", "Wynik różni się od oczekiwanego",
                              "Dla tego wejścia program wypisuje coś innego niż podany oczekiwany wynik (" +
                              _first_diff(rel.stdout, case["expected"]) + ").",
                              "Porównaj wyniki w narzędziu Diff albo uruchom Testerkę z brutem.", 0.9, "runtime",
                              code=code))
    return out


def _run_python(code: str, ins: list[dict], tl: float, job) -> dict:
    t0 = time.perf_counter()
    issues, per_input = [], []
    prog = runner.prepare(code, lang="python")
    if not prog.ok:
        ok, msg, line = runner.python_syntax(code)
        tr = errors.translate(msg, code)
        for it in tr:
            issues.append(_dyn_issue("py-syntax", it["line"] or line or 0, it["col"], "error", it["title"],
                                     it["explanation"], it["fix"], 1.0, "compiler", evidence=msg, code=code))
    else:
        for idx, case in enumerate(ins):
            if job is not None:
                job.progress(0.1 + 0.8 * idx / max(1, len(ins)), f"Test {idx + 1}/{len(ins)}")
            rr = runner.run(prog.cmd, case["input"], timeout=tl * 3)
            rec = {"index": idx, "name": case.get("name") or f"Test {idx + 1}", "input_preview": _preview(case["input"], 200),
                   "variants": {"python": {"verdict": rr.verdict, "time": rr.time, "mem_kb": rr.mem_kb,
                                           "exit_code": rr.exit_code, "signal": rr.signal,
                                           "stdout": _preview(rr.stdout, 400), "stderr": _preview(rr.stderr, 1500)}},
                   "agree": True, "notes": []}
            if rr.verdict != "OK":
                ex = errors.explain_runtime(rr.to_dict(), code)
                it = _dyn_issue("py-" + ex.get("kind", "re"), ex.get("line") or 0, 0, "error", ex.get("title", ""),
                                ex.get("explanation", ""), ex.get("fix", ""), 0.9, "runtime",
                                evidence=_preview(rr.stderr, 600), code=code)
                it["inputs"] = [idx]
                issues.append(it)
            per_input.append(rec)
    issues = _merge_issues(issues)
    return {"language": "python", "issues": issues, "inputs": per_input, "static": None,
            "summary": {"health": rules.health(issues), "issues": len(issues),
                        "time": round(time.perf_counter() - t0, 2), "language": "python"},
            "compile": {"ok": prog.ok}, "notes": ["Program w Pythonie: uruchomiono go na wejściach i wyjaśniono błędy "
                                                  "wykonania (sanitizery dotyczą tylko C++)."], "variants": {}}


# ================================================================================================ scalanie
_CONFIRMS = {
    "cc-uninitialized": {"uninit-local", "uninit-local-array"},
    "uninit-diff": {"uninit-local", "uninit-local-array"},
    "ub-uninit-bool": {"uninit-local", "uninit-local-array"},
    "cc-control-reaches-end": {"missing-return", "missing-return-path"},
    "ub-missing-return": {"missing-return", "missing-return-path"},
    "cc-parentheses-assign": {"assign-in-condition"},
    "cc-shift-count": {"shift-overflow"},
    "ub-shift": {"shift-overflow", "shift-int-wide"},
    "cc-overflow-const": {"const-int-overflow", "int-literal-overflow"},
    "cc-array-bounds": {"index-out-of-range", "index-negative"},
    "cc-aggressive-loop": {"index-out-of-range"},
    "ub-index": {"index-out-of-range", "index-negative"},
    "asan-heap-overflow": {"index-out-of-range", "index-negative", "vector-no-size-index", "size-before-read"},
    "asan-container-overflow": {"index-out-of-range", "vector-no-size-index"},
    "asan-stack-overflow-array": {"index-out-of-range", "index-negative"},
    "asan-global-overflow": {"index-out-of-range", "index-negative"},
    "stl-hardening": {"index-out-of-range", "index-negative", "vector-no-size-index", "comparator-non-strict"},
    "asan-stack-exhausted": {"recursion-no-base", "large-local-array"},
    "ub-overflow": {"overflow-int-mul", "overflow-mod-mul", "overflow-int-sum", "int-max-plus", "inf-overflow",
                    "overflow-cast-late"},
    "cc-format": {"printf-format", "scanf-format"},
    "cc-type-limits": {"unsigned-reverse-loop"},
    "cc-sizeof-array-arg": {"sizeof-array-param"},
    "cc-empty-body": {"empty-body"},
    "cc-shadow": {"shadow-local", "shadow-loop-var", "shadow-global"},
    "cc-unused-variable": {"unused-variable"},
    "cc-parentheses-bitwise": {"bitwise-precedence"},
    "cc-sign-compare": {"signed-unsigned-loop"},
    "cc-float-equal": {"float-equality"},
    "cc-class-memaccess": {"memset-vector"},
    "cc-fallthrough": {"switch-fallthrough"},
    "cc-misleading-indentation": set(),
    "ub-div-zero": set(),
}


def _merge_issues(items: list[dict]) -> list[dict]:
    """Scala duplikaty (ta sama reguła + linia) i łączy listy wejść."""
    out: dict = {}
    for it in items:
        k = (it["rule"], it["line"], it["title"] if it["line"] == 0 else "")
        if k in out:
            ex = out[k]
            ex["inputs"] = sorted(set(ex.get("inputs", [])) | set(it.get("inputs", [])))
            ex["confidence"] = max(ex["confidence"], it["confidence"])
            continue
        out[k] = dict(it)
    return list(out.values())


_MEMORY_RULES = ("stl-hardening", "stl-debug", "stl-assert", "asan-heap-overflow", "asan-container-overflow",
                 "asan-stack-overflow", "asan-global-overflow", "asan-use-after-free", "asan-segv")


def _finish(code, static, issues, per_input, compile_info, notes, specs, compiled, t_start, ce=False) -> dict:
    dyn = _merge_issues(issues)
    # pewny błąd pamięci (sanitizer) tłumaczy różne wyniki wariantów — „zależy od niezainicjalizowanej zmiennej” itp.
    # to wtedy zwykle jego skutek, nie osobny błąd: obniżamy do informacji zamiast dublować czerwone karty
    if any(d.get("source") == "sanitizer" and d["severity"] == "error" for d in dyn):
        for d in dyn:
            if d.get("source") == "differential" or d.get("rule") in ("uninit-diff", "opt-diff"):
                d["severity"] = "info"
                d["title"] = d["title"] + " (prawdopodobnie skutek błędu pamięci wyżej)"
    st_issues = [dict(i) for i in static.get("issues", [])]
    # potwierdzenia: dynamiczne/kompilator ↔ statyczne
    for d in dyn:
        targets = _CONFIRMS.get(d["rule"], set())
        if not targets:
            continue
        for s in st_issues:
            if s["rule"] not in targets:
                continue
            near = d["line"] == 0 or abs(s["line"] - d["line"]) <= 1
            if near:
                s["confidence"] = min(1.0, round(s["confidence"] + 0.15, 2))
                s.setdefault("confirmed_by", []).append(d["source"])
                if s["severity"] in ("warning", "style", "info") and d["severity"] == "error":
                    s["severity"] = "error"
                if d["line"] and d["source"] == "compiler":
                    d["duplicate_of"] = s.get("id")
    dyn = [d for d in dyn if not d.get("duplicate_of")]
    all_issues = dyn + st_issues
    src_rank = {"sanitizer": 0, "differential": 1, "runtime": 2, "compiler": 3, "static": 4}
    all_issues.sort(key=lambda d: (SEV_ORDER.get(d["severity"], 9), src_rank.get(d.get("source"), 5),
                                   -d.get("confidence", 0), d.get("line") or 10 ** 6))
    for n, it in enumerate(all_issues):
        it["id"] = f"i{n}"
    _log_journal(all_issues, "dynamic")
    health = rules.health([i for i in all_issues if i.get("severity") != "info"])
    dyn_err = sum(1 for d in dyn if d["severity"] == "error")
    if dyn_err:
        # błąd, który NAPRAWDĘ wystąpił na danych, to nie „prawdopodobny błąd” — pewny werdykt
        first = next(d for d in all_issues if d in dyn and d["severity"] == "error")
        health = {"score": min(health["score"], 30), "label": "Błąd: " + first["title"][:60] if first.get("title") else "Błąd w działaniu",
                  "tone": "danger", "sure": True}
    if ce:
        health = {"score": 0, "label": "Błąd kompilacji", "tone": "danger"}
        verdict = "Program się nie kompiluje — popraw najpierw błędy kompilacji (pierwszy błąd to główna przyczyna)."
    elif dyn_err:
        verdict = (f"Uruchomienia wykryły {dyn_err} poważny(ch) problem(ów) — to realne błędy, które wystąpiły na "
                   f"Twoich danych.")
    elif any(i["severity"] == "error" for i in st_issues):
        verdict = ("Na podanych testach program zachował się poprawnie, ale analiza statyczna widzi prawdopodobne błędy — "
                   "spróbuj testów brzegowych (max n, zera, liczby ujemne).")
    elif any(i["severity"] == "warning" for i in all_issues):
        verdict = "Nie znaleziono pewnych błędów; są ostrzeżenia warte sprawdzenia."
    else:
        verdict = "Nie znaleziono problemów na podanych testach — kod wygląda zdrowo."
    by_sev: dict = {}
    for it in all_issues:
        by_sev[it["severity"]] = by_sev.get(it["severity"], 0) + 1
    return {
        "issues": all_issues,
        "inputs": per_input,
        "compile": {"ok": compile_info["ok"], "compiler": compile_info.get("compiler"),
                    "translated": compile_info.get("translated", [])[:30],
                    "stderr": (compile_info.get("stderr") or "")[:20000]},
        "static": static.get("stats"),
        "variants": {n: {"ok": bool(compiled.get(n) and compiled[n].ok), "info": VARIANT_INFO.get(n, n),
                         "compiler": compiled[n].compiler if compiled.get(n) else None,
                         "cached": bool(compiled.get(n) and compiled[n].cached)} for n in specs},
        "notes": notes,
        "summary": {"health": health, "verdict": verdict, "by_severity": by_sev, "issues": len(all_issues),
                    "dynamic_errors": dyn_err, "inputs": len(per_input),
                    "time": round(time.perf_counter() - t_start, 2), "sanitizers": bool(config.capabilities().get("sanitizers"))},
    }


def guard_run(code: str, input_text: str = "", timeout=None, std=None) -> dict:
    """Strażnik zwykłego „Uruchom”: ten sam program na tym samym wejściu z sanitizerami (ASan/UBSan + kontrola zakresów
    STL) i w trybie debug STL. Zwraca tylko PEWNE błędy pamięci/UB z linią — wersja -O2 często ich nie pokazuje
    (np. wyjście poza vector po cichu wypisuje śmieci albo zera)."""
    code = (code or "").replace("\r\n", "\n")
    if not code.strip() or _is_python(code):
        return {"checked": False, "issues": [], "reason": "python" if code.strip() else "empty"}
    caps = config.capabilities()
    variants = [v for v, ok in (("sanitize", caps.get("sanitizers")), ("stl_debug", bool(config.GCC))) if ok]
    if not variants:
        return {"checked": False, "issues": [], "reason": "Sanitizery i tryb debug STL są niedostępne na tym komputerze."}
    t0 = time.perf_counter()
    res = run_dynamic(code, [str(input_text or "")[:MAX_INPUT_LEN]], timeout=timeout, variants=variants, std=std,
                      include_static=False)
    if not res["compile"]["ok"]:
        return {"checked": False, "issues": [], "reason": "compile"}
    keep = [i for i in res["issues"] if i.get("severity") == "error"
            and (i.get("source") == "sanitizer" or i.get("rule") in ("stl-assert", "stl-debug"))]
    seen, out = set(), []
    for i in keep:
        key = (i.get("rule"), i.get("line"))
        if key in seen:
            continue
        seen.add(key)
        out.append({k: i.get(k) for k in ("rule", "line", "col", "title", "message", "fix", "code", "kb", "evidence")})
    return {"checked": True, "issues": out[:5], "variants": variants, "time": round(time.perf_counter() - t0, 2)}


def handle_guard(body: dict) -> dict:
    code = body.get("code") or ""
    if len(code) > MAX_CODE:
        raise ApiError("Kod jest za długi (limit 400 tys. znaków).")
    return guard_run(code, body.get("input") or "", timeout=body.get("timeout"), std=body.get("std"))


ROUTES = {
    "POST /api/checker/analyze": handle_analyze,
    "POST /api/checker/run": handle_run,
    "POST /api/checker/guard": handle_guard,
    "GET /api/checker/rules": handle_rules,
}
