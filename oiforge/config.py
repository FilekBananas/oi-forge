"""Ścieżki, wykrywanie platformy i narzędzi (kompilatory, debuggery) — macOS / Linux / Windows."""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
INCLUDE_DIR = ROOT / "include"
USER_DIR = Path(os.environ.get("OIFORGE_USER_DIR", str(ROOT / "user_data")))
IS_WINDOWS_ = os.name == "nt"


def _cache_dir() -> Path:
    """Cache (binarki, PCH, modele) poza katalogiem projektu: na macOS ~/Documents jest chroniony (TCC)
    i debugger nie może tam uruchamiać binarek; poza tym cache bywa duży (setki MB)."""
    env = os.environ.get("OIFORGE_CACHE_DIR")
    if env:
        cands = [Path(env)]
    elif sys.platform == "darwin":
        cands = [Path.home() / "Library" / "Caches" / "OIForge"]
    elif IS_WINDOWS_:
        cands = [Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "OIForge" / "Cache"]
    else:
        cands = [Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "oiforge"]
    cands.append(USER_DIR / "cache")
    for c in cands:
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / ".write_test"
            probe.write_text("ok")
            probe.unlink()
            return c
        except OSError:
            continue
    return USER_DIR / "cache"


CACHE_DIR = _cache_dir()
TMP_DIR = USER_DIR / "tmp"
STORE_DIR = USER_DIR / "store"
PROGRAMS_DIR = USER_DIR / "programs"

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
PLATFORM = "windows" if IS_WINDOWS else ("macos" if IS_MAC else ("linux" if IS_LINUX else sys.platform))
EXE_SUFFIX = ".exe" if IS_WINDOWS else ""
PYTHON = sys.executable or "python3"
DEFAULT_PORT = 8777
APP_NAME = "OI Forge"
VERSION = "1.0.0"

for _d in (USER_DIR, CACHE_DIR, TMP_DIR, STORE_DIR, PROGRAMS_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

_WINDOWS_TOOL_DIRS = [
    r"C:\msys64\ucrt64\bin", r"C:\msys64\mingw64\bin", r"C:\msys64\clang64\bin",
    r"C:\MinGW\bin", r"C:\mingw64\bin", r"C:\winlibs\mingw64\bin",
    r"C:\Program Files\mingw-w64\x86_64-8.1.0-posix-seh-rt_v6-rev0\mingw64\bin",
    r"C:\ProgramData\chocolatey\bin", r"C:\Program Files\LLVM\bin",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\mingw64\bin"),
]
_UNIX_TOOL_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/home/linuxbrew/.linuxbrew/bin"]


def which(*names: str) -> str | None:
    """Szuka pierwszego dostępnego programu z listy (PATH + typowe katalogi)."""
    extra = _WINDOWS_TOOL_DIRS if IS_WINDOWS else _UNIX_TOOL_DIRS
    for n in names:
        if not n:
            continue
        p = shutil.which(n)
        if p:
            return p
        for d in extra:
            cand = Path(d) / (n + (".exe" if IS_WINDOWS and not n.endswith(".exe") else ""))
            if cand.is_file():
                return str(cand)
    return None


@functools.lru_cache(maxsize=32)
def tool_version(path: str | None) -> str:
    if not path:
        return ""
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""
    except Exception:
        return ""


@functools.lru_cache(maxsize=32)
def _is_real_gcc(path: str | None) -> bool:
    if not path:
        return False
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
        txt = (out.stdout + out.stderr).lower()
        return "free software foundation" in txt or ("gcc" in txt and "clang" not in txt)
    except Exception:
        return False


def _find_gcc() -> str | None:
    env = os.environ.get("OIFORGE_CXX")
    if env and which(env):
        return which(env)
    versioned = [f"g++-{v}" for v in range(20, 8, -1)]
    for name in versioned + ["g++", "x86_64-w64-mingw32-g++", "c++"]:
        p = which(name)
        if p and _is_real_gcc(p):
            return p
    return None


GCC = _find_gcc()
CLANG = which(os.environ.get("OIFORGE_CLANG", ""), "clang++", *[f"clang++-{v}" for v in range(22, 10, -1)])
# Domyślny kompilator: prawdziwy GCC (jak na OI), w przeciwnym razie clang/whatever jest.
CXX = GCC or CLANG or which("g++", "c++")
CXX_IS_GCC = bool(GCC) and CXX == GCC
LLDB = which("lldb")
GDB = which("gdb")


@functools.lru_cache(maxsize=1)
def lldb_python_path() -> str | None:
    """Katalog z modułem `lldb` dla Pythona (macOS/Linux), jeśli dostępny."""
    if not LLDB:
        return None
    try:
        out = subprocess.run([LLDB, "-P"], capture_output=True, text=True, timeout=15)
        p = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
        return p if p and Path(p).exists() else None
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def find_browser() -> str | None:
    """Chrome/Edge/Chromium do trybu okna aplikacji (--app)."""
    if IS_MAC:
        for p in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                  str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]:
            if Path(p).exists():
                return p
        return None
    if IS_WINDOWS:
        cands = []
        for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                cands += [Path(base) / "Google/Chrome/Application/chrome.exe",
                          Path(base) / "Microsoft/Edge/Application/msedge.exe",
                          Path(base) / "BraveSoftware/Brave-Browser/Application/brave.exe"]
        for c in cands:
            if c.exists():
                return str(c)
        return None
    return which("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser")


@functools.lru_cache(maxsize=1)
def capabilities() -> dict:
    """Co działa na tej maszynie — używane przez UI do pokazywania/ukrywania funkcji i komunikatów."""
    caps = {
        "platform": PLATFORM,
        "python": sys.version.split()[0],
        "gcc": GCC, "gcc_version": tool_version(GCC),
        "clang": CLANG, "clang_version": tool_version(CLANG),
        "cxx": CXX, "cxx_is_gcc": CXX_IS_GCC,
        "lldb": LLDB, "gdb": GDB,
        "lldb_python": lldb_python_path() is not None,
        "browser": find_browser(),
    }
    # Sanitizery: clang (macOS/Linux) albo gcc na Linuksie. MinGW na Windows ich nie wspiera.
    caps["sanitizers"] = bool((CLANG and not IS_WINDOWS) or (GCC and IS_LINUX))
    caps["debugger"] = "lldb" if caps["lldb_python"] else ("gdb" if GDB else None)
    caps["cpp"] = CXX is not None
    return caps
