"""Edytor projektów: prawdziwe pliki ucznia w `USER_DIR/workspace` (projekty, zadania, testy).

Wszystkie ścieżki w API są WZGLĘDNE wobec katalogu workspace (separator `/`). Każda ścieżka jest sprawdzana:
bez `..`, bez ścieżek absolutnych / dysków Windows, bez dowiązań wychodzących poza katalog. Nazwy nowych plików
są sanityzowane (znaki `<>:"|?*` i sterujące → `_`, nazwy zarezerwowane Windows CON/NUL/COM1… → `CON_`).
Usuwanie NIE kasuje — przenosi do `workspace/.trash/<id>/…` (z możliwością przywrócenia).

HTTP:
    GET  /api/ws/info                         → {root, files, dirs}
    GET  /api/ws/tree                         → {root, tree: [{name, path, type: dir|file, size, mtime, children?}]}
    GET  /api/ws/file?path=                   → {path, content, mtime, size, lang, binary?}
    POST /api/ws/save {path, content, mtime?, force?}   → {path, mtime, size} | {conflict: true, disk_content, mtime}
    POST /api/ws/create {path, kind: file|dir, template?, content?, unique?}  → {path, kind}
    POST /api/ws/rename {path, new_path | new_name}     → {path, old_path}
    POST /api/ws/delete {path}                → {trash_id, path}          (do kosza)
    POST /api/ws/restore {trash_id}           → {path}
    GET  /api/ws/trash                        → {items: [{id, path, name, kind, deleted_at, size}]}
    POST /api/ws/trash/empty                  → {removed}
    POST /api/ws/duplicate {path}             → {path}
    POST /api/ws/new_task {name, statement?, parent?}   → {path, files, open}
    POST /api/ws/new_project {name, parent?}  → {path}
    POST /api/ws/import_program {program_id}  → {path, files, open}
    GET  /api/ws/task?path=&content=1         → kontekst zadania: {dir, solution, brute, generator, statement, tests_dir, ...}
    GET  /api/ws/tests?path=                  → {tests_dir, tests: [{name, in, out, in_size, out_size, in_preview, out_preview}]}
    POST /api/ws/run_tests {path, timeout?, tests?}     → {job}  (zdarzenia: compiled, compile_error, test; wynik: podsumowanie)
    POST /api/ws/save_test {dir, input, output?}        → {number, in, out}
    GET  /api/ws/search?q=                    → {results: [{path, line, col, text, kind}], truncated}
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

from oiforge import config
from oiforge.core import jobs, runner
from oiforge.server_api import ApiError

# ============================================================================================== stałe
TRASH = ".trash"
MARKER = ".oiforge-workspace.json"
MAX_FILE_BYTES = 5 * 1024 * 1024           # edycja / zapis
MAX_TEST_BYTES = 64 * 1024 * 1024          # pliki testów (.in/.out) zapisywane przez save_test
MAX_SEARCH_FILE = 2 * 1024 * 1024
MAX_SEARCH_RESULTS = 300
MAX_SEARCH_PER_FILE = 40
MAX_TREE_NODES = 10_000
MAX_TREE_DEPTH = 16
MAX_DUP_BYTES = 100 * 1024 * 1024
MAX_TESTS = 500
EVENT_TEXT = 20_000
TRASH_KEEP_DAYS = 30
HIDDEN = {TRASH, MARKER, ".ds_store", "thumbs.db", "desktop.ini", "__pycache__", ".git", ".svn", ".idea", ".vscode"}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f\x7f/\\]')
_TRASH_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4,12}$")
CPP_EXT = {".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".h", ".c"}
PY_EXT = {".py", ".pyw"}
TESTS_DIRS = ("testy", "tests", "Testy", "Tests", "TESTY")
OUT_EXT = (".out", ".ans", ".ok", ".wy")

_lock = threading.RLock()
_root_override: Path | None = None


# ============================================================================================== szablony
def _cpp_solution(title: str = "") -> str:
    head = f"// {title}\n" if title else ""
    return (head + "#include <bits/stdc++.h>\n"
            "using namespace std;\n"
            "typedef long long ll;\n"
            "\n"
            "int main() {\n"
            "    ios_base::sync_with_stdio(false);\n"
            "    cin.tie(nullptr);\n"
            "\n"
            "    \n"
            "    return 0;\n"
            "}\n")


def _cpp_brute(title: str = "") -> str:
    head = f"// Brut (wolne, ale NA PEWNO poprawne rozwiązanie){': ' + title if title else ''}\n"
    return (head + "// Testerka porównuje je z rozw.cpp na małych losowych testach z gen.py.\n"
            "#include <bits/stdc++.h>\n"
            "using namespace std;\n"
            "typedef long long ll;\n"
            "\n"
            "int main() {\n"
            "    ios_base::sync_with_stdio(false);\n"
            "    cin.tie(nullptr);\n"
            "\n"
            "    \n"
            "    return 0;\n"
            "}\n")


GEN_PY = '''# Generator testów (format Testerki OI Forge).
# Uruchamiany jako: python3 gen.py <seed> <liczba_testów>
# Wypisuje wiele testów, a MIĘDZY testami — PUSTĄ LINIĘ.
import random
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 12345
T = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
random.seed(seed)

tests = []
for _ in range(T):
    n = random.randint(1, 8)                      # MAŁE testy — brut musi zdążyć!
    a = [random.randint(1, 10) for _ in range(n)]
    tests.append(f"{n}\\n{' '.join(map(str, a))}")
print("\\n\\n".join(tests))
'''

PY_SOLUTION = '''import sys
input = sys.stdin.readline


def main():
    n = int(input())
    print(n)


main()
'''

STATEMENT_TEMPLATE = """# {title}

Wklej tutaj treść zadania (albo napisz ją własnymi słowami).

## Wejście

## Wyjście

## Przykład

Wejście:
```
```

Wyjście:
```
```
"""

NOTES_TEMPLATE = """# Notatki — {title}

- Pomysł:
- Złożoność:
- Pułapki (przepełnienie, n = 1, puste wejście…):
"""

EXAMPLE_NAME = "Przykład — suma liczb"
EXAMPLE_FILES = {
    "tresc.md": """# Suma liczb

Dana jest liczba **n** oraz **n** liczb całkowitych. Wypisz ich sumę.

## Wejście
W pierwszym wierszu liczba n (1 ≤ n ≤ 10^6). W drugim wierszu n liczb całkowitych a_i (|a_i| ≤ 10^9).

## Wyjście
Jedna liczba — suma wszystkich a_i.

## Przykład
Wejście:
```
3
1 2 3
```
Wyjście:
```
6
```

> Uwaga: suma może nie zmieścić się w `int` — użyj `long long`!
""",
    "rozw.cpp": """// Suma liczb — rozwiązanie wzorcowe
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);

    int n;
    cin >> n;
    ll suma = 0;               // long long — suma może przekroczyć 2^31
    for (int i = 0; i < n; i++) {
        ll x;
        cin >> x;
        suma += x;
    }
    cout << suma << '\\n';
    return 0;
}
""",
    "brute.cpp": """// Suma liczb — brut (prosty i na pewno poprawny)
#include <bits/stdc++.h>
using namespace std;

int main() {
    long long n, x, s = 0;
    cin >> n;
    while (n--) {
        cin >> x;
        s += x;
    }
    cout << s << endl;
}
""",
    "gen.py": GEN_PY.replace("random.randint(1, 10)", "random.randint(-10, 10)"),
    "notatki.md": """# Notatki — Suma liczb

To przykładowy projekt, żeby edytor nie był pusty. Spróbuj:

1. Otwórz `rozw.cpp` i naciśnij **Uruchom** (Ctrl+Enter).
2. W dolnym panelu wybierz test `testy/1.in` jako wejście.
3. W zakładce **Testy** kliknij „Uruchom wszystkie”.
4. Zmień `ll suma` na `int suma` i zobacz, który test nie przechodzi (WA).

Możesz go spokojnie usunąć — trafi do kosza.
""",
    "testy/1.in": "3\n1 2 3\n",
    "testy/1.out": "6\n",
    "testy/2.in": "1\n-5\n",
    "testy/2.out": "-5\n",
    "testy/3.in": "4\n1000000000 1000000000 1000000000 1000000000\n",
    "testy/3.out": "4000000000\n",
}


# ============================================================================================== ścieżki
def set_root(path: str | Path | None) -> None:
    """Testy: podmiana katalogu workspace (None = domyślny `USER_DIR/workspace`)."""
    global _root_override
    _root_override = Path(path) if path else None


def root_dir() -> Path:
    return _root_override or (Path(config.USER_DIR) / "workspace")


def _root_real() -> Path:
    return root_dir().resolve()


def sanitize_name(name, *, what: str = "Nazwa") -> str:
    """Bezpieczna nazwa pliku/folderu (działa na Windows, macOS i Linuksie)."""
    if not isinstance(name, str):
        raise ApiError(f"{what} musi być tekstem.")
    s = unicodedata.normalize("NFC", name)
    s = _BAD_CHARS.sub("_", s).strip()
    s = s.rstrip(". ")
    if not s:
        raise ApiError(f"{what} jest pusta albo niepoprawna.")
    if s in (".", ".."):
        raise ApiError(f"{what} nie może być „.” ani „..”.")
    if len(s) > 150:
        stem, dot, ext = s.rpartition(".")
        s = (stem[: 150 - len(ext) - 1] + "." + ext) if dot and 0 < len(ext) <= 10 else s[:150]
        s = s.rstrip(". ")
    base = s.split(".")[0].rstrip().upper()
    if base in _RESERVED:
        i = len(s.split(".")[0])
        s = s[:i] + "_" + s[i:]
    if s.lower() in HIDDEN or s.lower().startswith(".oiforge"):
        raise ApiError(f"Nazwa „{s}” jest zarezerwowana.")
    return s


def _split(rel, *, sanitize: bool = False, allow_root: bool = False) -> list[str]:
    if rel is None:
        rel = ""
    if not isinstance(rel, str):
        raise ApiError("Ścieżka musi być tekstem.")
    s = rel.replace("\\", "/").strip()
    if "\x00" in s:
        raise ApiError("Niepoprawna ścieżka.")
    if s.startswith("/") or s.startswith("~") or re.match(r"^[A-Za-z]:", s):
        raise ApiError("Ścieżka musi być względna (wewnątrz katalogu projektów).", 403)
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ApiError("Ścieżka nie może wychodzić poza katalog projektów („..”).", 403)
    if parts and parts[0].lower() in (TRASH, MARKER.lower()):
        raise ApiError("Ten katalog jest zarezerwowany (kosz) — użyj „Przywróć”.", 403)
    if not parts and not allow_root:
        raise ApiError("Podaj ścieżkę pliku.")
    if sanitize:
        parts = [sanitize_name(p) for p in parts]
    elif config.IS_WINDOWS and any(":" in p for p in parts):
        raise ApiError("Niepoprawna nazwa (dwukropek).", 403)
    return parts


def _check_inside(p: Path) -> None:
    rr = _root_real()
    try:
        rp = p.resolve()
    except (OSError, RuntimeError):
        raise ApiError("Nie można rozwiązać ścieżki (pętla dowiązań?).", 403) from None
    if rp != rr and not rp.is_relative_to(rr):
        raise ApiError("Ścieżka prowadzi poza katalog projektów (dowiązanie symboliczne?).", 403)


def resolve(rel, *, sanitize: bool = False, allow_root: bool = False, must_exist: bool = False) -> Path:
    """Ścieżka względna → bezpieczna ścieżka absolutna w workspace (nie wychodzi poza nią)."""
    parts = _split(rel, sanitize=sanitize, allow_root=allow_root)
    p = root_dir().joinpath(*parts)
    _check_inside(p)
    if must_exist and not p.exists():
        raise ApiError(f"Nie ma takiego pliku ani folderu: {'/'.join(parts) or '/'}", 404)
    return p


def rel_of(p: Path) -> str:
    try:
        return p.relative_to(root_dir()).as_posix()
    except ValueError:
        return p.resolve().relative_to(_root_real()).as_posix()


def _hidden(name: str) -> bool:
    low = name.lower()
    return low in HIDDEN or low.startswith(".oiforge") or (low.startswith(".") and low.endswith(".tmp"))


def lang_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in CPP_EXT:
        return "cpp"
    if ext in PY_EXT:
        return "python"
    if ext in (".md", ".markdown"):
        return "markdown"
    return "text"


def _natkey(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _mtime_us(p: Path) -> int:
    return p.stat().st_mtime_ns // 1000


def _unique(parent: Path, name: str, style: str = "num") -> Path:
    """Wolna nazwa w katalogu: `a.cpp` → `a (2).cpp` / `a (kopia).cpp`, `a (kopia 2).cpp`."""
    cand = parent / name
    if not cand.exists():
        return cand
    p = Path(name)
    stem, ext = (p.stem, p.suffix) if p.suffix and len(p.suffix) <= 10 and p.stem else (name, "")
    for i in range(2 if style == "num" else 1, 10_000):
        if style == "num":
            nm = f"{stem} ({i}){ext}"
        else:
            nm = f"{stem} (kopia){ext}" if i == 1 else f"{stem} (kopia {i}){ext}"
        cand = parent / nm
        if not cand.exists():
            return cand
    raise ApiError("Nie udało się znaleźć wolnej nazwy.")


# ============================================================================================== I/O
def _is_binary(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return True
    try:
        data[:65536].decode("utf-8")
    except UnicodeDecodeError as e:
        return e.start < len(data[:65536]) - 4      # ucięty znak wielobajtowy na końcu próbki jest OK
    return False


def read_text(p: Path, limit: int = MAX_FILE_BYTES) -> str:
    size = p.stat().st_size
    if size > limit:
        raise ApiError(f"Plik jest za duży do otwarcia w edytorze ({size / 1048576:.1f} MB, limit "
                       f"{limit / 1048576:.0f} MB).", 413)
    data = p.read_bytes()
    return data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def _norm(text) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ApiError("Zawartość pliku musi być tekstem.")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def atomic_write(p: Path, text: str, limit: int = MAX_FILE_BYTES) -> None:
    """Zapis atomowy: plik tymczasowy w tym samym katalogu + os.replace (nigdy pół-zapisany plik)."""
    data = _norm(text).encode("utf-8")
    if len(data) > limit:
        raise ApiError(f"Plik jest za duży ({len(data) / 1048576:.1f} MB, limit {limit // 1048576} MB).", 413)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + p.name[:40] + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        try:
            if p.exists():
                shutil.copymode(p, tmp)
            elif os.name == "posix":
                os.chmod(tmp, 0o644)
        except OSError:
            pass
        for attempt in range(6):
            try:
                os.replace(tmp, p)
                break
            except PermissionError:              # Windows: plik chwilowo otwarty przez inny proces
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_new(p: Path, text: str) -> None:
    if p.exists():
        raise ApiError(f"„{rel_of(p)}” już istnieje.", 409)
    atomic_write(p, text)


# ============================================================================================== inicjalizacja
def ensure_root() -> Path:
    """Tworzy workspace; przy PIERWSZYM uruchomieniu dodaje przykładowy projekt (raz — znacznik w MARKER)."""
    root = root_dir()
    with _lock:
        root.mkdir(parents=True, exist_ok=True)
        marker = root / MARKER
        if not marker.exists():
            try:
                existing = [c for c in root.iterdir() if not _hidden(c.name)]
                if not existing:
                    ex = root / EXAMPLE_NAME
                    for name, content in EXAMPLE_FILES.items():
                        target = ex.joinpath(*name.split("/"))
                        if not target.exists():
                            atomic_write(target, content)
                marker.write_text(json.dumps({"created": time.strftime("%Y-%m-%d %H:%M:%S"), "version": 1}),
                                  encoding="utf-8")
            except OSError:
                pass
    return root


# ============================================================================================== drzewo
def tree() -> dict:
    root = ensure_root()
    count = [0]
    truncated = [False]

    def walk(d: Path, depth: int) -> list:
        try:
            entries = list(os.scandir(d))
        except OSError:
            return []
        dirs, files = [], []
        for e in entries:
            if _hidden(e.name):
                continue
            if count[0] >= MAX_TREE_NODES:
                truncated[0] = True
                break
            try:
                is_dir = e.is_dir()
                st = e.stat()
            except OSError:
                continue
            if e.is_symlink():
                try:
                    _check_inside(Path(e.path))
                except ApiError:
                    continue                      # dowiązanie poza workspace — nie pokazujemy
            count[0] += 1
            rel = rel_of(Path(e.path))
            if is_dir:
                node = {"name": e.name, "path": rel, "type": "dir", "mtime": round(st.st_mtime, 3), "size": 0}
                node["children"] = walk(Path(e.path), depth + 1) if depth < MAX_TREE_DEPTH else []
                dirs.append(node)
            else:
                files.append({"name": e.name, "path": rel, "type": "file", "size": st.st_size,
                              "mtime": round(st.st_mtime, 3), "lang": lang_of(e.name)})
        dirs.sort(key=lambda n: _natkey(n["name"]))
        files.sort(key=lambda n: _natkey(n["name"]))
        return dirs + files

    with _lock:
        t = walk(root, 0)
    return {"root": str(root), "tree": t, "count": count[0], "truncated": truncated[0]}


# ============================================================================================== operacje na plikach
def read_file(rel: str) -> dict:
    ensure_root()
    p = resolve(rel, must_exist=True)
    if p.is_dir():
        raise ApiError("To jest folder, a nie plik.")
    size = p.stat().st_size
    out = {"path": rel_of(p), "name": p.name, "mtime": _mtime_us(p), "size": size, "lang": lang_of(p.name)}
    if size > MAX_FILE_BYTES:
        raise ApiError(f"Plik jest za duży do otwarcia w edytorze ({size / 1048576:.1f} MB, limit "
                       f"{MAX_FILE_BYTES // 1048576} MB).", 413)
    data = p.read_bytes()
    if _is_binary(data):
        out.update(binary=True, content="")
        return out
    out["content"] = data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    return out


def save_file(rel: str, content, mtime=None, force: bool = False) -> dict:
    ensure_root()
    text = _norm(content)
    with _lock:
        p = resolve(rel)
        if not p.exists():
            if mtime not in (None, "", 0) and not force:
                return {"conflict": True, "deleted": True, "path": rel_of(p),
                        "message": "Plik został usunięty lub przeniesiony poza edytorem."}
            p = resolve(rel, sanitize=True)
            if p.exists() and p.is_dir():
                raise ApiError("Pod tą ścieżką jest folder.")
        elif p.is_dir():
            raise ApiError("To jest folder, a nie plik.")
        else:
            cur = _mtime_us(p)
            if mtime not in (None, "") and not force:
                try:
                    want = int(mtime)
                except (TypeError, ValueError):
                    raise ApiError("Niepoprawny znacznik czasu pliku (mtime).") from None
                if want != cur:
                    try:
                        disk = read_text(p)
                    except ApiError:
                        disk = None
                    if disk != text:
                        return {"conflict": True, "path": rel_of(p), "mtime": cur, "disk_content": disk,
                                "message": "Plik zmienił się na dysku od czasu otwarcia (np. w innym edytorze)."}
                    return {"path": rel_of(p), "mtime": cur, "size": p.stat().st_size, "unchanged": True}
        atomic_write(p, text)
        return {"path": rel_of(p), "mtime": _mtime_us(p), "size": p.stat().st_size, "saved": True}


def _template_for(name: str, template: str | None) -> str:
    t = (template or "").lower()
    title = Path(name).stem
    if t in ("empty", "pusty", "none"):
        return ""
    if t == "brute":
        return _cpp_brute()
    if t in ("gen", "generator"):
        return GEN_PY
    ext = Path(name).suffix.lower()
    if t in ("cpp", "solution") or (not t and ext in (".cpp", ".cc", ".cxx")):
        return _cpp_solution()
    if t == "python" or (not t and ext == ".py"):
        return GEN_PY if Path(name).stem.lower().startswith("gen") else PY_SOLUTION
    if not t and ext == ".md":
        return f"# {title}\n\n"
    return ""


def create(rel: str, kind: str = "file", template: str | None = None, content=None, unique: bool = False) -> dict:
    ensure_root()
    kind = (kind or "file").lower()
    if kind not in ("file", "dir"):
        raise ApiError("Rodzaj musi być „file” albo „dir”.")
    with _lock:
        p = resolve(rel, sanitize=True)
        if p.parent.exists() and not p.parent.is_dir():
            raise ApiError("Rodzicem musi być folder.")
        if p.exists():
            if not unique:
                raise ApiError(f"„{rel_of(p)}” już istnieje.", 409)
            p = _unique(p.parent, p.name)
        _check_inside(p)
        if kind == "dir":
            p.mkdir(parents=True)
        else:
            text = _norm(content) if content is not None else _template_for(p.name, template)
            _write_new(p, text)
        return {"path": rel_of(p), "kind": kind, "name": p.name,
                **({"mtime": _mtime_us(p)} if kind == "file" else {})}


def rename(rel: str, new_path: str | None = None, new_name: str | None = None) -> dict:
    ensure_root()
    with _lock:
        src = resolve(rel, must_exist=True)
        if src == root_dir():
            raise ApiError("Nie można zmienić nazwy katalogu głównego.")
        if new_name is not None and not new_path:
            dst = src.parent / sanitize_name(new_name)
            _check_inside(dst)
        else:
            dst = resolve(new_path, sanitize=True)
        if dst == src:
            return {"path": rel_of(src), "old_path": rel_of(src), "unchanged": True}
        if src.is_dir() and (dst.resolve() == src.resolve() or dst.resolve().is_relative_to(src.resolve())):
            raise ApiError("Nie można przenieść folderu do jego własnego wnętrza.")
        if dst.exists():
            same = False
            try:
                same = os.path.samefile(src, dst)       # zmiana wielkości liter na systemie bez rozróżniania
            except OSError:
                pass
            if not same:
                raise ApiError(f"„{rel_of(dst)}” już istnieje.", 409)
            tmp = _unique(src.parent, f".oiforge-rename-{os.getpid()}-{threading.get_ident()}")
            os.replace(src, tmp)
            os.replace(tmp, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        return {"path": rel_of(dst), "old_path": rel_of(src), "name": dst.name}


def _trash_root() -> Path:
    return root_dir() / TRASH


def _new_trash_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(3).hex()


def delete(rel: str) -> dict:
    ensure_root()
    with _lock:
        src = resolve(rel, must_exist=True)
        if src == root_dir():
            raise ApiError("Nie można usunąć katalogu głównego.")
        r = rel_of(src)
        tid = _new_trash_id()
        box = _trash_root() / tid
        dst = box.joinpath(*r.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        size = _size_of(src)
        shutil.move(str(src), str(dst))
        meta = {"id": tid, "path": r, "name": src.name, "kind": "dir" if dst.is_dir() else "file",
                "deleted_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(), "size": size}
        (box / ".meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        _purge_trash()
        return {"trash_id": tid, "path": r, "name": src.name}


def _size_of(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    tot = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                tot += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return tot


def _trash_meta(box: Path) -> dict | None:
    try:
        m = json.loads((box / ".meta.json").read_text(encoding="utf-8"))
        return m if isinstance(m, dict) and m.get("path") else None
    except (OSError, ValueError):
        return None


def trash_list() -> dict:
    ensure_root()
    _purge_trash()
    items = []
    tr = _trash_root()
    if tr.is_dir():
        for box in tr.iterdir():
            if not box.is_dir() or not _TRASH_ID.match(box.name):
                continue
            m = _trash_meta(box)
            if m:
                items.append({k: m.get(k) for k in ("id", "path", "name", "kind", "deleted_at", "size")} | {"id": box.name})
    items.sort(key=lambda x: x["id"], reverse=True)
    return {"items": items}


def restore(trash_id: str) -> dict:
    ensure_root()
    if not isinstance(trash_id, str) or not _TRASH_ID.match(trash_id):
        raise ApiError("Niepoprawny identyfikator elementu w koszu.")
    with _lock:
        box = _trash_root() / trash_id
        m = _trash_meta(box)
        if not box.is_dir() or not m:
            raise ApiError("Nie ma takiego elementu w koszu (mógł zostać już przywrócony).", 404)
        parts = _split(m["path"])
        src = box.joinpath(*parts)
        if not src.exists():
            raise ApiError("Element w koszu jest uszkodzony (brak pliku).", 404)
        dst = resolve(m["path"])
        if dst.exists():
            stem = Path(dst.name)
            nm = (f"{stem.stem} (przywrócony){stem.suffix}" if src.is_file() and stem.suffix else f"{dst.name} (przywrócony)")
            dst = _unique(dst.parent, nm)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        shutil.rmtree(box, ignore_errors=True)
        return {"path": rel_of(dst), "kind": "dir" if dst.is_dir() else "file"}


def _purge_trash(days: int = TRASH_KEEP_DAYS) -> int:
    tr = _trash_root()
    if not tr.is_dir():
        return 0
    limit = time.time() - days * 86400
    n = 0
    for box in tr.iterdir():
        try:
            if box.is_dir() and _TRASH_ID.match(box.name):
                m = _trash_meta(box)
                ts = (m or {}).get("ts") or box.stat().st_mtime
                if ts < limit:
                    shutil.rmtree(box, ignore_errors=True)
                    n += 1
        except OSError:
            pass
    return n


def empty_trash() -> dict:
    ensure_root()
    with _lock:
        tr = _trash_root()
        n = 0
        if tr.is_dir():
            for box in tr.iterdir():
                if box.is_dir() and _TRASH_ID.match(box.name):
                    shutil.rmtree(box, ignore_errors=True)
                    n += 1
        return {"removed": n}


def duplicate(rel: str) -> dict:
    ensure_root()
    with _lock:
        src = resolve(rel, must_exist=True)
        if src == root_dir():
            raise ApiError("Nie można zduplikować katalogu głównego.")
        dst = _unique(src.parent, src.name, style="copy")
        if src.is_dir():
            if _size_of(src) > MAX_DUP_BYTES:
                raise ApiError("Folder jest za duży do zduplikowania (limit 100 MB).")
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
        return {"path": rel_of(dst), "kind": "dir" if dst.is_dir() else "file", "name": dst.name}


# ============================================================================================== zadania i projekty
def _parse_statement(text: str) -> dict:
    if not text or not text.strip():
        return {}
    try:
        from oiforge.core import statement as st
        r = st.parse(text)
        return r if isinstance(r, dict) else {}
    except Exception:  # noqa: BLE001 — parser treści jest opcjonalny
        return {}


def _samples(parsed: dict) -> list[dict]:
    out = []
    for s in (parsed.get("samples") or [])[:20]:
        if isinstance(s, dict) and (s.get("input") or "").strip():
            out.append({"input": _norm(s.get("input")), "output": _norm(s.get("output") or "")})
    return out


def _end_nl(s: str) -> str:
    return s if not s or s.endswith("\n") else s + "\n"


def _parent_dir(parent) -> Path:
    if not parent:
        return root_dir()
    p = resolve(parent, must_exist=True)
    if not p.is_dir():
        p = p.parent
    return p


def _limits_md(parsed: dict) -> str:
    bits = []
    if parsed.get("time_limit"):
        bits.append(f"- Limit czasu: {parsed['time_limit']}")
    if parsed.get("memory_limit"):
        bits.append(f"- Limit pamięci: {parsed['memory_limit']}")
    cons = parsed.get("constraints") or {}
    for var, c in list(cons.items())[:8]:
        if isinstance(c, dict) and (c.get("min") is not None or c.get("max") is not None):
            bits.append(f"- {var}: {c.get('min', '?')} … {c.get('max', '?')}")
    return ("\n## Z treści\n" + "\n".join(bits) + "\n") if bits else ""


def new_task(name: str = "", statement: str = "", parent=None, solution: str | None = None,
             solution_lang: str = "cpp", extra_notes: str = "") -> dict:
    ensure_root()
    statement = _norm(statement or "")
    parsed = _parse_statement(statement)
    title = (name or "").strip() or (parsed.get("title") or "").strip() or "Nowe zadanie"
    folder = sanitize_name(title[:80], what="Nazwa zadania")
    with _lock:
        base = _parent_dir(parent)
        d = _unique(base, folder)
        _check_inside(d)
        d.mkdir(parents=True)
        files = []

        def put(relname: str, text: str):
            target = d.joinpath(*relname.split("/"))
            atomic_write(target, text)
            files.append(rel_of(target))

        sol_name = "rozw.py" if solution_lang == "python" else "rozw.cpp"
        put(sol_name, _norm(solution) if solution is not None else _cpp_solution(title))
        put("brute.cpp", _cpp_brute(title))
        put("gen.py", GEN_PY)
        samples = _samples(parsed)
        if samples:
            for i, s in enumerate(samples, 1):
                put(f"testy/{i}.in", _end_nl(s["input"]))
                put(f"testy/{i}.out", _end_nl(s["output"]))
        else:
            put("testy/1.in", "")
            put("testy/1.out", "")
        if statement.strip():
            st_md = statement if statement.lstrip().startswith("#") else f"# {title}\n\n{statement}"
            put("tresc.md", _end_nl(st_md))
        else:
            put("tresc.md", STATEMENT_TEMPLATE.format(title=title))
        put("notatki.md", NOTES_TEMPLATE.format(title=title) + _limits_md(parsed) + (extra_notes or ""))
        return {"path": rel_of(d), "files": files, "open": rel_of(d / sol_name), "samples": len(samples),
                "title": title}


def new_project(name: str, parent=None) -> dict:
    ensure_root()
    folder = sanitize_name((name or "").strip() or "Nowy projekt", what="Nazwa projektu")
    with _lock:
        d = _unique(_parent_dir(parent), folder)
        _check_inside(d)
        d.mkdir(parents=True)
        return {"path": rel_of(d), "name": d.name}


def import_program(program_id: str) -> dict:
    if not program_id:
        raise ApiError("Podaj identyfikator programu.")
    try:
        from oiforge.tools import programs
        p = programs.get(str(program_id))
    except Exception as e:  # noqa: BLE001 — moduł „Moje programy” może być niedostępny
        raise ApiError(f"Moduł „Moje programy” jest niedostępny: {e}") from None
    if not p:
        raise ApiError("Nie znaleziono programu.", 404)
    code = p.get("code") or ""
    stmt = p.get("statement") or ""
    if not code.strip() and not stmt.strip():
        raise ApiError("Ten program nie ma ani kodu, ani treści.")
    name = p.get("problem_name") or p.get("problem_short") or "Zadanie"
    short = p.get("problem_short")
    title = f"{name} ({short})" if short and str(short).lower() != str(name).lower() else name
    notes = ["", "## Źródło"]
    for label, key in (("Konkurs", "contest_name"), ("Runda", "round"), ("Wynik", "score"), ("Zgłoszenie", "url"),
                       ("Zadanie", "problem_url")):
        if p.get(key) not in (None, ""):
            notes.append(f"- {label}: {p.get(key)}")
    lang = "python" if str(p.get("lang") or "").lower().startswith("py") else "cpp"
    res = new_task(title, stmt, solution=code, solution_lang=lang, extra_notes="\n".join(notes) + "\n")
    res["program_id"] = p.get("id") or program_id
    return res


# ============================================================================================== kontekst zadania i testy
def _task_dir_for(p: Path) -> Path:
    """Folder zadania dla pliku: katalog pliku (albo rodzic, jeśli plik leży w testy/)."""
    d = p if p.is_dir() else p.parent
    if d.name in TESTS_DIRS and d != root_dir():
        d = d.parent
    return d


def find_tests_dir(p: Path) -> Path | None:
    """Szuka folderu testy/ (lub tests/) w katalogu pliku albo wyżej (do korzenia workspace)."""
    root = root_dir()
    d = p if p.is_dir() else p.parent
    if d.name in TESTS_DIRS and d != root:
        return d
    while True:
        for nm in TESTS_DIRS:
            cand = d / nm
            if cand.is_dir():
                return cand
        if d == root or root not in d.parents:
            return None
        d = d.parent


def _out_for(inp: Path) -> Path | None:
    for ext in OUT_EXT:
        c = inp.with_suffix(ext)
        if c.is_file():
            return c
    return None


def list_tests(tests_dir: Path) -> list[dict]:
    out = []
    try:
        ins = [c for c in tests_dir.iterdir() if c.is_file() and c.suffix.lower() == ".in"]
    except OSError:
        return []
    for c in sorted(ins, key=lambda x: _natkey(x.name))[:MAX_TESTS]:
        o = _out_for(c)
        out.append({"name": c.stem, "in": rel_of(c), "out": rel_of(o) if o else None,
                    "in_size": c.stat().st_size, "out_size": o.stat().st_size if o else None})
    return out


def _preview(p: Path | None, n: int = 400) -> str | None:
    if not p:
        return None
    try:
        with open(p, "rb") as f:
            data = f.read(n + 1)
        s = data[:n].decode("utf-8", "replace").replace("\r\n", "\n")
        return s + ("…" if len(data) > n else "")
    except OSError:
        return None


def tests_info(rel: str) -> dict:
    ensure_root()
    p = resolve(rel, must_exist=True, allow_root=True)
    td = find_tests_dir(p)
    if not td:
        task = _task_dir_for(p)
        return {"tests_dir": None, "task_dir": rel_of(task) if task != root_dir() else "", "tests": [],
                "suggested_dir": rel_of(task / "testy") if task != root_dir() else None}
    tests = list_tests(td)
    for t in tests:
        t["in_preview"] = _preview(root_dir() / t["in"])
        t["out_preview"] = _preview(root_dir() / t["out"]) if t["out"] else None
    return {"tests_dir": rel_of(td), "task_dir": rel_of(td.parent) if td.parent != root_dir() else "", "tests": tests}


_ROLE_NAMES = {
    "solution": ["rozw.cpp", "rozw.py", "rozwiazanie.cpp", "main.cpp", "sol.cpp", "solution.cpp", "wzor.cpp", "main.py"],
    "brute": ["brute.cpp", "brut.cpp", "brute.py", "brut.py", "naive.cpp", "wolne.cpp"],
    "generator": ["gen.py", "gen.cpp", "generator.py", "generator.cpp", "gen2.py"],
    "statement": ["tresc.md", "treść.md", "tresc.txt", "treść.txt", "statement.md", "zadanie.md", "statement.txt"],
}


def task_context(rel: str, with_content: bool = False) -> dict:
    """Pliki zadania (rozwiązanie, brut, generator, treść, testy) dla pliku lub folderu."""
    ensure_root()
    p = resolve(rel, must_exist=True, allow_root=True)
    d = _task_dir_for(p)
    names = {}
    try:
        names = {c.name.lower(): c for c in d.iterdir() if c.is_file()}
    except OSError:
        pass
    res = {"dir": rel_of(d) if d != root_dir() else "", "name": d.name if d != root_dir() else ""}
    for role, cands in _ROLE_NAMES.items():
        hit = next((names[c] for c in cands if c in names), None)
        res[role] = None
        if hit:
            item = {"path": rel_of(hit), "lang": lang_of(hit.name)}
            if with_content:
                try:
                    item["content"] = read_text(hit, limit=1024 * 1024)
                except (ApiError, OSError):
                    item["content"] = None
            res[role] = item
    # bieżący plik C++/Python, który nie jest brutem ani generatorem, traktujemy jako rozwiązanie
    if p.is_file() and lang_of(p.name) in ("cpp", "python") and \
            not any(res[r] and res[r]["path"] == rel_of(p) for r in ("brute", "generator")):
        item = {"path": rel_of(p), "lang": lang_of(p.name)}
        if with_content:
            try:
                item["content"] = read_text(p, limit=1024 * 1024)
            except (ApiError, OSError):
                item["content"] = None
        res["solution"] = item
    td = find_tests_dir(p)
    res["tests_dir"] = rel_of(td) if td else None
    res["tests_count"] = len(list_tests(td)) if td else 0
    return res


def save_test(rel_dir: str, input_text, output_text=None) -> dict:
    ensure_root()
    if input_text is None or not isinstance(input_text, str):
        raise ApiError("Brak wejścia testu.")
    with _lock:
        p = resolve(rel_dir, must_exist=True, allow_root=True)
        td = find_tests_dir(p)            # ten sam folder, którego użyje „Uruchom wszystkie”
        if not td:
            td = _task_dir_for(p) / "testy"
            _check_inside(td)
            td.mkdir(parents=True, exist_ok=True)
        nums = [int(c.stem) for c in td.iterdir() if c.suffix.lower() in (".in",) + OUT_EXT and c.stem.isdigit()]
        n = max(nums, default=0) + 1
        fin = td / f"{n}.in"
        atomic_write(fin, _end_nl(_norm(input_text)), limit=MAX_TEST_BYTES)
        res = {"number": n, "in": rel_of(fin), "out": None, "tests_dir": rel_of(td)}
        if output_text is not None:
            fout = td / f"{n}.out"
            atomic_write(fout, _end_nl(_norm(output_text)), limit=MAX_TEST_BYTES)
            res["out"] = rel_of(fout)
        return res


# ============================================================================================== uruchamianie testów
def _clip(s: str | None, n: int = EVENT_TEXT) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n… (obcięto, {len(s)} znaków)"


def first_diff(got: str, exp: str) -> dict | None:
    """Pierwsza różniąca się linia (porównanie tokenowe linia po linii, białe znaki na końcach nieistotne)."""
    g = runner.normalize_output(got).split("\n")
    e = runner.normalize_output(exp).split("\n")
    while g and not g[-1].strip():
        g.pop()
    while e and not e[-1].strip():
        e.pop()
    for i in range(max(len(g), len(e))):
        a = g[i] if i < len(g) else None
        b = e[i] if i < len(e) else None
        if a is None or b is None or a.split() != b.split():
            col = None
            if a is not None and b is not None:
                ta, tb = a.split(), b.split()
                col = next((k for k in range(min(len(ta), len(tb))) if ta[k] != tb[k]), min(len(ta), len(tb))) + 1
            return {"line": i + 1, "got": _clip(a, 300) if a is not None else None,
                    "expected": _clip(b, 300) if b is not None else None, "token": col}
    return None


def _settings() -> dict:
    try:
        from oiforge.core import store
        return store.settings() or {}
    except Exception:  # noqa: BLE001
        return {}


def _program_file(p: Path) -> Path:
    if p.is_file():
        return p
    ctx = task_context(rel_of(p))
    if ctx.get("solution"):
        return root_dir() / ctx["solution"]["path"]
    raise ApiError("W tym folderze nie ma pliku z rozwiązaniem (rozw.cpp / rozw.py).")


def prepare_run_tests(body: dict) -> dict:
    ensure_root()
    p = _program_file(resolve(body.get("path"), must_exist=True))
    lang = lang_of(p.name)
    if lang not in ("cpp", "python"):
        raise ApiError("Uruchamiać można tylko pliki C++ (.cpp) i Pythona (.py).")
    td = find_tests_dir(p)
    if not td:
        raise ApiError("Nie znaleziono folderu „testy/” obok pliku ani wyżej. Dodaj test przyciskiem „+ Dodaj test”.")
    tests = list_tests(td)
    only = body.get("tests")
    if isinstance(only, list) and only:
        want = {str(x) for x in only}
        tests = [t for t in tests if t["name"] in want or t["in"] in want]
    if not tests:
        raise ApiError(f"W folderze „{rel_of(td)}” nie ma plików .in.")
    s = _settings()
    try:
        timeout = float(body.get("timeout") or s.get("time_limit") or 2.0)
    except (TypeError, ValueError):
        timeout = 2.0
    mem = body.get("memory_limit") or s.get("memory_limit")
    try:
        mem = int(mem) if mem else None
    except (TypeError, ValueError):
        mem = None
    return {"file": rel_of(p), "lang": lang, "source": read_text(p), "tests_dir": rel_of(td), "tests": tests,
            "timeout": max(0.1, min(timeout, 60.0)), "mem": mem, "std": s.get("cpp_std") or "c++20",
            "float_eps": body.get("float_eps")}


def _run_tests_work(job, cfg: dict) -> dict:
    root = root_dir()
    tests = cfg["tests"]
    n = len(tests)
    job.progress(0.0, "Kompiluję…" if cfg["lang"] == "cpp" else "Przygotowuję…")
    try:
        prog = runner.prepare(cfg["source"], lang=cfg["lang"], std=cfg["std"])
    except TypeError:
        prog = runner.prepare(cfg["source"], lang=cfg["lang"])
    comp = prog.compile.to_dict() if prog.compile else None
    if not prog.ok:
        job.push({"type": "compile_error", "compile": comp, "file": cfg["file"]})
        return {"status": "CE", "file": cfg["file"], "compile": comp, "total": n, "passed": 0,
                "message": "Błąd kompilacji — popraw kod i spróbuj ponownie."}
    job.push({"type": "compiled", "file": cfg["file"], "cached": bool(comp and comp.get("cached")),
              "time": comp.get("time") if comp else None, "warnings": comp.get("warnings") if comp else 0})
    counts: dict[str, int] = {}
    results = []
    max_time, max_mem = 0.0, 0
    eps = None
    try:
        eps = float(cfg["float_eps"]) if cfg.get("float_eps") not in (None, "") else None
    except (TypeError, ValueError):
        eps = None
    cwd = None
    try:
        config.TMP_DIR.mkdir(parents=True, exist_ok=True)
        cwd = str(config.TMP_DIR)
    except OSError:
        pass
    for i, t in enumerate(tests):
        if job.cancelled:
            break
        job.progress(i / max(1, n), f"Test {t['name']} ({i + 1}/{n})")
        try:
            inp = (root / t["in"]).read_bytes().decode("utf-8", "replace")
        except OSError as e:
            ev = {"type": "test", "index": i, "name": t["name"], "verdict": "ERR", "message": f"Nie można odczytać: {e}"}
            job.push(ev)
            results.append(ev)
            counts["ERR"] = counts.get("ERR", 0) + 1
            continue
        rr = runner.run(prog.cmd, inp, timeout=cfg["timeout"], mem_limit_mb=cfg["mem"], cwd=cwd)
        verdict = rr.verdict
        diff = None
        expected = None
        checked = False
        if t["out"]:
            try:
                expected = (root / t["out"]).read_bytes().decode("utf-8", "replace")
            except OSError:
                expected = None
        if verdict == "OK" and expected is not None:
            checked = True
            if not runner.outputs_equal(rr.stdout, expected, float_eps=eps):
                verdict = "WA"
                diff = first_diff(rr.stdout, expected)
        counts[verdict] = counts.get(verdict, 0) + 1
        max_time = max(max_time, rr.time or 0.0)
        max_mem = max(max_mem, rr.mem_kb or 0)
        ev = {"type": "test", "index": i, "name": t["name"], "in": t["in"], "out": t["out"], "verdict": verdict,
              "checked": checked, "time": rr.time, "mem_kb": rr.mem_kb, "exit_code": rr.exit_code, "signal": rr.signal,
              "timed_out": rr.timed_out, "diff": diff, "stdout": _clip(rr.stdout), "stderr": _clip(rr.stderr, 4000),
              "truncated": rr.truncated}
        job.push(ev)
        results.append({k: ev[k] for k in ("name", "verdict", "checked", "time", "mem_kb", "diff")})
    done = len(results)
    passed = sum(1 for r in results if r["verdict"] == "OK")
    unchecked = sum(1 for r in results if r["verdict"] == "OK" and not r.get("checked"))
    all_ok = done == n and passed == n
    return {"status": "OK" if all_ok else ("CANCELLED" if job.cancelled else "FAIL"), "file": cfg["file"],
            "tests_dir": cfg["tests_dir"], "total": n, "done": done, "passed": passed, "unchecked": unchecked,
            "counts": counts, "max_time": round(max_time, 4), "max_mem_kb": max_mem, "timeout": cfg["timeout"],
            "all_ok": all_ok, "tests": results, "compile": {k: comp.get(k) for k in ("cached", "time", "warnings")}
            if comp else None}


def run_tests_sync(rel: str, **kw) -> dict:
    """Synchronicznie (testy jednostkowe): uruchamia program na testach z folderu testy/."""
    cfg = prepare_run_tests({"path": rel, **kw})
    _job, res = jobs.run_sync(_run_tests_work, cfg, title="Testy")
    return res


# ============================================================================================== szukanie
def search(q: str, limit: int = MAX_SEARCH_RESULTS) -> dict:
    root = ensure_root()
    q = (q or "").strip()
    if len(q) < 2:
        return {"results": [], "truncated": False, "files": 0}
    ql = q.lower()
    results = []
    truncated = False
    files = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted((d for d in dn if not _hidden(d)), key=_natkey)
        for f in sorted(fn, key=_natkey):
            if _hidden(f):
                continue
            p = Path(dp) / f
            rel = rel_of(p)
            if ql in f.lower():
                results.append({"path": rel, "line": None, "col": None, "text": f, "kind": "name"})
            try:
                if p.is_symlink():
                    _check_inside(p)
                if p.stat().st_size > MAX_SEARCH_FILE:
                    continue
                data = p.read_bytes()
            except (OSError, ApiError):
                continue
            if _is_binary(data):
                continue
            files += 1
            text = data.decode("utf-8", "replace")
            if ql not in text.lower():
                continue
            per = 0
            for ln, line in enumerate(text.splitlines(), 1):
                idx = line.lower().find(ql)
                if idx >= 0:
                    results.append({"path": rel, "line": ln, "col": idx + 1, "text": line.strip()[:240], "kind": "text"})
                    per += 1
                    if per >= MAX_SEARCH_PER_FILE or len(results) >= limit:
                        break
            if len(results) >= limit:
                truncated = True
                break
        if truncated:
            break
    return {"results": results[:limit], "truncated": truncated, "files": files, "q": q}


def info() -> dict:
    root = ensure_root()
    nf = nd = 0
    for _dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not _hidden(d)]
        nd += len(dn)
        nf += sum(1 for f in fn if not _hidden(f))
    return {"root": str(root), "files": nf, "dirs": nd, "max_file_mb": MAX_FILE_BYTES // 1048576}


# ============================================================================================== HTTP
def _flag(v) -> bool:
    return v in (True, 1, "1", "true", "tak", "yes")


def handle_info(_q: dict) -> dict:
    return info()


def handle_tree(_q: dict) -> dict:
    return tree()


def handle_file(q: dict) -> dict:
    return read_file(q.get("path") or "")


def handle_save(b: dict) -> dict:
    if "content" not in b:
        raise ApiError("Brak zawartości pliku.")
    return save_file(b.get("path") or "", b.get("content"), b.get("mtime"), force=_flag(b.get("force")))


def handle_create(b: dict) -> dict:
    return create(b.get("path") or "", b.get("kind") or "file", b.get("template"), b.get("content"),
                  unique=_flag(b.get("unique")))


def handle_rename(b: dict) -> dict:
    return rename(b.get("path") or "", b.get("new_path"), b.get("new_name"))


def handle_delete(b: dict) -> dict:
    return delete(b.get("path") or "")


def handle_restore(b: dict) -> dict:
    return restore(b.get("trash_id") or b.get("id") or "")


def handle_trash(_q: dict) -> dict:
    return trash_list()


def handle_trash_empty(_b: dict) -> dict:
    return empty_trash()


def handle_duplicate(b: dict) -> dict:
    return duplicate(b.get("path") or "")


def handle_new_task(b: dict) -> dict:
    return new_task(b.get("name") or "", b.get("statement") or "", parent=b.get("parent"))


def handle_new_project(b: dict) -> dict:
    return new_project(b.get("name") or "", parent=b.get("parent"))


def handle_import_program(b: dict) -> dict:
    return import_program(b.get("program_id") or b.get("id") or "")


def handle_task(q: dict) -> dict:
    return task_context(q.get("path") or "", with_content=_flag(q.get("content")))


def handle_tests(q: dict) -> dict:
    return tests_info(q.get("path") or "")


def handle_run_tests(b: dict) -> dict:
    cfg = prepare_run_tests(b)
    title = f"Testy: {Path(cfg['file']).name}"
    return {"job": jobs.submit(_run_tests_work, cfg, title=title), "total": len(cfg["tests"]),
            "tests_dir": cfg["tests_dir"], "file": cfg["file"]}


def handle_save_test(b: dict) -> dict:
    return save_test(b.get("dir") or b.get("path") or "", b.get("input"), b.get("output"))


def handle_search(q: dict) -> dict:
    return search(q.get("q") or "")


ROUTES = {
    "GET /api/ws/info": handle_info,
    "GET /api/ws/tree": handle_tree,
    "GET /api/ws/file": handle_file,
    "POST /api/ws/save": handle_save,
    "POST /api/ws/create": handle_create,
    "POST /api/ws/rename": handle_rename,
    "POST /api/ws/delete": handle_delete,
    "POST /api/ws/restore": handle_restore,
    "GET /api/ws/trash": handle_trash,
    "POST /api/ws/trash/empty": handle_trash_empty,
    "POST /api/ws/duplicate": handle_duplicate,
    "POST /api/ws/new_task": handle_new_task,
    "POST /api/ws/new_project": handle_new_project,
    "POST /api/ws/import_program": handle_import_program,
    "GET /api/ws/task": handle_task,
    "GET /api/ws/tests": handle_tests,
    "POST /api/ws/run_tests": handle_run_tests,
    "POST /api/ws/save_test": handle_save_test,
    "GET /api/ws/search": handle_search,
}
