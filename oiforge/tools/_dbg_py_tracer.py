"""Tracer programów w Pythonie dla wizualnego debuggera (sys.settrace) — uruchamiany w OSOBNYM procesie.

Użycie: python3 _dbg_py_tracer.py CONFIG.json
Konfiguracja: {"code_path", "max_steps", "time_budget", "max_items", "watch": [nazwy] | null}
Wejście programu = stdin procesu (rodzic podaje plik), wyjście tracera = linie JSON na stdout
(ten sam format co driver lldb): {"t": "step"|"info"|"end"|"error", ...}.
Wyjście programu (print, sys.stdout.write) jest przechwytywane i dołączane przyrostowo do kroków.
"""
import builtins
import collections
import io
import json
import os
import sys
import time
import types

FILENAME = "<main.py>"
MAX_STR = 400


class StopTracing(BaseException):
    """Przerywa program po przekroczeniu limitu kroków/czasu (BaseException — `except Exception` jej nie złapie)."""


class _BufCapture(object):
    def __init__(self, owner):
        self.owner = owner

    def write(self, b):
        if isinstance(b, (bytes, bytearray, memoryview)):
            self.owner.parts.append(bytes(b).decode("utf-8", "replace"))
            self.owner.size += len(b)
            return len(b)
        return self.owner.write(str(b))

    def flush(self):
        pass


class Capture(io.TextIOBase):
    """Zastępuje sys.stdout: zbiera tekst, pozwala odczytać przyrost od ostatniego kroku."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.size = 0
        self.taken = 0
        self.buffer = _BufCapture(self)

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        self.parts.append(s)
        self.size += len(s)
        return len(s)

    def writable(self):
        return True

    def flush(self):
        pass

    @property
    def encoding(self):
        return "utf-8"

    def isatty(self):
        return False

    def take(self):
        if len(self.parts) == self.taken:
            return ""
        s = "".join(self.parts[self.taken:])
        self.taken = len(self.parts)
        return s


def short_repr(x, limit=60):
    try:
        r = repr(x)
    except Exception:  # noqa: BLE001
        r = "<?>"
    return r if len(r) <= limit else r[: limit - 1] + "…"


def scalar_text(x):
    if isinstance(x, bool) or x is None:
        return str(x)
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return repr(x)
    return short_repr(x)


SCALARS = (bool, int, float, complex, type(None))


class Converter(object):
    def __init__(self, max_items=256, max_rows=48, max_cols=48, max_cells=1024, max_map=100):
        self.max_items = max_items
        self.max_rows = max_rows
        self.max_cols = max_cols
        self.max_cells = max_cells
        self.max_map = max_map

    def elem(self, x, depth):
        if isinstance(x, SCALARS):
            return scalar_text(x)
        if isinstance(x, str):
            return x if len(x) <= MAX_STR else x[:MAX_STR] + "…"
        if depth > 3:
            return {"kind": "scalar", "value": short_repr(x)}
        n = self.node(x, depth)
        if n is not None:
            n.pop("type", None)
            return n
        return {"kind": "scalar", "value": short_repr(x)}

    def is_matrix(self, seq):
        if not seq or len(seq) < 1:
            return False
        rows = seq[: self.max_rows]
        if not all(isinstance(r, (list, tuple)) for r in rows):
            return False
        if not any(len(r) for r in rows):
            return False
        for r in rows:
            for y in r[: self.max_cols]:
                if not (isinstance(y, (int, float)) or (isinstance(y, str) and len(y) == 1)):
                    return False
        return True

    def node(self, x, depth=0):
        tname = type(x).__name__
        if isinstance(x, SCALARS):
            return {"type": tname, "kind": "scalar", "value": scalar_text(x)}
        if isinstance(x, str):
            return {"type": "str", "kind": "string", "value": x if len(x) <= MAX_STR else x[:MAX_STR] + "…",
                    "size": len(x)}
        if isinstance(x, (bytes, bytearray)):
            return {"type": tname, "kind": "string", "value": short_repr(x, MAX_STR)}
        if isinstance(x, tuple) and len(x) <= 8 and not hasattr(x, "_fields"):
            return {"type": "tuple", "kind": "pair", "items": [self.elem(y, depth + 1) for y in x]}
        if isinstance(x, (list, tuple, collections.deque)) or tname == "array":
            seq = list(x) if not isinstance(x, list) else x
            n = len(seq)
            if depth == 0 and self.is_matrix(seq):
                r = min(n, self.max_rows)
                rows = []
                cells = 0
                maxc = 0
                trunc = n > r
                ischar = True
                for row in seq[:r]:
                    c = min(len(row), self.max_cols)
                    if cells + c > self.max_cells:
                        trunc = True
                        break
                    vals = []
                    for y in list(row)[:c]:
                        if isinstance(y, str):
                            vals.append("'" + y + "'")
                        else:
                            ischar = False
                            vals.append(scalar_text(y))
                    rows.append(vals)
                    cells += c
                    maxc = max(maxc, len(row))
                    if len(row) > c:
                        trunc = True
                d = {"type": tname, "kind": "matrix", "rows": rows, "size": [n, maxc]}
                if ischar:
                    d["elem"] = "char"
                if trunc:
                    d["truncated"] = True
                return d
            m = min(n, self.max_items)
            d = {"type": tname, "kind": "vector", "items": [self.elem(y, depth + 1) for y in seq[:m]], "size": n}
            if n > m:
                d["truncated"] = True
            return d
        if isinstance(x, dict):
            n = len(x)
            items = []
            for i, (k, v) in enumerate(x.items()):
                if i >= self.max_map:
                    break
                items.append([self.elem(k, depth + 1), self.elem(v, depth + 1)])
            d = {"type": tname, "kind": "map", "items": items, "size": n}
            if n > self.max_map:
                d["truncated"] = True
            return d
        if isinstance(x, (set, frozenset)):
            try:
                seq = sorted(x)
            except TypeError:
                seq = list(x)
            n = len(seq)
            d = {"type": tname, "kind": "set", "items": [self.elem(y, depth + 1) for y in seq[: self.max_map]], "size": n}
            if n > self.max_map:
                d["truncated"] = True
            return d
        if isinstance(x, (types.FunctionType, types.BuiltinFunctionType, types.ModuleType, type, types.MethodType)):
            return None
        if hasattr(x, "_fields") and isinstance(x, tuple):
            return {"type": tname, "kind": "struct", "fields": {f: self.elem(getattr(x, f), depth + 1) for f in x._fields[:24]}}
        attrs = None
        if hasattr(x, "__dict__") and isinstance(getattr(x, "__dict__", None), dict):
            attrs = {k: v for k, v in vars(x).items() if not k.startswith("_")}
        elif hasattr(x, "__slots__"):
            attrs = {}
            for k in getattr(x, "__slots__", ()):
                if hasattr(x, k) and not k.startswith("_"):
                    attrs[k] = getattr(x, k)
        if attrs is not None and depth <= 2:
            return {"type": tname, "kind": "struct",
                    "fields": {k: self.elem(v, depth + 1) for k, v in list(attrs.items())[:24]}}
        return {"type": tname, "kind": "scalar", "value": short_repr(x)}


class Tracer(object):
    def __init__(self, cfg, out, cap):
        self.cfg = cfg
        self.out = out
        self.cap = cap
        self.max_steps = int(cfg.get("max_steps") or 3000)
        self.budget = float(cfg.get("time_budget") or 25.0)
        self.watch = set(cfg.get("watch") or [])
        self.conv = Converter(max_items=int(cfg.get("max_items") or 256))
        self.steps = 0
        self.t0 = time.time()
        self.buf = []
        self.pending_ret = None
        self.hidden_globals = set()
        self.stopped = False

    def emit(self, obj, flush=False):
        self.buf.append(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
        if flush or len(self.buf) >= 32:
            self.flush()

    def flush(self):
        if self.buf:
            try:
                self.out.write("\n".join(self.buf) + "\n")
                self.out.flush()
            except (OSError, ValueError):
                pass
            self.buf = []

    # --- sys.settrace
    def global_trace(self, frame, event, arg):
        if self.stopped:
            return None
        if frame.f_code.co_filename != FILENAME:
            return None
        return self.local_trace

    def local_trace(self, frame, event, arg):
        if self.stopped:
            return None
        if event == "line":
            if self.steps >= self.max_steps:
                self.stopped = True
                raise StopTracing("max_steps")
            if time.time() - self.t0 > self.budget:
                self.stopped = True
                raise StopTracing("timeout")
            self.emit(self.snapshot(frame, frame.f_lineno))
        elif event == "return":
            name = frame.f_code.co_name
            if name != "<module>" and not name.startswith("<"):
                self.pending_ret = {"from": name, "value": short_repr(arg)}
        return self.local_trace

    # --- stan
    def user_frames(self, frame):
        out = []
        f = frame
        while f is not None:
            if f.f_code.co_filename == FILENAME:
                out.append(f)
            f = f.f_back
        return out

    def func_name(self, f):
        n = f.f_code.co_name
        return "main" if n == "<module>" else n

    def args_text(self, f):
        code = f.f_code
        names = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
        parts = []
        for nm in names[:6]:
            if nm in f.f_locals:
                v = f.f_locals[nm]
                if isinstance(v, SCALARS):
                    parts.append("%s=%s" % (nm, scalar_text(v)))
                elif isinstance(v, str):
                    parts.append('%s="%s"' % (nm, v[:12] + ("…" if len(v) > 12 else "")))
                elif hasattr(v, "__len__"):
                    try:
                        parts.append("%s=[%d el.]" % (nm, len(v)))
                    except Exception:  # noqa: BLE001
                        parts.append("%s=…" % nm)
                else:
                    parts.append("%s=…" % nm)
        return ", ".join(parts)

    def visible(self, name, value):
        if name.startswith("__") or name in self.hidden_globals:
            return False
        if isinstance(value, (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, type,
                              types.MethodType)):
            return False
        if self.watch and name not in self.watch:
            return False
        return True

    def snapshot(self, frame, line, extra=None):
        self.steps += 1
        frames = self.user_frames(frame)
        stack = []
        for f in frames[:40]:
            stack.append({"func": self.func_name(f), "line": f.f_lineno if f is not frame else line,
                          "id": "%x" % id(f), "args": "" if f.f_code.co_name == "<module>" else self.args_text(f)})
        vars_ = {}
        is_module = frame.f_code.co_name == "<module>"
        if not is_module:
            code = frame.f_code
            argnames = set(code.co_varnames[: code.co_argcount + code.co_kwonlyargcount])
            for nm, v in list(frame.f_locals.items()):
                if not self.visible(nm, v):
                    continue
                try:
                    node = self.conv.node(v)
                except Exception as e:  # noqa: BLE001
                    node = {"type": "", "kind": "scalar", "value": "<błąd: %s>" % e}
                if node is None:
                    continue
                node["scope"] = "arg" if nm in argnames else "local"
                vars_[nm] = node
        for nm, v in list(frame.f_globals.items()):
            if not self.visible(nm, v):
                continue
            try:
                node = self.conv.node(v)
            except Exception as e:  # noqa: BLE001
                node = {"type": "", "kind": "scalar", "value": "<błąd: %s>" % e}
            if node is None:
                continue
            node["scope"] = "global"
            vars_["::" + nm if nm in vars_ else nm] = node
        step = {"t": "step", "line": line, "func": self.func_name(frame), "depth": max(0, len(frames) - 1),
                "stack": stack, "vars": vars_, "frame": "%x" % id(frame)}
        out = self.cap.take()
        if out:
            step["out"] = out
        if self.pending_ret is not None:
            step["ret"] = self.pending_ret
            self.pending_ret = None
        if extra:
            step.update(extra)
        return step


def explain_exception(e):
    name = e.__class__.__name__
    msg = str(e)
    hints = {
        "IndexError": "indeks poza zakresem listy",
        "KeyError": "brak klucza w słowniku",
        "ZeroDivisionError": "dzielenie przez zero",
        "RecursionError": "zbyt głęboka rekurencja (zwiększ sys.setrecursionlimit albo przepisz iteracyjnie)",
        "ValueError": "niepoprawna wartość (np. int() z pustego napisu — sprawdź wczytywanie wejścia)",
        "TypeError": "niezgodne typy",
        "NameError": "nieznana nazwa (literówka albo zmienna niezdefiniowana)",
        "AttributeError": "brak atrybutu/metody",
        "EOFError": "koniec wejścia — program czyta więcej danych niż podano",
        "MemoryError": "brak pamięci",
    }
    h = hints.get(name, "wyjątek")
    return "Program zakończył się wyjątkiem %s: %s (%s)." % (name, msg, h)


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"t": "error", "message": "Brak pliku konfiguracji."}))
        return 2
    with open(sys.argv[1], encoding="utf-8") as f:
        cfg = json.load(f)
    with open(cfg["code_path"], encoding="utf-8") as f:
        code = f.read().replace("\r\n", "\n")
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
    except OSError:
        pass
    cap = Capture()
    tr = Tracer(cfg, out, cap)
    try:
        compiled = compile(code, FILENAME, "exec")
    except SyntaxError as e:
        tr.emit({"t": "error", "message": "Błąd składni w linii %s: %s" % (e.lineno, e.msg), "line": e.lineno}, True)
        return 0
    g = {"__name__": "__main__", "__builtins__": builtins, "__file__": "main.py"}
    tr.hidden_globals = set(g)
    lines = sorted(set(ln for _, _, ln in _code_lines(compiled) if ln))
    tr.emit({"t": "info", "lines": lines, "python": sys.version.split()[0]}, True)
    real_stdout = sys.stdout
    sys.stdout = cap
    reason, message, exit_code = "exit", None, 0
    sys.settrace(tr.global_trace)
    try:
        exec(compiled, g)
    except StopTracing as e:
        reason = str(e) if str(e) in ("max_steps", "timeout") else "max_steps"
        if reason == "max_steps":
            message = "Osiągnięto limit %d kroków — dalsza część programu nie została prześledzona." % tr.max_steps
        else:
            message = "Przekroczono budżet czasu (%.0f s) po %d krokach." % (tr.budget, tr.steps)
        exit_code = None
    except SystemExit as e:
        code_ = e.code
        exit_code = code_ if isinstance(code_, int) else (0 if code_ is None else 1)
    except BaseException as e:  # noqa: BLE001
        sys.settrace(None)
        reason = "crash"
        message = explain_exception(e)
        exit_code = 1
        tb = e.__traceback__
        last = None
        while tb is not None:
            if tb.tb_frame.f_code.co_filename == FILENAME:
                last = tb
            tb = tb.tb_next
        if last is not None:
            tr.stopped = False
            try:
                step = tr.snapshot(last.tb_frame, last.tb_lineno, {"crash": message})
                tr.emit(step)
            except Exception:  # noqa: BLE001
                pass
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout
    rest = cap.take()
    if rest:
        tr.emit({"t": "out", "out": rest})
    tr.emit({"t": "end", "reason": reason, "message": message, "exit_code": exit_code, "steps": tr.steps,
             "elapsed": round(time.time() - tr.t0, 3), "stderr": ""}, True)
    return 0


def _code_lines(code):
    try:
        for item in code.co_lines():
            yield item
    except AttributeError:
        for _, ln in __import__("dis").findlinestarts(code):
            yield (0, 0, ln)
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            for item in _code_lines(c):
                yield item


if __name__ == "__main__":
    sys.exit(main())
