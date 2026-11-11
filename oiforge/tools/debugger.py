"""Wizualny debugger: śledzenie programu linia po linii z wartościami zmiennych, tablicami, stosem i wyjściem.

Backendy (wybierane automatycznie):
- C++ + lldb (macOS/Linux): `_dbg_lldb_driver.py` uruchamiany w osobnym procesie przez Pythona, który ma moduł `lldb`
  (na macOS: `xcrun python3` / `/usr/bin/python3`, ścieżka modułu z `lldb -P`);
- C++ + gdb (Linux/Windows): `_dbg_gdb.py` (GDB/MI) w wątku zadania;
- Python: `_dbg_py_tracer.py` (sys.settrace) w osobnym procesie.

HTTP:
    POST /api/debugger/trace {code, input?, lang?, max_steps?, watch?, max_items?} -> {job}
         wynik zadania: {steps:[{line, func, depth, stack, vars, stdout, out, ret?, crash?}], source, lang, backend,
                         truncated, reason, message, error, stdout, exit_code, lines, stats}
    GET|POST /api/debugger/capabilities -> co jest dostępne na tej maszynie
    GET /api/debugger/examples -> przykładowe programy do śledzenia
"""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from oiforge import config
from oiforge.core import cpp_lexer, jobs, runner
from oiforge.server_api import ApiError

HERE = Path(__file__).resolve().parent
LLDB_DRIVER = HERE / "_dbg_lldb_driver.py"
PY_TRACER = HERE / "_dbg_py_tracer.py"

DEFAULT_MAX_STEPS = 3000
HARD_MAX_STEPS = 20000
TIME_BUDGET = 25.0
MAX_RESULT_BYTES = 60_000_000

# Dopisywane NA KOŃCU źródła (numery linii użytkownika się nie zmieniają): wyjście bez buforowania, żeby stdout
# programu był widoczny krok po kroku (także przy cin.tie(0) / sync_with_stdio(false)).
UNBUF_SNIPPET = (
    "\n#include <cstdio>\n#include <iostream>\n"
    "namespace { struct OIForgeUnbuf_ { OIForgeUnbuf_() { std::setvbuf(stdout, nullptr, _IONBF, 0); "
    "std::cout.setf(std::ios::unitbuf); } } oiforge_unbuf_; }\n"
)


# ============================================================================================ możliwości
def _run_quiet(cmd: list, timeout: float = 20.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **_no_window())
    except (OSError, subprocess.SubprocessError):
        return None


def _no_window() -> dict:
    return {"creationflags": 0x08000000} if config.IS_WINDOWS else {}


@functools.lru_cache(maxsize=1)
def lldb_interpreter() -> tuple[tuple[str, ...], str] | None:
    """(polecenie interpretera Pythona, ścieżka modułu lldb) — taki, w którym `import lldb` działa."""
    if config.IS_WINDOWS or not config.LLDB:
        return None
    path = config.lldb_python_path()
    if not path:
        return None
    cands: list[tuple[str, ...]] = []
    if config.IS_MAC and shutil.which("xcrun"):
        cands.append(("xcrun", "python3"))
    for p in ("/usr/bin/python3", shutil.which("python3"), sys.executable):
        if p and (p,) not in cands:
            cands.append((p,))
    probe = "import sys; sys.path.insert(0, sys.argv[1]); import lldb; print('OK', sys.version.split()[0])"
    for c in cands:
        r = _run_quiet([*c, "-c", probe, path], timeout=30)
        if r is not None and r.returncode == 0 and "OK" in r.stdout:
            return c, path
    return None


def capabilities() -> dict:
    caps = config.capabilities()
    li = lldb_interpreter()
    if li:
        cpp = "lldb"
    elif config.GDB:
        cpp = "gdb"
    else:
        cpp = None
    notes = []
    if cpp is None:
        if config.IS_MAC:
            notes.append("Brak lldb z modułem Pythona. Zainstaluj Xcode Command Line Tools: `xcode-select --install`.")
        elif config.IS_WINDOWS:
            notes.append("Brak gdb. Zainstaluj go przez MSYS2: `pacman -S mingw-w64-ucrt-x86_64-gdb` i uruchom ponownie.")
        else:
            notes.append("Brak gdb/lldb. Zainstaluj: `sudo apt install gdb` (albo `lldb python3-lldb`).")
        notes.append("Do tego czasu możesz śledzić programy w Pythonie (tryb Python działa zawsze).")
    if cpp == "lldb" and config.IS_MAC:
        notes.append("Jeśli macOS zapyta o zgodę na debugowanie, potwierdź ją. Na stałe: `sudo DevToolsSecurity -enable`.")
    return {
        "cpp": cpp,
        "python": True,
        "platform": config.PLATFORM,
        "lldb": config.LLDB,
        "gdb": config.GDB,
        "lldb_interpreter": " ".join(li[0]) if li else None,
        "compiler": caps.get("clang") or caps.get("gcc"),
        "max_steps": DEFAULT_MAX_STEPS,
        "hard_max_steps": HARD_MAX_STEPS,
        "time_budget": TIME_BUDGET,
        "notes": notes,
    }


def handle_capabilities(_body: dict) -> dict:
    return {"capabilities": capabilities()}


# ============================================================================================ analiza źródła
_DECL_STOP = {"struct", "class", "enum", "union", "using", "typedef", "template", "namespace", "static_assert",
              "friend", "return", "#define", "operator", "extern"}
_TYPEISH_PREV = {"int", "long", "short", "char", "bool", "double", "float", "auto", "unsigned", "signed", "void",
                 "size_t", "const", "static", "constexpr", "register", "volatile", "mutable", "inline", "thread_local"}


def global_names(code: str) -> list[str]:
    """Nazwy zmiennych globalnych zadeklarowanych w kodzie (na poziomie pliku), w kolejności deklaracji."""
    try:
        toks = cpp_lexer.significant(cpp_lexer.tokenize(code))
    except Exception:  # noqa: BLE001
        return []
    pairs = cpp_lexer.match_brackets(toks)
    names: list[str] = []
    i = 0
    n = len(toks)
    stmt: list = []
    while i < n:
        t = toks[i]
        if t.text == "{":
            close = pairs.get(i, i)
            head = [x.text for x in stmt]
            is_init = "=" in head or (head and head[-1] not in (")", "const", "noexcept", "override") and
                                      not any(h in _DECL_STOP for h in head) and "(" not in head)
            if head and head[0] == "namespace":
                stmt = []
                i += 1                                       # wejdź do przestrzeni nazw
                continue
            if is_init and head and not any(h in _DECL_STOP for h in head):
                stmt.append(t)
                stmt.extend(toks[i + 1:close + 1])
                i = close + 1
                continue
            stmt = []
            i = close + 1
            if i < n and toks[i].text == ";":
                i += 1
            continue
        if t.text == "}":
            stmt = []
            i += 1
            continue
        if t.text == ";":
            names.extend(_declarators(stmt))
            stmt = []
            i += 1
            continue
        stmt.append(t)
        i += 1
    seen = set()
    out = []
    for nm in names:
        if nm not in seen:
            seen.add(nm)
            out.append(nm)
    return out


def _declarators(stmt: list) -> list[str]:
    if not stmt:
        return []
    texts = [t.text for t in stmt]
    if texts[0] in _DECL_STOP or any(x in ("struct", "class", "enum", "typedef", "using", "template") for x in texts[:2]):
        return []
    # podział po przecinkach na najwyższym poziomie (nawiasy, klamry, ostre nawiasy szablonów)
    parts: list[list] = [[]]
    depth = 0
    angle = 0
    for t in stmt:
        x = t.text
        if x in "([{":
            depth += 1
        elif x in ")]}":
            depth -= 1
        elif x == "<" and depth == 0:
            angle += 1
        elif x == ">" and depth == 0 and angle:
            angle -= 1
        elif x == ">>" and depth == 0 and angle:
            angle = max(0, angle - 2)
        if x == "," and depth == 0 and angle == 0:
            parts.append([])
            continue
        parts[-1].append(t)
    out = []
    for k, part in enumerate(parts):
        name = None
        angle = 0
        for j, t in enumerate(part):
            if t.text == "<":
                angle += 1
            elif t.text == ">":
                angle = max(0, angle - 1)
            elif t.text == ">>":
                angle = max(0, angle - 2)
            if angle:
                continue
            if t.text in ("=", "[", "{", "("):
                break
            if t.kind == "id":
                name = t
        if name is None:
            continue
        idx = part.index(name)
        nxt = part[idx + 1].text if idx + 1 < len(part) else ""
        if nxt == "(":
            inner = part[idx + 2:idx + 3]
            if not inner or inner[0].text == ")" or inner[0].kind == "kw" or (k == 0 and idx == 0):
                continue                                     # prototyp funkcji
        if k == 0 and idx == 0:
            continue                                         # brak typu (np. wywołanie)
        if name.text in ("std", "main"):
            continue
        out.append(name.text)
    return out


def decl_lines(code: str) -> dict:
    """{funkcja: {zmienna: linia pierwszej deklaracji}} — heurystyka dla gdb (lldb ma to w informacjach debugowych)."""
    out: dict = {}
    try:
        funcs = cpp_lexer.find_functions(code)
        toks = cpp_lexer.significant(cpp_lexer.tokenize(code))
    except Exception:  # noqa: BLE001
        return out
    fnames = {f["name"] for f in funcs}
    for f in funcs:
        d: dict = {}
        body = [t for t in toks if f["body_start"] <= t.pos <= f["body_end"]]
        in_decl = False
        depth = 0
        for j, t in enumerate(body):
            if t.text in "([":
                depth += 1
            elif t.text in ")]":
                depth -= 1
            if t.text in (";", "{", "}"):
                in_decl = False
                continue
            if t.kind != "id" or j == 0 or t.text in fnames:
                continue
            prev = body[j - 1]
            nxt = body[j + 1].text if j + 1 < len(body) else ""
            if nxt not in ("=", ";", ",", "[", "(", "{", ":", ")"):
                continue
            if prev.text in ("*", "&") and (nxt == "(" or body[j - 2].kind in ("num",) or
                                            body[j - 2].text in (")", "]")):
                continue
            typeish = (prev.kind == "kw" and prev.text in _TYPEISH_PREV) or prev.text in (">", ">>", "*", "&") and \
                j >= 2 or (prev.kind == "id" and body[j - 2].text in (";", "{", "}", "(", "const", "static", ","))
            if typeish or (in_decl and prev.text == ","):
                d.setdefault(t.text, t.line)
                in_decl = True
        out[f["name"]] = d
    return out


def parse_watch(w) -> list[str]:
    if not w:
        return []
    if isinstance(w, str):
        w = re.split(r"[\s,;]+", w)
    return [x.strip() for x in w if isinstance(x, str) and re.fullmatch(r"[A-Za-z_]\w*", x.strip() or "")][:64]


# ============================================================================================ zmiany
def _core(node):
    if not isinstance(node, dict):
        return node
    return (node.get("kind"), node.get("value"), node.get("items"), node.get("rows"), node.get("fields"), node.get("size"))


def mark_changes(steps: list[dict]) -> None:
    """Dodaje `changed` (i changed_idx / changed_cells / changed_fields) względem poprzedniego stanu tej samej
    zmiennej w tej samej aktywacji funkcji (globalne — zawsze względem poprzedniego kroku)."""
    prev: dict = {}
    prev_ids: set = set()
    for si, st in enumerate(steps):
        ids = [e.get("id") for e in st.get("stack") or []]
        top = st.get("frame") or (ids[0] if ids else "")
        if top not in prev_ids:
            for k in [k for k in prev if k[0] == top]:
                del prev[k]
        live = set(ids) | {top}
        for k in [k for k in prev if k[0] != "::" and k[0] not in live]:
            del prev[k]
        for name, node in (st.get("vars") or {}).items():
            if not isinstance(node, dict):
                continue
            key = ("::", name) if node.get("scope") == "global" else (top, name)
            old = prev.get(key)
            core = _core(node)
            prev[key] = (core, node)
            if old is None:
                if si > 0:
                    node["changed"] = True
                    node["new"] = True
                continue
            if old[0] == core:
                continue
            node["changed"] = True
            _diff_detail(old[1], node)
        prev_ids = live


def _diff_detail(old: dict, node: dict) -> None:
    k = node.get("kind")
    if k != old.get("kind"):
        return
    if k in ("array", "vector", "set", "pair", "map"):
        a, b = old.get("items") or [], node.get("items") or []
        idx = [i for i in range(len(b)) if i >= len(a) or a[i] != b[i]]
        if idx:
            node["changed_idx"] = idx[:512]
    elif k == "matrix":
        a, b = old.get("rows") or [], node.get("rows") or []
        cells = []
        for r, row in enumerate(b):
            orow = a[r] if r < len(a) else []
            for c, v in enumerate(row):
                if c >= len(orow) or orow[c] != v:
                    cells.append([r, c])
                    if len(cells) >= 1024:
                        break
        if cells:
            node["changed_cells"] = cells
    elif k == "struct":
        a, b = old.get("fields") or {}, node.get("fields") or {}
        ch = [f for f in b if a.get(f) != b[f]]
        if ch:
            node["changed_fields"] = ch


# ============================================================================================ uruchamianie
class _Collector:
    def __init__(self, job, max_steps: int):
        self.job = job
        self.max_steps = max_steps
        self.steps: list[dict] = []
        self.info: dict = {}
        self.end: dict = {}
        self.error: str | None = None
        self.trace: str | None = None
        self.tail_out = ""
        self.size = 0
        self.size_capped = False

    def __call__(self, ev: dict) -> None:
        t = ev.get("t")
        if t == "step":
            if self.size_capped:
                return
            ev.pop("t", None)
            self.steps.append(ev)
            n = len(self.steps)
            if n % 25 == 0 or n < 5:
                self.job.progress(min(0.99, n / max(1, self.max_steps)), f"Krok {n} (linia {ev.get('line')})")
        elif t == "info":
            self.info = ev
            self.job.progress(0.02, "Program uruchomiony pod debuggerem…")
        elif t == "end":
            self.end = ev
        elif t == "out":
            self.tail_out += ev.get("out") or ""
        elif t == "error":
            self.error = ev.get("message") or "Błąd debuggera."
            self.trace = ev.get("trace")


def _pump_process(cmd: list, cwd: str, stdin_path: str | None, collector: _Collector, job, hard_timeout: float,
                  env: dict | None = None) -> tuple[int | None, str]:
    """Uruchamia proces drivera/tracera i czyta linie JSON przyrostowo. Zwraca (kod wyjścia, stderr)."""
    kw = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    if os.name == "posix":
        kw["start_new_session"] = True
    else:
        kw.update(_no_window())
    fin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    full_env = dict(os.environ)
    full_env.pop("PYTHONHOME", None)
    full_env.pop("PYTHONPATH", None)
    full_env["PYTHONIOENCODING"] = "utf-8"
    full_env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        full_env.update(env)
    try:
        proc = subprocess.Popen(cmd, stdin=fin, env=full_env, **kw)
    except OSError as e:
        if fin is not subprocess.DEVNULL:
            fin.close()
        collector.error = f"Nie udało się uruchomić debuggera: {e}"
        return None, ""
    err_chunks: list[bytes] = []

    def read_err():
        try:
            for chunk in iter(lambda: proc.stderr.read(4096), b""):
                if sum(len(c) for c in err_chunks) < 200_000:
                    err_chunks.append(chunk)
        except (OSError, ValueError):
            pass
    te = threading.Thread(target=read_err, daemon=True)
    te.start()
    deadline = time.time() + hard_timeout
    killed = [False]

    def watchdog():
        while proc.poll() is None:
            if job.cancelled or time.time() > deadline:
                _terminate(proc)
                killed[0] = True
                return
            time.sleep(0.1)
    tw = threading.Thread(target=watchdog, daemon=True)
    tw.start()
    try:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                collector(ev)
                if ev.get("t") == "info" and ev.get("pid"):
                    collector.info["pid"] = ev["pid"]
    except (OSError, ValueError):
        pass
    try:
        code = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _terminate(proc)
        code = None
    if fin is not subprocess.DEVNULL:
        fin.close()
    te.join(timeout=2)
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except (OSError, AttributeError):
            pass
    pid = collector.info.get("pid")
    if killed[0] and pid:
        _kill_pid(pid)
    if killed[0] and not collector.end:
        collector.end = {"reason": "cancelled" if job.cancelled else "timeout",
                         "message": "Przerwano śledzenie." if job.cancelled else
                         "Debugger przekroczył limit czasu i został zatrzymany."}
    return code, b"".join(err_chunks).decode("utf-8", "replace")


def _terminate(proc) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=1.5)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except OSError:
            pass


def _kill_pid(pid: int) -> None:
    try:
        if os.name == "posix":
            import signal
            os.kill(int(pid), signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, **_no_window())
    except (OSError, ValueError):
        pass


def _session_dir() -> str:
    # katalog systemowy tmp, NIE user_data: na macOS debugserver nie uruchamia binarek z ~/Documents (TCC)
    return tempfile.mkdtemp(prefix="oiforge_dbg_")


def _trace_cpp(job, body: dict, code: str, inp: str, max_steps: int, watch: list[str], max_items: int) -> dict:
    caps_li = lldb_interpreter()
    backend = "lldb" if caps_li else ("gdb" if config.GDB else None)
    if backend is None:
        return {"error": "Na tym komputerze nie ma debuggera C++ (lldb ani gdb). " + " ".join(capabilities()["notes"]),
                "suggest_python": True, "backend": None}
    job.progress(0.0, "Kompilacja w trybie debug (-g -O0)…")
    user_lines = code.count("\n") + 1
    src = code.rstrip("\n") + "\n" + UNBUF_SNIPPET
    cr = runner.compile_cpp(src, mode="debug", std=body.get("std") or "c++20")
    if not cr.ok:
        stderr = cr.stderr or ""
        return {"error": "Błąd kompilacji — popraw kod, zanim go prześledzisz.", "compile": cr.to_dict(),
                "compile_errors": _translate(stderr, code), "backend": backend}
    if job.cancelled:
        return {"error": "Przerwano.", "backend": backend}
    d = _session_dir()
    try:
        in_path = os.path.join(d, "input.txt")
        with open(in_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(inp)
        out_path = os.path.join(d, "stdout.txt")
        err_path = os.path.join(d, "stderr.txt")
        Path(out_path).touch()
        Path(err_path).touch()
        exe = os.path.join(d, "prog" + config.EXE_SUFFIX)
        shutil.copy2(cr.exe, exe)
        dsym = None
        if os.path.isdir(cr.exe + ".dSYM"):
            shutil.copytree(cr.exe + ".dSYM", exe + ".dSYM")
            dsym = exe + ".dSYM"
        cfg = {"exe": exe, "dsym": dsym, "source": cr.source_path, "user_lines": user_lines, "input_path": in_path,
               "output_path": out_path, "stderr_path": err_path, "cwd": d, "max_steps": max_steps,
               "time_budget": TIME_BUDGET, "watch": watch, "max_items": max_items, "globals": global_names(code)}
        col = _Collector(job, max_steps)
        job.progress(0.01, f"Uruchamianie debuggera ({backend})…")
        stderr = ""
        if backend == "lldb":
            interp, lpath = caps_li
            cfg["lldb_path"] = lpath
            cfg_path = os.path.join(d, "config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            _code, stderr = _pump_process([*interp, str(LLDB_DRIVER), cfg_path], d, None, col, job,
                                          hard_timeout=TIME_BUDGET + 40)
        else:
            from oiforge.tools import _dbg_gdb
            cfg["gdb"] = config.GDB
            cfg["decl_lines"] = decl_lines(code)
            try:
                _dbg_gdb.trace(cfg, col)
            except Exception as e:  # noqa: BLE001
                col.error = f"Błąd backendu gdb: {e.__class__.__name__}: {e}"
        res = _finish(col, code, "cpp", backend, stderr)
        res["compile"] = {"compiler": cr.compiler, "cached": cr.cached, "time": cr.time, "warnings": cr.warnings}
        return res
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _trace_python(job, code: str, inp: str, max_steps: int, watch: list[str], max_items: int) -> dict:
    ok, msg, line = runner.python_syntax(code)
    if not ok:
        return {"error": "Błąd składni Pythona: " + msg, "error_line": line, "backend": "python"}
    d = _session_dir()
    try:
        code_path = os.path.join(d, "main.py")
        with open(code_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(code)
        in_path = os.path.join(d, "input.txt")
        with open(in_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(inp)
        cfg = {"code_path": code_path, "max_steps": max_steps, "time_budget": TIME_BUDGET, "watch": watch,
               "max_items": max_items}
        cfg_path = os.path.join(d, "config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        col = _Collector(job, max_steps)
        job.progress(0.01, "Uruchamianie tracera Pythona…")
        _code, stderr = _pump_process([config.PYTHON, "-X", "utf8", str(PY_TRACER), cfg_path], d, in_path, col, job,
                                      hard_timeout=TIME_BUDGET + 15)
        return _finish(col, code, "python", "python", stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _translate(stderr: str, code: str) -> list:
    try:
        from oiforge.tools import errors as _errors   # moduł agenta B1 (opcjonalny)
        return _errors.translate(stderr, code)[:20]
    except Exception:  # noqa: BLE001
        return []


def _finish(col: _Collector, code: str, lang: str, backend: str, stderr: str) -> dict:
    steps = col.steps
    mark_changes(steps)
    stdout_parts = []
    total = 0
    size = 0
    capped = False
    for i, st in enumerate(steps):
        delta = st.pop("out", "") or ""
        st["stdout"] = delta
        stdout_parts.append(delta)
        total += len(delta)
        st["out"] = total
        st["i"] = i
        size += len(json.dumps(st, ensure_ascii=False, separators=(",", ":")))
        if size > MAX_RESULT_BYTES:
            del steps[i + 1:]
            capped = True
            break
    stdout = "".join(stdout_parts) + (col.tail_out or "")
    end = col.end or {}
    reason = end.get("reason")
    message = end.get("message")
    if capped:
        reason, message = "size", "Za dużo danych do pokazania — śledzenie obcięte (ogranicz „Śledź tylko zmienne”)."
    error = col.error
    if not error and not steps and not end:
        error = "Debugger nie zwrócił żadnych kroków." + (f" Szczegóły: {stderr.strip()[-800:]}" if stderr.strip() else "")
    if error and backend == "lldb":
        error = _explain_lldb_error(error, stderr)
    truncated = reason in ("max_steps", "timeout", "size", "cancelled")
    elapsed = end.get("elapsed")
    res = {
        "steps": steps,
        "source": code,
        "lang": lang,
        "backend": backend,
        "truncated": truncated,
        "reason": reason,
        "message": message,
        "error": error,
        "stdout": stdout,
        "stderr": (end.get("stderr") or "")[-20000:],
        "exit_code": end.get("exit_code"),
        "lines": (col.info or {}).get("lines") or [],
        "stats": {"steps": len(steps), "elapsed": elapsed,
                  "steps_per_s": round(len(steps) / elapsed, 1) if elapsed else None,
                  "setup_s": (col.info or {}).get("setup_s"), "launch_s": (col.info or {}).get("launch_s")},
    }
    if col.trace:
        res["trace"] = col.trace
    return res


def _explain_lldb_error(err: str, stderr: str) -> str:
    low = (err + " " + stderr).lower()
    if "import" in low and "lldb" in low:
        return err + " (Moduł lldb nie pasuje do wybranego Pythona — sprawdź `lldb -P` i Xcode Command Line Tools.)"
    return err


def _work(job, body: dict) -> dict:
    code = (body.get("code") or "").replace("\r\n", "\n")
    inp = (body.get("input") or "").replace("\r\n", "\n")
    lang = (body.get("lang") or "").lower() or runner.detect_lang(code)
    if lang in ("py", "python3"):
        lang = "python"
    max_steps = max(10, min(int(body.get("max_steps") or DEFAULT_MAX_STEPS), HARD_MAX_STEPS))
    max_items = max(16, min(int(body.get("max_items") or 256), 1024))
    watch = parse_watch(body.get("watch"))
    t0 = time.time()
    if lang == "python":
        res = _trace_python(job, code, inp, max_steps, watch, max_items)
    else:
        res = _trace_cpp(job, body, code, inp, max_steps, watch, max_items)
    res.setdefault("source", code)
    res.setdefault("lang", lang)
    res.setdefault("steps", [])
    res.setdefault("truncated", False)
    res["watch"] = watch
    res["max_steps"] = max_steps
    res["total_time"] = round(time.time() - t0, 3)
    return res


def run_trace(body: dict) -> dict:
    """Synchroniczne śledzenie (testy i inne moduły)."""
    _job, res = jobs.run_sync(_work, body, title="Debugger")
    return res


def handle_trace(body: dict) -> dict:
    code = body.get("code") or ""
    if not code.strip():
        raise ApiError("Wklej kod programu do prześledzenia.")
    if len(code) > 200_000:
        raise ApiError("Kod jest za długi do śledzenia (limit 200 kB).")
    if len(body.get("input") or "") > 2_000_000:
        raise ApiError("Wejście jest za duże do śledzenia (limit 2 MB) — debuguj na małym teście.")
    return {"job": jobs.submit(_work, body, title="Debugger")}


# ============================================================================================ przykłady
EXAMPLES = [
    {"id": "binsearch", "title": "Wyszukiwanie binarne (wskaźniki l, r, mid)", "lang": "cpp",
     "input": "8 23\n2 5 8 12 16 23 38 56\n",
     "code": """#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, x;
    cin >> n >> x;
    vector<int> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    int l = 0, r = n - 1, ans = -1;
    while (l <= r) {
        int mid = (l + r) / 2;
        if (a[mid] == x) {
            ans = mid;
            break;
        } else if (a[mid] < x) {
            l = mid + 1;
        } else {
            r = mid - 1;
        }
    }
    cout << ans << "\\n";
    return 0;
}
"""},
    {"id": "bubble", "title": "Sortowanie bąbelkowe (i, j)", "lang": "cpp", "input": "6\n5 1 4 2 8 0\n",
     "code": """#include <bits/stdc++.h>
using namespace std;

int main() {
    int n;
    cin >> n;
    int a[100];
    for (int i = 0; i < n; i++) cin >> a[i];
    for (int i = 0; i < n; i++) {
        for (int j = 0; j + 1 < n - i; j++) {
            if (a[j] > a[j + 1]) {
                swap(a[j], a[j + 1]);
            }
        }
    }
    for (int i = 0; i < n; i++) cout << a[i] << " ";
    cout << "\\n";
}
"""},
    {"id": "fib", "title": "Rekurencja — silnia i Fibonacci", "lang": "cpp", "input": "",
     "code": """#include <bits/stdc++.h>
using namespace std;

long long silnia(int n) {
    if (n <= 1) return 1;
    return n * silnia(n - 1);
}

int fib(int n) {
    if (n < 2) return n;
    int a = fib(n - 1);
    int b = fib(n - 2);
    return a + b;
}

int main() {
    cout << silnia(5) << "\\n";
    cout << fib(4) << "\\n";
}
"""},
    {"id": "prefix", "title": "Sumy prefiksowe i zapytania", "lang": "cpp", "input": "6 3\n3 1 4 1 5 9\n1 3\n2 5\n1 6\n",
     "code": """#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, q;
    cin >> n >> q;
    vector<long long> a(n + 1), pref(n + 1, 0);
    for (int i = 1; i <= n; i++) {
        cin >> a[i];
        pref[i] = pref[i - 1] + a[i];
    }
    while (q--) {
        int l, r;
        cin >> l >> r;
        cout << pref[r] - pref[l - 1] << "\\n";
    }
}
"""},
    {"id": "lcs", "title": "Programowanie dynamiczne 2D (NWP)", "lang": "cpp", "input": "ABCB\nBDCAB\n",
     "code": """#include <bits/stdc++.h>
using namespace std;

int main() {
    string s, t;
    cin >> s >> t;
    int n = s.size(), m = t.size();
    vector<vector<int>> dp(n + 1, vector<int>(m + 1, 0));
    for (int i = 1; i <= n; i++) {
        for (int j = 1; j <= m; j++) {
            if (s[i - 1] == t[j - 1]) dp[i][j] = dp[i - 1][j - 1] + 1;
            else dp[i][j] = max(dp[i - 1][j], dp[i][j - 1]);
        }
    }
    cout << dp[n][m] << "\\n";
}
"""},
    {"id": "bfs", "title": "BFS na grafie (kolejka, odległości)", "lang": "cpp", "input": "5 5\n1 2\n1 3\n2 4\n3 4\n4 5\n",
     "code": """#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, m;
    cin >> n >> m;
    vector<vector<int>> adj(n + 1);
    for (int i = 0; i < m; i++) {
        int a, b;
        cin >> a >> b;
        adj[a].push_back(b);
        adj[b].push_back(a);
    }
    vector<int> dist(n + 1, -1);
    queue<int> q;
    dist[1] = 0;
    q.push(1);
    while (!q.empty()) {
        int v = q.front();
        q.pop();
        for (int u : adj[v]) {
            if (dist[u] == -1) {
                dist[u] = dist[v] + 1;
                q.push(u);
            }
        }
    }
    for (int v = 1; v <= n; v++) cout << dist[v] << " ";
    cout << "\\n";
}
"""},
    {"id": "py_two", "title": "Python — dwa wskaźniki", "lang": "python", "input": "7 10\n1 2 3 4 6 8 9\n",
     "code": """n, x = map(int, input().split())
a = list(map(int, input().split()))
l, r = 0, n - 1
pary = []
while l < r:
    s = a[l] + a[r]
    if s == x:
        pary.append((a[l], a[r]))
        l += 1
        r -= 1
    elif s < x:
        l += 1
    else:
        r -= 1
print(len(pary))
"""},
]


def handle_examples(_body: dict) -> dict:
    return {"examples": EXAMPLES}


ROUTES = {
    "POST /api/debugger/trace": handle_trace,
    "GET /api/debugger/capabilities": handle_capabilities,
    "POST /api/debugger/capabilities": handle_capabilities,
    "GET /api/debugger/examples": handle_examples,
}
