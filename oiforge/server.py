"""Serwer HTTP OI Forge: statyki z web/, API JSON, auto-discovery modułów oiforge/tools/*.py."""
from __future__ import annotations

import importlib
import json
import mimetypes
import pkgutil
import re
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import config
from .core import jobs, kb, runner, store
from .server_api import ApiError

ROUTES: dict[tuple[str, str], callable] = {}
PATTERN_ROUTES: list[tuple[str, re.Pattern, callable]] = []
TOOL_MODULES: list[str] = []
TOOL_ERRORS: dict[str, str] = {}
MAX_BODY = 96 * 1024 * 1024
VERBOSE = False

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".json")


def route(spec: str):
    """Dekorator dla wbudowanych tras: @route("GET /api/x")."""
    def deco(fn):
        register(spec, fn)
        return fn
    return deco


def register(spec: str, fn, source: str = "core") -> None:
    parts = spec.split(None, 1)
    method, path = (parts[0].upper(), parts[1]) if len(parts) == 2 else ("POST", parts[0])
    if "<" in path:
        rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", path) + "$")
        PATTERN_ROUTES.append((method, rx, fn))
        return
    key = (method, path)
    if key in ROUTES and VERBOSE:
        print(f"[oiforge] trasa {method} {path} nadpisana przez {source}", file=sys.stderr)
    ROUTES[key] = fn


def discover_tools() -> None:
    from . import tools as tools_pkg
    for mod in sorted(pkgutil.iter_modules(tools_pkg.__path__), key=lambda m: m.name):
        if mod.name.startswith("_"):
            continue
        name = f"oiforge.tools.{mod.name}"
        try:
            m = importlib.import_module(name)
        except Exception:  # noqa: BLE001
            TOOL_ERRORS[mod.name] = traceback.format_exc()
            print(f"[oiforge] BŁĄD importu modułu {mod.name}:\n{TOOL_ERRORS[mod.name]}", file=sys.stderr)
            continue
        routes = getattr(m, "ROUTES", None)
        if isinstance(routes, dict):
            for spec, fn in routes.items():
                register(spec, fn, source=mod.name)
            TOOL_MODULES.append(mod.name)


# ---------------------------------------------------------------------------------------------- wbudowane API
@route("GET /api/health")
def _health(_):
    return {"version": config.VERSION, "platform": config.PLATFORM, "tools": TOOL_MODULES,
            "tool_errors": {k: v.strip().splitlines()[-1] for k, v in TOOL_ERRORS.items()},
            "kb_articles": len(kb.articles()), "kb_errors": kb.load_errors(), "routes": len(ROUTES) + len(PATTERN_ROUTES)}


@route("GET /api/capabilities")
def _caps(_):
    return {"capabilities": config.capabilities()}


@route("GET /api/views")
def _views(_):
    vdir = config.WEB_DIR / "js" / "views"
    cdir = config.WEB_DIR / "css" / "views"
    out = []
    for p in sorted(vdir.glob("*.js")):
        if p.name.startswith("_"):
            continue
        v = {"id": p.stem, "js": f"/js/views/{p.name}", "v": int(p.stat().st_mtime)}
        css = cdir / f"{p.stem}.css"
        if css.exists():
            v["css"] = f"/css/views/{css.name}"
            v["cv"] = int(css.stat().st_mtime)
        out.append(v)
    return {"views": out}


@route("GET /api/jobs/<job_id>")
def _job(q, job_id):
    j = jobs.get(job_id)
    if not j:
        raise ApiError("Nie ma takiego zadania (mogło wygasnąć).", 404)
    return j.snapshot(int(q.get("since") or 0))


@route("POST /api/jobs/<job_id>/cancel")
def _job_cancel(_, job_id):
    return {"cancelled": jobs.cancel(job_id)}


@route("GET /api/store/get")
def _store_get(q):
    return {"value": store.get(q.get("key", ""), q.get("default"))}


@route("POST /api/store/set")
def _store_set(b):
    store.set(b.get("key", ""), b.get("value"))
    return {}


@route("POST /api/store/delete")
def _store_del(b):
    store.delete(b.get("key", ""))
    return {}


@route("GET /api/settings")
def _settings(_):
    return {"settings": store.settings()}


@route("POST /api/settings")
def _settings_set(b):
    return {"settings": store.update_settings(b.get("patch") or {})}


def _limits(b):
    s = store.settings()
    t = float(b.get("timeout") or s.get("time_limit") or 2.0)
    return max(0.1, min(t, 60.0)), b.get("memory_limit") or s.get("memory_limit")


@route("POST /api/run")
def _run(b):
    code = b.get("code") or ""
    if not code.strip():
        raise ApiError("Brak kodu do uruchomienia.")
    timeout, mem = _limits(b)
    res = runner.run_source(code, b.get("input") or "", lang=b.get("lang"), mode=b.get("mode") or "release",
                            timeout=timeout, std=b.get("std") or store.settings().get("cpp_std", "c++20"),
                            mem_limit_mb=mem, args=b.get("args"))
    res["ok"] = True
    res["success"] = bool(res.get("run")) and res["run"]["verdict"] == "OK"
    _journal_run(res, code)
    return res


def _journal_run(res: dict, source: str) -> None:
    """Dziennik postępów (moduł journal, jeśli jest): uruchomienia i błędy kompilacji."""
    try:
        from .tools import journal
    except Exception:  # noqa: BLE001
        return
    try:
        comp = res.get("compile") or {}
        if not comp.get("ok", True):
            title, kb_id = "Błąd kompilacji", None
            try:
                from .tools import errors as _errors
                items = _errors.translate(comp.get("stderr") or "", source)
                if items:
                    title = items[0].get("title") or title
                    kb_id = items[0].get("kb")
            except Exception:  # noqa: BLE001
                pass
            journal.log_event("compile_error", {"title": title, "kb": kb_id})
        elif res.get("run"):
            journal.log_event("run", {"verdict": res["run"].get("verdict")})
    except Exception:  # noqa: BLE001
        pass


@route("POST /api/compile")
def _compile(b):
    code = b.get("code") or ""
    lang = b.get("lang") or runner.detect_lang(code)
    if lang == "python":
        ok, msg, line = runner.python_syntax(code)
        return {"compile": {"ok": ok, "stderr": msg, "compiler": "python", "errors": 0 if ok else 1, "warnings": 0}}
    cr = runner.compile_cpp(code, mode=b.get("mode") or "release", std=b.get("std") or "c++20")
    return {"compile": cr.to_dict()}


@route("POST /api/cache/clear")
def _cache_clear(b):
    import shutil
    what = b.get("what") or "bin"
    removed = 0
    dirs = {"bin": [runner.BIN_DIR, runner.SRC_DIR, runner.PY_DIR], "pch": [runner.PCH_DIR]}
    for d in dirs.get(what, []) if what != "all" else dirs["bin"] + dirs["pch"]:
        if not d.exists():
            continue
        for p in d.iterdir():
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
                removed += 1
            except OSError:
                pass
    if what in ("pch", "all"):
        runner._pch_state.clear()
    return {"removed": removed}


@route("GET /api/cache/usage")
def _cache_usage(_):
    return {"usage": runner.cache_usage()}


@route("GET /api/kb/articles")
def _kb_articles(q):
    cat = q.get("category")
    arts = [kb.article_meta(a) for a in kb.articles() if not cat or a.get("category") == cat]
    return {"articles": arts, "categories": kb.categories()}


@route("GET /api/kb/article")
def _kb_article(q):
    a = kb.get(q.get("id", ""))
    if not a:
        raise ApiError("Nie znaleziono artykułu.", 404)
    return {"article": {k: v for k, v in a.items() if not k.startswith("_")}}


_kb_index = {"sig": None, "idx": None}
_kb_lock = threading.Lock()


def kb_index():
    """Bazowy indeks bazy wiedzy (moduł asystenta może mieć własny, bogatszy)."""
    from .core.search import SearchIndex
    with _kb_lock:
        sig = kb.signature()
        if _kb_index["sig"] != sig or _kb_index["idx"] is None:
            idx = SearchIndex({"title": 4.0, "aliases": 3.5, "tags": 2.5, "summary": 1.5, "body": 1.0, "qa": 1.6})
            for a in kb.articles():
                body = " ".join(str(b.get("md") or b.get("title") or "") + " " + " ".join(" ".join(r) for r in b.get("rows", []))
                                for b in a.get("body", []) if isinstance(b, dict))
                qa = " ".join(" ".join(q.get("q") if isinstance(q.get("q"), list) else [str(q.get("q"))]) for q in a.get("qa", []) if isinstance(q, dict))
                idx.add(a["id"], {"title": a.get("title", ""), "aliases": " ".join(a.get("aliases", [])),
                                  "tags": " ".join(a.get("tags", [])), "summary": a.get("summary", ""),
                                  "body": body, "qa": qa}, payload=kb.article_meta(a))
            _kb_index.update(sig=sig, idx=idx.build())
        return _kb_index["idx"]


@route("GET /api/kb/search")
def _kb_search(q):
    query = (q.get("q") or "").strip()
    if not query:
        return {"results": []}
    res = kb_index().search(query, k=int(q.get("k") or 12))
    return {"results": [dict(r["payload"], score=r["score"], matched=r["matched"]) for r in res]}


@route("GET /api/snippets")
def _snippets(_):
    return {"snippets": kb.snippets()}


@route("POST /api/snippets/save")
def _snip_save(b):
    sn = b.get("snippet") or {}
    trig = (sn.get("trigger") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", trig):
        raise ApiError("Trigger: 1–32 znaki (litery, cyfry, _ i -).")
    if not (sn.get("code") or "").strip():
        raise ApiError("Snippet nie może być pusty.")
    items = [s for s in (store.get("snippets_user", []) or []) if s.get("trigger") != trig]
    items.append({k: sn.get(k) for k in ("trigger", "name", "code", "description", "scope", "lang", "tags", "usage")})
    store.set("snippets_user", items)
    return {"snippets": kb.snippets()}


@route("POST /api/snippets/delete")
def _snip_del(b):
    trig = b.get("trigger")
    store.set("snippets_user", [s for s in (store.get("snippets_user", []) or []) if s.get("trigger") != trig])
    return {"snippets": kb.snippets()}


# ---------------------------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "OIForge/" + config.VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: N802
        if VERBOSE:
            sys.stderr.write("[http] " + (fmt % args) + "\n")

    # --- bezpieczeństwo: tylko lokalnie, ochrona przed CSRF / DNS rebinding
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1", "") or host == getattr(self.server, "bind_host", "")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            return origin is None
        try:
            o = urlparse(origin)
        except ValueError:
            return False
        return (o.hostname or "").lower() in ("127.0.0.1", "localhost", "::1") and \
            (o.port or 80) == self.server.server_address[1]

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, status: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False, default=_json_default).encode("utf-8")
        extra = {"Cache-Control": "no-store"}
        # duże odpowiedzi (np. ślad debuggera, wyniki testerki) — gzip, jeśli przeglądarka akceptuje
        if len(body) > 64 * 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            import gzip
            body = gzip.compress(body, compresslevel=5)
            extra["Content-Encoding"] = "gzip"
            extra["Vary"] = "Accept-Encoding"
        self._send(status, body, "application/json; charset=utf-8", extra)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "Niedozwolony host."})
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            return self._dispatch("GET", url.path, q)
        return self._static(url.path)

    def do_POST(self):  # noqa: N802
        if not self._host_ok() or not self._origin_ok():
            return self._json(403, {"ok": False, "error": "Żądanie z niedozwolonego źródła."})
        if self.headers.get("X-OIForge") != "1":
            return self._json(403, {"ok": False, "error": "Brak nagłówka X-OIForge: 1."})
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(413, {"ok": False, "error": "Za duże żądanie."})
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"ok": False, "error": "Niepoprawny JSON."})
        if not isinstance(body, dict):
            body = {"data": body}
        return self._dispatch("POST", url.path, body)

    def _dispatch(self, method: str, path: str, data: dict):
        fn = ROUTES.get((method, path))
        kwargs = {}
        if fn is None:
            for m, rx, f in PATTERN_ROUTES:
                if m == method:
                    mm = rx.match(path)
                    if mm:
                        fn, kwargs = f, mm.groupdict()
                        break
        if fn is None:
            return self._json(404, {"ok": False, "error": f"Nieznany endpoint {method} {path}"})
        t0 = time.perf_counter()
        try:
            res = fn(data, **kwargs) if kwargs else fn(data)
            if res is None:
                res = {}
            if isinstance(res, dict):
                res.setdefault("ok", True)
            else:
                res = {"ok": True, "data": res}
            if VERBOSE:
                print(f"[api] {method} {path} {1000 * (time.perf_counter() - t0):.0f} ms", file=sys.stderr)
            return self._json(200, res)
        except ApiError as e:
            return self._json(e.status, {"ok": False, "error": e.message, **e.extra})
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            print(f"[oiforge] wyjątek w {method} {path}:\n{tb}", file=sys.stderr)
            return self._json(500, {"ok": False, "error": f"Błąd wewnętrzny: {e.__class__.__name__}: {e}", "trace": tb})

    def _static(self, path: str):
        path = unquote(path)
        if path in ("", "/"):
            path = "/index.html"
        target = (config.WEB_DIR / path.lstrip("/")).resolve()
        try:
            target.relative_to(config.WEB_DIR.resolve())
        except ValueError:
            return self._json(403, {"ok": False, "error": "Zabroniona ścieżka."})
        if not target.is_file():
            if "." not in Path(path).name:      # trasy SPA
                target = config.WEB_DIR / "index.html"
            else:
                return self._send(404, b"Not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        cache = "public, max-age=604800, immutable" if "/vendor/" in str(target) else "no-cache"
        try:
            data = target.read_bytes()
        except OSError:
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        self._send(200, data, ctype, {"Cache-Control": cache})


def _json_default(o):
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, bytes):
        return o.decode("utf-8", "replace")
    return str(o)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


def make_server(host: str = "127.0.0.1", port: int = config.DEFAULT_PORT, tries: int = 20) -> Server:
    if not TOOL_MODULES and not TOOL_ERRORS:
        discover_tools()
    last = None
    for p in range(port, port + max(1, tries)):
        try:
            srv = Server((host, p), Handler)
            srv.bind_host = host
            return srv
        except OSError as e:
            last = e
    raise OSError(f"Nie udało się uruchomić serwera na portach {port}–{port + tries - 1}: {last}")
