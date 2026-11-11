"""Kompilacja i uruchamianie programów (C++ / Python) z limitami czasu, pomiarem czasu i pamięci.

Wieloplatformowo: macOS, Linux, Windows. Binarki są cache'owane po SHA1(kompilator + flagi + źródło).
"""
from __future__ import annotations

import hashlib
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import config

BIN_DIR = config.CACHE_DIR / "bin"
SRC_DIR = config.CACHE_DIR / "src"
PY_DIR = config.CACHE_DIR / "py"
for _d in (BIN_DIR, SRC_DIR, PY_DIR):
    _d.mkdir(parents=True, exist_ok=True)

MAX_OUTPUT = 4_000_000          # bajtów na strumień
COMPILE_TIMEOUT = 90.0
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

MODES = ("release", "fast", "sanitize", "debug", "syntax")
_LAYOUT = "src-dir-main.cpp"      # zmiana układu plików źródłowych = nowe klucze cache


# ----------------------------------------------------------------------------------------------- wyniki
@dataclass
class CompileResult:
    ok: bool
    exe: str | None = None
    stderr: str = ""
    cached: bool = False
    compiler: str | None = None
    cmd: list = field(default_factory=list)
    time: float = 0.0
    mode: str = "release"
    source_path: str | None = None
    warnings: int = 0
    errors: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = 0
    time: float = 0.0           # czas CPU (user+sys) jeśli dostępny, inaczej czas ścienny
    wall: float = 0.0
    mem_kb: int | None = None
    timed_out: bool = False
    signal: str | None = None
    verdict: str = "OK"         # OK | TLE | RE | MLE
    truncated: bool = False
    error: str | None = None    # błąd uruchomienia (np. brak pliku)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Program:
    ok: bool
    lang: str
    cmd: list = field(default_factory=list)
    compile: CompileResult | None = None
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["compile"] = self.compile.to_dict() if self.compile else None
        return d


# ----------------------------------------------------------------------------------------------- język
_CPP_HINTS = re.compile(r"#\s*include|\bint\s+main\s*\(|\bstd::|\bcout\b|\bcin\b|\bprintf\s*\(|\bscanf\s*\(|;\s*$", re.M)
_PY_HINTS = re.compile(r"^\s*(def |import |from \w+ import|print\(|for \w+ in |while .*:\s*$|if .*:\s*$|elif |class \w+.*:)", re.M)


def detect_lang(source: str) -> str:
    """Zgaduje język: "cpp" albo "python"."""
    cpp = len(_CPP_HINTS.findall(source or ""))
    py = len(_PY_HINTS.findall(source or ""))
    if "#include" in (source or ""):
        return "cpp"
    if py > cpp:
        return "python"
    if cpp == 0 and re.search(r"\binput\(\)|\bprint\(", source or ""):
        return "python"
    return "cpp"


# ----------------------------------------------------------------------------------------------- flagi
def _compiler_for(mode: str) -> str | None:
    if mode in ("sanitize", "debug"):
        if config.CLANG and not config.IS_WINDOWS:
            return config.CLANG
        return config.CXX
    return config.CXX


def _is_clang(path: str | None) -> bool:
    return bool(path) and "clang" in Path(path).name.lower()


def _stack_flags(stack_mb: int | None = 512) -> list[str]:
    """Rozmiar stosu głównego wątku (domyślnie 512 MB; None/0 = domyślny systemu, np. by udawać sprawdzarkę)."""
    if not stack_mb:
        return []
    size = int(stack_mb) * 1024 * 1024
    if config.IS_MAC:
        return [f"-Wl,-stack_size,{size:#x}"]
    if config.IS_WINDOWS:
        return [f"-Wl,--stack,{size}"]
    return []                                           # Linux: limit stosu ustawiany przy uruchomieniu


def build_flags(compiler: str, mode: str, std: str = "c++20", extra: list[str] | None = None,
                stack_mb: int | None = 512) -> list[str]:
    clang = _is_clang(compiler)
    flags = [f"-std={std}", "-Wall", "-Wextra", "-Wno-unused-result", "-Wno-sign-compare"]
    # Własny bits/stdc++.h (include/bits) dla OBU kompilatorów: GCC 16 nie dołącza już <cassert>,
    # a sprawdzarki OI (starszy GCC) tak — kod z assert() ma się kompilować tak samo jak na zawodach.
    flags += ["-I", str(config.INCLUDE_DIR)]
    if clang:
        flags += ["-Wno-unknown-warning-option"]
    if mode == "release":
        flags += ["-O2", "-pipe"]
    elif mode == "fast":
        flags += ["-O3", "-pipe", "-funroll-loops"]
    elif mode == "sanitize":
        flags += ["-g", "-O1", "-fno-omit-frame-pointer"]
        if config.capabilities()["sanitizers"]:
            flags += ["-fsanitize=address,undefined"]
        if clang:
            flags += ["-D_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_DEBUG"]
        else:
            flags += ["-D_GLIBCXX_DEBUG", "-D_GLIBCXX_DEBUG_PEDANTIC"]
    elif mode == "debug":
        flags += ["-g", "-O0", "-fno-inline", "-fno-omit-frame-pointer"]
    elif mode == "syntax":
        flags += ["-fsyntax-only"]
    if mode != "syntax":
        flags += _stack_flags(stack_mb)
    if extra:
        flags += list(extra)
    return flags


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.Lock()
        return lk


def _count_diags(stderr: str) -> tuple[int, int]:
    errs = len(re.findall(r"\berror\b:", stderr))
    warns = len(re.findall(r"\bwarning\b:", stderr))
    return errs, warns


CACHE_LIMIT_MB = int(os.environ.get("OIFORGE_CACHE_LIMIT_MB", "1500"))   # binarki + .dSYM + źródła
CACHE_LIMIT_COUNT = 1500   # szablony auto-solvera (~300 binarek po ~40 KB) nie mogą wypychać się nawzajem
PCH_KEEP = 6
_cleanup_guard = threading.Lock()
_last_cleanup = [0.0]


def _path_size(p: Path) -> int:
    try:
        if p.is_dir():
            total = 0
            for root, _dirs, files in os.walk(p):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total
        return p.stat().st_size
    except OSError:
        return 0


def _remove(p: Path) -> None:
    import shutil
    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink()
    except OSError:
        pass


def _cleanup_cache(force: bool = False) -> None:
    """Sprząta cache binarek (w tle, najwyżej raz na minutę): grupuje <klucz>, <klucz>.log, <klucz>.dSYM i src/<klucz>,
    usuwa najstarsze grupy ponad limit liczby/rozmiaru oraz osierocone źródła."""
    now = time.time()
    if not force and now - _last_cleanup[0] < 60:
        return
    if not _cleanup_guard.acquire(blocking=False):
        return
    _last_cleanup[0] = now

    def work():
        try:
            groups: dict[str, dict] = {}
            for e in BIN_DIR.iterdir():
                key = e.name.split(".")[0]
                g = groups.setdefault(key, {"paths": [], "mtime": 0.0, "size": 0})
                g["paths"].append(e)
                g["size"] += _path_size(e)
                if e.suffix in ("", ".exe"):
                    try:
                        g["mtime"] = max(g["mtime"], e.stat().st_mtime)
                    except OSError:
                        pass
            for g in groups.values():
                if not g["mtime"]:
                    g["mtime"] = min((x.stat().st_mtime for x in g["paths"] if x.exists()), default=0.0)
            order = sorted(groups.items(), key=lambda kv: kv[1]["mtime"])
            total = sum(g["size"] for _, g in order)
            limit = CACHE_LIMIT_MB * 1024 * 1024
            count = len(order)
            for key, g in order:
                if count <= CACHE_LIMIT_COUNT and total <= limit:
                    break
                for x in g["paths"]:
                    _remove(x)
                _remove(SRC_DIR / key)
                _remove(SRC_DIR / f"{key}.cpp")
                total -= g["size"]
                count -= 1
                groups.pop(key, None)
            # osierocone źródła (np. po kompilacjach składniowych) starsze niż godzina
            for e in SRC_DIR.iterdir():
                key = e.name.split(".")[0]
                try:
                    old = now - e.stat().st_mtime > 3600
                except OSError:
                    continue
                if key not in groups and old:
                    _remove(e)
        except OSError:
            pass
        finally:
            _cleanup_guard.release()

    threading.Thread(target=work, daemon=True).start()


def cache_usage() -> dict:
    """Rozmiar cache (MB) — do widoku ustawień."""
    def mb(d: Path) -> float:
        return round(_path_size(d) / 1024 / 1024, 1) if d.exists() else 0.0
    return {"dir": str(config.CACHE_DIR), "bin_mb": mb(BIN_DIR), "src_mb": mb(SRC_DIR), "pch_mb": mb(PCH_DIR),
            "limit_mb": CACHE_LIMIT_MB}


# ----------------------------------------------------------------------------------------------- PCH
# Prekompilowany <bits/stdc++.h>: kompilacja 6 s → ~1 s. Budowany w tle przy pierwszym użyciu danego zestawu flag.
PCH_DIR = config.CACHE_DIR / "pch"
_pch_state: dict[str, str] = {}          # klucz -> "building" | "ready" | "failed"
_pch_guard = threading.Lock()
_BITS_RE = re.compile(r"#\s*include\s*<bits/stdc\+\+\.h>")


def _codegen_flags(flags: list[str]) -> list[str]:
    out, skip = [], False
    for f in flags:
        if skip:
            skip = False
            continue
        if f == "-I":
            skip = True
            continue
        if f.startswith(("-W", "-I", "-o")) or f in ("-pipe", "-fsyntax-only"):
            continue
        out.append(f)
    return out


def _gcc_bits_header(cxx: str, std: str) -> str | None:
    try:
        p = subprocess.run([cxx, f"-std={std}", "-x", "c++", "-M", "-"], input="#include <bits/stdc++.h>\n",
                           capture_output=True, text=True, timeout=30, **_no_window())
        for tok in p.stdout.replace("\\\n", " ").split():
            if tok.replace("\\", "/").endswith("bits/stdc++.h"):
                return tok
    except Exception:
        pass
    return None


def _shim_hash() -> str:
    try:
        return hashlib.sha1((config.INCLUDE_DIR / "bits" / "stdc++.h").read_bytes()).hexdigest()[:10]
    except OSError:
        return "noshim"


_PCH_VERSION = "v3-" + _shim_hash()      # zmiana nagłówka-shimu = nowy PCH


def _pch_key(cxx: str, flags: list[str]) -> str:
    return hashlib.sha1("\x00".join([_PCH_VERSION, cxx, *_codegen_flags(flags)]).encode()).hexdigest()[:16]


def _build_pch(cxx: str, flags: list[str], std: str, key: str) -> None:
    d = PCH_DIR / key
    try:
        d.mkdir(parents=True, exist_ok=True)
        cg = _codegen_flags(flags)
        if _is_clang(cxx):
            out = d / "stdc++.pch"
            cmd = [cxx, *cg, "-I", str(config.INCLUDE_DIR), "-x", "c++-header",
                   str(config.INCLUDE_DIR / "bits" / "stdc++.h"), "-o", str(out)]
        else:
            hdr = str(config.INCLUDE_DIR / "bits" / "stdc++.h")
            (d / "bits").mkdir(exist_ok=True)
            out = d / "bits" / "stdc++.h.gch"
            cmd = [cxx, *cg, "-x", "c++-header", hdr, "-o", str(out)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, **_no_window())
        ok = p.returncode == 0 and out.exists()
        if ok:
            (d / "ok").write_text(_PCH_VERSION)
        with _pch_guard:
            _pch_state[key] = "ready" if ok else "failed"
    except Exception:
        with _pch_guard:
            _pch_state[key] = "failed"


def _pch_args(cxx: str, flags: list[str], std: str, source: str, mode: str) -> list[str]:
    """Argumenty włączające PCH, jeśli gotowy; w przeciwnym razie (pierwszy raz) startuje budowę w tle."""
    if mode == "syntax" or not _BITS_RE.search(source) or os.environ.get("OIFORGE_NO_PCH"):
        return []
    key = _pch_key(cxx, flags)
    d = PCH_DIR / key
    with _pch_guard:
        st = _pch_state.get(key)
        if st is None and (d / "ok").exists():
            st = _pch_state[key] = "ready"
        if st is None:
            _pch_state[key] = "building"
            threading.Thread(target=_build_pch, args=(cxx, flags, std, key), daemon=True).start()
            return []
    if st != "ready":
        return []
    try:
        os.utime(d / "ok", None)          # „ostatnio używany” — do sprzątania najstarszych zestawów
    except OSError:
        pass
    if _is_clang(cxx):
        return ["-include-pch", str(d / "stdc++.pch")]
    return ["-I", str(d)]


def cleanup_pch(keep: int = PCH_KEEP) -> int:
    """Usuwa zestawy PCH z inną wersją nagłówka-shimu i nadmiarowe (najdawniej używane). Zwraca liczbę usuniętych."""
    removed = 0
    try:
        dirs = [d for d in PCH_DIR.iterdir() if d.is_dir()]
    except OSError:
        return 0
    live = []
    for d in dirs:
        ok = d / "ok"
        try:
            ver = ok.read_text().strip() if ok.exists() else ""
        except OSError:
            ver = ""
        building = _pch_state.get(d.name) == "building"
        if building:
            continue
        if ver != _PCH_VERSION:
            _remove(d)
            _pch_state.pop(d.name, None)
            removed += 1
        else:
            live.append((ok.stat().st_mtime, d))
    live.sort(reverse=True)
    for _, d in live[keep:]:
        _remove(d)
        _pch_state.pop(d.name, None)
        removed += 1
    return removed


def warm_up(std: str = "c++20") -> None:
    """Wywoływane przy starcie serwera: sprząta cache i buduje PCH dla trybu release w tle."""
    cleanup_pch()
    _cleanup_cache(force=True)
    cxx = _compiler_for("release")
    if cxx:
        _pch_args(cxx, build_flags(cxx, "release", std), std, "#include <bits/stdc++.h>", "release")


# ----------------------------------------------------------------------------------------------- kompilacja
def compile_cpp(source: str, mode: str = "release", std: str = "c++20", extra_flags: list[str] | None = None,
                compiler: str | None = None, stack_mb: int | None = 512, low_priority: bool = False) -> CompileResult:
    """Kompiluje źródło C++ (z cache). Zwraca CompileResult (ok=False przy błędzie).

    low_priority=True — kompilacja w tle (np. rozgrzewka szablonów) z obniżonym priorytetem procesu,
    żeby nie spowalniać tego, co uczeń robi w tej chwili."""
    if mode not in MODES:
        mode = "release"
    cxx = compiler or _compiler_for(mode)
    if not cxx:
        return CompileResult(ok=False, mode=mode, stderr=(
            "Nie znaleziono kompilatora C++. Zainstaluj g++ (Linux: `sudo apt install g++`, macOS: `brew install gcc` "
            "albo Xcode Command Line Tools, Windows: MSYS2/MinGW-w64 lub WinLibs) i uruchom OI Forge ponownie."))
    flags = build_flags(cxx, mode, std, extra_flags, stack_mb=stack_mb)
    source = source.replace("\r\n", "\n")
    key = hashlib.sha1(("\x00".join([_LAYOUT, cxx, *flags, source])).encode("utf-8", "replace")).hexdigest()[:24]
    src_dir = SRC_DIR / key
    src_path = src_dir / "main.cpp"          # __FILE__ / komunikaty pokazują „main.cpp”
    exe_path = BIN_DIR / f"{key}{config.EXE_SUFFIX}"
    log_path = BIN_DIR / f"{key}.log"
    with _lock_for(key):
        if mode != "syntax" and exe_path.exists():
            try:
                os.utime(exe_path, None)
            except OSError:
                pass
            stderr = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
            e, w = _count_diags(stderr)
            return CompileResult(ok=True, exe=str(exe_path), stderr=stderr, cached=True, compiler=cxx,
                                 cmd=[cxx, *flags], mode=mode, source_path=str(src_path), warnings=w, errors=e)
        src_dir.mkdir(parents=True, exist_ok=True)
        src_path.write_text(source, encoding="utf-8")
        # PCH tylko dla standardowych trybów (bez dodatkowych flag) — warianty sprawdzarki nie mnożą zestawów PCH
        pch = _pch_args(cxx, flags, std, source, mode) if not extra_flags and stack_mb == 512 else []
        prefix_map = [f"-fmacro-prefix-map={src_dir}{os.sep}="]
        cmd = [cxx, *pch, *flags, *prefix_map, str(src_path)]
        if mode != "syntax":
            cmd += ["-o", str(exe_path)]
        t0 = time.perf_counter()
        try:
            p = subprocess.run(_niced(cmd, low_priority), capture_output=True, text=True, timeout=COMPILE_TIMEOUT,
                               encoding="utf-8", errors="replace", **_no_window(low_priority))
            if p.returncode != 0 and pch and re.search(r"pch|precompiled", p.stderr, re.I):
                with _pch_guard:
                    _pch_state[_pch_key(cxx, flags)] = "failed"
                cmd = [cxx, *flags, *prefix_map, str(src_path)] + (["-o", str(exe_path)] if mode != "syntax" else [])
                p = subprocess.run(_niced(cmd, low_priority), capture_output=True, text=True, timeout=COMPILE_TIMEOUT,
                                   encoding="utf-8", errors="replace", **_no_window(low_priority))
        except subprocess.TimeoutExpired:
            return CompileResult(ok=False, stderr="Kompilacja trwała zbyt długo (przekroczono limit).", compiler=cxx,
                                 cmd=cmd, mode=mode, source_path=str(src_path))
        except OSError as ex:
            return CompileResult(ok=False, stderr=f"Nie udało się uruchomić kompilatora: {ex}", compiler=cxx,
                                 cmd=cmd, mode=mode, source_path=str(src_path))
        dt = time.perf_counter() - t0
        stderr = _clean_paths(p.stderr, src_path)
        ok = p.returncode == 0 and (mode == "syntax" or exe_path.exists())
        if ok and mode != "syntax":
            log_path.write_text(stderr, encoding="utf-8")
            _cleanup_cache()
        e, w = _count_diags(stderr)
        res = CompileResult(ok=ok, exe=str(exe_path) if ok and mode != "syntax" else None, stderr=stderr, cached=False,
                            compiler=cxx, cmd=cmd, time=round(dt, 3), mode=mode, source_path=str(src_path),
                            warnings=w, errors=e)
    # sanitize/debug na clangu nie przechodzi (np. pb_ds tylko w libstdc++) → spróbuj GCC
    if not res.ok and mode in ("sanitize", "debug") and compiler is None and _is_clang(cxx) and config.GCC:
        alt = compile_cpp(source, mode, std, extra_flags, compiler=config.GCC, stack_mb=stack_mb)
        if alt.ok:
            return alt
    return res


def _clean_paths(stderr: str, src_path: Path) -> str:
    """Zamienia długą ścieżkę pliku tymczasowego na `main.cpp`."""
    s = stderr.replace(str(src_path), "main.cpp")
    return s.replace(src_path.name, "main.cpp")


def syntax_check(source: str, std: str = "c++20") -> CompileResult:
    return compile_cpp(source, mode="syntax", std=std)


def python_syntax(source: str) -> tuple[bool, str, int | None]:
    """(ok, komunikat, linia) — szybkie sprawdzenie składni Pythona bez uruchamiania."""
    try:
        compile(source, "main.py", "exec")
        return True, "", None
    except SyntaxError as e:
        return False, f"main.py:{e.lineno}:{e.offset or 0}: SyntaxError: {e.msg}", e.lineno


def prepare(source: str, lang: str | None = None, mode: str = "release", std: str = "c++20") -> Program:
    """Przygotowuje program do uruchomienia: kompiluje C++ albo zapisuje skrypt Pythona."""
    lang = (lang or detect_lang(source)).lower()
    if lang in ("py", "python", "python3"):
        ok, msg, _ = python_syntax(source)
        key = hashlib.sha1(source.encode("utf-8", "replace")).hexdigest()[:24]
        path = PY_DIR / f"{key}.py"
        if not path.exists():
            path.write_text(source.replace("\r\n", "\n"), encoding="utf-8")
        cr = CompileResult(ok=ok, stderr=msg, compiler="python", mode=mode, source_path=str(path), cached=True,
                           errors=0 if ok else 1)
        if not ok:
            return Program(ok=False, lang="python", compile=cr, error="Błąd składni Pythona")
        return Program(ok=True, lang="python", cmd=[config.PYTHON, "-X", "utf8", str(path)], compile=cr)
    cr = compile_cpp(source, mode=mode, std=std)
    if not cr.ok:
        return Program(ok=False, lang="cpp", compile=cr, error="Błąd kompilacji")
    return Program(ok=True, lang="cpp", cmd=[cr.exe], compile=cr)


# ----------------------------------------------------------------------------------------------- uruchamianie
def _no_window(low_priority: bool = False) -> dict:
    if config.IS_WINDOWS:
        # CREATE_NO_WINDOW (+ BELOW_NORMAL_PRIORITY_CLASS dla prac w tle)
        return {"creationflags": 0x08000000 | (0x00004000 if low_priority else 0)}
    return {}


_NICE = shutil.which("nice") if not config.IS_WINDOWS else None


def _niced(cmd: list[str], low_priority: bool) -> list[str]:
    """Na Linuksie/macOS prace w tle idą przez `nice -n 10` (na Windows priorytet ustawia creationflags)."""
    return [_NICE, "-n", "10", *cmd] if low_priority and _NICE else cmd


def _raise_child_limits() -> None:
    """Limity dziedziczone przez procesy potomne (bez preexec_fn, który nie jest bezpieczny z wątkami).
    Linux: stos = maksimum twarde (głęboka rekurencja), wszędzie: brak plików core."""
    if os.name != "posix":
        return
    try:
        import resource
        if config.IS_LINUX:
            soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
            if soft != hard:
                resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


_raise_child_limits()


_WIN_CODES = {
    0xC0000005: "ACCESS_VIOLATION (odwołanie do złej pamięci)",
    0xC00000FD: "STACK_OVERFLOW (przepełnienie stosu)",
    0xC0000094: "INTEGER_DIVIDE_BY_ZERO (dzielenie przez zero)",
    0xC000008E: "FLOAT_DIVIDE_BY_ZERO",
    0xC0000409: "STACK_BUFFER_OVERRUN / abort",
    0xC0000374: "HEAP_CORRUPTION (uszkodzona sterta)",
    0xC0000017: "NO_MEMORY (brak pamięci)",
}


def signal_name(code: int | None) -> str | None:
    if code is None:
        return None
    if config.IS_WINDOWS:
        u = code & 0xFFFFFFFF
        return _WIN_CODES.get(u)
    if code < 0:
        try:
            return signal.Signals(-code).name
        except (ValueError, AttributeError):
            return f"SIG{-code}"
    if code > 128 and code - 128 < 65:
        try:
            return signal.Signals(code - 128).name
        except (ValueError, AttributeError):
            return None
    return None


def _reader(stream, sink: list, limit: int, flag: list):
    total = 0
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            if total < limit:
                take = chunk[: limit - total]
                sink.append(take)
                total += len(take)
                if len(take) < len(chunk):
                    flag[0] = True
            else:
                flag[0] = True
    except (OSError, ValueError):
        pass


def _writer(stream, data: bytes):
    try:
        if data:
            stream.write(data)
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _win_stats(proc) -> tuple[int | None, float | None]:
    """(peak_mem_kb, cpu_seconds) dla procesu na Windows (best effort, przez ctypes)."""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        h = int(proc._handle)
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        mem = None
        if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            mem = int(pmc.PeakPagefileUsage or pmc.PeakWorkingSetSize) // 1024
        ft = [wintypes.FILETIME() for _ in range(4)]
        cpu = None
        if ctypes.windll.kernel32.GetProcessTimes(h, *[ctypes.byref(x) for x in ft]):
            def tosec(f):
                return ((f.dwHighDateTime << 32) | f.dwLowDateTime) / 1e7
            cpu = tosec(ft[2]) + tosec(ft[3])
        return mem, cpu
    except Exception:
        return None, None


_CACHE_SRC_RE = re.compile(r"[^\s:'\"()]*[\\/]src[\\/][0-9a-f]{24}[\\/]main\.cpp")


def run(cmd: list | str, input_text: str | bytes = "", timeout: float = 2.0, args: list | None = None,
        cwd: str | None = None, env: dict | None = None, mem_limit_mb: int | None = None,
        max_output: int = MAX_OUTPUT) -> RunResult:
    """Uruchamia program z wejściem, limitem czasu (s) i pomiarem czasu/pamięci."""
    if isinstance(cmd, (str, Path)):
        cmd = [str(cmd)]
    full = [str(c) for c in cmd] + [str(a) for a in (args or [])]
    data = input_text.encode("utf-8", "replace") if isinstance(input_text, str) else (input_text or b"")
    run_env = dict(os.environ)
    run_env.setdefault("PYTHONIOENCODING", "utf-8")
    run_env["PYTHONDONTWRITEBYTECODE"] = "1"
    run_env.setdefault("ASAN_OPTIONS", "detect_leaks=0:abort_on_error=0:symbolize=1:print_summary=1")
    run_env.setdefault("UBSAN_OPTIONS", "print_stacktrace=1")
    if env:
        run_env.update(env)
    kw = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=run_env)
    if os.name == "posix":
        kw["start_new_session"] = True
    else:
        kw.update(_no_window())
    t0 = time.perf_counter()
    try:
        proc = subprocess.Popen(full, **kw)
    except OSError as ex:
        return RunResult(exit_code=None, verdict="RE", error=f"Nie można uruchomić programu: {ex}", stderr=str(ex))
    out_chunks: list = []
    err_chunks: list = []
    trunc = [False]
    threads = [threading.Thread(target=_reader, args=(proc.stdout, out_chunks, max_output, trunc), daemon=True),
               threading.Thread(target=_reader, args=(proc.stderr, err_chunks, max_output, trunc), daemon=True),
               threading.Thread(target=_writer, args=(proc.stdin, data), daemon=True)]
    for t in threads:
        t.start()
    deadline = t0 + max(0.05, float(timeout))
    timed_out = False
    mem_kb = None
    cpu = None
    code = None
    if os.name == "posix" and hasattr(os, "wait4"):
        # Koniec procesu = EOF na jego stdout/stderr → czekamy na wątki czytające (bez opóźnień odpytywania),
        # potem zbieramy status i zużycie zasobów przez wait4.
        for t in threads[:2]:
            t.join(max(0.0, deadline - time.perf_counter()))
        delay = 0.0001
        while True:
            try:
                pid, status, ru = os.wait4(proc.pid, os.WNOHANG)
            except ChildProcessError:
                pid, status, ru = proc.pid, 0, None
            if pid:
                code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status
                proc.returncode = code
                if ru is not None:
                    cpu = ru.ru_utime + ru.ru_stime
                    mem_kb = int(ru.ru_maxrss // 1024) if config.IS_MAC else int(ru.ru_maxrss)
                break
            if time.perf_counter() > deadline:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    try:
                        proc.kill()
                    except OSError:
                        pass
                try:
                    pid, status, ru = os.wait4(proc.pid, 0)
                    code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status
                    proc.returncode = code
                    if ru is not None:
                        cpu = ru.ru_utime + ru.ru_stime
                        mem_kb = int(ru.ru_maxrss // 1024) if config.IS_MAC else int(ru.ru_maxrss)
                except ChildProcessError:
                    pass
                break
            time.sleep(delay)
            delay = min(delay * 2, 0.004)
    else:
        try:
            code = proc.wait(timeout=max(0.05, float(timeout)))
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, **_no_window())
            except OSError:
                proc.kill()
            try:
                code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = None
        mem_kb, cpu = _win_stats(proc)
    wall = time.perf_counter() - t0
    for t in threads[:2]:
        t.join(timeout=2)
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except (OSError, ValueError):
            pass
    out = b"".join(out_chunks).decode("utf-8", "replace")
    err = b"".join(err_chunks).decode("utf-8", "replace")
    if err and "main.cpp" in err:
        err = _CACHE_SRC_RE.sub("main.cpp", err)
    sig = signal_name(code) if code not in (0, None) else None
    if timed_out:
        verdict = "TLE"
    elif code not in (0, None):
        verdict = "RE"
    elif mem_limit_mb and mem_kb and mem_kb > mem_limit_mb * 1024:
        verdict = "MLE"
    else:
        verdict = "OK"
    if mem_limit_mb and mem_kb and mem_kb > mem_limit_mb * 1024 and verdict == "RE":
        verdict = "MLE"
    t_used = cpu if (cpu is not None and not timed_out) else wall
    return RunResult(stdout=out, stderr=err, exit_code=code, time=round(t_used, 4), wall=round(wall, 4),
                     mem_kb=mem_kb, timed_out=timed_out, signal=sig, verdict=verdict, truncated=trunc[0])


def run_program(prog: Program, input_text: str = "", timeout: float = 2.0, **kw) -> RunResult:
    if not prog.ok:
        return RunResult(verdict="CE", exit_code=None, error=prog.error,
                         stderr=prog.compile.stderr if prog.compile else prog.error)
    return run(prog.cmd, input_text, timeout=timeout, **kw)


def run_source(source: str, input_text: str = "", lang: str | None = None, mode: str = "release",
               timeout: float = 2.0, std: str = "c++20", mem_limit_mb: int | None = None, args: list | None = None) -> dict:
    """Kompiluje (jeśli trzeba) i uruchamia. Zwraca {ok, lang, compile, run}."""
    prog = prepare(source, lang=lang, mode=mode, std=std)
    res = {"ok": prog.ok, "lang": prog.lang, "compile": prog.compile.to_dict() if prog.compile else None, "run": None}
    if not prog.ok:
        return res
    rr = run(prog.cmd, input_text, timeout=timeout, mem_limit_mb=mem_limit_mb, args=args)
    res["run"] = rr.to_dict()
    return res


def normalize_output(s: str) -> str:
    """Normalizacja do porównań: CRLF→LF, bez spacji na końcach linii i pustych linii na końcu."""
    lines = s.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln.rstrip() for ln in lines]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def outputs_equal(a: str, b: str, float_eps: float | None = None) -> bool:
    """Porównanie tokenowe (białe znaki nieistotne); opcjonalnie tolerancja dla liczb rzeczywistych."""
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb):
        return False
    if float_eps is None:
        return ta == tb
    for x, y in zip(ta, tb):
        if x == y:
            continue
        try:
            fx, fy = float(x), float(y)
        except ValueError:
            return False
        if abs(fx - fy) > float_eps * max(1.0, abs(fy)):
            return False
    return True
