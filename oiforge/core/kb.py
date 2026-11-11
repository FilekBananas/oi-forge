"""Ładowanie bazy wiedzy (data/kb/*.json) i snippetów (data/snippets/*.json + snippety użytkownika)."""
from __future__ import annotations

import hashlib
import json
import threading

from .. import config
from . import store

KB_DIR = config.DATA_DIR / "kb"
SNIP_DIR = config.DATA_DIR / "snippets"
_lock = threading.Lock()
_cache: dict = {"sig": None, "articles": [], "by_id": {}, "errors": []}
_snip_cache: dict = {"sig": None, "items": []}


def _signature(folder) -> tuple:
    try:
        return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in folder.glob("*.json")))
    except OSError:
        return ()


def _content_signature(folder) -> tuple:
    """Podpis zależny od TREŚCI plików (sha1) — samo dotknięcie pliku nie unieważnia indeksów/sieci."""
    out = []
    for p in sorted(folder.glob("*.json")):
        try:
            st = p.stat()
            k = (p.name, st.st_mtime_ns, st.st_size)
            h = _hash_cache.get(k)
            if h is None:
                h = hashlib.sha1(p.read_bytes()).hexdigest()[:16]
                _hash_cache[k] = h
            out.append((p.name, h))
        except OSError:
            continue
    return tuple(out)


_hash_cache: dict = {}


def _load() -> None:
    msig = _signature(KB_DIR)
    if msig == _cache.get("msig"):
        return
    sig = _content_signature(KB_DIR)
    _cache["msig"] = msig
    if sig == _cache["sig"]:
        return
    arts, by_id, errors = [], {}, []
    for p in sorted(KB_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{p.name}: {e}")
            continue
        for a in data.get("articles", []) if isinstance(data, dict) else []:
            if not isinstance(a, dict) or not a.get("id"):
                continue
            a = dict(a)
            a["_file"] = p.name
            if a["id"] in by_id:
                errors.append(f"{p.name}: duplikat id {a['id']}")
                continue
            by_id[a["id"]] = a
            arts.append(a)
    _cache.update(sig=sig, articles=arts, by_id=by_id, errors=errors)


def articles() -> list[dict]:
    with _lock:
        _load()
        return _cache["articles"]


def get(article_id: str) -> dict | None:
    with _lock:
        _load()
        return _cache["by_id"].get(article_id)


def load_errors() -> list[str]:
    with _lock:
        _load()
        return list(_cache["errors"])


def signature() -> tuple:
    """Zmienia się, gdy zmieni się którykolwiek plik bazy — do unieważniania indeksów."""
    with _lock:
        _load()
        return _cache["sig"]


def categories() -> dict[str, int]:
    out: dict[str, int] = {}
    for a in articles():
        out[a.get("category", "Inne")] = out.get(a.get("category", "Inne"), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def article_meta(a: dict) -> dict:
    return {k: a.get(k) for k in ("id", "title", "category", "summary", "tags", "level", "complexity")} | {
        "qa_count": len(a.get("qa") or [])}


def default_snippets() -> list[dict]:
    sig = _signature(SNIP_DIR)
    with _lock:
        if sig != _snip_cache["sig"]:
            items = []
            for p in sorted(SNIP_DIR.glob("*.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    for s in data.get("snippets", []):
                        if isinstance(s, dict) and s.get("trigger") and s.get("code"):
                            items.append(dict(s, source="builtin"))
                except (OSError, json.JSONDecodeError):
                    continue
            _snip_cache.update(sig=sig, items=items)
        return list(_snip_cache["items"])


def user_snippets() -> list[dict]:
    return [dict(s, source="user") for s in (store.get("snippets_user", []) or []) if isinstance(s, dict)]


def snippets() -> list[dict]:
    """Snippety użytkownika nadpisują wbudowane o tym samym triggerze."""
    merged: dict[str, dict] = {}
    for s in default_snippets():
        merged[s["trigger"]] = s
    for s in user_snippets():
        if s.get("trigger"):
            merged[s["trigger"]] = s
    return sorted(merged.values(), key=lambda s: s["trigger"])
