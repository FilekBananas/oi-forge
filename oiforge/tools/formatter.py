"""Formatter i upiększacz kodu C++ (bez clang-format, bez AI) + „naucz się mojego stylu”.

Python:  `format_code(code, style=None) -> str`  (style=None → zapisany styl użytkownika z ustawień),
         `resolve_style(style) -> dict`, `beautify(code, transforms, style) -> dict`, `learn_style(codes) -> dict`.
HTTP:    POST /api/format {code, style?, diff?} -> {code, changed, warnings, diff?}
         GET  /api/format/presets              -> {presets, schema, current, active}
         POST /api/format/style {style|preset}  -> zapis stylu „moja” / wybór aktywnego presetu
         POST /api/format/learn {save?, async?} -> styl wywnioskowany z user_data/programs (sync albo job)
         POST /api/beautify {code, transforms, style?} -> {code, changed, changes, diff, warnings}
         GET  /api/beautify/options            -> lista przekształceń upiększacza
"""
from __future__ import annotations

import difflib
import json
import re
import time
from collections import Counter

from .. import config
from ..core import jobs, store
from ..server_api import ApiError
from . import _fmt_engine as E

STYLE_SCHEMA = E.STYLE_SCHEMA
PRESETS = E.PRESETS
MAX_CODE = 2_000_000


# ============================================================================================ style
def _format_settings() -> dict:
    try:
        fs = store.settings().get("format_style") or {}
    except Exception:  # noqa: BLE001
        fs = {}
    if isinstance(fs, str):
        fs = {"preset": fs}
    return fs if isinstance(fs, dict) else {}


def preset_id(name) -> str | None:
    """Przyjmuje id ('google'), etykietę ('Google', 'Kompaktowy CP') albo dowolną wielkość liter."""
    if not name:
        return None
    s = str(name).strip().lower()
    if s in PRESETS:
        return s
    for pid, p in PRESETS.items():
        if p["label"].lower() == s:
            return pid
    aliases = {"k&r": "kr", "knr": "kr", "kompaktowy": "compact", "cp": "compact", "mój": "moja", "moj": "moja",
               "my": "moja", "mine": "moja"}
    return aliases.get(s)


def preset_options(pid: str, fs: dict | None = None) -> dict:
    o = dict(E.BASE_STYLE)
    o.update(PRESETS.get(pid, PRESETS["moja"])["options"])
    if pid == "moja":
        fs = _format_settings() if fs is None else fs
        mine = fs.get("moja") or fs.get("options") or {}
        if isinstance(mine, dict):
            o.update({k: v for k, v in mine.items() if k in E.BASE_STYLE})
    return E.normalize_options(o)


def resolve_style(style=None) -> dict:
    """Pełny słownik opcji stylu. None → styl użytkownika; str → preset; dict → preset + nadpisania."""
    fs = _format_settings()
    if style is None or style == "" or style == {}:
        return preset_options(preset_id(fs.get("preset")) or "moja", fs)
    if isinstance(style, str):
        return preset_options(preset_id(style) or "moja", fs)
    if isinstance(style, dict):
        pid = preset_id(style.get("preset"))
        base = preset_options(pid, fs) if pid else resolve_style(None)
        over = dict(style.get("options") or {})
        over.update({k: v for k, v in style.items() if k in E.BASE_STYLE})
        base.update(over)
        return E.normalize_options(base)
    return resolve_style(None)


def format_code(code: str, style=None) -> str:
    """Formatuje kod C++ wg stylu (None = styl użytkownika). Przy problemie zwraca kod bez zmian."""
    out, _ = E.format_source(code or "", resolve_style(style))
    return out


def format_with_report(code: str, style=None) -> tuple[str, list[str]]:
    return E.format_source(code or "", resolve_style(style))


# ============================================================================================ diff
def line_diff(a: str, b: str, context: int | None = None) -> list[dict]:
    """Diff liniowy: [{type: eq|add|del, a: nr_linii_a|None, b: nr_linii_b|None, text}]."""
    la, lb = a.replace("\r\n", "\n").split("\n"), b.replace("\r\n", "\n").split("\n")
    if la and la[-1] == "":
        la.pop()
    if lb and lb[-1] == "":
        lb.pop()
    out = []
    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                out.append({"type": "eq", "a": i1 + k + 1, "b": j1 + k + 1, "text": la[i1 + k]})
        else:
            for k in range(i1, i2):
                out.append({"type": "del", "a": k + 1, "b": None, "text": la[k]})
            for k in range(j1, j2):
                out.append({"type": "add", "a": None, "b": k + 1, "text": lb[k]})
    if context is not None:
        keep = set()
        for idx, d in enumerate(out):
            if d["type"] != "eq":
                keep.update(range(max(0, idx - context), min(len(out), idx + context + 1)))
        res, last = [], -1
        for idx in sorted(keep):
            if last >= 0 and idx != last + 1:
                res.append({"type": "skip", "a": None, "b": None, "text": "…"})
            res.append(out[idx])
            last = idx
        out = res
    return out


def diff_stats(diff: list[dict]) -> dict:
    c = Counter(d["type"] for d in diff)
    return {"added": c.get("add", 0), "removed": c.get("del", 0)}


# ============================================================================================ nauka stylu
def _mode(counter: Counter, default):
    if not counter:
        return default
    return counter.most_common(1)[0][0]


def _ratio(yes: int, no: int) -> float:
    return yes / (yes + no) if yes + no else 0.0


class _StyleStats:
    def __init__(self):
        self.c: dict[str, Counter] = {}

    def add(self, key: str, value, n: int = 1):
        self.c.setdefault(key, Counter())[value] += n

    def get(self, key: str) -> Counter:
        return self.c.get(key, Counter())


def _gap(code: str, a, b) -> int | None:
    """Liczba spacji między tokenami a i b w oryginale; None, gdy są w różnych liniach."""
    s = code[a.pos + len(a.text):b.pos]
    if "\n" in s:
        return None
    return len(s.replace("\t", "    "))


def _line_indent(code: str, pos: int) -> str:
    ls = code.rfind("\n", 0, pos) + 1
    m = re.match(r"[ \t]*", code[ls:])
    return m.group(0)


def _analyze_program(code: str, st: _StyleStats) -> None:
    code = code.replace("\r\n", "\n")
    f = E._Fmt(code, dict(E.BASE_STYLE))
    f.parse()
    A, T, S = f.A, f.T, f.S
    n = f.n

    def sig_gap(p, q):
        if S[q] != S[p] + 1:          # komentarz pomiędzy
            return None
        return _gap(code, T[p], T[q])

    # --- wcięcia: zawartość bloków względem linii nagłówka
    for p in range(n):
        if f.is_p(p, "{") and f.bkind[p] in ("ctrl", "func", "class", "block", "lambda", "case") and p + 1 < n:
            c = f.pairs.get(p)
            q = p + 1
            if c is None or q >= c or f.nlb[S[q]] == 0 or T[q].kind == "pp":
                continue
            head_ind = _line_indent(code, T[p].pos)
            if f.nlb[S[p]] > 0 and f.bkind[p] != "block" and p > 0:
                head_ind = _line_indent(code, T[p - 1].pos)      # Allman/GNU: nagłówek linię wyżej
            ind = _line_indent(code, T[q].pos)
            if "\t" in ind:
                st.add("tabs", True)
                continue
            st.add("tabs", False)
            if "\t" in head_ind:
                continue
            d = len(ind) - len(head_ind)
            if 1 <= d <= 8:
                st.add("indent", d)
    # --- klamry
    for p in range(n):
        if not f.is_p(p, "{") or p == 0:
            continue
        bk = f.bkind[p]
        if bk not in ("ctrl", "func", "class", "ns"):
            continue
        prev = p - 1
        own = f.nlb[S[p]] > 0 and S[prev] == S[p] - 1
        if f.nlb[S[p]] == 0 and S[prev] != S[p] - 1:
            continue
        kind = "func" if bk == "func" else ("class" if bk in ("class", "ns") else "ctrl")
        st.add("brace_" + kind, "own" if own else "attach")
        if own:
            hi = len(_line_indent(code, T[prev].pos).replace("\t", "    "))
            bi = len(_line_indent(code, T[p].pos).replace("\t", "    "))
            st.add("brace_indented", bi > hi)
        else:
            g = sig_gap(prev, p)
            if g is not None:
                st.add("space_before_brace", g > 0)
    # --- else po }
    for p in range(1, n):
        if T[p].kind == "kw" and T[p].text in ("else", "catch") and f.is_p(p - 1, "}") and f.bkind[p - 1] in ("ctrl",):
            if S[p] == S[p - 1] + 1:
                st.add("else_newline", f.nlb[S[p]] > 0)
    # --- spacje
    for p in range(n - 1):
        t = T[p]
        r = f.role[p]
        if t.kind == "kw" and t.text in ("if", "for", "while", "switch", "catch") and f.is_p(p + 1, "("):
            g = sig_gap(p, p + 1)
            if g is not None:
                st.add("space_after_keyword", g > 0)
        if t.kind == "kw" and t.text == "template" and f.is_op(p + 1, "<"):
            g = sig_gap(p, p + 1)
            if g is not None:
                st.add("space_after_template", g > 0)
        if t.kind == "punct" and t.text == "," and not f.is_p(p + 1, ")"):
            g = sig_gap(p, p + 1)
            if g is not None:
                st.add("space_after_comma", g > 0)
        if r == "for_semi" and not f.is_p(p + 1, ";") and not f.is_p(p + 1, ")"):
            g = sig_gap(p, p + 1)
            if g is not None:
                st.add("space_for_semicolon", g > 0)
        if t.kind == "punct" and t.text == "(" and not f.is_p(p + 1, ")") and r != "opname":
            g = sig_gap(p, p + 1)
            if g is not None:
                st.add("space_inside_parens", g > 0)
        if t.kind == "punct" and t.text == "{" and f.bkind[p] == "init" and not f.is_p(p + 1, "}"):
            g = sig_gap(p, p + 1)
            if g is not None:
                st.add("space_inside_braces", g > 0)
        if p == 0:
            continue
        if t.kind == "op" and r in ("bin", "range", "ternary") and t.text != ":" or (t.kind == "op" and r in ("range",)):
            g1, g2 = sig_gap(p - 1, p), sig_gap(p, p + 1)
            if g1 is None or g2 is None:
                continue
            key = {"range": "space_range_colon", "ternary": "space_ternary"}.get(r) or {
                "assign": "space_assign", "arith": "space_arith", "compare": "space_compare",
                "logical": "space_logical", "stream": "space_stream"}.get(f.cat[p])
            if key and (g1 > 0) == (g2 > 0):
                st.add(key, g1 > 0)
        if r == "ptr" and t.text in ("*", "&"):
            g1, g2 = sig_gap(p - 1, p), sig_gap(p, p + 1)
            if g1 is None or g2 is None or (g1 > 0) == (g2 > 0):
                continue
            st.add("pointer_align", "left" if g2 > 0 else "right")
    # --- komentarze na końcu linii
    for k, t in enumerate(A):
        if t.kind == "comment" and t.text.startswith("//") and k > 0 and f.nlb[k] == 0:
            g = _gap(code, A[k - 1], t)
            if g is not None and 1 <= g <= 4:
                st.add("trailing_comment_spaces", g)
    # --- krótkie if-y i bloki (wg regionów parsera)
    for rg in f.regions:
        if rg["kind"] == "if":
            st.add("short_if", f.nlb[S[rg["first"]]] == 0)
        elif rg["kind"] == "block" and rg.get("what") in ("ctrl", "func"):
            one = not any(f.nlb[S[q]] for q in range(rg["first"] + 1, rg["last"] + 1))
            st.add("short_blocks", one)
        elif rg["kind"] == "case":
            st.add("short_case", True)
    # --- case względem switch, public: względem struct
    for p in range(n):
        t = T[p]
        if t.kind == "kw" and t.text == "switch" and f.is_p(p + 1, "("):
            c = f.pairs.get(p + 1)
            if c is None or not f.is_p(c + 1, "{"):
                continue
            sw = len(_line_indent(code, t.pos).replace("\t", "    "))
            close = f.pairs.get(c + 1)
            for q in range(c + 2, close or c + 2):
                if T[q].kind == "kw" and T[q].text in ("case", "default") and f.nlb[S[q]] > 0:
                    st.add("indent_case", len(_line_indent(code, T[q].pos).replace("\t", "    ")) > sw)
                    break
        if t.kind == "kw" and t.text in ("public", "private", "protected") and f.is_op(p + 1, ":") and f.nlb[S[p]] > 0:
            st.add("access_seen", True)
            # porównaj z kolumną linii otwierającej klasę
            q = p - 1
            while q >= 0 and not (f.is_p(q, "{") and f.bkind[q] == "class"):
                q -= 1
            if q >= 0:
                cl = len(_line_indent(code, T[q].pos).replace("\t", "    "))
                ac = len(_line_indent(code, t.pos).replace("\t", "    "))
                st.add("access_offset", "outdent" if ac <= cl else ("indent" if ac >= cl + 3 else "half"))
    # --- preprocesor
    for p in range(n):
        if T[p].kind == "pp":
            st.add("pp_left", T[p].col == 1)
    # --- puste linie
    run = 0
    for ln in code.split("\n"):
        if ln.strip() == "":
            run += 1
        else:
            if run:
                st.add("blank_run", run)
            run = 0
    for first, last in f.defs:
        if first > 0 and last is not None:
            k = S[first]
            while k > 0 and A[k - 1].kind == "comment" and f.nlb[k] == 1:
                k -= 1
            st.add("separate_definitions", f.nlb[k] >= 2)


_LABELS = {s["key"]: s["label"] for s in STYLE_SCHEMA}


def learn_style(codes: list[str], job=None) -> dict:
    """Wnioskuje styl z listy programów. Zwraca {options, evidence, programs}."""
    st = _StyleStats()
    used = 0
    for i, code in enumerate(codes):
        if job is not None:
            if job.cancelled:
                break
            if i % 10 == 0:
                job.progress(i / max(1, len(codes)), f"Analiza programu {i + 1}/{len(codes)}")
        if not code or not code.strip():
            continue
        try:
            _analyze_program(code, st)
            used += 1
        except Exception:  # noqa: BLE001 - pojedynczy dziwny plik nie psuje nauki
            continue
    o = dict(E.BASE_STYLE)
    ev = []

    def note(key, value, yes, total, extra=""):
        o[key] = value
        pct = round(100 * yes / total) if total else 0
        ev.append({"key": key, "label": _LABELS.get(key, key), "value": value,
                   "detail": (f"{pct}% przypadków ({yes}/{total})" if total else "brak danych — wartość domyślna") + extra})

    def bool_opt(key, src=None, threshold=0.5):
        c = st.get(src or key)
        yes, no = c.get(True, 0), c.get(False, 0)
        if yes + no == 0:
            note(key, E.BASE_STYLE[key], 0, 0)
            return
        val = _ratio(yes, no) >= threshold
        note(key, val, yes if val else no, yes + no)

    tabs = st.get("tabs")
    use_tabs = tabs.get(True, 0) > tabs.get(False, 0)
    note("use_tabs", use_tabs, tabs.get(use_tabs, 0), sum(tabs.values()))
    ind = st.get("indent")
    if ind:
        w = _mode(ind, 4)
        note("indent_width", int(w), ind[w], sum(ind.values()))
    else:
        note("indent_width", 4, 0, 0)
    # klamry
    bf, bc, bcl = st.get("brace_func"), st.get("brace_ctrl"), st.get("brace_class")
    f_own = _ratio(bf.get("own", 0), bf.get("attach", 0)) > 0.5
    c_own = _ratio(bc.get("own", 0), bc.get("attach", 0)) > 0.5
    en = st.get("else_newline")
    else_nl = _ratio(en.get(True, 0), en.get(False, 0)) > 0.5
    indented = st.get("brace_indented")
    if c_own and f_own:
        bs = "gnu" if _ratio(indented.get(True, 0), indented.get(False, 0)) > 0.5 else "allman"
    elif f_own and not c_own:
        bs = "stroustrup" if else_nl else "kr"
    else:
        bs = "attach"
    tot = sum(bf.values()) + sum(bc.values()) + sum(bcl.values())
    agree = (bf.get("own" if f_own else "attach", 0) + bc.get("own" if c_own else "attach", 0) +
             bcl.get("own" if (c_own and f_own) else "attach", 0))
    note("brace_style", bs, agree, tot,
         f"; funkcje: {bf.get('own', 0)} w nowej linii / {bf.get('attach', 0)} przyklejonych, "
         f"if/for/while: {bc.get('own', 0)} / {bc.get('attach', 0)}")
    if bs in ("allman", "gnu", "stroustrup"):
        else_nl = True
    note("else_newline", else_nl, en.get(else_nl, 0), sum(en.values()))
    for key in ("space_after_keyword", "space_before_brace", "space_assign", "space_arith", "space_compare",
                "space_logical", "space_ternary", "space_stream", "space_after_comma", "space_for_semicolon",
                "space_range_colon", "space_after_template", "space_inside_parens", "space_inside_braces"):
        thr = 0.5 if key not in ("space_inside_parens", "space_inside_braces") else 0.6
        bool_opt(key, threshold=thr)
    pa = st.get("pointer_align")
    if pa:
        v = _mode(pa, "left")
        note("pointer_align", v, pa[v], sum(pa.values()))
    else:
        note("pointer_align", "left", 0, 0)
    tc = st.get("trailing_comment_spaces")
    if tc:
        v = _mode(tc, 1)
        note("trailing_comment_spaces", int(v), tc[v], sum(tc.values()))
    else:
        note("trailing_comment_spaces", 1, 0, 0)
    si = st.get("short_if")
    yes, no = si.get(True, 0), si.get(False, 0)
    v = "keep" if yes and _ratio(yes, no) >= 0.15 else ("never" if yes + no else "keep")
    note("short_if", v, yes if v == "keep" else no, yes + no, " w jednej linii" if v == "keep" else " w dwóch liniach")
    sb = st.get("short_blocks")
    yes, no = sb.get(True, 0), sb.get(False, 0)
    v = "keep" if yes and _ratio(yes, no) >= 0.15 else ("never" if yes + no else "keep")
    note("short_blocks", v, yes if v == "keep" else no, yes + no)
    bool_opt("indent_case")
    ao = st.get("access_offset")
    if ao:
        v = _mode(ao, "outdent")
        note("access_offset", v, ao[v], sum(ao.values()))
    else:
        note("access_offset", "outdent", 0, 0)
    bool_opt("pp_left", threshold=0.5)
    br = st.get("blank_run")
    if br:
        total = sum(br.values())
        acc, k = 0, 1
        for k in sorted(br):
            acc += br[k]
            if acc / total >= 0.9:
                break
        k = max(1, min(2, k))
        note("max_empty_lines", k, sum(v for kk, v in br.items() if kk <= k), total)
    else:
        note("max_empty_lines", 1, 0, 0)
    bool_opt("separate_definitions", threshold=0.7)
    o["indent_namespace"] = False
    o["max_line_length"] = 0
    o["keep_block_start_blank"] = False
    return {"options": E.normalize_options(o), "evidence": ev, "programs": used}


def _user_programs() -> list[str]:
    root = config.PROGRAMS_DIR
    idx = root / "index.json"
    codes = []
    if idx.exists():
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            items = data.get("programs", data) if isinstance(data, dict) else data
            for rec in items or []:
                if not isinstance(rec, dict) or (rec.get("lang") or "cpp") != "cpp":
                    continue
                path = root / str(rec.get("path", ""))
                try:
                    path.resolve().relative_to(root.resolve())
                except ValueError:
                    continue
                if path.is_file():
                    codes.append(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            pass
    if not codes and root.exists():
        for path in sorted(root.rglob("*.cpp"))[:2000]:
            try:
                codes.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return codes


def save_my_style(options: dict, meta: dict | None = None) -> dict:
    fs = _format_settings()
    new = {"preset": "moja", "moja": E.normalize_options(options)}
    if meta:
        new["learned"] = meta
    elif fs.get("learned"):
        new["learned"] = fs["learned"]
    # zachowaj nieznane klucze innych modułów
    for k, v in fs.items():
        new.setdefault(k, v)
    new["preset"] = "moja"
    new["moja"] = E.normalize_options(options)
    store.update_settings({"format_style": new})
    return new


def _learn_work(job, body):
    codes = body.get("codes") if isinstance(body.get("codes"), list) else _user_programs()
    if not codes:
        raise ApiError("Brak programów do analizy — zaimportuj swoje rozwiązania w widoku „Moje programy”.")
    res = learn_style([str(c) for c in codes], job=job)
    if body.get("save", True):
        save_my_style(res["options"], {"at": time.strftime("%Y-%m-%d %H:%M"), "programs": res["programs"]})
        res["saved"] = True
    else:
        res["saved"] = False
    return res


# ============================================================================================ upiększacz
TRANSFORMS = [
    {"id": "remove_banner", "label": "Usuń baner „Online C++ Compiler”", "default": True,
     "description": "Kasuje komentarz-szablon wstawiany przez OnlineGDB na początku pliku."},
    {"id": "remove_dead_code", "label": "Usuń zakomentowany kod", "default": False,
     "description": "Komentarze, które wyglądają jak kod (np. `//cout << x << endl;`), znikają. Opisy słowne zostają."},
    {"id": "sort_includes", "label": "Posortuj i usuń powtórzone #include", "default": True,
     "description": "Najpierw <bits/stdc++.h>, potem nagłówki systemowe alfabetycznie, na końcu \"lokalne\"."},
    {"id": "bits_stdcpp", "label": "Zastąp nagłówki standardowe jednym <bits/stdc++.h>", "default": False,
     "description": "Gdy jest kilka standardowych #include (albo już jest bits/stdc++.h) — zostaje jeden."},
    {"id": "namespace_std", "label": "using namespace std", "default": "off", "type": "choice",
     "choices": [["off", "Bez zmian"], ["add", "Dodaj i usuń std::"], ["remove", "Usuń i dopisz std::"]],
     "description": "„Dodaj” wstawia `using namespace std;` i skraca `std::cout` → `cout`; „Usuń” robi odwrotnie."},
    {"id": "endl", "label": "endl → '\\n'", "default": False,
     "description": "`endl` opróżnia bufor przy każdym wypisaniu — `'\\n'` jest dużo szybsze przy dużym wyjściu."},
    {"id": "braces", "label": "Klamry przy jednolinijkowych if/for/while", "default": False,
     "description": "`if (x) y;` → `if (x) { y; }` — łatwiej potem dopisać drugą instrukcję."},
    {"id": "function_spacing", "label": "Pusta linia między funkcjami", "default": True,
     "description": "Czytelne odstępy między definicjami funkcji i struktur."},
    {"id": "blank_lines", "label": "Normalizuj puste linie", "default": True,
     "description": "Maks. tyle pustych linii z rzędu, ile w stylu; bez pustych linii na początku i końcu pliku."},
    {"id": "trailing_ws", "label": "Usuń spacje na końcach linii", "default": True,
     "description": "Niewidoczne spacje i tabulatory na końcach linii (raw stringi nietknięte)."},
    {"id": "format", "label": "Sformatuj wg stylu", "default": True,
     "description": "Wcięcia, klamry i spacje wg wybranego stylu (jak Ctrl+Shift+F)."},
]
TRANSFORM_IDS = [t["id"] for t in TRANSFORMS]

STD_HEADERS = set("""
algorithm any array atomic bit bitset cassert cctype cerrno cfloat charconv chrono climits clocale cmath codecvt
compare complex concepts condition_variable csetjmp csignal cstdarg cstddef cstdint cstdio cstdlib cstring ctime
cuchar cwchar cwctype deque exception execution filesystem forward_list fstream functional future initializer_list
iomanip ios iosfwd iostream istream iterator limits list locale map memory memory_resource mutex new numbers
numeric optional ostream queue random ranges ratio regex scoped_allocator set shared_mutex span sstream stack
stdexcept streambuf string string_view system_error thread tuple type_traits typeindex typeinfo unordered_map
unordered_set utility valarray variant vector version assert.h ctype.h math.h stdio.h stdlib.h string.h time.h
limits.h stdint.h float.h source_location format barrier latch semaphore stop_token syncstream bits/stdc++.h
""".split())
STD_NAMES = set("""
cout cin cerr clog endl flush vector string map set multiset multimap unordered_map unordered_set unordered_multimap
unordered_multiset pair make_pair tuple make_tuple tie queue priority_queue stack deque list array bitset sort
stable_sort reverse unique lower_bound upper_bound equal_range binary_search min max swap min_element max_element
accumulate fill fill_n iota next_permutation prev_permutation greater less greater_equal less_equal gcd lcm to_string
stoi stol stoll stoul stoull stod stof getline ios_base ios numeric_limits function move begin end find find_if count
count_if all_of any_of none_of for_each transform partial_sum nth_element partial_sort merge rotate shuffle
fixed setprecision setw setfill boolalpha mt19937 mt19937_64 uniform_int_distribution uniform_real_distribution
random_device istringstream ostringstream stringstream ifstream ofstream string_view optional variant
unique_ptr shared_ptr make_unique make_shared chrono distance advance prev next back_inserter inserter exchange
memcmp_unused iter_swap max_element minmax minmax_element clamp hash popcount
""".split())


def _sig_tokens(code: str):
    return [t for t in E.tokenize(code) if t.kind != "comment"]


def _apply_edits(code: str, edits: list[tuple[int, int, str]]) -> str:
    """edits = [(start, end, replacement)] na pozycjach oryginału, niezachodzące."""
    out = []
    last = 0
    for s, e, r in sorted(edits, key=lambda x: (x[0], x[1])):
        if s < last:
            continue
        out.append(code[last:s])
        out.append(r)
        last = e
    out.append(code[last:])
    return "".join(out)


_BANNER_RE = re.compile(r"/\*{5,}[^*]*?Online C\+\+ Compiler.*?\*{5,}/[ \t]*\n?", re.S)


def t_remove_banner(code: str):
    new, n = _BANNER_RE.subn("", code, count=1)
    if n:
        new = new.lstrip("\n")
    return new, n


_PROSE_END = re.compile(r"[.!?]$")
_CODE_START = re.compile(r"^(for|if|while|return|int|long|cout|cin|printf|scanf|else|auto|vector|sort|break|continue|"
                         r"#\s*include|#\s*define|bool|char|double|string|void|ll|const|switch|case|do|cerr|"
                         r"[A-Za-z_]\w*(\[[^\]]*\])*\s*(=|\+=|-=|\+\+|--|\.|->)|[A-Za-z_]\w*\s*\()")


def looks_like_code(s: str) -> bool:
    """Heurystyka: czy treść komentarza wygląda jak (zakomentowany) kod C++?"""
    s = s.strip()
    if len(s) < 3:
        return False
    bare = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', s)
    if re.search(r"[^\x00-\x7f]", bare):
        return False
    if re.match(r"^(TODO|FIXME|NOTE|XXX|UWAGA|HACK)\b", bare, re.I):
        return False
    try:
        toks = E.tokenize(bare)
    except Exception:  # noqa: BLE001
        return False
    if not toks or any(t.kind == "op" and t.text in ("@", "`", "$") for t in toks):
        return False
    score = 0
    if bare.endswith(";"):
        score += 3
    elif bare.endswith(("{", "}", ")")):
        score += 2
    if _CODE_START.match(bare):
        score += 2
    if re.search(r"\b(cout|cerr)\s*<<|\bcin\s*>>", bare):
        score += 2
    ops = sum(1 for t in toks if t.kind == "op" and t.text in ("=", "==", "++", "--", "+=", "-=", "->", "::", "<<", ">>",
                                                                "&&", "||", "!=", "<=", ">="))
    score += min(3, ops)
    if any(t.kind == "punct" and t.text == "[" for t in toks):
        score += 1
    # proza: ≥3 słowa z rzędu bez interpunkcji (poza deklaracjami typu `long long x`)
    run = best = 0
    for t in toks:
        if t.kind in ("id", "kw") and not (t.kind == "kw" and t.text in E.TYPE_KW):
            run += 1
            best = max(best, run)
        else:
            run = 0
    if best >= 3:
        score -= 3
    if _PROSE_END.search(bare) and not bare.endswith(("..", "...")):
        score -= 2
    if len(toks) <= 2 and not bare.endswith(";"):
        score -= 1
    return score >= 3


def t_remove_dead_code(code: str):
    toks = E.tokenize(code)
    edits = []
    removed = 0
    # grupy kolejnych komentarzy // w osobnych liniach
    i = 0
    lines_start = [0]
    for m in re.finditer("\n", code):
        lines_start.append(m.end())

    def own_line(t):
        ls = code.rfind("\n", 0, t.pos) + 1
        return code[ls:t.pos].strip() == ""

    while i < len(toks):
        t = toks[i]
        if t.kind != "comment":
            i += 1
            continue
        if t.text.startswith("//") and own_line(t):
            group = [t]
            j = i + 1
            while j < len(toks) and toks[j].kind == "comment" and toks[j].text.startswith("//") and own_line(toks[j]) \
                    and toks[j].line == group[-1].line + 1:
                group.append(toks[j])
                j += 1
            contents = [g.text[2:].strip() for g in group]
            real = [looks_like_code(c) for c in contents]
            punct = [re.fullmatch(r"[{}();]+", c) is not None for c in contents]
            kill = list(real)
            # same nawiasy (np. `//}`) usuwamy razem z sąsiadującym zakomentowanym kodem
            changed = True
            while changed:
                changed = False
                for gi in range(len(group)):
                    if punct[gi] and not kill[gi] and ((gi > 0 and kill[gi - 1]) or (gi + 1 < len(group) and kill[gi + 1])):
                        kill[gi] = True
                        changed = True
            if any(real):
                for g, is_code in zip(group, kill):
                    if is_code:
                        ls = code.rfind("\n", 0, g.pos) + 1
                        le = code.find("\n", g.pos)
                        le = len(code) if le < 0 else le + 1
                        edits.append((ls, le, ""))
                        removed += 1
            i = j
            continue
        if t.text.startswith("//"):
            if looks_like_code(t.text[2:]):
                s = t.pos
                while s > 0 and code[s - 1] in " \t":
                    s -= 1
                edits.append((s, t.pos + len(t.text), ""))
                removed += 1
        elif t.text.startswith("/*") and "Online C++ Compiler" not in t.text:
            inner = t.text[2:-2] if t.text.endswith("*/") else t.text[2:]
            lines = [ln.strip().lstrip("*").strip() for ln in inner.split("\n")]
            lines = [ln for ln in lines if ln]
            if lines and all(looks_like_code(ln) or re.fullmatch(r"[{}();]+", ln) for ln in lines) and \
                    any(looks_like_code(ln) for ln in lines):
                if own_line(t):
                    ls = code.rfind("\n", 0, t.pos) + 1
                    le = code.find("\n", t.pos + len(t.text))
                    rest = code[t.pos + len(t.text): (len(code) if le < 0 else le)]
                    if rest.strip() == "":
                        le = len(code) if le < 0 else le + 1
                        edits.append((ls, le, ""))
                    else:
                        edits.append((t.pos, t.pos + len(t.text), ""))
                else:
                    edits.append((t.pos, t.pos + len(t.text), ""))
                removed += 1
        i += 1
    return _apply_edits(code, edits), removed


_INC_LINE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*([<"])([^>"\n]+)([>"])(.*)$')


def _include_lines(code: str) -> dict[int, tuple]:
    """Numery linii (0-based) z dyrektywami #include (wg leksera: pomija komentarze i raw stringi)."""
    res = {}
    for t in E.tokenize(code):
        if t.kind == "pp" and "\n" not in t.text:
            m = _INC_LINE.match(t.text)
            if m:
                res[t.line - 1] = (m.group(1), m.group(2).strip(), m.group(4).strip())
    return res


def _inc_key(kind_hdr):
    q, hdr = kind_hdr[0], kind_hdr[1]
    if hdr == "bits/stdc++.h":
        return (0, hdr)
    if q == "<":
        return (1 if hdr in STD_HEADERS else 2, hdr)
    return (3, hdr)


def t_includes(code: str, sort: bool = True, bits: bool = False):
    lines = code.split("\n")
    inc = _include_lines(code)
    if not inc:
        return code, 0
    changes = 0
    # bloki: kolejne linie #include (puste linie pomiędzy dozwolone)
    blocks = []
    cur = []
    for i, ln in enumerate(lines):
        if i in inc:
            cur.append(i)
        elif ln.strip() == "" and cur:
            continue
        else:
            if cur:
                blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    seen = set()
    has_bits = any(v[1] == "bits/stdc++.h" for v in inc.values())
    std_count = sum(1 for v in inc.values() if v[0] == "<" and v[1] in STD_HEADERS)
    use_bits = bits and (has_bits or std_count >= 2)
    replace = {}
    remove = set()
    bits_placed = False
    for blk in blocks:
        entries = []
        for i in blk:
            q, hdr, rest = inc[i]
            key = (q, hdr)
            if use_bits and q == "<" and hdr in STD_HEADERS:
                if bits_placed:
                    remove.add(i)
                    if hdr != "bits/stdc++.h":
                        changes += 1
                    continue
                bits_placed = True
                if hdr != "bits/stdc++.h":
                    changes += 1
                key = ("<", "bits/stdc++.h")
                rest = rest if hdr == "bits/stdc++.h" else ""
            if key in seen:
                remove.add(i)
                changes += 1
                continue
            seen.add(key)
            text = f"#include {key[0]}{key[1]}{'>' if key[0] == '<' else chr(34)}" + (f" {rest}" if rest else "")
            entries.append((i, key, text))
        ordered = sorted(entries, key=lambda e: _inc_key(e[1])) if sort else entries
        if sort and [e[0] for e in ordered] != [e[0] for e in entries]:
            changes += 1
        # wpisz posortowane w miejsca linii bloku (pierwsze len(entries) linii), resztę usuń
        slots = [i for i in blk if i not in remove]
        for slot, e in zip(slots, ordered):
            if lines[slot] != e[2]:
                replace[slot] = e[2]
        # puste linie wewnątrz bloku include — usuń
        if sort:
            for i in range(blk[0], blk[-1] + 1):
                if i not in inc and lines[i].strip() == "":
                    remove.add(i)
    out = []
    for i, ln in enumerate(lines):
        if i in remove:
            continue
        out.append(replace.get(i, ln))
    new = "\n".join(out)
    return new, changes if new != code else 0


def _top_insert_pos(code: str) -> int:
    """Pozycja (znak) po ostatniej dyrektywie #include najwyższego poziomu (albo 0)."""
    pos = 0
    for t in E.tokenize(code):
        if t.kind == "pp" and re.match(r"#\s*include", t.text):
            pos = t.pos + len(t.text)
        elif t.kind not in ("comment", "pp"):
            break
    return pos


def _user_declared(toks) -> set:
    names = set()
    for i, t in enumerate(toks):
        if t.kind != "id":
            continue
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if prev is None or nxt is None:
            continue
        if (prev.kind == "kw" and prev.text in E.TYPE_KW | {"struct", "class", "enum", "union", "using", "typedef"}
                or prev.text in (">", "*", "&") and prev.kind == "op") and nxt.text in ("=", ";", ",", "(", "[", "{", ")"):
            names.add(t.text)
        if prev.kind == "pp":
            pass
    return names


def t_namespace_std(code: str, mode: str):
    if mode not in ("add", "remove"):
        return code, 0
    toks = _sig_tokens(code)
    has_using = any(toks[i].text == "using" and i + 2 < len(toks) and toks[i + 1].text == "namespace"
                    and toks[i + 2].text == "std" for i in range(len(toks)))
    defines = set(re.findall(r"^\s*#\s*define\s+(\w+)", code, re.M))
    declared = _user_declared(toks) | defines
    edits = []
    count = 0
    if mode == "add":
        for i in range(len(toks) - 2):
            if toks[i].kind == "id" and toks[i].text == "std" and toks[i + 1].text == "::" and \
                    toks[i + 2].kind == "id" and (i == 0 or toks[i - 1].text != "::") and toks[i + 2].text not in declared:
                edits.append((toks[i].pos, toks[i + 2].pos, ""))
                count += 1
        new = _apply_edits(code, edits)
        if not has_using and (count or re.search(r"\b(cout|cin|vector|string)\b", new)):
            pos = _top_insert_pos(new)
            ins = ("\n" if pos else "") + "using namespace std;" + ("\n" if not pos else "")
            new = new[:pos] + ins + new[pos:]
            count += 1
        return new, count
    # remove
    for i in range(len(toks)):
        t = toks[i]
        if t.text == "using" and i + 3 < len(toks) and toks[i + 1].text == "namespace" and toks[i + 2].text == "std" \
                and toks[i + 3].text == ";":
            s, e = t.pos, toks[i + 3].pos + 1
            ls = code.rfind("\n", 0, s) + 1
            le = code.find("\n", e)
            if code[ls:s].strip() == "" and code[e:(len(code) if le < 0 else le)].strip() == "":
                s, e = ls, (len(code) if le < 0 else le + 1)
            edits.append((s, e, ""))
            count += 1
    if not count:
        return code, 0
    for i, t in enumerate(toks):
        if t.kind != "id" or t.text not in STD_NAMES or t.text in declared:
            continue
        prev = toks[i - 1] if i else None
        if prev is not None and (prev.text in ("::", ".", "->") or (prev.kind == "kw" and prev.text in
                                                                    ("struct", "class", "enum", "operator", "goto"))):
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if nxt is not None and nxt.text == "::" and t.text not in ("chrono",):
            pass
        edits.append((t.pos, t.pos, "std::"))
        count += 1
    return _apply_edits(code, edits), count


def t_endl(code: str):
    toks = _sig_tokens(code)
    edits = []
    count = 0
    for i, t in enumerate(toks):
        if t.kind != "id" or t.text != "endl":
            continue
        j = i
        if i >= 2 and toks[i - 1].text == "::" and toks[i - 2].text == "std":
            j = i - 2
        if j == 0 or toks[j - 1].text != "<<":
            continue
        k = j - 1      # indeks '<<'
        s = toks[k - 1] if k >= 1 else None
        if s is not None and s.kind == "str" and s.text.startswith('"') and s.text.endswith('"') and len(s.text) >= 2 \
                and k >= 2 and toks[k - 2].text == "<<":
            # "abc" << endl  →  "abc\n"
            edits.append((s.pos, t.pos + len(t.text), s.text[:-1] + '\\n"'))
        else:
            edits.append((toks[j].pos, t.pos + len(t.text), "'\\n'"))
        count += 1
    return _apply_edits(code, edits), count


def t_braces(code: str, multiline: bool = True):
    try:
        f = E._Fmt(code.replace("\r\n", "\n"), dict(E.BASE_STYLE))
        f.parse()
    except Exception:  # noqa: BLE001
        return code, 0
    T = f.T
    ins = []
    for depth_order, (head, first, last) in enumerate(f.unbraced):
        if last < first or any(T[q].kind == "pp" for q in range(first, last + 1)):
            continue
        a = T[first].pos
        b = T[last].pos + len(T[last].text)
        k = f.S[last] + 1
        if multiline and k < len(f.A) and f.A[k].kind == "comment" and f.nlb[k] == 0 and f.A[k].text.startswith("//"):
            b = f.A[k].pos + len(f.A[k].text)      # `if (x) y; // opis` → komentarz zostaje przy instrukcji
        ins.append((a, 0, depth_order, "{\n" if multiline else "{ "))
        ins.append((b, 1, depth_order, "\n}" if multiline else " }"))
    if not ins:
        return code, 0
    src = code.replace("\r\n", "\n")
    # od końca; przy równych pozycjach: zamknięcia zewnętrznych (mniejszy depth_order) najpierw
    ins.sort(key=lambda x: (-x[0], x[1], -x[2]))
    out = src
    for pos, _, _, text in ins:
        out = out[:pos] + text + out[pos:]
    return out, len(ins) // 2


def t_trailing_ws(code: str):
    keep = set()
    for t in E.tokenize(code):
        if t.kind in ("str",) and "\n" in t.text:
            keep.update(range(t.line, t.line + t.text.count("\n")))
    lines = code.split("\n")
    n = 0
    for i, ln in enumerate(lines):
        if (i + 1) in keep:
            continue
        s = ln.rstrip()
        if s != ln:
            lines[i] = s
            n += 1
    return "\n".join(lines), n


def t_blank_lines(code: str, maxe: int):
    keep = set()
    for t in E.tokenize(code):
        if t.kind in ("str", "comment") and "\n" in t.text:
            keep.update(range(t.line + 1, t.line + t.text.count("\n") + 1))
    lines = code.split("\n")
    out = []
    run = 0
    n = 0
    for i, ln in enumerate(lines):
        if ln.strip() == "" and (i + 1) not in keep:
            run += 1
            if run > maxe or not out:
                n += 1
                continue
        else:
            run = 0
        out.append(ln)
    while out and out[-1].strip() == "":
        out.pop()
        n += 1
    return "\n".join(out) + "\n", n


def beautify(code: str, transforms: dict | None = None, style=None) -> dict:
    """Upiększacz: zestaw przełączanych przekształceń + formatowanie. Zwraca {code, changed, changes, warnings}."""
    src = (code or "").replace("\r\n", "\n").replace("\r", "\n")
    tr = {t["id"]: t["default"] for t in TRANSFORMS}
    for k, v in (transforms or {}).items():
        if k in tr:
            tr[k] = v
    opts = resolve_style(style)
    changes = []
    warnings = []
    cur = src

    def step(tid, fn, *args):
        nonlocal cur
        try:
            new, cnt = fn(cur, *args)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Przekształcenie „{_tlabel(tid)}” pominięte ({e.__class__.__name__}).")
            return
        if new != cur and cnt:
            changes.append({"id": tid, "label": _tlabel(tid), "count": cnt})
            cur = new

    if tr["remove_banner"]:
        step("remove_banner", t_remove_banner)
    if tr["remove_dead_code"]:
        step("remove_dead_code", t_remove_dead_code)
    if tr["sort_includes"] or tr["bits_stdcpp"]:
        step("sort_includes" if not tr["bits_stdcpp"] else "bits_stdcpp", t_includes, bool(tr["sort_includes"]),
             bool(tr["bits_stdcpp"]))
    if tr["namespace_std"] in ("add", "remove"):
        step("namespace_std", t_namespace_std, tr["namespace_std"])
    if tr["endl"]:
        step("endl", t_endl)
    if tr["braces"]:
        step("braces", t_braces, bool(tr["format"]))
    if tr["format"]:
        o = dict(opts)
        if tr["function_spacing"]:
            o["separate_definitions"] = True
        if not tr["blank_lines"]:
            o["max_empty_lines"] = 4
        before = cur
        out, w = E.format_source(cur, o)
        warnings.extend(w)
        if out != before:
            d = diff_stats(line_diff(before, out))
            changes.append({"id": "format", "label": _tlabel("format"),
                            "count": max(d["added"], d["removed"])})
            cur = out
    else:
        if tr["trailing_ws"]:
            step("trailing_ws", t_trailing_ws)
        if tr["blank_lines"]:
            step("blank_lines", t_blank_lines, int(opts["max_empty_lines"]))
    return {"code": cur, "changed": cur != src, "changes": changes, "warnings": warnings}


def _tlabel(tid: str) -> str:
    for t in TRANSFORMS:
        if t["id"] == tid:
            return t["label"]
    return tid


# ============================================================================================ HTTP
def _code_arg(body: dict) -> str:
    code = body.get("code")
    if code is None:
        raise ApiError("Brak kodu do sformatowania.")
    code = str(code)
    if len(code) > MAX_CODE:
        raise ApiError("Kod jest za duży (limit 2 MB).")
    return code


def handle_format(body: dict) -> dict:
    code = _code_arg(body)
    style = body.get("style")
    opts = resolve_style(style)
    out, warnings = E.format_source(code, opts)
    res = {"code": out, "changed": out != code.replace("\r\n", "\n"), "warnings": warnings}
    if body.get("diff"):
        res["diff"] = line_diff(code, out, context=body.get("context"))
        res["stats"] = diff_stats(res["diff"])
    if body.get("with_style"):
        res["style"] = opts
    return res


def handle_presets(_q: dict) -> dict:
    fs = _format_settings()
    active = preset_id(fs.get("preset")) or "moja"
    presets = [{"id": pid, "label": PRESETS[pid]["label"], "description": PRESETS[pid]["description"],
                "options": preset_options(pid, fs)} for pid in E.PRESET_ORDER]
    return {"presets": presets, "schema": STYLE_SCHEMA, "active": active, "current": resolve_style(None),
            "learned": fs.get("learned")}


def handle_style(body: dict) -> dict:
    if body.get("style") is None and body.get("preset"):
        pid = preset_id(body.get("preset"))
        if not pid:
            raise ApiError("Nieznany preset stylu.")
        fs = _format_settings()
        fs["preset"] = pid
        store.update_settings({"format_style": fs})
        return {"active": pid, "style": resolve_style(None)}
    style = body.get("style")
    if isinstance(style, str):
        pid = preset_id(style)
        if not pid:
            raise ApiError("Nieznany preset stylu.")
        opts = preset_options(pid)
    elif isinstance(style, dict):
        opts = resolve_style(style)
    else:
        raise ApiError("Podaj styl do zapisania (słownik opcji albo nazwę presetu).")
    save_my_style(opts)
    return {"active": "moja", "style": resolve_style(None), "saved": True}


def handle_learn(body: dict) -> dict:
    if body.get("async"):
        return {"job": jobs.submit(_learn_work, body, title="Nauka stylu formatowania")}
    return _learn_work(None, body)


def handle_beautify(body: dict) -> dict:
    code = _code_arg(body)
    res = beautify(code, body.get("transforms") or body.get("options") or {}, body.get("style"))
    res["diff"] = line_diff(code, res["code"], context=body.get("context"))
    res["stats"] = diff_stats(res["diff"])
    return res


def handle_beautify_options(_q: dict) -> dict:
    return {"transforms": TRANSFORMS}


ROUTES = {
    "POST /api/format": handle_format,
    "GET /api/format/presets": handle_presets,
    "POST /api/format/style": handle_style,
    "POST /api/format/learn": handle_learn,
    "POST /api/beautify": handle_beautify,
    "GET /api/beautify/options": handle_beautify_options,
}
