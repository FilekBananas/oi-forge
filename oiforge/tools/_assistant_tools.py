"""Narzędzia asystenta (bez AI): piaskownica do uruchamiania kodu z czatu, komendy (/uruchom, /sprawdz, /clear…),
proste wyjaśnienia na „nie rozumiem” i lekki profil ucznia do personalizacji odpowiedzi.

Tryb (`OIFORGE_ASSISTANT_TOOLS` albo `config.ASSISTANT_TOOLS`):
  "all"   — pełny OI Forge: asystent korzysta ze wszystkich narzędzi (sprawdzarka, złożoność, objaśniacz, formatter,
            optymalizator, Python→C++, tłumacz błędów, objaśniacz treści, doradca, testy brzegowe, auto-solver, programy);
  "basic" — OI Forge Mini: tylko uruchamianie kodu, programy i dokumentacja.
Wszystko deterministyczne: reguły, szablony i istniejące moduły — żadnego modelu językowego."""
from __future__ import annotations

import importlib
import os
import re
import shutil
import sys
import tempfile
import threading

from oiforge import config
from oiforge.core import runner
from oiforge.core import store
from oiforge.core import text as T

MODE = (os.environ.get("OIFORGE_ASSISTANT_TOOLS") or getattr(config, "ASSISTANT_TOOLS", "all") or "all").lower()
FULL = MODE != "basic"

RUN_TIMEOUT = 2.0
RUN_MAX_OUTPUT = 64_000
RUN_MEM_MB = 512


# ============================================================================================== kod w wiadomości
_FENCE = re.compile(r"```[ \t]*([\w+#.-]*)[ \t]*\n(.*?)```", re.S)
_INPUT_LINE = re.compile(r"^\s*(?:wej(?:s|ś)cie|input|stdin|dane(?: wej(?:s|ś)ciowe)?)\s*[:=]\s*(.*)$", re.I)
_CODE_START = re.compile(r"(#\s*include|\bint\s+main\s*\(|\bcout\s*<<|\bcin\s*>>|\bprintf\s*\(|\bputs\s*\(|"
                         r"\bstd::|\b(?:int|long long|long|double|char|bool|string|auto|vector\s*<[^>]*>)\s+\w+\s*[=;\[({]|"
                         r"\bfor\s*\(|\bwhile\s*\(|^\s*print\s*\(|^\s*def\s+\w+\s*\(|^\s*import\s+\w+|^\s*for\s+\w+\s+in\s+)", re.M)
_PY_RX = re.compile(r"^\s*(def\s+\w+\s*\(|import\s+\w+|from\s+\w+\s+import|print\s*\(|for\s+\w+\s+in\s+|"
                    r"\w+\s*=\s*(int|input|list|map)\s*\()", re.M)
_CPP_RX = re.compile(r"#\s*include|\bcout\b|\bcin\b|\bprintf\s*\(|;\s*$|\bint\s+main\b|\bstd::", re.M)


def detect_lang(code: str) -> str:
    if _CPP_RX.search(code or ""):
        return "cpp"
    return "python" if _PY_RX.search(code or "") else "cpp"


def split_message(q: str) -> tuple[str, str, str]:
    """Wiadomość → (tekst pytania, kod, wejście programu). Rozpoznaje bloki ```…```, linię „wejście: …” (lub
    „wejście:” i dane w kolejnych liniach) oraz kod w jednej linii z pytaniem („co wypisze cout << 'a';”)."""
    q = (q or "").strip()
    stdin = ""
    fences = _FENCE.findall(q)
    if fences:
        code_parts, text = [], _FENCE.sub("\n", q)
        for lang, body in fences:
            if lang.lower() in ("in", "input", "txt", "text", "wejscie", "wejście", "stdin"):
                stdin = body
            else:
                code_parts.append(body)
        text, stdin2 = _cut_input(text)
        return _clean_text(text), "\n".join(code_parts).strip(), stdin or stdin2
    q, stdin = _cut_input(q)
    lines = q.splitlines()
    if len(lines) == 1:
        m = _CODE_START.search(q)
        if m:
            return _clean_text(q[:m.start()]), q[m.start():].strip(), stdin
        return q, "", stdin
    text_lines, code_lines, started = [], [], False
    for ln in lines:
        if not started and ln.strip() and not _CODE_START.search(ln) and not re.search(r"[;{}]\s*$", ln):
            text_lines.append(ln)
            continue
        started = True
        code_lines.append(ln)
    return _clean_text(" ".join(text_lines)), "\n".join(code_lines).strip(), stdin


def _cut_input(q: str) -> tuple[str, str]:
    lines = q.splitlines()
    for i, ln in enumerate(lines):
        m = _INPUT_LINE.match(ln)
        if m:
            rest = [m.group(1)] if m.group(1).strip() else []
            return "\n".join(lines[:i]), "\n".join(rest + lines[i + 1:]).strip() + "\n"
    return q, ""


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip(" :-–—\t")


def has_runnable_code(code: str) -> bool:
    """Czy kod coś wypisuje / jest programem (wtedy warto go uruchomić)."""
    if not code or len(code) > 60_000:
        return False
    if detect_lang(code) == "python":
        return bool(re.search(r"\bprint\s*\(|sys\.stdout", code))
    # program bez wypisywania nic nie pokaże — wtedy zostaje zwykła analiza kodu (techniki, sprawdzarka)
    return bool(re.search(r"\bcout\b|\bprintf\s*\(|\bputs\s*\(|\bputchar\s*\(|\bcerr\b", code))


# ============================================================================================== owijanie fragmentu
_GLOBAL_START = re.compile(r"^\s*(template\s*<|struct\s+\w+|class\s+\w+|using\s+\w+\s*=|typedef\s|#define\s|const(expr)?\s+\w[\w:<>, ]*\s+\w+\s*=|"
                           r"[\w:<>,\*&\s]+?\s+\**&?\w+\s*\([^;]*\)\s*(const\s*)?\{?\s*$)")


def wrap_cpp(code: str) -> tuple[str, bool]:
    """Fragment C++ → kompletny program (#include, using namespace std, main). Definicje funkcji/struktur idą przed main."""
    if re.search(r"\bint\s+main\s*\(", code):
        if re.search(r"#\s*include", code):
            return code, False
        return "#include <bits/stdc++.h>\nusing namespace std;\n" + code, True
    includes, globals_, body = [], [], []
    lines = code.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("#include") or s.startswith("using namespace"):
            includes.append(ln)
            i += 1
            continue
        if _GLOBAL_START.match(ln) and not re.match(r"^\s*(return|if|for|while|else|cout|cin|printf)\b", ln):
            depth, block = 0, []
            seen_brace = False
            while i < len(lines):
                block.append(lines[i])
                depth += lines[i].count("{") - lines[i].count("}")
                seen_brace = seen_brace or "{" in lines[i]
                i += 1
                if (seen_brace and depth <= 0) or (not seen_brace and lines[i - 1].rstrip().endswith(";")):
                    break
            globals_.append("\n".join(block))
            continue
        body.append(ln)
        i += 1
    head = "#include <bits/stdc++.h>\nusing namespace std;\n" + ("\n".join(includes) + "\n" if includes else "")
    src = (head + ("\n" + "\n\n".join(globals_) + "\n" if globals_ else "") +
           "\nint main() {\n" + "\n".join("    " + b if b.strip() else b for b in body) + "\n    return 0;\n}\n")
    return src, True


# ============================================================================================== piaskownica
def _sandbox_prefix(workdir: str) -> tuple[list[str], str]:
    wd = os.path.realpath(workdir).replace("\\", "\\\\").replace('"', '\\"')
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        prof = ("(version 1)(allow default)(deny network*)(deny file-write*)"
                f'(allow file-write* (subpath "{wd}") (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/fd/"))')
        return ["sandbox-exec", "-p", prof], "piaskownica macOS: bez sieci, zapis plików tylko w katalogu tymczasowym"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return (["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--bind", workdir, workdir,
                 "--unshare-net", "--die-with-parent", "--chdir", workdir],
                "piaskownica bubblewrap: bez sieci, system tylko do odczytu")
    return [], "limit czasu, pamięci i wyjścia (pełna piaskownica niedostępna w tym systemie)"


def _errors_mod():
    try:
        return importlib.import_module("oiforge.tools.errors")
    except Exception:  # noqa: BLE001
        return None


def run_code(code: str, stdin: str = "", lang: str | None = None, timeout: float = RUN_TIMEOUT) -> dict:
    """Kompiluje i uruchamia kod w piaskownicy. Fragment C++ bez main jest owijany w kompletny program."""
    lang = lang if lang in ("cpp", "python") else detect_lang(code)
    src, wrapped = (wrap_cpp(code) if lang == "cpp" else (code, False))
    out = {"lang": lang, "wrapped": wrapped, "source": src if wrapped else None, "stdin": stdin or "",
           "timeout": timeout}
    prog = runner.prepare(src, lang=lang)
    if not prog.ok:
        stderr = (prog.compile.stderr if prog.compile else prog.error) or prog.error or ""
        items = []
        em = _errors_mod()
        if em is not None and stderr:
            try:
                items = (em.translate(stderr, src) or [])[:5]
            except Exception:  # noqa: BLE001
                items = []
        out.update(ok=False, stage="compile", verdict="CE", errors=items, raw=stderr[:3000])
        return out
    work = tempfile.mkdtemp(prefix="oif-asst-")
    try:
        prefix, label = _sandbox_prefix(work)
        rr = runner.run(prefix + list(prog.cmd), stdin or "", timeout=timeout, cwd=work, mem_limit_mb=RUN_MEM_MB,
                        max_output=RUN_MAX_OUTPUT)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out.update(ok=rr.verdict == "OK", stage="run", verdict=rr.verdict, stdout=rr.stdout, stderr=(rr.stderr or "")[-3000:],
               time=round(rr.time or rr.wall or 0.0, 3), mem_mb=round((rr.mem_kb or 0) / 1024, 1) if rr.mem_kb else None,
               exit_code=rr.exit_code, signal=rr.signal, truncated=rr.truncated, sandbox=label)
    if rr.verdict != "OK":
        em = _errors_mod()
        if em is not None:
            try:
                out["explanation"] = em.explain_runtime(rr.to_dict(), src)
            except Exception:  # noqa: BLE001
                pass
    return out


def run_block(res: dict, title: str | None = None) -> dict:
    return {"type": "run", "title": title, **{k: res.get(k) for k in (
        "lang", "ok", "stage", "verdict", "stdout", "stderr", "time", "mem_mb", "exit_code", "signal", "truncated",
        "errors", "raw", "explanation", "wrapped", "source", "stdin", "sandbox", "timeout")}}


# ============================================================================================== podpowiedzi do wyniku
# (wzorzec w kodzie, artykuł KB, wyjaśnienie) — „dlaczego wypisało 98, a nie b?”
OUTPUT_HINTS = [
    (r"'(?:\\.|[^'\\])'\s*[-+*/%]\s*[\w'(]|[\w')]\s*[-+*/%]\s*'(?:\\.|[^'\\])'", ["str-char-print", "str-char-arithmetic"],
     "Znak (`char`) w działaniu z liczbą zamienia się na `int`, więc wypisuje się **kod ASCII**, a nie litera "
     "(`'a' + 1` to `98`). Żeby dostać znak, rzutuj: `char('a' + 1)` → `b`."),
    (r"cout\s*<<\s*'(?:\\.|[^'\\])'\s*(;|<<)", ["str-ascii-table", "str-char-print"],
     "`'a'` w apostrofach to jeden znak (`char`) — `cout` wypisuje go jako literę. Każdy znak ma swój kod ASCII "
     "(`'a'` = 97, `'A'` = 65, `'0'` = 48); kod dostaniesz przez `(int)'a'`."),
    (r"\(\s*int\s*\)\s*'|int\s*\(\s*'", ["str-ascii-table"],
     "Rzutowanie znaku na `int` daje jego kod ASCII (`(int)'a'` = 97)."),
    (r"(?<![\w.])\d+\s*/\s*\d+(?![\w.])", ["pit-integer-division"],
     "Dzielenie dwóch liczb całkowitych obcina część ułamkową (`7 / 2` to `3`). Dla wyniku z ułamkiem użyj "
     "`7.0 / 2` albo `(double)a / b`."),
    (r"cout\s*<<\s*(true|false)\b|<<\s*\(\s*\w+\s*[<>=!]=?\s*\w+\s*\)", ["io-print-bool"],
     "`cout` wypisuje wartości logiczne jako `1` / `0`. Słowa `true` / `false` dostaniesz przez `cout << boolalpha`."),
    (r"\.size\(\)\s*-\s*1", ["pit-unsigned-size"],
     "`v.size()` jest bez znaku — dla pustego wektora `v.size() - 1` to ogromna liczba, nie `-1`."),
    (r"-\s*\d+\s*%|%\s*-", ["num-negative-mod"],
     "W C++ reszta z dzielenia liczby ujemnej jest ujemna (`-7 % 3` to `-1`). Poprawne modulo: `((a % m) + m) % m`."),
    (r"\bsizeof\s*\(", ["cpp-c-arrays"], "`sizeof` zwraca rozmiar w **bajtach**, nie liczbę elementów."),
    (r"\b(float|double)\b", ["io-precision", "num-floating"],
     "Liczby rzeczywiste `cout` wypisuje domyślnie z 6 cyframi znaczącymi; stałą liczbę miejsc po przecinku daje "
     "`cout << fixed << setprecision(2)`."),
]


def output_hints(code: str, res: dict, exists) -> list[dict]:
    """Wyjaśnienia typowych „niespodzianek” w wyniku programu (tylko tematy istniejące w KB; reguły są dla C++)."""
    out, seen = [], set()
    if detect_lang(code) == "python":
        return out
    for rx, arts, md in OUTPUT_HINTS:
        if not re.search(rx, code):
            continue
        aid = next((a for a in arts if exists(a)), None)
        if md in seen:
            continue
        seen.add(md)
        out.append({"md": md, "article": aid})
        if len(out) >= 2:
            break
    stdout = res.get("stdout") or ""
    if res.get("ok") and re.search(r"(^|\s)-\d{6,}", stdout) and re.search(r"\*", code) and exists("pit-int-overflow") \
            and not any(h["article"] == "pit-int-overflow" for h in out):
        out.append({"md": "Duża **ujemna** liczba w wyniku to zwykle przepełnienie `int` — iloczyn liczy się w `int`, "
                          "zanim trafi do `long long`. Rzutuj: `1LL * a * b`.", "article": "pit-int-overflow"})
    return out


# ============================================================================================== komendy
# nazwa → (aliasy, opis, czego potrzebuje, tylko pełny OI Forge)
COMMANDS = {
    "pomoc": (["help", "h", "komendy", "?"], "lista komend", None, False),
    "clear": (["wyczysc", "wyczyść", "reset", "nowa", "clean"], "czyści rozmowę i kontekst", None, False),
    "uruchom": (["run", "r", "odpal", "wykonaj", "exec"], "uruchamia kod w piaskownicy (dane po linii `wejście:`)", "code", False),
    "programy": (["moje", "program", "szukaj"], "szuka w Twoich programach z SIO2", "text", False),
    "docs": (["dokumentacja", "artykul", "artykuł", "doc"], "otwiera temat z dokumentacji", "text", False),
    "sprawdz": (["sprawdź", "check", "bledy", "błędy", "lint"], "sprawdzarka: typowe błędy w kodzie", "code", True),
    "zlozonosc": (["złożoność", "complexity", "big-o", "bigo"], "szacuje złożoność kodu", "code", True),
    "wyjasnij": (["wyjaśnij", "objasnij", "objaśnij", "explain", "co-robi"], "objaśnia, co robi kod", "code", True),
    "formatuj": (["format", "upiekszaj", "upiększ", "wciecia"], "formatuje kod", "code", True),
    "optymalizuj": (["optymalizacja", "optimize", "przyspiesz"], "proponuje przyspieszenia kodu", "code", True),
    "py2cpp": (["cpp", "tlumacz", "tłumacz", "przetlumacz", "przetłumacz"], "tłumaczy kod z Pythona na C++", "code", True),
    "blad": (["błąd", "error", "kompilator"], "tłumaczy komunikat kompilatora / błąd wykonania", "text", True),
    "zadanie": (["task", "tresc", "treść", "objasnij-zadanie"], "objaśnia treść zadania i dobiera algorytm", "text", True),
    "algorytm": (["doradca", "jaki-algorytm"], "doradca algorytmów dla treści zadania", "text", True),
    "testy": (["brzegowe", "edge", "testy-brzegowe"], "testy brzegowe z treści zadania", "text", True),
    "rozwiaz": (["rozwiąż", "solve", "solver", "auto-solver"], "auto-solver: próbuje rozwiązać zadanie", "text", True),
}
_ALIAS = {}
for _name, (_aliases, *_rest) in COMMANDS.items():
    for _a in [_name] + _aliases:
        _ALIAS[T.strip_accents(_a).lower()] = _name


def available_commands() -> list[dict]:
    return [{"name": n, "aliases": a, "desc": d, "needs": need} for n, (a, d, need, full) in COMMANDS.items()
            if FULL or not full]


def parse_command(q: str) -> tuple[str, str] | None:
    """„/uruchom cout << 1;” → ("uruchom", "cout << 1;"). Nieznana komenda → ("?", nazwa)."""
    m = re.match(r"^\s*/([\w\-?ąćęłńóśźż]+)\s*(.*)$", q or "", re.S | re.I)
    if not m:
        return None
    name = _ALIAS.get(T.strip_accents(m.group(1)).lower())
    if name is None:
        return ("?", m.group(1))
    if COMMANDS[name][3] and not FULL:
        return ("full-only", name)
    return name, m.group(2).strip()


# naturalne prośby przy wklejonym kodzie (bez „/”)
_NATURAL = [
    ("py2cpp", r"\b(przetlumacz|przeloz|zamien|tlumacz)\w*\b.*\b(c\+\+|cpp)\b|\bna c\+\+|python na c|py2cpp"),
    ("formatuj", r"\b(sformatuj|formatuj|popraw (wciecia|formatowanie)|upieksz)\w*"),
    ("optymalizuj", r"\b(przyspiesz|zoptymalizuj|optymalizuj|szybciej|tle)\w*\b"),
    ("zlozonosc", r"\b(zlozonosc\w*|jak szybk\w*|ile operacji|big ?o)\b"),
    ("sprawdz", r"\b(sprawdz (ten |moj |mi )?(kod|program)|znajdz blad|gdzie (jest )?blad|czemu (nie dziala|zle|wa)|"
                r"dlaczego (nie dziala|zle|wa|wywala)|co jest zle|bledy w kodzie)\b"),
    ("wyjasnij", r"\b(co robi|wyjasnij|objasnij|wytlumacz)\b.*\b(kod|program|to)\b|\bco robi ten\b"),
    ("uruchom", r"\b(uruchom|odpal|wykonaj|co (to |on |ten kod |program )?(wypisze|wyswietli|zwroci|da)|jaki (bedzie )?wynik|"
                r"co sie stanie|co wypisuje|run)\b"),
]


def natural_command(text: str) -> str | None:
    n = T.normalize(text or "")
    for name, rx in _NATURAL:
        if re.search(rx, n) and (FULL or not COMMANDS[name][3]):
            return name
    return None


# ============================================================================================== narzędzia pełnego OI Forge
def call_tool(module: str, spec: str, body: dict) -> dict | None:
    """Wywołuje handler HTTP innego narzędzia bezpośrednio (ten sam kod co w jego widoku)."""
    try:
        m = importlib.import_module("oiforge.tools." + module)
        fn = (getattr(m, "ROUTES", None) or {}).get(spec)
        return fn(body) if fn else None
    except Exception as e:  # noqa: BLE001 — narzędzie nie może wywrócić czatu
        return {"_error": str(e)[:300]}


def _line_mapper(src: str, orig: str):
    """Numer linii w owiniętym programie → numer linii we fragmencie ucznia (po treści linii)."""
    s_lines, o_lines = src.splitlines(), orig.splitlines()
    index: dict[str, int] = {}
    for i, ln in enumerate(o_lines, 1):
        index.setdefault(ln.strip(), i)

    def f(line):
        if not isinstance(line, int) or not (1 <= line <= len(s_lines)):
            return line
        return index.get(s_lines[line - 1].strip(), line)
    return f


def tool_blocks(name: str, arg: str, code: str, lang: str) -> tuple[list[dict], list[dict]]:
    """Wynik narzędzia → (bloki odpowiedzi, akcje). Zwraca ([], []) gdy narzędzie niedostępne."""
    b: list[dict] = []
    act: list[dict] = []
    orig = code
    fix_line = lambda x: x  # noqa: E731
    if lang == "cpp" and code and name in ("sprawdz", "zlozonosc", "wyjasnij", "optymalizuj"):
        src, wrapped = wrap_cpp(code)
        if wrapped:
            fix_line = _line_mapper(src, orig)
            code = src
    if name == "sprawdz":
        r = call_tool("checker", "POST /api/checker/analyze", {"code": code, "lang": lang})
        issues = (r or {}).get("issues") or []
        if not issues:
            b.append({"type": "notice", "tone": "success", "md": "**Sprawdzarka:** nie znalazłem typowych błędów w tym kodzie."})
        else:
            b.append({"type": "text", "md": f"**Sprawdzarka** znalazła {len(issues)} "
                                            f"{'uwagę' if len(issues) == 1 else 'uwagi' if len(issues) < 5 else 'uwag'}:"})
            b.append({"type": "errors", "items": [{"line": fix_line(i.get("line")), "col": i.get("col"), "severity": i.get("severity"),
                                                    "title": i.get("title"), "explanation": i.get("message"), "fix": i.get("fix")}
                                                   for i in issues[:8]], "source": "checker"})
        act.append({"label": "Otwórz w Sprawdzarce", "icon": "bug", "view": "checker", "params": {"code": code}})
    elif name == "zlozonosc":
        r = call_tool("complexity", "POST /api/complexity/analyze", {"code": code}) or {}
        if r.get("complexity"):
            t = r.get("time") or {}
            md = f"**Złożoność: {r['complexity']}**"
            if t.get("ops_fmt"):
                md += f" — ok. {t['ops_fmt']} operacji" + (f" ({', '.join(r.get('assumptions') or [])})" if r.get("assumptions") else "")
            if t.get("label"):
                md += f". {t['label']}"
            b.append({"type": "complexity", "title": "Złożoność kodu", "text": md})
            hs = r.get("hotspots") or []
            if hs:
                b.append({"type": "text", "md": "Najdroższe miejsca:\n" + "\n".join(
                    f"- linia {fix_line(h_.get('line'))}: `{(h_.get('code') or '').strip()[:80]}` — {h_.get('expr')} "
                    f"({round(100 * (h_.get('share') or 0))}%)" for h_ in hs[:3])})
        act.append({"label": "Otwórz Analizę złożoności", "icon": "gauge", "view": "complexity", "params": {"code": code}})
    elif name == "wyjasnij":
        r = call_tool("explainer", "POST /api/explain", {"code": code}) or {}
        if r.get("overview_md"):
            b.append({"type": "text", "md": r["overview_md"]})
            notes = r.get("line_notes") or []
            if notes:
                b.append({"type": "text", "md": "**Krok po kroku:**\n" + "\n".join(
                    f"{i + 1}. linia {fix_line(n_.get('line'))}: {n_.get('note')}" for i, n_ in enumerate(notes[:12]))})
        act.append({"label": "Otwórz w Objaśniaczu", "icon": "message-square", "view": "explain", "params": {"code": code}})
    elif name == "formatuj":
        r = call_tool("formatter", "POST /api/format", {"code": code}) or {}
        if r.get("code"):
            b.append({"type": "text", "md": "Sformatowany kod:" if r.get("changed") else "Kod był już ładnie sformatowany:"})
            b.append({"type": "code", "lang": "cpp", "code": r["code"]})
    elif name == "optymalizuj":
        r = call_tool("optimizer", "POST /api/optimize", {"code": code}) or {}
        ch = r.get("changes") or []
        if ch:
            b.append({"type": "text", "md": "**Przyspieszenia:**\n" + "\n".join(f"- **{c_.get('title')}** — {c_.get('why')}" for c_ in ch[:6])})
            b.append({"type": "code", "lang": "cpp", "code": r.get("optimized_code") or code})
        else:
            b.append({"type": "notice", "tone": "info", "md": "Nie widzę prostych przyspieszeń (szybkie I/O, endl, kopie…). "
                                                              "Jeśli to TLE, sprawdź złożoność: `/zlozonosc`."})
        act.append({"label": "Otwórz Optymalizator", "icon": "zap", "view": "optimizer", "params": {"code": code}})
    elif name == "py2cpp":
        r = call_tool("py2cpp", "POST /api/py2cpp/translate", {"code": code}) or {}
        if r.get("cpp"):
            b.append({"type": "text", "md": "Kod przetłumaczony z Pythona na C++:"})
            b.append({"type": "code", "lang": "cpp", "code": r["cpp"]})
            if r.get("warnings"):
                b.append({"type": "pitfalls", "items": [str(w.get("message") if isinstance(w, dict) else w) for w in r["warnings"][:5]]})
        elif r.get("error"):
            b.append({"type": "notice", "tone": "warn", "md": f"Nie udało się przetłumaczyć: {r['error']}"})
        act.append({"label": "Otwórz Python → C++", "icon": "languages", "view": "py2cpp", "params": {"code": code}})
    elif name == "blad":
        r = call_tool("errors", "POST /api/errors/translate", {"stderr": arg or code}) or {}
        items = r.get("items") or []
        if items:
            head = (r.get("summary") or {}).get("headline")
            if head:
                b.append({"type": "text", "md": f"**{head}**"})
            b.append({"type": "errors", "items": items[:8], "source": "errors"})
        act.append({"label": "Otwórz Tłumacz błędów", "icon": "languages", "view": "errors", "params": {"stderr": arg or code}})
    elif name in ("zadanie", "algorytm"):
        st_text = arg or code
        if name == "zadanie":
            r = call_tool("taskexplain", "POST /api/taskexplain", {"statement": st_text}) or {}
            brief = (r.get("brief") or {}).get("text")
            if brief:
                b.append({"type": "text", "md": f"**W skrócie:** {brief}"})
            tt = r.get("task_type") or {}
            if tt.get("label"):
                b.append({"type": "text", "md": f"Typ zadania: **{tt['label']}** — {tt.get('consequence') or ''}"})
            act.append({"label": "Pełne objaśnienie treści", "icon": "file-text", "view": "taskexplain", "params": {"text": st_text}})
        s = call_tool("suggest", "POST /api/suggest", {"statement": st_text}) or {}
        cx = s.get("complexity") or {}
        if cx.get("complexity"):
            b.append({"type": "complexity", "title": "Docelowa złożoność",
                      "text": f"{cx.get('var')} ≤ {cx.get('max_fmt')} → celuj w **{cx['complexity']}**" + (f" ({cx['hint']})" if cx.get("hint") else "")})
        algs = s.get("algorithms") or []
        if algs:
            b.append({"type": "text", "md": "**Pasujące algorytmy:**\n" + "\n".join(
                f"- **{a.get('name')}** ({a.get('complexity')}) — {a.get('when') or ''}" for a in algs[:4])})
        act.append({"label": "Doradca algorytmów", "icon": "lightbulb", "view": "suggest", "params": {"text": st_text}})
    elif name == "testy":
        r = call_tool("edgecases", "POST /api/edgecases", {"statement": arg or code}) or {}
        tests = r.get("tests") or []
        if tests:
            b.append({"type": "text", "md": f"**{len(tests)} testów brzegowych** — najważniejsze:"})
            for t_ in tests[:4]:
                b.append({"type": "text", "md": f"**{t_.get('title')}** — {t_.get('why') or ''}"})
                inp = t_.get("input") or ""
                b.append({"type": "code", "lang": "text", "code": inp if len(inp) < 400 else inp[:400] + "\n…"})
        act.append({"label": "Wszystkie testy brzegowe", "icon": "target", "view": "edgecases", "params": {"text": arg or code}})
    elif name == "rozwiaz":
        r = call_tool("autosolver", "POST /api/autosolver/solve", {"statement": arg or code}) or {}
        if r.get("message"):
            b.append({"type": "notice", "tone": "success" if r.get("status") == "solved" else "warn", "md": r["message"]})
        best = r.get("best") or {}
        if best.get("code"):
            b.append({"type": "code", "lang": "cpp", "code": best["code"]})
        act.append({"label": "Otwórz Auto-solver", "icon": "wand-sparkles", "view": "autosolver", "params": {"text": arg or code}})
    for blk in b:
        if blk.get("type") == "code" and not blk.get("lang"):
            blk["lang"] = "cpp"
    return b, act


# ============================================================================================== „nie rozumiem”
_CLARIFY_RX = re.compile(
    r"^(nie (rozumiem|kumam|czaje|lapie|ogarniam|wiem o co chodzi)|dalej nie|nadal nie|wciaz nie|nic nie rozumiem|"
    r"ze co|co\?+$|hm+\??$|niejasne|nie jasne|za trudne|zbyt trudne|trudne|"
    r"(wytlumacz|wyjasnij|powiedz|napisz)( to)? (prosciej|jasniej|inaczej|jeszcze raz|po ludzku|krocej)|"
    r"(prosciej|jasniej|inaczej|po ludzku|krocej)\??$|mozesz (prosciej|jasniej|inaczej)|eli5|jak dla (dziecka|laika|5)|"
    r"(pokaz|daj|podaj)( mi)?( jakis| inny| kolejny)? przyklad|przyklad\??$|inny przyklad|"
    r"krok po kroku|po kolei|co to znaczy\??$|o co chodzi\??$)")


def clarify_mode(q: str) -> str | None:
    """None, gdy to nie jest prośba o prostsze wyjaśnienie; inaczej "simple" | "example" | "steps"."""
    n = T.normalize(q or "").strip()
    if len(n) > 80 or not _CLARIFY_RX.search(n):
        return None
    if re.search(r"przyklad", n):
        return "example"
    if re.search(r"krok po kroku|po kolei", n):
        return "steps"
    return "simple"


# proste analogie do najważniejszych tematów (ręcznie napisane — działają bez AI)
ANALOGIES = {
    "stl-vector": "Wyobraź sobie **rozciągliwą szafkę z ponumerowanymi szufladami** (od 0). Możesz dokładać szuflady na końcu (`push_back`), a do każdej zajrzeć po numerze (`v[i]`).",
    "stl-set": "To **pudełko, które samo trzyma porządek**: każda wartość jest w nim najwyżej raz, a elementy zawsze leżą posortowane. Sprawdzenie „czy jest w środku?” jest bardzo szybkie.",
    "stl-multiset": "Jak `set`, ale **powtórki są dozwolone** — jak posortowana kupka kart, w której może być kilka takich samych.",
    "stl-map": "To **słownik**: do każdego słowa (klucza) przypisujesz jego znaczenie (wartość). `m[\"kot\"] = 3` — pod hasłem „kot” zapisujesz 3.",
    "stl-unordered-map": "Słownik jak `map`, ale **bez porządku** — w zamian zwykle szybszy (hashowanie).",
    "stl-queue": "**Kolejka w sklepie**: kto pierwszy przyszedł, ten pierwszy wychodzi (`push` na koniec, `front`/`pop` z przodu).",
    "stl-stack": "**Stos talerzy**: odkładasz na wierzch i zdejmujesz z wierzchu — ostatni włożony wychodzi pierwszy.",
    "stl-priority-queue": "**Izba przyjęć w szpitalu**: zawsze obsługujesz najpilniejszy (największy) element, niezależnie od kolejności przyjścia.",
    "stl-deque": "Kolejka, do której można **wchodzić i wychodzić z obu końców**.",
    "stl-pair": "**Dwie rzeczy spięte gumką**: `first` i `second`, np. (wiek, imię).",
    "stl-sort": "Jak **układanie kart w ręce** od najmniejszej do największej — tylko komputer robi to w O(n log n).",
    "stl-binary-search": "`lower_bound` to **szukanie słowa w słowniku**: otwierasz w połowie i od razu wiesz, w którą stronę iść.",
    "tech-binary-search": "Gra **„zgadnij liczbę — wyżej/niżej”**: za każdym razem wyrzucasz połowę możliwości, więc dla miliona liczb wystarczy ~20 pytań.",
    "tech-binary-search-answer": "Zamiast liczyć odpowiedź wprost, **zgadujesz ją** i sprawdzasz „czy się da?” — jak w zgadywance wyżej/niżej, tylko pytaniem jest funkcja sprawdzająca.",
    "tech-two-pointers": "**Gąsienica**: przód wysuwa się do przodu, a tył podciąga, gdy okno robi się za duże — każdy wskaźnik idzie tylko w jedną stronę.",
    "tech-sliding-window": "**Okno w pociągu**: przesuwasz się o jedno miejsce — jeden element wjeżdża, jeden wyjeżdża, nie liczysz wszystkiego od nowa.",
    "tech-greedy": "**Wydawanie reszty najpierw największymi monetami**: w każdym kroku bierzesz to, co teraz wygląda najlepiej — trzeba tylko udowodnić, że to się opłaca.",
    "ds-prefix-sums": "**Licznik kilometrów w samochodzie**: żeby wiedzieć, ile przejechałeś między miastem A i B, odejmujesz dwa odczyty licznika — nie sumujesz trasy od nowa.",
    "graph-bfs": "**Kręgi na wodzie po wrzuceniu kamienia**: najpierw odwiedzasz wszystko w odległości 1, potem 2, potem 3… dlatego BFS znajduje najkrótszą drogę.",
    "graph-dfs": "**Zwiedzanie labiryntu**: idziesz jak najgłębiej, a gdy trafisz na ślepy zaułek, cofasz się do ostatniego rozwidlenia.",
    "graph-dijkstra": "**Rozchodzenie się wiadomości po mapie z czasami przejazdu**: zawsze „zatwierdzasz” miasto, do którego wiadomość dotarła najwcześniej.",
    "graph-dsu": "**Grupy znajomych z szefem**: każda grupa ma szefa; żeby sprawdzić, czy dwie osoby są w tej samej grupie, porównujesz ich szefów.",
    "graph-representation": "Graf to **mapa miast (wierzchołki) i dróg (krawędzie)**. Lista sąsiedztwa to dla każdego miasta lista miast, do których prowadzi droga.",
    "dp-intro": "**Zapisywanie wyników w zeszycie**: zanim policzysz duży problem, liczysz małe i zapisujesz odpowiedzi, żeby nie liczyć tego samego dwa razy.",
    "cpp-recursion-basics": "**Matrioszka**: funkcja rozwiązuje problem, otwierając mniejszą wersję samego siebie — aż do najmniejszej laleczki (warunek stopu).",
    "tech-backtracking": "**Szukanie wyjścia z labiryntu z kredą**: próbujesz drogi, a gdy się nie da — wracasz i próbujesz następnej.",
    "ds-segment-tree": "**Turniej sportowy w drabince**: każdy mecz (węzeł) pamięta zwycięzcę swojej części, więc wynik dowolnego fragmentu składasz z kilku gotowych meczów.",
    "ds-fenwick": "Sumy prefiksowe, które **da się szybko aktualizować** — każdy element pamięta sumę kawałka o długości potęgi dwójki.",
    "math-gcd-lcm": "NWD to **największy kafelek**, którym da się równo wyłożyć podłogę a × b; NWW to najmniejsza długość, którą da się zmierzyć oboma miarkami.",
    "math-sieve": "**Skreślanie wielokrotności**: zaczynasz od 2 i skreślasz 4, 6, 8…, potem od 3 itd. — co zostanie nieskreślone, jest pierwsze.",
    "str-ascii-table": "Komputer pamięta litery jako **numery z tabelki**: 'A' to 65, 'a' to 97, '0' to 48. `char` to po prostu mała liczba wypisywana jako znak.",
    "num-overflow": "**Licznik w starym samochodzie**: po 999999 przeskakuje na 000000. `int` po ~2,1 mld „przekręca się” na liczby ujemne.",
    "pit-integer-division": "Dzielenie liczb całkowitych to **dzielenie cukierków bez krojenia**: 7 cukierków na 2 osoby = po 3, reszta zostaje.",
}


def _sentences(md: str) -> list[str]:
    txt = re.sub(r"```.*?```", " ", md or "", flags=re.S)
    txt = re.sub(r"^\s*#+\s*", "", txt, flags=re.M)
    txt = re.sub(r"\s+", " ", txt).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ`*(])", txt)
    return [p.strip() for p in parts if 12 <= len(p.strip()) <= 320 and not p.strip().endswith(":")]


def simple_steps(article: dict, k: int = 5) -> list[str]:
    """Kilka zdań z treści artykułu jako „po kolei” (pierwsze bloki tekstu, bez tabel i kodu)."""
    out = []
    for b in article.get("body") or []:
        if b.get("type") in ("text", "tip") and b.get("md"):
            out += _sentences(b["md"])
        if len(out) >= k:
            break
    return out[:k]


def code_examples(article: dict, max_lines: int = 25, first_qa: int | None = None) -> list[tuple[str, str, str]]:
    """Przykłady kodu z artykułu: najpierw kod z odpowiedzi, której uczeń nie zrozumiał (`first_qa`), potem
    najprostsze — pozytywne (nie „tak NIE rób”), wypisujące coś i najkrótsze: [(tytuł, kod, język)]."""
    ex = []
    for b in article.get("body") or []:
        if b.get("type") == "code" and b.get("code"):
            ex.append((b.get("title") or "Przykład", b["code"], b.get("lang") or "cpp"))
    for qa in article.get("qa") or []:
        if qa.get("code"):
            qs = qa.get("q") or []
            ex.append(((qs[0] if qs else "Przykład"), qa["code"], qa.get("lang") or "cpp"))
    ex = [e for e in ex if e[1].count("\n") < max_lines]
    bad = re.compile(r"\b(zle|źle|ub|crash|blad|błąd|pulapk|pułapk|nie dziala|nie działa|wrong|bug)\b", re.I)

    def key(e):
        title, code, _ = e
        negative = bool(bad.search(title) or bad.search(code))
        prints = bool(re.search(r"\bcout\b|printf\s*\(|print\s*\(", code))
        return (negative, not prints, code.count("\n"), len(code))
    ex.sort(key=key)
    qas = article.get("qa") or []
    if first_qa is not None and 0 <= first_qa < len(qas) and qas[first_qa].get("code"):
        qa = qas[first_qa]
        lead = ((qa.get("q") or ["Przykład"])[0], qa["code"], qa.get("lang") or "cpp")
        ex = [lead] + [e for e in ex if e[1] != lead[1]]
    return ex


# ============================================================================================== profil ucznia
_PROFILE_KEY = "assistant_profile"
_plock = threading.Lock()


def profile() -> dict:
    try:
        p = store.get(_PROFILE_KEY, None) or {}
    except Exception:  # noqa: BLE001
        p = {}
    p.setdefault("topics", {})
    p.setdefault("confused", {})
    p.setdefault("lang", {"cpp": 0, "python": 0})
    p.setdefault("levels", [])
    p.setdefault("last", [])
    p.setdefault("asked", 0)
    return p


def _save(p: dict) -> None:
    try:
        store.set(_PROFILE_KEY, p)
    except Exception:  # noqa: BLE001
        pass


def note_question(q: str, topic: dict | None, level: int | None = None, code_lang: str | None = None) -> None:
    with _plock:
        p = profile()
        p["asked"] += 1
        n = T.normalize(q or "")
        if re.search(r"\bpython\w*|\bpy\b|\bpypy\b", n) or code_lang == "python":
            p["lang"]["python"] = p["lang"].get("python", 0) + 1
        elif re.search(r"c\+\+|\bcpp\b|\bstl\b", (q or "").lower()) or code_lang == "cpp":
            p["lang"]["cpp"] = p["lang"].get("cpp", 0) + 1
        if topic and topic.get("id"):
            aid = topic["id"]
            p["topics"][aid] = p["topics"].get(aid, 0) + 1
            p["last"] = ([aid] + [x for x in p["last"] if x != aid])[:12]
            if level:
                p["levels"] = (p["levels"] + [int(level)])[-40:]
        _save(p)


def note_confused(aid: str) -> int:
    with _plock:
        p = profile()
        p["confused"][aid] = p["confused"].get(aid, 0) + 1
        _save(p)
        return p["confused"][aid]


def user_level(p: dict | None = None) -> float:
    lv = sorted((p or profile()).get("levels") or [])
    return float(lv[len(lv) // 2]) if len(lv) >= 3 else 2.0


def prefers_python(p: dict | None = None) -> bool:
    lang = (p or profile()).get("lang") or {}
    return lang.get("python", 0) >= 3 and lang.get("python", 0) > 1.5 * lang.get("cpp", 0)


def reset_profile() -> None:
    _save({})
