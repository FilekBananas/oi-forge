"""Trwały magazyn JSON (user_data/store/<klucz>.json) i ustawienia aplikacji."""
from __future__ import annotations

import copy
import json
import os
import re
import threading

from .. import config

_lock = threading.RLock()
_KEY_RE = re.compile(r"^[a-z0-9_.\-]{1,80}$")

DEFAULT_SETTINGS: dict = {
    "theme": "dark",                 # dark | light | system
    "accent": "violet",              # violet | blue | emerald | amber | rose | cyan
    "density": "comfortable",        # comfortable | compact
    "cpp_std": "c++20",
    "time_limit": 2.0,               # s, domyślny limit uruchomień
    "memory_limit": 256,             # MB
    "editor": {"font_size": 14, "tab_size": 4, "line_wrap": False, "vim": False, "snippets": True,
               "auto_format_on_save": False},
    "format_style": {"preset": "moja"},
    "tester": {"tests": 1000, "workers": 0, "stop_on_first": True, "float_eps": None},
    "sio2": {"base_url": ""},        # puste = adres, z którego już masz programy, albo Szkopuł
    "flashcards": {"new_per_day": 15},
    "user": {"name": ""},
}


def _path(key: str):
    if not _KEY_RE.match(key or ""):
        raise ValueError(f"Niepoprawny klucz magazynu: {key!r}")
    return config.STORE_DIR / f"{key}.json"


def get(key: str, default=None):
    p = _path(key)
    with _lock:
        if not p.exists():
            return copy.deepcopy(default)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return copy.deepcopy(default)


def set(key: str, value) -> None:  # noqa: A001 - świadomie jak w kontrakcie
    p = _path(key)
    with _lock:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)


def delete(key: str) -> None:
    p = _path(key)
    with _lock:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def keys(prefix: str = "") -> list[str]:
    return sorted(p.stem for p in config.STORE_DIR.glob("*.json") if p.stem.startswith(prefix))


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def settings() -> dict:
    return _merge(DEFAULT_SETTINGS, get("settings", {}) or {})


def update_settings(patch: dict) -> dict:
    with _lock:
        cur = get("settings", {}) or {}
        new = _merge(cur, patch or {})
        set("settings", new)
        return _merge(DEFAULT_SETTINGS, new)
