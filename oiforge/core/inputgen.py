"""Wnioskowanie formatu wejścia z przykładów, generowanie testów i shrinking (minimalizacja kontrprzykładów).

Współdzielone przez testerkę (oiforge.tools.tester) i auto-testerkę (oiforge.tools.autotester). Zero AI — tylko
reguły, przeszukiwanie hipotez (DFS z ograniczeniem kosztu, zasada najkrótszego opisu) i losowanie.

Publiczne API:
    extract_samples(statement_text) -> [{"input", "output", "name"}]
    statement_hints(statement_text) -> {"constraints", "names", "global", "flags"}
    infer(sample_inputs, statement_text=None) -> spec            (spec = JSON, edytowalny w UI)
    parse(spec, text, strict=True) -> Parsed  /  validate(spec, text) -> (ok, komunikat)
    generate(spec, strategy="random", size="small", rng=None, seed=None) -> str
    shrink(spec, input_text, predicate, max_evals=400, time_budget=30.0) -> dict
    describe(spec) -> [str]                                       (czytelny opis po polsku)
    STRATEGIES, SIZES

Format specyfikacji (wersja 1):
{
  "version": 1,
  "vars":  {"n": {"min": 1, "max": 100000, "source": "treść"}, ...},
  "items": [
    {"kind": "line",    "fields": [{"type": "int", "var": "n"}, {"type": "int", "min": 1, "max": "n"}, ...]},
    {"kind": "array",   "len": "n", "layout": "row"|"column", "elem": FIELD, "order": null|"sorted"|"sorted_desc"|
                        "distinct"|"permutation"},
    {"kind": "repeat",  "count": "m", "fields": [FIELD, ...], "pairs": [[0, 1, "le"]]},   # m linii po kilka pól
    {"kind": "graph",   "n": "n", "count": "m"|"n-1", "base": 1, "directed": false, "tree": false,
                        "connected": false, "simple": true, "weight": FIELD|null},
    {"kind": "parents", "n": "n", "base": 1},                      # p_2..p_n w jednej linii (p_i < i)
    {"kind": "matrix",  "rows": "n", "cols": "m", "elem": FIELD},
    {"kind": "grid",    "rows": "n", "cols": "m", "alphabet": ".#"},
    {"kind": "string",  "len": "n" | null, "min_len": 1, "max_len": 10, "alphabet": "ab"},
    {"kind": "queries", "count": "q", "types": [{"tag": "push", "fields": [FIELD]}, {"tag": "pop", "fields": []}]},
    {"kind": "lists",   "count": "n", "elem": FIELD, "min_len": 0, "max_len": "n"},   # linie „k x1 .. xk”
    {"kind": "block",   "count": "t", "items": [...]}               # multitest: t razy blok
  ]
}
FIELD = {"type": "int", "min": E, "max": E} | {"type": "float", "min", "max", "decimals"} |
        {"type": "word", "choices": [...]} | {"type": "str", "alphabet", "min_len", "max_len"} |
        {"type": "bigint", "name", "min": 0|1, "max_len": D, "max_lead"?, "max_pow10"?}   # liczba do D cyfr jako napis
        E = liczba lub wyrażenie
("n", "n-1", "n*(n-1)/2", "10**9", "min(n, 5)"). Długości/liczności („len”, „count”) mogą być też zakresem
{"min": a, "max": b} — wtedy element czyta „do końca linii / wejścia”.
"""
from __future__ import annotations

import ast
import copy
import html as _html
import math
import random
import re
import time

# ============================================================================================== stałe
SIZES = {"tiny": 4, "small": 10, "medium": 100, "large": 3000, "max": None}
STRATEGIES = [
    {"id": "random", "name": "Małe losowe", "description": "Małe rozmiary i małe wartości — dużo powtórzeń i kolizji."},
    {"id": "random_full", "name": "Losowe (pełny zakres)", "description": "Małe rozmiary, wartości z pełnego zakresu (przepełnienia)."},
    {"id": "min", "name": "Minimalne", "description": "Wszystko na dolnych granicach (n = 1, wartości minimalne)."},
    {"id": "max", "name": "Maksymalne", "description": "Rozmiary i wartości maksymalne — przepełnienia, TLE, za małe tablice."},
    {"id": "equal", "name": "Wszystkie równe", "description": "Wszystkie elementy tablic równe."},
    {"id": "sorted", "name": "Posortowane", "description": "Tablice rosnąco."},
    {"id": "reversed", "name": "Odwrotnie posortowane", "description": "Tablice malejąco."},
    {"id": "alternating", "name": "Naprzemienne", "description": "min, max, min, max… (piła)."},
    {"id": "extreme", "name": "Wartości skrajne", "description": "Tylko min, min+1, max−1, max, 0, ±1."},
    {"id": "path", "name": "Drzewo-ścieżka", "description": "Graf/drzewo jako długa ścieżka (głęboka rekurencja)."},
    {"id": "star", "name": "Gwiazda", "description": "Jeden wierzchołek połączony ze wszystkimi."},
    {"id": "random_tree", "name": "Losowe drzewo", "description": "Losowe drzewo (lub szkielet grafu)."},
    {"id": "connected", "name": "Graf spójny", "description": "Losowy graf spójny."},
    {"id": "dag", "name": "DAG", "description": "Skierowany graf acykliczny (krawędzie zgodne z losowym porządkiem)."},
]
STRATEGY_IDS = [s["id"] for s in STRATEGIES]
_INT_RE = re.compile(r"[+-]?\d+")
_FLOAT_RE = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
_WORD_RE = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż_]+")
_DEFAULT_NAMES = ["n", "m", "k", "q", "p", "r", "s", "x", "y", "z", "c", "d", "e", "f", "g", "h", "u", "w"]


_SENTINEL_TOKENS = {"0", "-1", "#", ".", "END", "KONIEC", "koniec", "end", "$", "*"}


class FormatError(ValueError):
    """Wejście nie pasuje do specyfikacji."""


# ============================================================================================== tekst, przykłady
def normalize_sample(text: str) -> str:
    """CRLF→LF, bez spacji na końcach linii; jeśli co druga linia jest pusta (tekst z PDF) — zwija puste linie."""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("−", "-").replace(" ", " ")
    lines = [ln.rstrip() for ln in s.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines)


def _tok_lines(text: str) -> list[list[str]]:
    """Linie jako listy tokenów, bez pustych linii."""
    return [ln.split() for ln in normalize_sample(text).split("\n") if ln.strip()]


def _tclass(tok: str) -> str:
    if _INT_RE.fullmatch(tok):
        return "int"
    if _FLOAT_RE.fullmatch(tok):
        return "float"
    if _WORD_RE.fullmatch(tok):
        return "word"
    return "str"


_FOOTER_RE = re.compile(r"^(©|sio2\.|Szablon treści|stworzonego przez|warunkami licencji|Wyjaśnienie|Wyjasnienie|"
                        r"Ocenianie|Podzadani|Uwaga|Komentarz|Autor|Natomiast|Dla danych|Poprawn|Wej[śs´]cie dla|"
                        r"Wej´?scie dla|Wyj[śs´]cie dla|Przyk|Limit|Kod zadania|Zadanie|W tym przykładzie|\d+/(\d+|\?\?)|"
                        r"Wej\S*\s+Wyj\S*\s*$)",
                        re.I)


def _clean_block(lines: list[str], title: str | None = None) -> list[str]:
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if _FOOTER_RE.match(s) or (title and s == title.strip()):
            break
        out.append(s.replace("−", "-"))
    return out


def extract_samples(text: str) -> list[dict]:
    """Wyciąga przykłady (wejście/wyjście) z treści zadania: tekst z PDF (sio2/OI) lub HTML (<pre>)."""
    if not text:
        return []
    own = _extract_html(text) if "<pre" in text.lower() else _extract_pdf_text(text)
    try:   # moduł treści zadań (agent E2), jeśli jest — także testy „ocen” opisane słownie
        from oiforge.core import statement as _st  # type: ignore
        res = _st.parse(text)
        samples = [s for s in (res.get("samples") or []) if isinstance(s, dict) and (s.get("input") or "").strip()]
    except Exception:  # noqa: BLE001
        samples = []

    def conv(s, i):
        d = {"input": normalize_sample(s.get("input", "")), "output": normalize_sample(s.get("output", "")),
             "name": s.get("name") or f"przykład {i + 1}"}
        if s.get("source"):
            d["source"] = s["source"]
        return d
    if not own:
        return [conv(s, i) for i, s in enumerate(samples)]
    seen = {normalize_sample(s["input"]) for s in own}
    extra = [conv(s, i) for i, s in enumerate(samples) if s.get("source") == "ocen"
             and normalize_sample(s.get("input", "")) not in seen]
    return own + extra


def _extract_html(text: str) -> list[dict]:
    pres = [(m.start(), _html.unescape(re.sub(r"<[^>]+>", "", m.group(1))))
            for m in re.finditer(r"<pre[^>]*>(.*?)</pre>", text, re.S | re.I)]
    out = []
    i = 0
    while i + 1 < len(pres):
        before = re.sub(r"<[^>]+>", " ", text[max(0, pres[i][0] - 300):pres[i][0]]).lower()
        if re.search(r"wyj[śs]ci|wynik|odpowied", before[-120:]) and not re.search(r"wej[śs]ci", before[-120:]):
            i += 1
            continue
        out.append({"input": normalize_sample(pres[i][1]), "output": normalize_sample(pres[i + 1][1]),
                    "name": f"przykład {len(out) + 1}"})
        i += 2
    return out


def _extract_pdf_text(text: str) -> list[dict]:
    s = text.replace("\r\n", "\n")
    lines = s.split("\n")
    title = next((ln.strip() for ln in lines if ln.strip()), None)
    out = []
    # A: „Wejście dla testu X:” … „Wyjście dla testu X:” …
    rx_in = re.compile(r"^\s*Wej(?:ście|scie|´scie|´?scie)\s+dla\s+testu\s*(\S*?):?\s*$", re.I)
    rx_out = re.compile(r"^\s*Wyj(?:ście|scie|´scie|´?scie)\s+dla\s+testu\s*(\S*?):?\s*$", re.I)
    i = 0
    while i < len(lines):
        m = rx_in.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1) or f"przykład {len(out) + 1}"
        j = i + 1
        while j < len(lines) and not rx_out.match(lines[j]):
            j += 1
        if j >= len(lines):
            break
        k = j + 1
        while k < len(lines) and not rx_in.match(lines[k]):
            k += 1
        inp = _clean_block(lines[i + 1:j], title)
        outp = _clean_block(lines[j + 1:k], title)
        if inp:
            out.append({"input": "\n".join(inp), "output": "\n".join(outp), "name": name})
        i = k
    if out:
        return out
    # B: „Wejście Wyjście” obok siebie — kolumny sklejone; podział wyznaczamy wnioskowaniem formatu
    rx_side = re.compile(r"^\s*Wej\S*\s+Wyj\S*\s*$", re.I)
    for i, ln in enumerate(lines):
        if rx_side.match(ln):
            block = _clean_block(lines[i + 1:], title)
            if len(block) >= 2:
                k = _split_combined(block, _hints_cache(text))
                out.append({"input": "\n".join(block[:k]), "output": "\n".join(block[k:]),
                            "name": f"przykład {len(out) + 1}", "split_guess": True})
    if out:
        return out
    # D: tekst skopiowany ze strony (HTML → tekst): „Dla danych wejściowych:” … „Poprawnym wynikiem jest:” …
    rx_d_in = re.compile(r"^\s*dla\s+danych\s+wej\w*\s*:?\s*$", re.I)
    rx_d_out = re.compile(r"^\s*(?:poprawn\w+\s+(?:wynikiem|odpowiedzią|odpowiedzia)|wynik(?:iem)?)\s*(?:jest)?\s*:?\s*$", re.I)
    i = 0
    while i < len(lines):
        if not rx_d_in.match(lines[i]):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not rx_d_out.match(lines[j]):
            j += 1
        if j >= len(lines):
            break
        k = j + 1
        while k < len(lines) and not lines[k].strip():
            k += 1
        e = k
        while e < len(lines) and lines[e].strip() and not rx_d_in.match(lines[e]):
            e += 1
        inp = _clean_block(lines[i + 1:j], title)
        outp = _clean_block(lines[k:e], title)
        if inp:
            out.append({"input": "\n".join(inp), "output": "\n".join(outp), "name": f"przykład {len(out) + 1}"})
        i = e
    if out:
        return out
    # C: „Wejście” / „Wyjście” jako osobne nagłówki w sekcji „Przykład”
    m = re.search(r"\nPrzyk\S*\s*\n", s)
    if m:
        rest = s[m.end():].split("\n")
        try:
            a = next(i for i, ln in enumerate(rest) if re.match(r"^\s*Wej\S*\s*:?\s*$", ln))
            b = next(i for i, ln in enumerate(rest) if i > a and re.match(r"^\s*Wyj\S*\s*:?\s*$", ln))
            inp = _clean_block(rest[a + 1:b], title)
            outp = _clean_block(rest[b + 1:], title)
            if inp:
                out.append({"input": "\n".join(inp), "output": "\n".join(outp), "name": "przykład 1"})
        except StopIteration:
            pass
    return out


_HINTS_CACHE: dict[int, dict] = {}


def _hints_cache(text: str) -> dict:
    key = hash(text)
    h = _HINTS_CACHE.get(key)
    if h is None:
        try:
            h = statement_hints(text)
        except Exception:  # noqa: BLE001
            h = {"flags": {}}
        if len(_HINTS_CACHE) > 64:
            _HINTS_CACHE.clear()
        _HINTS_CACHE[key] = h
    return h


def _split_combined(block: list[str], hints: dict | None = None) -> int:
    """Dla sklejonych kolumn „Wejście Wyjście”: wybiera k (liczbę linii wejścia). Kryterium: koszt opisu / k,
    z poprawką na to, co treść mówi o wyjściu (jedna liczba / tyle wierszy, ile zapytań …)."""
    hints = hints or {"flags": {}}
    fl = hints.get("flags") or {}
    L = len(block)
    cands = []
    for k in range(1, L):
        lines = [ln.split() for ln in block[:k]]
        srch = _Search([lines], hints, allow_block=True, max_nodes=1500)
        srch.run()
        if srch.best_items is None:
            continue
        score = srch.best_cost / k
        out_lines = L - k
        out_tokens = sum(len(x.split()) for x in block[k:])
        env = {}
        try:
            sp = {"items": copy.deepcopy(srch.best_items), "vars": {}}
            _rename_vars(sp, hints)
            env = parse(sp, "\n".join(block[:k]), strict=False).env
        except (FormatError, ValueError, KeyError):
            pass
        tags = set()
        ov = fl.get("out_var")
        if fl.get("out_nlines") and out_lines == fl["out_nlines"]:
            tags.add("var")
        elif ov and ov[0] in env and env[ov[0]] == (out_lines if ov[1] == "lines" else out_tokens):
            tags.add("var")
        if fl.get("out_per_query") and any(
                it.get("kind") in ("queries", "repeat", "array") and isinstance(it.get("count", it.get("len")), str)
                and env.get(str(it.get("count", it.get("len")))) in (out_lines, out_tokens) for it in srch.best_items):
            tags.add("query")
        if fl.get("out_single") and out_lines == 1 and not fl.get("out_nlines"):
            tags.add("single")
        cands.append((score, -k, k, tags))
    if not cands:
        return L - 1
    # twarde wskazówki z sekcji „Wyjście” (jeśli którykolwiek podział je spełnia)
    for tag in ("var", "query", "single"):
        sub = [c for c in cands if tag in c[3]]
        if sub:
            cands = sub
            break
    cands.sort(key=lambda c: (c[0], c[1]))
    return cands[0][2]


# ============================================================================================== wskazówki z treści
_LE = r"(?:≤|⩽|¬|≦|<=|&lt;=|=<)"
_GE = r"(?:≥|⩾|≧|>=|&gt;=)"


def _prep_statement(text: str) -> str:
    s = text.replace("\r\n", "\n")
    s = re.sub(r"<sup>\s*(\d+)\s*</sup>", r"^\1", s)
    s = re.sub(r"<sub>\s*(\w+)\s*</sub>", r"_\1", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"(\d)\s*\n\s*(\d{1,2})\s*\n", r"\1^\2 ", s)       # potęgi z PDF: "10\n5\n" → 10^5
    s = re.sub(r"(\d)\s*\n\s*(\d{1,2})\s*$", r"\1^\2", s, flags=re.M)
    s = s.replace("−", "-").replace("−", "-").replace("–", "-")
    s = re.sub(_LE, "≤", s)
    s = re.sub(_GE, "≥", s)
    # niektóre PDF-y mapują „≤” na cyfrę 6: „1 6 n, q 6 5 000”
    for _ in range(2):
        s = re.sub(r"(?<=[\dA-Za-z]) 6 (?=[A-Za-z])", " ≤ ", s)
        s = re.sub(r"(?<=[A-Za-z]) 6 (?=-?\d)", " ≤ ", s)
    s = s.replace("·", "*").replace("×", "*").replace("⋅", "*")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(?<=\d) (?=\d{3}(?!\d))", "", s)                 # 1 000 000 → 1000000
    s = re.sub(r"(?<=\d) (?=\d{3}(?!\d))", "", s)
    return s


def _num_value(tok: str) -> int | None:
    t = tok.replace(" ", "")
    m = re.fullmatch(r"(-?\d+(?:[.,]\d+)?)(?:\*10\^(-?\d+))?", t)
    if m:
        base = float(m.group(1).replace(",", "."))
        if m.group(2):
            base *= 10 ** int(m.group(2))
        return int(round(base))
    m = re.fullmatch(r"(-?\d+)\^(\d+)", t)
    if m:
        return int(m.group(1)) ** int(m.group(2)) if int(m.group(2)) < 80 else None
    m = re.fullmatch(r"-?(\d+)\^(\d+)-1", t)
    if m:
        return int(m.group(1)) ** int(m.group(2)) - 1
    return None


_ID = r"(?<![\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ])[A-Za-z][A-Za-z0-9_]{0,3}(?![\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ])"
_TERM = (r"-?\s*\d+(?:[.,]\d+)?(?:\s*\*\s*10\s*\^\s*-?\d+|\s*\^\s*\d+(?:\s*-\s*1)?)?|"
         + _ID + r"(?:\s*,\s*" + _ID + r")*")


def statement_hints(text: str | None) -> dict:
    """Ograniczenia i nazwy zmiennych z treści (regex + opcjonalnie oiforge.core.statement)."""
    hints = {"constraints": {}, "names": [], "global": {}, "flags": {}, "relations": []}
    if not text:
        return hints
    hints["header_groups"] = []
    try:
        from oiforge.core import statement as _st  # type: ignore
        parsed_st = _st.parse(text) or {}
        cons = parsed_st.get("constraints") or {}
        for k, v in cons.items():
            if isinstance(v, dict) and v.get("bignum") and v.get("max_digits") and re.fullmatch(r"[A-Za-z]\w{0,3}", str(k)):
                hints.setdefault("bignum", {})[str(k)] = {
                    "digits": int(v["max_digits"]), "min": 0 if (v.get("min") is not None and v["min"] <= 0) else 1,
                    "max_lead": v.get("max_lead"), "max_pow10": v.get("max_pow10")}
                continue
            if isinstance(v, dict) and re.fullmatch(r"[A-Za-z]\w{0,3}", str(k)):
                c = {kk: v[kk] for kk in ("min", "max") if isinstance(v.get(kk), int) and not isinstance(v.get(kk), bool)}
                if c:
                    hints.setdefault("_module_constraints", {})[str(k)] = c
        for ent in parsed_st.get("input_format") or []:
            if isinstance(ent, dict) and ent.get("kind") == "scalars" and ent.get("vars"):
                grp = [str(x) for x in ent["vars"] if re.fullmatch(r"[A-Za-z]\w{0,3}", str(x))]
                if grp and not any(re.fullmatch(r"x\d+|v\d+|_.*", g) for g in grp):
                    hints["header_groups"].append(grp)
    except Exception:  # noqa: BLE001
        pass
    s = _prep_statement(text)
    low = s.lower()
    # sekcja wejścia
    m_in = re.search(r"\bwej´?\s?[śs]cie\b(?! dla)", s, re.I)
    m_out = re.search(r"\bwyj´?\s?[śs]cie\b(?! dla)", s[m_in.end():] if m_in else s, re.I)
    if m_in:
        inp = s[m_in.end(): m_in.end() + (m_out.start() if m_out else 3000)]
    else:
        inp = s[:3000]
    hints["input_section"] = inp[:4000]
    # łańcuchy nierówności: A ≤ x, y ≤ B ≤ ...
    chain_rx = re.compile(rf"(?:(?:{_TERM})\s*[≤<]\s*)+(?:{_TERM})")
    for scope_text, weight in ((inp, 2), (s, 1)):
        for m in chain_rx.finditer(scope_text):
            parts = re.split(r"\s*([≤<])\s*", m.group(0))
            terms, ops = parts[0::2], parts[1::2]
            _apply_chain(terms, ops, hints, weight)
    for m in re.finditer(rf"\b([A-Za-z]\w{{0,2}})\s*≥\s*(-?\d[\d^*.]*)", s):
        v = _num_value(m.group(2))
        if v is not None:
            _merge_c(hints["constraints"], m.group(1), lo=v)
    # nazwy zmiennych (kolejność wystąpień w sekcji wejścia)
    names: list[tuple[int, str]] = []
    groups: list[tuple[int, list[str]]] = []
    for m in re.finditer(r"liczb\w*\s+(?:(?:całkowit|naturaln|dodatni|nieujemn|rzeczywist|testów|zapytań|operacji|"
                         r"wierzchołków|krawędzi|dni|wysp)\w*\s+)*:?\s*([A-Za-z]\w?(?:\s*,\s*[A-Za-z]\w?)*"
                         r"(?:\s+(?:i|oraz)\s+[A-Za-z]\w?)?)\b", inp):
        grp = []
        for nm in re.split(r"\s*,\s*|\s+(?:i|oraz)\s+", m.group(1)):
            names.append((m.start(1) + inp[m.start(1):].find(nm), nm))
            grp.append(nm)
        groups.append((m.start(1), grp))
    for m in chain_rx.finditer(inp):
        for part in re.split(r"\s*[≤<]\s*", m.group(0)):
            if _num_value(re.sub(r"\s+", "", part)) is None:
                ids = [x.strip() for x in part.split(",") if x.strip()]
                if ids and all(re.fullmatch(r"[A-Za-z]\w{0,2}", x) for x in ids):
                    groups.append((m.start(), ids))
    for nm in hints["constraints"]:
        p = re.search(rf"(?<![A-Za-z0-9_]){re.escape(nm)}(?![A-Za-z0-9_])", inp)
        if p:
            names.append((p.start(), nm))
    seen, ordered = set(), []
    for _, nm in sorted(names):
        if nm not in seen and nm.lower() not in ("i", "w", "z", "o", "u", "oraz", "do", "od", "na", "s") \
                and not re.fullmatch(r"[a-z]{3,}", nm):
            seen.add(nm)
            ordered.append(nm)
    for k, c in (hints.pop("_module_constraints", None) or {}).items():
        if k not in hints["constraints"]:
            hints["constraints"][k] = c
    # „1 ≤ n ≤ 10^1000000” — liczba ma tysiące cyfr: nie wolno jej losować jako int (ani brać max z podzadań)
    if "bignum" not in hints:
        for m in re.finditer(r"(?<![\w^])([A-Za-z]\w{0,2})\s*≤\s*(?:(\d{1,3})\s*\*\s*)?10\s*\^\s*(\d{2,})", inp):
            k = int(m.group(3))
            lead = m.group(2) or "1"
            if k > 18:
                cur = hints.setdefault("bignum", {}).get(m.group(1))
                if cur is None or cur["digits"] < len(lead) + k:
                    hints["bignum"][m.group(1)] = {"digits": len(lead) + k, "min": 1, "max_lead": lead, "max_pow10": k}
    for k in hints.get("bignum") or {}:
        c = hints["constraints"].get(k)
        if isinstance(c, dict):
            c.pop("max", None)
            c.pop("max_expr", None)
    hints["count_mentions"] = [m.group(1) for m in re.finditer(
        r"(?<![\wąćęłńóśźż])([A-Za-z]\w?)\s*(?:-\s*1\s+)?(?:linii|liniach|linijek|wierszy|wierszach|liczb|par|krawędzi|"
        r"zapytań|operacji|napisów|słów|testów|zestawów|elementów|punktów|odcinków|przedziałów|kolejnych)\b", inp)]
    hints["names"] = ordered
    bad = {"i", "w", "z", "o", "u", "s", "do", "od", "na"}
    hints["name_groups"] = [g for _, g in sorted(groups, key=lambda x: x[0])
                            if all(nm.lower() not in bad and not re.fullmatch(r"[a-z]{3,}", nm) for nm in g)]
    # globalne granice wartości
    g = {}
    m = re.search(r"(?:nie\s+przekr(?:acz|ocz)\w*|nie\s+większ\w*\s+(?:niż|od)|co\s+najwyżej|mniejsz\w*\s+(?:niż|od))\s+"
                  r"(-?\d[\d^*.,]*)", s, re.I)
    if m:
        v = _num_value(m.group(1).rstrip(".,"))
        if v is not None:
            g["max"] = v
            if re.search(r"warto\w+\s+bezwzgl", low):
                g["min"] = -v
    m = re.search(r"z\s+(?:zakresu|przedziału)\s*[〈<\[(⟨]\s*(-?\d[\d^*.]*)\s*[,;]\s*(-?\d[\d^*.]*)\s*[〉>\])⟩]", s)
    if m:
        a, b = _num_value(m.group(1)), _num_value(m.group(2))
        if a is not None and b is not None and a <= b:
            g.setdefault("min", a)
            g["max"] = b
    if re.search(r"liczb\w*\s+(?:całkowit\w+\s+)?dodatni", low) or re.search(r"dodatni\w*\s+liczb", low):
        g.setdefault("min", 1)
    elif re.search(r"nieujemn", low):
        g.setdefault("min", 0)
    hints["global"] = g
    fl = hints["flags"]
    fl["graph"] = bool(re.search(r"wierzchoł|krawęd|\bgraf|drzew", low))
    fl["directed"] = bool(re.search(r"skierowan", low)) and not re.search(r"nieskierowan", low)
    fl["undirected"] = bool(re.search(r"nieskierowan", low))
    fl["tree"] = bool(re.search(r"\bdrzew", low))
    fl["connected"] = bool(re.search(r"\bspójn", low)) and not re.search(r"niekoniecznie\s+spójn|nie\s+musi\s+być\s+spójn", low)
    fl["not_connected"] = bool(re.search(r"niekoniecznie\s+spójn|nie\s+musi\s+być\s+spójn", low))
    fl["dag"] = bool(re.search(r"acykliczn|\bdag\b", low))
    fl["sentinel"] = bool(re.search(r"kończ\w*\s+(?:dane|wejście|wczytywanie)|zakończ\w*\s+(?:wejści|dan)|"
                                    r"oznacza\w*\s+koniec|ostatni\w*\s+(?:wiersz|lini)\w*\s+(?:wejścia\s+)?zawiera\s+"
                                    r"(?:liczb\w*\s+)?(?:-1|0)", low))
    m_o = re.search(r"\bwyj´?\s?[śs]cie\b(?! dla)(.{0,700})", s[m_in.end():] if m_in else s, re.I | re.S)
    out_sec = (m_o.group(1) if m_o else "").lower()
    out_sec = re.split(r"przykład|przyklad|wejście dla|ocenianie", out_sec)[0]
    hints["output_section"] = out_sec
    fl["out_single"] = bool(re.search(r"\b(?:jedn[ąoa]|jedyn\w*|pojedyncz\w*)\s+(?:liczb|słow|wiersz|lini|napis|wartość)|"
                                      r"jedyny\w*\s+wiersz|\b(?:tak|nie)\b.{0,40}\b(?:nie|tak)\b", out_sec)) and not \
        re.search(r"dla\s+każde\w*|w\s+kolejn\w+|w\s+przeciwnym|w\s+drugim|po\s+jedn\w*\s+w\s+(?:każdym\s+)?wierszu|i-t\w*|każd\w*\s+(?:wierszu|linii)|"
                  r"(?<![\w])[a-z]\s+(?:wierszy|linii|liczb)", out_sec)
    mo = re.search(r"(?<![\w])([a-zA-Z])\s+(wierszy|linii|liczb|słów)", out_sec)
    fl["out_var"] = (mo.group(1), "lines" if mo.group(2) in ("wierszy", "linii") else "tokens") if mo else None
    mo = re.search(r"(?:z|w)\s+(jedn\w*|dwóch|dwu|trzech|czterech)\s+(?:linii|wierszy|linijek)", out_sec)
    fl["out_nlines"] = {"jedn": 1, "dwóch": 2, "dwu": 2, "trzech": 3, "czterech": 4}.get(
        (mo.group(1)[:4] if mo and mo.group(1).startswith("jedn") else (mo.group(1) if mo else "")), None)
    fl["out_per_query"] = bool(re.search(r"dla\s+każde\w*\s+(?:zapyta|operacj|pytani|polece)", out_sec))
    fl["matrix"] = bool(re.search(r"macierz|tablic\w*\s+(?:o\s+wymiarach\s+)?[a-z]\s*[x×*]\s*[a-z]\b|"
                                  r"(?<![\w])[a-z]\s*[x×*]\s*[a-z](?![\w])|po\s+[a-z]\s+liczb|[a-z]\s+liczb\w*\s+w\s+każd", low))
    fl["fixed_tuple"] = bool(re.search(r"(?:trzy|cztery|pięć|trójk\w*|czwórk\w*)\s+liczb|trójk", low))
    fl["distinct"] = bool(re.search(r"parami\s+różn|wszystkie\s+(?:te\s+)?(?:liczby|elementy|wartości)\s+(?:są\s+)?(?:parami\s+)?różn|"
                                    r"różnych\s+liczb|liczby\s+(?:są\s+)?różne", low))
    fl["multitest"] = bool(re.search(r"liczb\w*\s+(?:zestawów|testów|przypadków)|zestaw\w*\s+danych", low))
    fl["lowercase"] = bool(re.search(r"mał\w*\s+liter", low))
    fl["uppercase"] = bool(re.search(r"wielk\w*\s+liter|dużych\s+liter", low))
    fl["digits_only"] = bool(re.search(r"składa\w*\s+się\s+(?:wyłącznie\s+|tylko\s+)?z\s+cyfr", low))
    fl["binary"] = bool(re.search(r"(?:z|cyfr)\s+0\s+i\s+1|zer\s+i\s+jedynek|binarn", low))
    fl["permutation"] = bool(re.search(r"permutac", low))
    fl["sorted"] = bool(re.search(r"posortowan|niemalej|nieros|uporządkowan|w\s+kolejności\s+(?:rosnąc|malejąc)", low))
    return hints


def _merge_c(cons: dict, name: str, lo=None, hi=None):
    c = cons.setdefault(name, {})
    if lo is not None:
        if isinstance(lo, int):
            c["min"] = lo if not isinstance(c.get("min"), int) else min(c["min"], lo)
        elif "min" not in c:
            c["min"] = lo
    if hi is not None:
        if isinstance(hi, int):
            c["max"] = hi if not isinstance(c.get("max"), int) else max(c["max"], hi)
        else:
            c["max_expr"] = hi
            c.setdefault("max", hi)


def _apply_chain(terms: list[str], ops: list[str], hints: dict, weight: int):
    vals = []
    for t in terms:
        t = t.strip()
        v = _num_value(re.sub(r"\s+", "", t))
        if v is not None:
            vals.append(("num", v))
        else:
            ids = [x.strip() for x in t.split(",") if x.strip()]
            vals.append(("ids", ids))
    for idx, (kind, x) in enumerate(vals):
        if kind != "ids":
            continue
        lo = hi = None
        # lewa granica: najbliższa liczba po lewej
        for j in range(idx - 1, -1, -1):
            if vals[j][0] == "num":
                lo = vals[j][1] + sum(1 for o in ops[j:idx] if o == "<")
                break
        for j in range(idx + 1, len(vals)):
            if vals[j][0] == "num":
                hi = vals[j][1] - sum(1 for o in ops[idx:j] if o == "<")
                break
            if vals[j][0] == "ids" and len(vals[j][1]) == 1 and j == idx + 1:
                # x ≤ n — granica zależna od innej zmiennej (ciaśniejsza niż liczba dalej w łańcuchu)
                hi = vals[j][1][0] + ("-1" if ops[idx] == "<" else "")
                break
        for name in x:
            if not re.fullmatch(r"[A-Za-z]\w{0,3}", name) or name.lower() in ("s", "ms", "mb", "mib", "kb"):
                continue
            _merge_c(hints["constraints"], name, lo=lo, hi=hi)
        if idx + 1 < len(vals) and vals[idx + 1][0] == "ids":
            for a in x:
                for b in vals[idx + 1][1]:
                    hints["relations"].append([a, b, "lt" if ops[idx] == "<" else "le"])


# ============================================================================================== wyrażenia
_EXPR_CACHE: dict[str, ast.AST] = {}
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant, ast.Call,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd)


def eval_expr(e, env: dict):
    """Wartość wyrażenia (liczba, nazwa zmiennej, "n-1", "n*(n-1)/2", "10**9", "min(n,5)")."""
    if e is None:
        return None
    if isinstance(e, bool):
        return int(e)
    if isinstance(e, int):
        return e
    if isinstance(e, float):
        return int(e) if e.is_integer() else e
    s = str(e).strip()
    if s in env:
        return env[s]
    if _INT_RE.fullmatch(s):
        return int(s)
    s2 = s.replace("^", "**")
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?[eE]\+?\d+", s2):
        return int(float(s2))
    tree = _EXPR_CACHE.get(s2)
    if tree is None:
        try:
            tree = ast.parse(s2, mode="eval")
        except SyntaxError:
            raise FormatError(f"Niepoprawne wyrażenie „{s}”.")
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED):
                raise FormatError(f"Niedozwolone wyrażenie „{s}”.")
            if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in ("min", "max", "abs")):
                raise FormatError(f"Niedozwolona funkcja w „{s}”.")
        _EXPR_CACHE[s2] = tree
    return _ev(tree.body, env, s)


def _ev(node, env, src):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise FormatError(f"Niepoprawne wyrażenie „{src}”.")
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise FormatError(f"Nieznana zmienna „{node.id}” w „{src}”.")
    if isinstance(node, ast.UnaryOp):
        v = _ev(node.operand, env, src)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.Call):
        args = [_ev(a, env, src) for a in node.args]
        return {"min": min, "max": max, "abs": abs}[node.func.id](*args)
    a, b = _ev(node.left, env, src), _ev(node.right, env, src)
    op = node.op
    if isinstance(op, ast.Add):
        return a + b
    if isinstance(op, ast.Sub):
        return a - b
    if isinstance(op, ast.Mult):
        return a * b
    if isinstance(op, (ast.Div, ast.FloorDiv)):
        return a // b if b else 0
    if isinstance(op, ast.Mod):
        return a % b if b else 0
    if isinstance(op, ast.Pow):
        if abs(b) > 64:
            raise FormatError("Za duża potęga.")
        return a ** b
    raise FormatError(f"Niepoprawne wyrażenie „{src}”.")


def _int_expr(e, env, default=None):
    if e is None:
        return default
    v = eval_expr(e, env)
    if isinstance(v, float):
        v = int(math.floor(v))
    return v


# ============================================================================================== parsowanie
class Parsed:
    """Wynik parsowania: węzły równoległe do spec["items"] + środowisko zmiennych."""

    def __init__(self, nodes: list, env: dict):
        self.nodes = nodes
        self.env = env

    def to_dict(self):
        return {"nodes": self.nodes, "env": self.env}


def _field_bounds(f: dict, env: dict, spec_vars: dict | None):
    if f.get("var") and spec_vars is not None:
        vd = spec_vars.get(f["var"], {})
        return _int_expr(vd.get("min"), env), _int_expr(vd.get("max"), env)
    return _num_or_expr(f.get("min"), env), _num_or_expr(f.get("max"), env)


def _num_or_expr(e, env):
    if e is None:
        return None
    try:
        v = eval_expr(e, env)
    except FormatError:
        return None
    return v


def _bigint_max(f: dict) -> str | None:
    """Największa dozwolona wartość pola bigint jako napis (np. 10^K → '1' + K zer), gdy jest znana."""
    lead, k = f.get("max_lead"), f.get("max_pow10")
    if isinstance(lead, str) and lead.isdigit() and isinstance(k, int) and k >= 0:
        return lead + "0" * k
    return None


def _parse_field(f: dict, tok: str, env: dict, strict: bool, spec_vars: dict | None):
    t = f.get("type", "int")
    if t == "bigint":
        if not _INT_RE.fullmatch(tok):
            raise FormatError(f"Oczekiwano liczby całkowitej (cyfr), jest „{tok[:20]}”.")
        if strict:
            dg = tok.lstrip("+-")
            if len(dg) > 1 and dg[0] == "0":
                raise FormatError(f"Liczba „{tok[:20]}…” ma zera wiodące.")
            if tok.startswith("-") and dg != "0":
                raise FormatError("Liczba nie może być ujemna.")
            if (f.get("min") or 0) >= 1 and dg.strip("0") == "":
                raise FormatError("Liczba musi być dodatnia.")
            mx = _int_expr(f.get("max_len"), env)
            if mx is not None and len(dg) > mx:
                raise FormatError(f"Liczba ma za dużo cyfr ({len(dg)} > {mx}).")
            top = _bigint_max(f)
            if top is not None and len(dg) == len(top) and dg > top:
                raise FormatError("Liczba jest większa niż pozwala treść.")
        return tok
    if t == "int":
        if not _INT_RE.fullmatch(tok):
            raise FormatError(f"Oczekiwano liczby całkowitej, jest „{tok[:20]}”.")
        v = int(tok)
        if strict:
            lo, hi = _field_bounds(f, env, spec_vars)
            if lo is not None and v < lo or hi is not None and v > hi:
                raise FormatError(f"Wartość {v} poza zakresem [{lo}, {hi}]" + (f" ({f['var']})" if f.get("var") else "") + ".")
        if f.get("var"):
            env[f["var"]] = v
        return v
    if t == "float":
        if not _FLOAT_RE.fullmatch(tok):
            raise FormatError(f"Oczekiwano liczby, jest „{tok[:20]}”.")
        v = float(tok)
        if strict:
            lo, hi = _field_bounds(f, env, spec_vars)
            if lo is not None and v < lo - 1e-9 or hi is not None and v > hi + 1e-9:
                raise FormatError(f"Wartość {tok} poza zakresem [{lo}, {hi}].")
        return tok
    if t == "word":
        if strict and f.get("choices") and tok not in f["choices"]:
            raise FormatError(f"Nieoczekiwane słowo „{tok[:20]}”.")
        return tok
    # str
    if strict:
        alph = f.get("alphabet")
        if alph and any(c not in alph for c in tok):
            raise FormatError(f"Napis „{tok[:20]}” ma znaki spoza alfabetu.")
        lo, hi = _int_expr(f.get("min_len"), env), _int_expr(f.get("max_len"), env)
        if lo is not None and len(tok) < lo or hi is not None and len(tok) > hi:
            raise FormatError(f"Napis ma złą długość ({len(tok)}).")
    return tok


def _count_of(e, env, avail: int, what: str):
    if isinstance(e, dict):     # zakres: czytamy wszystko, co jest
        lo, hi = _int_expr(e.get("min"), env, 0), _int_expr(e.get("max"), env, None)
        c = avail
        if c < lo or (hi is not None and c > hi):
            raise FormatError(f"Liczba {what} ({c}) poza zakresem [{lo}, {hi}].")
        return c
    c = _int_expr(e, env)
    if c is None or c < 0:
        raise FormatError(f"Liczba {what} ujemna ({c}).")
    return c


class _Reader:
    __slots__ = ("lines", "pos")

    def __init__(self, lines, pos=0):
        self.lines = lines
        self.pos = pos

    def line(self, what="linii"):
        if self.pos >= len(self.lines):
            raise FormatError(f"Wejście kończy się za wcześnie (brak {what}).")
        ln = self.lines[self.pos]
        self.pos += 1
        return ln


def parse(spec: dict, text: str, strict: bool = True) -> Parsed:
    """Parsuje wejście zgodnie ze specyfikacją. Rzuca FormatError z opisem po polsku."""
    lines = _tok_lines(text)
    rd = _Reader(lines)
    env: dict = {}
    nodes = _parse_items(spec.get("items") or [], rd, env, strict, spec.get("vars") or {})
    if rd.pos != len(lines):
        raise FormatError(f"Nadmiarowe dane od linii {rd.pos + 1}: „{' '.join(lines[rd.pos])[:40]}”.")
    return Parsed(nodes, env)


def validate(spec: dict, text: str, strict: bool = True) -> tuple[bool, str]:
    try:
        parse(spec, text, strict)
        return True, ""
    except FormatError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"Błąd specyfikacji: {e}"


def _parse_items(items, rd, env, strict, svars, rec=None):
    out = []
    for it in items:
        before = dict(env) if rec is not None else None
        nd = _parse_item(it, rd, env, strict, svars, rec)
        if rec is not None:
            rec(it, nd, before, dict(env))
        out.append(nd)
    return out


def _parse_item(it, rd: _Reader, env: dict, strict: bool, svars: dict, rec=None):
    k = it.get("kind")
    if k == "line":
        toks = rd.line("linii z wartościami")
        fields = it.get("fields") or []
        if len(toks) != len(fields):
            raise FormatError(f"Linia {rd.pos}: oczekiwano {len(fields)} wartości, jest {len(toks)}.")
        vals = [_parse_field(f, t, env, False, svars) for f, t in zip(fields, toks)]
        if strict:      # granice mogą odwoływać się do zmiennych z dalszej części tej samej linii
            for f, t in zip(fields, toks):
                _parse_field(f, t, env, True, svars)
        return {"vals": vals}
    if k == "array":
        elem = it.get("elem") or {"type": "int"}
        if it.get("layout") == "column":
            n = _count_of(it.get("len"), env, _avail_lines(rd, 1), "elementów")
            vals = []
            for _ in range(n):
                toks = rd.line("elementu tablicy")
                if len(toks) != 1:
                    raise FormatError(f"Linia {rd.pos}: oczekiwano jednej wartości.")
                vals.append(_parse_field(elem, toks[0], env, strict, None))
        else:
            if isinstance(it.get("len"), dict):
                toks = rd.line("tablicy")
                n = _count_of(it.get("len"), env, len(toks), "elementów")
            else:
                n = _count_of(it.get("len"), env, 0, "elementów")
                if n == 0:
                    return {"vals": []}
                toks = rd.line("tablicy")
            if len(toks) != n:
                raise FormatError(f"Linia {rd.pos}: oczekiwano {n} liczb, jest {len(toks)}.")
            vals = [_parse_field(elem, t, env, strict, None) for t in toks]
        if strict:
            _check_order(it, vals)
        return {"vals": vals}
    if k == "parents":
        n = _int_expr(it.get("n"), env)
        if n is None or n < 1:
            raise FormatError("Liczba wierzchołków musi być ≥ 1.")
        if n == 1:
            return {"vals": []}
        toks = rd.line("tablicy rodziców")
        if len(toks) != n - 1:
            raise FormatError(f"Linia {rd.pos}: oczekiwano {n - 1} rodziców, jest {len(toks)}.")
        base = it.get("base", 1)
        vals = []
        for i, t in enumerate(toks):
            if not _INT_RE.fullmatch(t):
                raise FormatError("Rodzic musi być liczbą.")
            v = int(t)
            if strict and not (base <= v <= i + base):
                raise FormatError(f"Rodzic p{i + 2} = {v} poza zakresem [{base}, {i + base}].")
            vals.append(v)
        return {"vals": vals}
    if k in ("repeat", "graph"):
        fields = _graph_fields(it) if k == "graph" else (it.get("fields") or [])
        cnt = it.get("count")
        if isinstance(cnt, dict) and cnt.get("until") is not None:
            stop = str(cnt["until"]).split()
            rows = []
            while True:
                toks = rd.line(f"linii kończącej „{cnt['until']}”")
                if toks == stop:
                    break
                if len(toks) != len(fields):
                    raise FormatError(f"Linia {rd.pos}: oczekiwano {len(fields)} wartości, jest {len(toks)}.")
                rows.append([_parse_field(f, t, env, strict, None) for f, t in zip(fields, toks)])
            return {"rows": rows}
        n = _count_of(cnt, env, _avail_lines(rd, len(fields)), "linii")
        rows = []
        for _ in range(n):
            toks = rd.line("kolejnej linii")
            if len(toks) != len(fields):
                raise FormatError(f"Linia {rd.pos}: oczekiwano {len(fields)} wartości, jest {len(toks)}.")
            rows.append([_parse_field(f, t, env, strict, None) for f, t in zip(fields, toks)])
        if strict:
            for a, b, rel in it.get("pairs") or []:
                for r in rows:
                    if (rel == "le" and r[a] > r[b]) or (rel == "lt" and r[a] >= r[b]):
                        raise FormatError(f"Wymagane {('≤' if rel == 'le' else '<')} między polami {a + 1} i {b + 1}.")
            if k == "graph":
                _check_graph(it, rows, env)
        return {"rows": rows}
    if k == "matrix":
        r = _count_of(it.get("rows"), env, 0, "wierszy")
        c = _count_of(it.get("cols"), env, 0, "kolumn")
        elem = it.get("elem") or {"type": "int"}
        rows = []
        for _ in range(r):
            toks = rd.line("wiersza macierzy")
            if len(toks) != c:
                raise FormatError(f"Linia {rd.pos}: oczekiwano {c} liczb w wierszu macierzy, jest {len(toks)}.")
            rows.append([_parse_field(elem, t, env, strict, None) for t in toks])
        return {"rows": rows}
    if k == "grid":
        r = _count_of(it.get("rows"), env, _avail_lines(rd, 1), "wierszy")
        c = _int_expr(it.get("cols"), env) if it.get("cols") is not None else None
        rows = []
        for _ in range(r):
            toks = rd.line("wiersza planszy")
            if len(toks) != 1:
                raise FormatError(f"Linia {rd.pos}: wiersz planszy powinien być jednym napisem.")
            s = toks[0]
            if c is not None and len(s) != c:
                raise FormatError(f"Linia {rd.pos}: wiersz planszy ma długość {len(s)}, oczekiwano {c}.")
            if strict and it.get("alphabet") and any(ch not in it["alphabet"] for ch in s):
                raise FormatError(f"Linia {rd.pos}: znaki spoza alfabetu planszy.")
            rows.append(s)
        if strict and it.get("unique"):
            for ch in it["unique"]:
                if sum(r.count(ch) for r in rows) != 1:
                    raise FormatError(f"Znak „{ch}” powinien wystąpić na planszy dokładnie raz.")
        return {"rows": rows}
    if k == "string":
        toks = rd.line("napisu")
        if len(toks) != 1:
            raise FormatError(f"Linia {rd.pos}: oczekiwano jednego napisu.")
        s = toks[0]
        if it.get("len") is not None:
            n = _int_expr(it.get("len"), env)
            if len(s) != n:
                raise FormatError(f"Napis ma długość {len(s)}, oczekiwano {n}.")
        elif strict:
            lo, hi = _int_expr(it.get("min_len"), env), _int_expr(it.get("max_len"), env)
            if lo is not None and len(s) < lo or hi is not None and len(s) > hi:
                raise FormatError(f"Napis ma długość {len(s)} poza zakresem [{lo}, {hi}].")
        if strict and it.get("alphabet") and any(ch not in it["alphabet"] for ch in s):
            raise FormatError("Napis zawiera znaki spoza alfabetu.")
        return {"val": s}
    if k == "queries":
        n = _count_of(it.get("count"), env, _avail_lines(rd, 1), "zapytań")
        types = {str(t.get("tag")): t for t in it.get("types") or []}
        rows = []
        for _ in range(n):
            toks = rd.line("zapytania")
            t = types.get(toks[0]) if toks else None
            if t is None:
                raise FormatError(f"Linia {rd.pos}: nieznany typ zapytania „{(toks or [''])[0][:20]}”.")
            fields = t.get("fields") or []
            if len(toks) - 1 != len(fields):
                raise FormatError(f"Linia {rd.pos}: zapytanie „{toks[0]}” ma mieć {len(fields)} argumentów.")
            row = [toks[0]] + [_parse_field(f, x, env, strict, None) for f, x in zip(fields, toks[1:])]
            if strict:
                for a, b, rel in t.get("pairs") or []:
                    if (rel == "le" and row[1 + a] > row[1 + b]) or (rel == "lt" and row[1 + a] >= row[1 + b]):
                        raise FormatError(f"Linia {rd.pos}: wymagane uporządkowanie argumentów.")
            rows.append(row)
        return {"rows": rows}
    if k == "lists":
        n = _count_of(it.get("count"), env, _avail_lines(rd, 1), "linii")
        elem = it.get("elem") or {"type": "int"}
        rows = []
        for _ in range(n):
            toks = rd.line("listy")
            if not toks or not _INT_RE.fullmatch(toks[0]) or int(toks[0]) != len(toks) - 1:
                raise FormatError(f"Linia {rd.pos}: pierwsza liczba ma być długością listy.")
            if strict:
                lo, hi = _int_expr(it.get("min_len"), env), _int_expr(it.get("max_len"), env)
                if lo is not None and len(toks) - 1 < lo or hi is not None and len(toks) - 1 > hi:
                    raise FormatError(f"Linia {rd.pos}: długość listy poza zakresem.")
            rows.append([_parse_field(elem, x, env, strict, None) for x in toks[1:]])
        return {"rows": rows}
    if k == "block":
        n = _count_of(it.get("count"), env, 10 ** 9, "testów")
        iters = []
        for _ in range(n):
            local = dict(env)
            nodes = _parse_items(it.get("items") or [], rd, local, strict, svars, rec)
            iters.append({"nodes": nodes, "env": {k2: v for k2, v in local.items() if env.get(k2, None) != v or k2 not in env}})
        return {"iters": iters}
    raise FormatError(f"Nieznany rodzaj elementu „{k}”.")


def _avail_lines(rd: _Reader, _w) -> int:
    return len(rd.lines) - rd.pos


def _graph_fields(it):
    vf = {"type": "int", "min": it.get("base", 1), "max": f"({it.get('n')})" + ("-1" if it.get("base", 1) == 0 else "")}
    fs = [dict(vf, role="u"), dict(vf, role="v")]
    if it.get("weight"):
        fs.append(dict(it["weight"], role="w"))
    return fs


def _check_order(it, vals):
    o = it.get("order")
    if not o or len(vals) < 2:
        return
    if o == "sorted" and any(vals[i] > vals[i + 1] for i in range(len(vals) - 1)):
        raise FormatError("Tablica powinna być posortowana niemalejąco.")
    if o == "sorted_desc" and any(vals[i] < vals[i + 1] for i in range(len(vals) - 1)):
        raise FormatError("Tablica powinna być posortowana nierosnąco.")
    if o == "distinct" and len(set(vals)) != len(vals):
        raise FormatError("Elementy tablicy powinny być różne.")
    if o == "permutation" and sorted(vals) != list(range(1, len(vals) + 1)):
        raise FormatError("Tablica powinna być permutacją 1..n.")


def _check_graph(it, rows, env):
    n = _int_expr(it.get("n"), env)
    base = it.get("base", 1)
    if it.get("simple", False):
        seen = set()
        for r in rows:
            a, b = r[0], r[1]
            if a == b:
                raise FormatError("Pętla własna w grafie prostym.")
            key = (a, b) if it.get("directed") else (min(a, b), max(a, b))
            if key in seen:
                raise FormatError("Powtórzona krawędź w grafie prostym.")
            seen.add(key)
    if it.get("tree") or it.get("connected"):
        if n and n > 1 and not _connected(n, [(r[0] - base, r[1] - base) for r in rows]):
            raise FormatError("Graf powinien być spójny." if not it.get("tree") else "Krawędzie nie tworzą drzewa.")
    if it.get("dag") and it.get("directed"):
        if _has_cycle(n, [(r[0] - base, r[1] - base) for r in rows]):
            raise FormatError("Graf powinien być acykliczny.")


def _connected(n, edges) -> bool:
    par = list(range(n))

    def f(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    comps = n
    for a, b in edges:
        if 0 <= a < n and 0 <= b < n:
            ra, rb = f(a), f(b)
            if ra != rb:
                par[ra] = rb
                comps -= 1
    return comps == 1


def _has_cycle(n, edges) -> bool:
    indeg = [0] * n
    adj = [[] for _ in range(n)]
    for a, b in edges:
        if 0 <= a < n and 0 <= b < n:
            adj[a].append(b)
            indeg[b] += 1
    st = [i for i in range(n) if indeg[i] == 0]
    seen = 0
    while st:
        x = st.pop()
        seen += 1
        for y in adj[x]:
            indeg[y] -= 1
            if indeg[y] == 0:
                st.append(y)
    return seen != n


# ============================================================================================== renderowanie
def render(spec: dict, parsed: Parsed) -> str:
    out: list[str] = []
    _render_items(spec.get("items") or [], parsed.nodes, parsed.env, out)
    return "\n".join(out) + "\n"


def _fmt(v) -> str:
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _render_items(items, nodes, env, out):
    for it, nd in zip(items, nodes):
        k = it.get("kind")
        if k == "line":
            vals = [env.get(f["var"], v) if f.get("var") else v for f, v in zip(it.get("fields") or [], nd["vals"])]
            out.append(" ".join(_fmt(v) for v in vals))
        elif k in ("array", "parents"):
            if it.get("layout") == "column":
                out.extend(_fmt(v) for v in nd["vals"])
            else:
                out.append(" ".join(_fmt(v) for v in nd["vals"]))
        elif k in ("repeat", "graph", "matrix", "queries"):
            out.extend(" ".join(_fmt(v) for v in r) for r in nd["rows"])
            if isinstance(it.get("count"), dict) and it["count"].get("until") is not None:
                out.append(str(it["count"]["until"]))
        elif k == "lists":
            out.extend(" ".join([str(len(r))] + [_fmt(v) for v in r]) for r in nd["rows"])
        elif k == "grid":
            out.extend(nd["rows"])
        elif k == "string":
            out.append(nd["val"])
        elif k == "block":
            for itr in nd["iters"]:
                local = dict(env)
                local.update(itr["env"])
                _render_items(it.get("items") or [], itr["nodes"], local, out)


# ============================================================================================== wnioskowanie
class _St:
    """Stan jednego przykładu w przeszukiwaniu."""
    __slots__ = ("lines", "pos", "env")

    def __init__(self, lines, pos, env):
        self.lines, self.pos, self.env = lines, pos, env


class _Search:
    """DFS po hipotezach gramatyki z ograniczeniem kosztu (branch & bound); propozycje z pierwszego przykładu,
    każda sprawdzana na wszystkich przykładach naraz."""

    def __init__(self, samples: list[list[list[str]]], hints: dict, allow_block=True, max_nodes=20000,
                 env0: list[dict] | None = None, var_order: list[str] | None = None, prefix="v", deadline=None,
                 keep: int = 1):
        self.samples = samples
        self.keep = max(1, keep)
        self.solutions: list[tuple[float, list]] = []
        self.hints = hints or {}
        self.allow_block = allow_block
        self.max_nodes = max_nodes
        self.nodes = 0
        self.best_cost = float("inf")
        self.best_items = None
        self.env0 = env0 or [{} for _ in samples]
        self.var_order0 = list(var_order or [])
        self.prefix = prefix
        self.deadline = deadline
        self.counter = 0

    def run(self):
        states = [_St(lines, 0, dict(e)) for lines, e in zip(self.samples, self.env0)]
        try:
            self._dfs(states, [], 0.0, [[v] for v in self.var_order0], set())
        except RecursionError:      # bardzo długie wejście bez struktury — zostaje to, co już znaleziono
            pass
        return self.best_items

    def _bound(self):
        if not self.solutions:
            return float("inf")
        if len(self.solutions) < self.keep:
            return self.solutions[0][0] + 0.4       # alternatywy tylko „prawie równie tanie” (do rerankingu)
        return min(self.solutions[-1][0], self.solutions[0][0] + 0.4)

    def _evidence(self, item, states) -> float:
        """Kara za słabe dowody liczności (licznik ≤ 2 we wszystkich przykładach), premia za zgodność
        w wielu przykładach o różnych licznikach."""
        key = next((k for k in ("len", "count", "rows") if isinstance(item.get(k), str)), None)
        if key is None and item.get("kind") == "parents":
            item = dict(item, len=f"{item['n']}-1")
            key = "len"
        if key is None:
            return 0.0
        vals = []
        for st in states:
            try:
                vals.append(_int_expr(item[key], st.env))
            except FormatError:
                return 0.0
        mx = max(vals) if vals else 0
        adj = 0.5 if mx <= 1 else (0.25 if mx == 2 else 0.0)
        if len(set(vals)) >= 2:
            adj -= 0.1
        m = re.fullmatch(r"([A-Za-z_]\w*)-1", str(item.get(key)))
        if m and item.get("kind") == "repeat" and len(item.get("fields") or []) in (2, 3):
            def is_tree(st):
                nv = st.env.get(m.group(1))
                if not isinstance(nv, int) or nv < 2:
                    return False
                rows = st.lines[st.pos: st.pos + nv - 1]
                try:
                    e = [(int(r[0]), int(r[1])) for r in rows]
                except (ValueError, IndexError):
                    return False
                base = 0 if min(min(a, b) for a, b in e) == 0 else 1
                return all(base <= a < nv + base and base <= b < nv + base for a, b in e) and \
                    _connected(nv, [(a - base, b - base) for a, b in e])
            if all(is_tree(st) for st in states):
                adj -= 0.12
        return adj

    def _new_var(self):
        self.counter += 1
        return f"{self.prefix}{self.counter}"

    def _dfs(self, states, items, cost, var_order, used):
        self.nodes += 1
        if self.nodes > self.max_nodes or cost >= self._bound() - 1e-12:
            return
        if self.deadline and self.nodes % 64 == 0 and time.monotonic() > self.deadline:
            self.nodes = self.max_nodes + 1
            return
        done = [st.pos >= len(st.lines) for st in states]
        if all(done):
            self.solutions.append((cost, copy.deepcopy(items)))
            self.solutions.sort(key=lambda x: x[0])
            del self.solutions[self.keep:]
            self.best_cost, self.best_items = self.solutions[0]
            return
        if any(done):
            return
        props = self._propose(states, items, var_order, used)
        props.sort(key=lambda p: p[0])
        for pcost, item, new_vars, used_add in props:
            if cost + pcost >= self._bound() - 1e-12:
                continue
            nstates = []
            ok = True
            for st in states:
                env = dict(st.env)
                rd = _Reader(st.lines, st.pos)
                try:
                    _parse_item(item, rd, env, False, None)
                except FormatError:
                    ok = False
                    break
                except (ValueError, OverflowError):
                    ok = False
                    break
                nstates.append(_St(st.lines, rd.pos, env))
            if not ok:
                continue
            if any(ns.pos == st.pos for ns, st in zip(nstates, states)) and item.get("kind") != "array":
                continue
            adj = self._evidence(item, states)
            if cost + pcost + adj >= self._bound() - 1e-12:
                continue
            self._dfs(nstates, items + [item], cost + pcost + adj, var_order + ([new_vars] if new_vars else []),
                      used | used_add)
            if self.nodes > self.max_nodes:
                return

    # ------------------------------------------------------------------------------------------ propozycje
    def _propose(self, states, items, var_order, used):
        st = states[0]
        lines, p, env = st.lines, st.pos, st.env
        L = lines[p]
        R = len(lines) - p
        cls = [_tclass(t) for t in L]
        props = []
        # --- struktury liczone zmiennymi (najpierw najświeższe)
        # kolejność: najświeższa linia nagłówka najpierw, w jej obrębie od lewej; nieużyte przed użytymi
        cand_vars = [v for g in reversed(var_order) for v in g if isinstance(env.get(v), int)]
        cand_vars = [v for v in cand_vars if v not in used] + [v for v in cand_vars if v in used]
        for rank, v in enumerate(cand_vars[:6]):
            c = env[v]
            reuse = 0.25 if v in used else 0.0
            for expr, cnt, extra in ((v, c, 0.0), (f"{v}-1", c - 1, 0.15)):
                if cnt < 1 or cnt > 10 ** 7:
                    continue
                base = 0.6 + 0.08 * rank + extra + reuse
                ua = {v}
                # (a) tablica w jednej linii
                if len(L) == cnt and len(set(cls)) == 1:
                    elem = _field_for_class(cls[0])
                    props.append((base, {"kind": "array", "len": expr, "layout": "row", "elem": elem}, [], ua))
                    if extra and cls[0] == "int" and cnt >= 1 and all(
                            _INT_RE.fullmatch(t) and 1 <= int(t) <= i + 1 for i, t in enumerate(L)):
                        props.append((base - 0.05, {"kind": "parents", "n": v, "base": 1}, [], ua))
                # (e) napis długości cnt
                if len(L) == 1 and cls[0] in ("word", "str", "int") and len(L[0]) == cnt and (cls[0] != "int" or cnt >= 2):
                    if cls[0] != "int" or set(L[0]) <= set("0123456789"):
                        props.append((base + (0.0 if cls[0] != "int" else 0.3),
                                      {"kind": "string", "len": expr, "alphabet": ""}, [], ua))
                # (a') tablica tokenów mieszanych (np. ONP: „7 3 + 9”)
                if len(L) == cnt and cnt >= 2 and len(set(cls)) > 1:
                    props.append((base + 0.1, {"kind": "array", "len": expr, "layout": "row",
                                               "elem": {"type": "str"}}, [], ua))
                if cnt > R:
                    continue
                block = lines[p:p + cnt]
                lens = {len(x) for x in block}
                # (b) kolumna liczb
                if lens == {1}:
                    bcls = {_tclass(x[0]) for x in block}
                    if bcls <= {"int"} or bcls <= {"int", "float"}:
                        props.append((base + 0.02, {"kind": "array", "len": expr, "layout": "column",
                                                    "elem": _field_for_class("float" if "float" in bcls else "int")}, [], ua))
                    elif bcls <= {"word", "str"}:
                        slen = {len(x[0]) for x in block}
                        for w in cand_vars:
                            if len(slen) == 1 and env[w] in slen and env[w] >= 1:
                                props.append((base, {"kind": "grid", "rows": expr, "cols": w,
                                                     "alphabet": ""}, [], ua | {w}))
                        props.append((base + 0.1, {"kind": "repeat", "count": expr,
                                                   "fields": [{"type": "str"}]}, [], ua))
                # (c) wiersze o stałej liczbie pól
                if len(lens) == 1 and min(lens) >= 2:
                    k = min(lens)
                    col_cls = [_merge_cls({_tclass(x[i]) for x in block}) for i in range(k)]
                    if all(c2 is not None for c2 in col_cls):
                        fields = [_field_for_class(c2) for c2 in col_cls]
                        q = _queries_item(expr, block) if col_cls[0] == "word" and len({x[0] for x in block}) <= 6 else None
                        if q:
                            props.append((base + 0.05, q, [], ua))
                        else:
                            props.append((base, {"kind": "repeat", "count": expr, "fields": fields}, [], ua))
                        if all(c2 in ("int", "float") for c2 in col_cls):
                            for w in cand_vars:
                                if env[w] == k and k >= 2:
                                    fl = self.hints.get("flags") or {}
                                    mcost = 0.1 if k <= 2 else (0.02 if k == 3 else -0.05)
                                    if fl.get("matrix"):
                                        mcost -= 0.12
                                    elif fl.get("fixed_tuple"):
                                        mcost += 0.15
                                    if w == v and not fl.get("matrix"):
                                        mcost += 0.05
                                    props.append((base + mcost,
                                                  {"kind": "matrix", "rows": expr, "cols": w,
                                                   "elem": _field_for_class("float" if "float" in col_cls else "int")},
                                                  [], ua | {w}))
                # (d) zmienna liczba pól: zapytania z typem / listy „k x1..xk”
                if len(lens) > 1 or (block and _tclass(block[0][0]) == "word"):
                    if all(x for x in block):
                        q = _queries_item(expr, block)
                        if q:
                            props.append((base + 0.05, q, [], ua))
                        if all(_INT_RE.fullmatch(x[0]) and int(x[0]) == len(x) - 1 for x in block):
                            ecls = _merge_cls({_tclass(t) for x in block for t in x[1:]} or {"int"})
                            props.append((base + 0.02, {"kind": "lists", "count": expr,
                                                        "elem": _field_for_class(ecls or "int")}, [], ua))
            # (f) multitest: blok powtarzany t razy (tylko zaraz po linii z jedną liczbą)
            if (self.allow_block and rank == 0 and items and items[-1].get("kind") == "line"
                    and len(items[-1]["fields"]) == 1 and items[-1]["fields"][0].get("var") == v and v not in used
                and var_order and var_order[-1] == [v]
                    and c >= 2 and R >= 2):
                props.extend(self._block_props(states, v, c, var_order))
        # --- linia o stałej liczbie wartości (liczby całkowite stają się zmiennymi)
        fields, new_vars = [], []
        for t, c2 in zip(L, cls):
            if c2 == "int":
                nv = self._new_var()
                fields.append({"type": "int", "var": nv})
                new_vars.append(nv)
            else:
                fields.append(_field_for_class(c2, t))
        props.append((1.0 + 0.02 * len(L), {"kind": "line", "fields": fields}, new_vars, set()))
        # --- napis bez znanej długości
        if len(L) == 1 and cls[0] in ("word", "str"):
            props.append((1.0, {"kind": "string", "len": None, "alphabet": ""}, [], set()))
        # --- awaryjnie: linia o zmiennej długości / linie do końca wejścia (tylko gdy przykłady się różnią)
        diff_tok = len({len(s2.lines[s2.pos]) for s2 in states if s2.pos < len(s2.lines)}) > 1
        diff_rest = len({len(s2.lines) - s2.pos for s2 in states}) > 1
        if diff_tok and len(L) >= 1 and len(set(cls)) == 1 and cls[0] in ("int", "float"):
            props.append((1.7, {"kind": "array", "len": {"min": 0, "max": None}, "layout": "row",
                                "elem": _field_for_class(cls[0])}, [], set()))
        rest = lines[p:]
        last = " ".join(lines[-1])
        if (len(rest) >= 2 and len(lines[-1]) <= 3 and all(t in _SENTINEL_TOKENS for t in lines[-1])
                and all(" ".join(s2.lines[-1]) == last for s2 in states)
                and ((self.hints.get("flags") or {}).get("sentinel") or len(states) >= 2)):
            body = rest[:-1]
            if len({len(x) for x in body}) == 1:
                k = len(body[0])
                col_cls = [_merge_cls({_tclass(x[i]) for x in body}) for i in range(k)]
                if all(col_cls) and all(" ".join(x) != last for x in body):
                    props.append((0.95, {"kind": "repeat", "count": {"until": last},
                                         "fields": [_field_for_class(c2) for c2 in col_cls]}, [], set()))
        if diff_rest and len(rest) >= 2 and len({len(x) for x in rest}) == 1:
            k = len(rest[0])
            col_cls = [_merge_cls({_tclass(x[i]) for x in rest}) for i in range(k)]
            if all(col_cls):
                props.append((1.9, {"kind": "repeat", "count": {"min": 0, "max": None},
                                    "fields": [_field_for_class(c2) for c2 in col_cls]}, [], set()))
        return props

    def _block_props(self, states, v, c, var_order):
        st = states[0]
        lines, p = st.lines, st.pos
        R = len(lines) - p
        out = []
        maxk = min(R - (c - 1), 60)
        for k in range(1, maxk + 1):
            if p + k < len(lines) and len(lines[p + k]) != len(lines[p]):
                continue
            body_lines = lines[p:p + k]
            sub = _Search([body_lines], self.hints, allow_block=False, max_nodes=800,
                          env0=[dict(st.env)], var_order=[], prefix=f"b{k}_", deadline=self.deadline, keep=4)
            sub.run()
            for bcost, body in sub.solutions:
                item = {"kind": "block", "count": v, "items": body}
                ok = True
                for s2 in states:
                    rd = _Reader(s2.lines, s2.pos)
                    try:
                        _parse_item(item, rd, dict(s2.env), False, None)
                    except (FormatError, ValueError):
                        ok = False
                        break
                if ok:
                    out.append((0.4 + bcost * 0.6, item, [], {v}))
                    break
            if len(out) >= 3:
                break
        return out


def _merge_cls(s: set) -> str | None:
    if s <= {"int"}:
        return "int"
    if s <= {"int", "float"}:
        return "float"
    if s <= {"word"}:
        return "word"
    if s <= {"word", "str", "int"}:
        return "str"
    return None


def _field_for_class(c: str, tok: str | None = None) -> dict:
    if c == "int":
        return {"type": "int"}
    if c == "float":
        return {"type": "float"}
    if c == "word":
        return {"type": "word", "choices": [tok] if tok else []}
    return {"type": "str"}


def _queries_item(expr, block):
    tags: dict[str, list] = {}
    for x in block:
        tags.setdefault(x[0], []).append(x[1:])
    if len(tags) > 8 or (len(tags) == len(block) and len(block) > 1):
        return None
    tcls = {_tclass(t) for t in tags}
    if tcls <= {"int"}:
        if len(tags) > 5 or any(not (0 <= int(t) <= 20) for t in tags) or len(block) < 3:
            return None
        if len({len(r[0]) for r in tags.values()}) < 2 and len(tags) > 1:
            return None     # wszystkie typy mają tyle samo argumentów — zwykłe wiersze wystarczą
        if len(block) < 2 * len(tags) or max(len(r) for r in tags.values()) < 2:
            return None
    elif not tcls <= {"word", "str"}:
        return None
    types = []
    for tag, rows in tags.items():
        lens = {len(r) for r in rows}
        if len(lens) != 1:
            return None
        k = lens.pop()
        fields = []
        for i in range(k):
            c = _merge_cls({_tclass(r[i]) for r in rows})
            if c is None:
                return None
            fields.append(_field_for_class(c))
        types.append({"tag": tag, "fields": fields})
    if not any(t["fields"] for t in types):
        return None
    tag_cls = _merge_cls({_tclass(t) for t in tags})
    return {"kind": "queries", "count": expr, "tag_type": "int" if tag_cls == "int" else "word", "types": types}


def infer(sample_inputs: list[str], statement_text: str | None = None, max_samples: int = 40,
          time_budget: float = 8.0) -> dict:
    """Wnioskuje specyfikację formatu wejścia z przykładowych wejść (i opcjonalnie treści zadania)."""
    raw = [s for s in (sample_inputs or []) if isinstance(s, str) and s.strip()]
    if not raw:
        raise ValueError("Podaj co najmniej jeden przykład wejścia.")
    hints = statement_hints(statement_text) if statement_text else statement_hints(None)
    # olbrzymie przykłady (np. test „ocen” z liczbą o milionie cyfr) tylko sprawdzamy, nie uczymy się z nich formatu
    small = [s for s in raw if len(s) <= HUGE_SAMPLE and not re.search(r"\S{4001}", s)]
    samples = [_tok_lines(s) for s in (small or raw)]
    order = sorted(range(len(samples)), key=lambda i: (-len(samples[i]), -sum(len(x) for x in samples[i])))
    if len(order) > max_samples:
        # najbardziej informacyjne + kilka najmniejszych (przypadki brzegowe)
        order = order[: max_samples - 5] + order[-5:]
    samples_sorted = [samples[i] for i in order]
    deadline = time.monotonic() + time_budget
    warnings = []
    used_samples = samples_sorted
    items = None
    srch = None
    # bardzo długie przykłady tylko do weryfikacji (głęboka rekurencja przy wejściach bez struktury)
    long_ones = [x for x in used_samples if len(x) > 2500]
    if long_ones and len(long_ones) < len(used_samples):
        used_samples = [x for x in used_samples if len(x) <= 2500]
    for attempt in range(3):
        srch = _Search(used_samples, hints, allow_block=True, max_nodes=30000, deadline=deadline, keep=6)
        items = srch.run()
        if items is not None and hints.get("count_mentions"):
            items = _rerank_by_statement(srch.solutions, hints)
        if items is not None:
            break
        # nie da się opisać wszystkich naraz → odrzuć najbardziej „inne” przykłady
        if len(used_samples) <= 1:
            break
        used_samples = used_samples[: max(1, len(used_samples) // 2)]
    if items is None:
        items = [{"kind": "line", "fields": [_field_for_class(_tclass(t), t) for t in ln]} for ln in samples_sorted[0]]
        warnings.append("Nie udało się znaleźć wspólnego formatu — specyfikacja opisuje tylko pierwszy przykład.")
    elif len(used_samples) < len(samples_sorted):
        warnings.append(f"Format pasuje do {len(used_samples)} z {len(samples_sorted)} przykładów.")
    spec = {"version": 1, "vars": {}, "items": items}
    _rename_vars(spec, hints)
    _finalize(spec, [s for s in (small or raw)], hints, warnings)
    if _apply_bignum(spec, hints) or len(small) != len(raw):
        spec["sample_parse"] = [validate(spec, s, strict=False)[0] for s in raw]
    spec["warnings"] = warnings
    spec["confidence"] = _confidence(spec, srch.best_cost if srch and srch.best_items is not None else None, raw)
    return spec


HUGE_SAMPLE = 200_000


def _apply_bignum(spec, hints) -> bool:
    """Pola-zmienne, które treść opisuje jako „liczba do 10^K” (K > 18), zamienia na pola bigint (napis cyfr)."""
    big = hints.get("bignum") or {}
    if not big:
        return False
    cvars = _count_vars(spec)
    lines = [it for it in _iter_items(spec.get("items") or []) if it.get("kind") == "line"]
    fields = [f for it in lines for f in it.get("fields") or [] if f.get("var") and f.get("type", "int") == "int"]
    if len(fields) == 1 and len(big) == 1 and fields[0]["var"] not in big:
        fields[0]["var"] = next(iter(big))           # jedyna liczba na wejściu = jedyna „wielka” zmienna z treści
    changed = False
    for f in fields:
        v = f.get("var")
        if v not in big or v in cvars:
            continue
        info = big[v]
        f.clear()
        f.update({"type": "bigint", "name": v, "min": info.get("min", 1), "max_len": info["digits"]})
        if info.get("max_lead") is not None and info.get("max_pow10") is not None:
            f["max_lead"], f["max_pow10"] = str(info["max_lead"]), int(info["max_pow10"])
        f["source"] = "treść"
        (spec.get("vars") or {}).pop(v, None)
        changed = True
    return changed


def _rerank_by_statement(solutions, hints):
    """Spośród prawie równie tanich hipotez wybiera tę, której liczności zgadzają się z treścią („w kolejnych m liniach”)."""
    mentions = set(hints.get("count_mentions") or [])
    best_cost = solutions[0][0]
    scored = []
    for cost, items in solutions:
        if cost > best_cost + 0.35:
            continue
        sp = {"items": copy.deepcopy(items), "vars": {}}
        try:
            _rename_vars(sp, hints)
        except Exception:  # noqa: BLE001
            continue
        hits = 0
        for it in _iter_items(sp["items"]):
            keys = ("len", "count", "rows") if it.get("kind") != "parents" else ("n",)
            for key in keys:
                e = it.get(key)
                if isinstance(e, str):
                    base = re.sub(r"\s*-\s*1$", "", e)
                    if base in mentions:
                        hits += 1
        scored.append((-hits, cost, items))
    if not scored:
        return solutions[0][1]
    scored.sort(key=lambda x: (x[0], x[1]))
    return scored[0][2]


# ---------------------------------------------------------------------------------------------- nazwy zmiennych
def _walk_items(items, fn, depth=0):
    for it in items:
        fn(it, depth)
        if it.get("kind") == "block":
            _walk_items(it.get("items") or [], fn, depth + 1)


def _rename_expr(e, mp):
    if isinstance(e, str):
        return re.sub(r"[A-Za-z_]\w*", lambda m: mp.get(m.group(0), m.group(0)), e)
    if isinstance(e, dict):
        return {k: _rename_expr(v, mp) for k, v in e.items()}
    return e


def _rename_vars(spec, hints):
    defs = []   # (var, depth, item, usage)

    def collect(it, depth):
        if it.get("kind") == "line":
            for f in it.get("fields") or []:
                if f.get("var"):
                    defs.append([f["var"], depth])
    _walk_items(spec["items"], collect)
    usage: dict[str, str] = {}

    def use(it, _d):
        for key in ("len", "count", "rows", "cols", "n"):
            e = it.get(key)
            if isinstance(e, str):
                for nm in re.findall(r"[A-Za-z_]\w*", e):
                    usage.setdefault(nm, it.get("kind") + ":" + key)
    _walk_items(spec["items"], use)
    cons = hints.get("constraints") or {}

    def elem_like(nm):
        c = cons.get(nm) or {}
        return bool(re.fullmatch(r"[a-zA-Z]_?[ij]|[a-zA-Z]_?\d|[a-z]{2,}", nm)) or isinstance(c.get("max"), str)
    header_groups = [list(g) for g in hints.get("header_groups") or []]
    groups = [list(g) for g in hints.get("name_groups") or [] if not any(elem_like(x) for x in g)]
    flat = [nm for nm in hints.get("names") or [] if not elem_like(nm)]
    # zmienne pogrupowane wg linii nagłówka
    lines_vars: list[list[str]] = []

    def collect_lines(it, _d):
        if it.get("kind") == "line":
            vs = [f["var"] for f in it.get("fields") or [] if f.get("var")]
            if vs:
                lines_vars.append(vs)
    _walk_items(spec["items"], collect_lines)
    mp: dict[str, str] = {}
    taken: set[str] = set()

    def take_group(pool, k):
        for gi, g in enumerate(pool):
            if len(g) == k and not any(x in taken for x in g) and len(set(g)) == k:
                pool.pop(gi)
                return g
        return None
    for vs in lines_vars:
        k = len(vs)
        g = take_group(header_groups, k) or take_group(groups, k)
        if g is None and k == 1 and usage.get(vs[0], "").startswith("block") and "t" not in taken:
            g = ["t"]
        if g is None:
            avail = [nm for nm in flat if nm not in taken]
            if len(avail) >= k and k <= 3 and all(nm in cons for nm in avail[:k]):
                g = avail[:k]
        if g is not None:
            for v, nm in zip(vs, g):
                mp[v] = nm
                taken.add(nm)
    for var, depth in defs:
        if var in mp:
            continue
        u = usage.get(var, "")
        name = None
        if u.startswith("block") and "t" not in taken:
            name = "t"
        if name is None:
            pref = ["q"] if u.startswith("queries") else []
            for nm in pref + _DEFAULT_NAMES:
                if nm not in taken:
                    name = nm
                    break
        if name is None:
            name = f"x{len(taken)}"
        taken.add(name)
        mp[var] = name

    def apply(it, _d):
        for key in ("len", "count", "rows", "cols", "n"):
            if key in it:
                it[key] = _rename_expr(it[key], mp)
        if it.get("kind") == "line":
            for f in it.get("fields") or []:
                if f.get("var"):
                    f["var"] = mp.get(f["var"], f["var"])
    _walk_items(spec["items"], apply)


# ---------------------------------------------------------------------------------------------- zakresy i semantyka
def _collect_obs(spec, raw):
    """Parsuje przykłady (nieściśle) i zbiera obserwacje: wartości zmiennych oraz (węzeł, env) dla każdego elementu.
    env = zmienne znane w chwili parsowania elementu (dla linii — łącznie z jej własnymi)."""
    obs_vars: dict[str, list] = {}
    obs_items: dict[int, list] = {}
    parsed_ok = []

    def rec(it, nd, before, after):
        env = after if it.get("kind") == "line" else before
        obs_items.setdefault(id(it), []).append((nd, env))
        if it.get("kind") == "line":
            for f, v in zip(it.get("fields") or [], nd["vals"]):
                if f.get("var"):
                    obs_vars.setdefault(f["var"], []).append(v)
    for s in raw:
        lines = _tok_lines(s)
        rd = _Reader(lines)
        try:
            _parse_items(spec.get("items") or [], rd, {}, False, spec.get("vars") or {}, rec)
            ok = rd.pos == len(lines)
        except (FormatError, ValueError):
            ok = False
        parsed_ok.append(ok)
    return obs_vars, obs_items, parsed_ok


def _round_up_nice(x: int) -> int:
    if x <= 10:
        return 10
    p = 10 ** (len(str(x)) - 1)
    return ((x + p - 1) // p) * p


def _value_range_from_obs(vals: list, hints_global: dict, is_count=False, scalar=False) -> tuple:
    lo, hi = min(vals), max(vals)
    g = hints_global or {}
    if is_count:
        rlo = 0 if lo <= 0 else 1
        rhi = max(hi * 2, 10)
        return rlo, rhi
    if g.get("min") is not None and g["min"] <= lo:
        rlo = g["min"]
    elif lo >= 1:
        rlo = 1
    elif lo >= 0:
        rlo = 0
    else:
        rlo = -_round_up_nice(-lo)
    if g.get("max") is not None and g["max"] >= hi:
        rhi = g["max"]
    elif hi <= 1 and lo >= 0 and len(vals) >= 6 and not scalar:
        rhi = 1
    else:
        rhi = max(_round_up_nice(hi), 10)
    return rlo, rhi


def _finalize(spec, raw, hints, warnings):
    cons = hints.get("constraints") or {}
    flags = hints.get("flags") or {}
    count_vars = _count_vars(spec)
    obs_vars, obs_items, parsed_ok = _collect_obs(spec, raw)
    spec["sample_parse"] = parsed_ok
    used_cons: set[str] = set()
    # --- zmienne
    for var, vals in obs_vars.items():
        if not vals:
            continue
        c = cons.get(var) or cons.get(var.lower()) or cons.get(var.upper())
        rlo, rhi = _value_range_from_obs(vals, {} if var in count_vars else hints.get("global"), var in count_vars,
                                         scalar=True)
        src = "przykłady"
        if c:
            used_cons.add(var)
            if isinstance(c.get("min"), int) and c["min"] <= min(vals):
                rlo = c["min"]
                src = "treść"
            if c.get("max") is not None:
                if isinstance(c["max"], int) and c["max"] >= max(vals):
                    rhi = c["max"]
                    src = "treść"
                elif isinstance(c["max"], str):
                    rhi = c["max"]
                    src = "treść"
        spec["vars"][var] = {"min": rlo, "max": rhi, "source": src}
        if var in count_vars:
            spec["vars"][var]["count"] = True
    if spec["vars"] and all(v.get("source") != "treść" for v in spec["vars"].values()):
        warnings.append("Zakresy zmiennych oszacowane z przykładów — sprawdź i popraw je w specyfikacji "
                        "(najlepiej wklej treść zadania).")
    leftover = [(nm, c) for nm, c in cons.items() if nm not in used_cons]

    # --- pola i struktury
    def fin(it, _d):
        k = it.get("kind")
        occ = obs_items.get(id(it), [])
        if k == "line":
            for i, f in enumerate(it.get("fields") or []):
                if f.get("var"):
                    continue
                vals = [nd["vals"][i] for nd, _ in occ]
                _finalize_field(f, vals, [e for _, e in occ], spec, hints, leftover)
        elif k == "array":
            vals_all = [v for nd, _ in occ for v in nd["vals"]]
            pairs = [(v, e) for nd, e in occ for v in nd["vals"]]
            lv = re.sub(r"\s*-\s*1$", "", it["len"]) if isinstance(it.get("len"), str) else None
            gvars = [g.get("n") for g in _iter_items(spec["items"]) if g.get("kind") in ("graph", "parents")
                     and isinstance(g.get("n"), str)]
            _finalize_field(it["elem"], vals_all, None, spec, hints, leftover, pairs=pairs,
                            len_var=[lv] + gvars if lv else (gvars or None))
            _array_props(it, [nd["vals"] for nd, _ in occ], flags)
            if isinstance(it.get("len"), dict):
                lens = [len(nd["vals"]) for nd, _ in occ] or [1]
                it["len"] = {"min": min(lens), "max": max(max(lens) * 2, 10)}
        elif k in ("repeat",):
            fields = it.get("fields") or []
            cnt_var = it.get("count") if isinstance(it.get("count"), str) else None
            joint = None
            if len(fields) >= 2 and fields[0].get("type") == "int" and fields[1].get("type") == "int":
                jp = [(r[i], e) for nd, e in occ for r in nd["rows"] for i in (0, 1)]
                joint = _var_bound(jp, spec, exclude=cnt_var)
            for i, f in enumerate(fields):
                pairs = [(r[i], e) for nd, e in occ for r in nd["rows"]]
                if joint and i < 2:
                    f["min"], f["max"], f["source"] = joint[0], joint[1], "zależność"
                    _apply_named_constraint(f, pairs, leftover)
                    continue
                _finalize_field(f, [v for v, _ in pairs], None, spec, hints, leftover, pairs=pairs, exclude=cnt_var)
            _pair_props(it, [r for nd, _ in occ for r in nd["rows"]], fields, hints)
            _maybe_graph(it, occ, spec, hints)
            if it.get("kind") == "graph":
                it.pop("pairs", None)
            if isinstance(it.get("count"), dict):
                cnts = [len(nd["rows"]) for nd, _ in occ] or [1]
                until = it["count"].get("until")
                it["count"] = {"min": min(cnts), "max": max(max(cnts) * 2, 10)}
                if until is not None:
                    it["count"]["until"] = until
                    it["count"]["min"] = 0 if min(cnts) == 0 else 1
        elif k == "matrix":
            vals = [v for nd, _ in occ for r in nd["rows"] for v in r]
            _finalize_field(it["elem"], vals, None, spec, hints, leftover)
        elif k in ("grid", "string"):
            chars = set()
            lens = []
            for nd, _ in occ:
                for s in (nd["rows"] if k == "grid" else [nd["val"]]):
                    chars |= set(s)
                    lens.append(len(s))
            it["alphabet"] = _alphabet(chars, flags)
            if k == "grid" and occ:
                uniq = None
                for nd, _ in occ:
                    cnt: dict = {}
                    for row in nd["rows"]:
                        for ch in row:
                            cnt[ch] = cnt.get(ch, 0) + 1
                    once = {ch for ch, c in cnt.items() if c == 1}
                    uniq = once if uniq is None else uniq & once
                rest = set(it["alphabet"]) - (uniq or set())
                if uniq and len(rest) >= 1 and sum(len(r) for nd, _ in occ for r in nd["rows"]) >= 3 * len(occ):
                    it["unique"] = "".join(sorted(uniq))
            if k == "string" and it.get("len") is None:
                it["min_len"] = 1 if min(lens or [1]) >= 1 else 0
                c_len = cons.get("|s|") or cons.get("dł")
                it["max_len"] = (c_len or {}).get("max") or max(max(lens or [1]) * 2, 10)
        elif k == "queries":
            for t in it.get("types") or []:
                rows = [r[1:] for nd, _ in occ for r in nd["rows"] if r[0] == t["tag"]]
                envs = [e for nd, e in occ for r in nd["rows"] if r[0] == t["tag"]]
                tf = t.get("fields") or []
                ints = [i for i, f in enumerate(tf) if f.get("type") == "int"]
                joint = None
                if len(ints) >= 2:
                    jp = [(r[i], e) for r, e in zip(rows, envs) for i in ints[:2]]
                    joint = _var_bound(jp, spec, exclude=it.get("count") if isinstance(it.get("count"), str) else None)
                for i, f in enumerate(tf):
                    pairs = [(r[i], e) for r, e in zip(rows, envs)]
                    if joint and i in ints[:2]:
                        f["min"], f["max"], f["source"] = joint[0], joint[1], "zależność"
                        continue
                    _finalize_field(f, [v for v, _ in pairs], None, spec, hints, leftover, pairs=pairs,
                                    exclude=it.get("count") if isinstance(it.get("count"), str) else None)
                _pair_props(t, rows, t.get("fields") or [], hints)
            freq = {}
            for nd, _ in occ:
                for r in nd["rows"]:
                    freq[r[0]] = freq.get(r[0], 0) + 1
            for t in it.get("types") or []:
                t["weight"] = freq.get(t["tag"], 1)
        elif k == "lists":
            vals = [v for nd, _ in occ for r in nd["rows"] for v in r]
            pairs = [(v, e) for nd, e in occ for r in nd["rows"] for v in r]
            _finalize_field(it["elem"], vals or [1], None, spec, hints, leftover, pairs=pairs)
            lens = [len(r) for nd, _ in occ for r in nd["rows"]] or [0]
            it["min_len"] = min(lens)
            it["max_len"] = max(max(lens) * 2, 5)
        elif k == "parents":
            pass
    _walk_items(spec["items"], fin)
    _graph_var_bounds(spec)

    def fix_m1(it, _d):
        for key in ("len", "count", "rows", "cols"):
            e = it.get(key)
            if isinstance(e, str):
                m = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*-\s*1\s*", e)
                if m and m.group(1) in spec["vars"]:
                    vd = spec["vars"][m.group(1)]
                    if isinstance(vd.get("min"), int) and vd["min"] < 1:
                        vd["min"] = 1
        if it.get("kind") in ("graph", "parents"):
            nv = it.get("n")
            if isinstance(nv, str) and nv in spec["vars"]:
                vd = spec["vars"][nv]
                if isinstance(vd.get("min"), int) and vd["min"] < 1:
                    vd["min"] = 1
    _walk_items(spec["items"], fix_m1)
    spec.pop("_bound_pref", None)


def _alphabet(chars: set, flags: dict) -> str:
    if flags.get("lowercase") and chars <= set("abcdefghijklmnopqrstuvwxyz"):
        return "abcdefghijklmnopqrstuvwxyz"
    if flags.get("uppercase") and chars <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if flags.get("binary") and chars <= set("01"):
        return "01"
    if flags.get("digits_only") and chars <= set("0123456789"):
        return "0123456789"
    return "".join(sorted(chars)) or "ab"


def _count_vars(spec) -> set:
    out = set()

    def f(it, _d):
        for key in ("len", "count", "rows", "cols", "n"):
            e = it.get(key)
            if isinstance(e, str):
                out.update(re.findall(r"[A-Za-z_]\w*", e))
    _walk_items(spec["items"], f)
    return out


def _all_vars_in_scope(spec) -> list[str]:
    return list(spec.get("vars", {}).keys())


def _apply_named_constraint(f, pairs, leftover):
    """Nazwa pola z treści (np. „a, b ≤ n”), gdy ograniczenie z treści zgadza się z zależnością."""
    for nm, c in leftover:
        if isinstance(c.get("max"), str) and c["max"].replace(" ", "") == str(f.get("max")).replace(" ", ""):
            f.setdefault("name", nm)
            if isinstance(c.get("min"), int):
                f["min"] = c["min"]
            f["source"] = "treść"
            return


def _finalize_field(f, vals, envs, spec, hints, leftover, pairs=None, len_var=None, exclude=None):
    t = f.get("type")
    if t == "word":
        ch = sorted(set(vals))
        if len(ch) <= 6:
            f["choices"] = ch
        else:
            f.clear()
            f.update({"type": "str", "alphabet": _alphabet(set("".join(vals)), hints.get("flags") or {}),
                      "min_len": min(len(v) for v in vals), "max_len": max(len(v) for v in vals) * 2})
        return
    if t == "str":
        f["alphabet"] = _alphabet(set("".join(vals)), hints.get("flags") or {})
        f["min_len"] = min((len(v) for v in vals), default=1)
        f["max_len"] = max(max((len(v) for v in vals), default=1) * 2, 5)
        return
    if t == "float":
        fv = [float(v) for v in vals] or [0.0]
        dec = max((len(str(v).split(".")[1]) if "." in str(v) else 0) for v in vals) if vals else 2
        f["decimals"] = max(1, dec)
        lo, hi = min(fv), max(fv)
        f["min"] = 0 if lo >= 0 else -_round_up_nice(int(-lo) + 1)
        f["max"] = max(_round_up_nice(int(hi) + 1), 10)
        return
    if t != "int" or not vals:
        if t == "int":
            f.setdefault("min", 1)
            f.setdefault("max", 10)
        return
    # 1) ograniczenie przez zmienną (indeksy/wierzchołki ≤ n)
    vbound = _var_bound(pairs, spec, exclude=exclude, only=len_var) if pairs else None
    if len_var is not None and vbound is None and pairs:
        vbound = None
    # 2) ograniczenie z treści (pierwsze zgodne z obserwacjami)
    lo_o, hi_o = min(vals), max(vals)
    for idx, (nm, c) in enumerate(leftover):
        cmin, cmax = c.get("min"), c.get("max")
        if isinstance(cmin, int) and cmin > lo_o:
            continue
        if isinstance(cmax, int) and cmax < hi_o:
            continue
        if isinstance(cmax, str):
            if not pairs or not _bounded_by_expr(pairs, cmax):
                continue
        if cmin is None and cmax is None:
            continue
        f["min"] = cmin if cmin is not None else (vbound[0] if vbound else min(0, lo_o))
        f["max"] = cmax if cmax is not None else (vbound[1] if vbound else hi_o)
        f["name"] = re.sub(r"(?<=[A-Za-z])_?i$", "", nm) or nm
        f["source"] = "treść"
        if not re.fullmatch(r"[a-zA-Z]_?[ij]", nm) and len(leftover) > 1:
            leftover.pop(idx)
        return
    if vbound:
        f["min"], f["max"] = vbound
        f["source"] = "zależność"
        return
    lo, hi = _value_range_from_obs(vals, hints.get("global"))
    f["min"], f["max"] = lo, hi
    f["source"] = "przykłady" if not (hints.get("global") or {}) else "treść (ogólnie)"


def _bounded_by_expr(pairs, expr) -> bool:
    try:
        return all(v <= eval_expr(expr, e) for v, e in pairs)
    except FormatError:
        return False


def _var_bound(pairs, spec, exclude=None, only=None):
    """Czy wszystkie wartości ∈ [1, v] (lub [0, v-1]) dla jakiejś zmiennej-liczności v? Zwraca (min, max-wyrażenie).
    Wybiera zmienną, do której wartości „dochodzą” najbliżej (indeksy / wierzchołki)."""
    if not pairs or len(pairs) < 2:
        return None
    vars_all = spec.get("vars", {})
    cvars = list(vars_all.keys())
    if only is not None:
        only_l = only if isinstance(only, list) else [only]
        cvars = [v for v in cvars if v in only_l]
    pref = spec.get("_bound_pref") or []
    allv = [v for v, _ in pairs]
    if not all(isinstance(v, int) for v in allv) or max(allv) <= 1:
        return None
    best = None
    for var in cvars:
        if not all(isinstance(e.get(var), int) for _, e in pairs):
            continue
        ok1 = all(1 <= v <= e[var] for v, e in pairs)
        ok0 = not ok1 and all(0 <= v <= e[var] - 1 for v, e in pairs)
        if not (ok1 or ok0):
            continue
        close = max((v + (1 if ok0 else 0)) / max(1, e[var]) for v, e in pairs)
        if close < 0.5 and len(pairs) < 6:
            continue
        if close < 0.25:
            continue
        key = (var == exclude, var not in pref, -round(close, 3), sum(e[var] for _, e in pairs))
        cand = (1, var) if ok1 else (0, f"{var}-1")
        if best is None or key < best[0]:
            best = (key, cand)
    if best:
        spec.setdefault("_bound_pref", [])
        if best[1][1].replace("-1", "") not in spec["_bound_pref"]:
            spec["_bound_pref"].append(re.sub(r"-1$", "", best[1][1]))
    return best[1] if best else None


def _array_props(it, arrays, flags=None):
    arrays = [a for a in arrays if a]
    if not arrays or it["elem"].get("type") != "int":
        return
    total = sum(len(a) for a in arrays)
    # Przykład „1 2 3” wygląda jak permutacja, ale jeśli treść daje zakres wartości (np. |a_i| ≤ 10^9) i nie mówi
    # o permutacji, to zakres z treści wygrywa — inaczej generator robi same małe liczby i nie złapie overflow.
    el = it["elem"]
    stmt_range = el.get("source") == "treść" and (
        (isinstance(el.get("max"), int) and el["max"] > 10 * max(len(a) for a in arrays))
        or (isinstance(el.get("min"), int) and el["min"] < 0))
    if stmt_range and not (flags or {}).get("permutation"):
        pass
    elif all(sorted(a) == list(range(1, len(a) + 1)) for a in arrays) and total >= 3 and max(len(a) for a in arrays) >= 2:
        it["order"] = "permutation"
        it["elem"]["min"], it["elem"]["max"] = 1, it.get("len") if isinstance(it.get("len"), str) else it["elem"].get("max")
        return
    if total >= 4 and all(len(a) >= 2 for a in arrays) and not (stmt_range and not (flags or {}).get("sorted")):
        if all(all(a[i] <= a[i + 1] for i in range(len(a) - 1)) for a in arrays) and any(len(set(a)) > 1 for a in arrays):
            it["order"] = "sorted"
            return
        if all(all(a[i] >= a[i + 1] for i in range(len(a) - 1)) for a in arrays) and any(len(set(a)) > 1 for a in arrays):
            it["order"] = "sorted_desc"
            return
    if total >= 8 and all(len(set(a)) == len(a) for a in arrays) and max(len(a) for a in arrays) >= 4:
        # różność jest dowodem tylko wtedy, gdy zakres wartości jest mały (inaczej to przypadek) albo mówi o tym treść
        span = max(max(a) for a in arrays) - min(min(a) for a in arrays) + 1
        if (flags or {}).get("distinct") or span <= 3 * max(len(a) for a in arrays):
            it["order"] = "distinct"


def _pair_props(it, rows, fields, hints):
    if len(rows) < 2:
        return
    pairs = []
    for i in range(len(fields) - 1):
        f1, f2 = fields[i], fields[i + 1]
        if f1.get("type") != "int" or f2.get("type") != "int":
            continue
        if str(f1.get("max")) != str(f2.get("max")) or str(f1.get("min")) != str(f2.get("min")):
            continue
        if all(r[i] <= r[i + 1] for r in rows) and any(r[i] < r[i + 1] for r in rows) and len(rows) >= 3:
            pairs.append([i, i + 1, "le"])
    if pairs:
        it["pairs"] = pairs


def _maybe_graph(it, occ, spec, hints):
    fields = it.get("fields") or []
    if len(fields) not in (2, 3) or any(f.get("type") != "int" for f in fields[:2]):
        return
    if len(fields) == 3 and fields[2].get("type") not in ("int", "float"):
        return
    m0, m1 = fields[0].get("max"), fields[1].get("max")
    if not (isinstance(m0, str) and m0 == m1):
        return
    nexpr = m0[:-2] if m0.endswith("-1") else m0
    if not re.fullmatch(r"[A-Za-z_]\w*", nexpr):
        return
    base = 0 if m0.endswith("-1") else 1
    count = it.get("count")
    if isinstance(count, dict):
        return
    flags = hints.get("flags") or {}
    rows_all = [(nd["rows"], e) for nd, e in occ]
    all_rows = [r for rows, _ in rows_all for r in rows]
    ordered = len(all_rows) >= 3 and all(r[0] <= r[1] for r in all_rows)
    tree_like = count == f"{nexpr}-1"
    if not (flags.get("graph") or tree_like or (not ordered and len(all_rows) >= 2)):
        return
    tree = count == f"{nexpr}-1" and all(_connected(e[nexpr], [(r[0] - base, r[1] - base) for r in rows])
                                         for rows, e in rows_all if e.get(nexpr, 0) > 1)
    connected = all(_connected(e[nexpr], [(r[0] - base, r[1] - base) for r in rows])
                    for rows, e in rows_all if e.get(nexpr, 0) > 1) and not flags.get("not_connected")
    selfloop = any(r[0] == r[1] for rows, _ in rows_all for r in rows)
    multi = any(len({(min(r[0], r[1]), max(r[0], r[1])) for r in rows}) != len(rows) for rows, _ in rows_all)
    directed = bool(flags.get("directed")) and not tree
    dag = directed and flags.get("dag") and not any(
        _has_cycle(e[nexpr], [(r[0] - base, r[1] - base) for r in rows]) for rows, e in rows_all)
    weight = fields[2] if len(fields) == 3 else None
    if weight is not None:
        weight = {k: v for k, v in weight.items() if k not in ("role",)}
    it.clear()
    it.update({"kind": "graph", "n": nexpr, "count": count, "base": base, "directed": directed, "tree": tree,
               "connected": bool(connected and (tree or flags.get("connected") or True)),
               "simple": not selfloop and not multi, "weight": weight})
    if dag:
        it["dag"] = True
    if not flags.get("connected") and not tree:
        # spójność z przykładów bywa przypadkowa — uznajemy ją tylko, gdy wszystkie przykłady są spójne i m ≥ n-1
        it["connected"] = bool(connected and rows_all and all(len(rows) >= e.get(nexpr, 0) - 1 for rows, e in rows_all)
                               and any(e.get(nexpr, 0) >= 3 for _, e in rows_all))


def _graph_var_bounds(spec):
    """m ≤ n(n-1)/2 dla grafów prostych; m ≥ n-1 dla spójnych."""
    def f(it, _d):
        if it.get("kind") != "graph":
            return
        c = it.get("count")
        if not isinstance(c, str) or c not in spec["vars"]:
            return
        n = it.get("n")
        vd = spec["vars"][c]
        if it.get("simple"):
            mx = f"{n}*({n}-1)" if it.get("directed") else f"{n}*({n}-1)//2"
            vd["max_graph"] = mx
        if it.get("connected"):
            vd["min_graph"] = f"{n}-1"
    _walk_items(spec["items"], f)


def _confidence(spec, cost, raw) -> float:
    ok = spec.get("sample_parse") or []
    if not ok:
        return 0.0
    frac = sum(ok) / len(ok)
    nitems = sum(1 for _ in _iter_items(spec["items"]))
    struct = sum(1 for it in _iter_items(spec["items"]) if it.get("kind") != "line")
    lines = sum(len(_tok_lines(s)) for s in raw) / max(1, len(raw))
    score = frac * (0.5 + 0.5 * min(1.0, (lines / max(1, nitems)) / 3))
    if struct == 0 and lines > 2:
        score *= 0.7
    if len(raw) >= 2:
        score = min(1.0, score + 0.1)
    return round(score, 2)


def _iter_items(items):
    for it in items:
        yield it
        if it.get("kind") == "block":
            yield from _iter_items(it.get("items") or [])


# ============================================================================================== opis po polsku
def _fmt_num(v) -> str:
    if isinstance(v, int) and abs(v) >= 10000:
        s = str(abs(v))
        if s.startswith("1") and set(s[1:]) == {"0"}:
            return ("-" if v < 0 else "") + f"10^{len(s) - 1}"
        if len(s) >= 5:
            return ("-" if v < 0 else "") + f"{abs(v):,}".replace(",", " ")
    return str(v)


def _rng_txt(f) -> str:
    if f.get("type") == "float":
        return f"liczba rzeczywista ∈ [{_fmt_num(f.get('min'))}, {_fmt_num(f.get('max'))}]"
    if f.get("type") == "word":
        return "słowo: " + " / ".join(f"„{c}”" for c in f.get("choices") or [])
    if f.get("type") == "str":
        return f"napis (alfabet „{_short_alph(f.get('alphabet'))}”, długość {f.get('min_len')}–{f.get('max_len')})"
    if f.get("type") == "bigint":
        top = f"10^{f['max_pow10']}" if f.get("max_lead") == "1" and isinstance(f.get("max_pow10"), int) else \
            f"{_fmt_num(f.get('max_len'))} cyfr"
        return (f"∈ [{f.get('min', 1)}, {top}] — liczba do {_fmt_num(f.get('max_len'))} cyfr, generowana jako napis "
                "(bez zer wiodących)")
    return f"∈ [{_fmt_num(f.get('min'))}, {_fmt_num(f.get('max'))}]"


def _short_alph(a):
    a = a or ""
    if a == "abcdefghijklmnopqrstuvwxyz":
        return "a–z"
    if a == "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        return "A–Z"
    if a == "0123456789":
        return "0–9"
    return a if len(a) <= 12 else a[:12] + "…"


def describe(spec: dict) -> list[str]:
    out: list[str] = []
    vars_ = spec.get("vars") or {}

    def var_txt(v):
        d = vars_.get(v, {})
        src = d.get("source")
        extra = "" if src == "treść" else " (z przykładów)"
        return f"{v} ∈ [{_fmt_num(d.get('min'))}, {_fmt_num(d.get('max'))}]{extra}"

    def fld(f, default_name=True):
        nm = f.get("name")
        if not nm and default_name and f.get("type", "int") in ("int", "float"):
            nm = "liczba"
        return (f"{nm} " if nm else "") + _rng_txt(f)

    def rec(items, indent):
        pad = "  " * indent
        for it in items:
            k = it.get("kind")
            if k == "line":
                parts = []
                for f in it.get("fields") or []:
                    parts.append(var_txt(f["var"]) if f.get("var") else fld(f))
                out.append(f"{pad}Linia: " + ", ".join(parts))
            elif k == "array":
                ln = it.get("len")
                ln_t = f"{ln}" if not isinstance(ln, dict) else f"od {ln.get('min')} do {ln.get('max')}"
                lay = "w jednej linii" if it.get("layout") != "column" else "po jednej w linii"
                order = {"sorted": ", posortowane niemalejąco", "sorted_desc": ", posortowane nierosnąco",
                         "distinct": ", parami różne", "permutation": f" — permutacja 1..{ln_t}"}.get(it.get("order"), "")
                out.append(f"{pad}{ln_t} {'liczb' if (it.get('elem') or {}).get('type', 'int') in ('int', 'float') else 'tokenów'} "
                           f"{lay}: {fld(it['elem'], False)}{order}")
            elif k == "repeat":
                c = it.get("count")
                fs = ", ".join(fld(f) for f in it.get("fields") or [])
                pr = " (uporządkowane: " + ", ".join(f"pole {a + 1} ≤ pole {b + 1}" for a, b, _ in it.get("pairs")) + ")" \
                    if it.get("pairs") else ""
                if isinstance(c, dict):
                    head = f"Linie aż do linii „{c['until']}”" if c.get("until") is not None else "Linie do końca wejścia"
                    out.append(f"{pad}{head}, w każdej: {fs}{pr}")
                else:
                    out.append(f"{pad}{c} linii, w każdej: {fs}{pr}")
            elif k == "graph":
                kind = "drzewo" if it.get("tree") else ("graf skierowany" if it.get("directed") else "graf nieskierowany")
                props = []
                if not it.get("tree"):
                    if it.get("connected"):
                        props.append("spójny")
                    if it.get("dag"):
                        props.append("acykliczny")
                    if it.get("simple"):
                        props.append("bez pętli i multikrawędzi")
                w = f", wagi {_rng_txt(it['weight'])}" if it.get("weight") else ""
                out.append(f"{pad}{it.get('count')} krawędzi „a b”: {kind} na {it.get('n')} wierzchołkach "
                           f"(numeracja od {it.get('base', 1)}){(', ' + ', '.join(props)) if props else ''}{w}")
            elif k == "parents":
                out.append(f"{pad}Drzewo jako tablica rodziców p₂…p_{it.get('n')} (p_i < i) w jednej linii")
            elif k == "matrix":
                out.append(f"{pad}Macierz {it.get('rows')} × {it.get('cols')}: {fld(it['elem'], False)}")
            elif k == "grid":
                u = f"; „{'”, „'.join(it['unique'])}” dokładnie raz" if it.get("unique") else ""
                out.append(f"{pad}Plansza {it.get('rows')} × {it.get('cols')} znaków z „{_short_alph(it.get('alphabet'))}”{u}")
            elif k == "string":
                ln = it.get("len") or f"{it.get('min_len')}–{it.get('max_len')}"
                out.append(f"{pad}Napis długości {ln} z alfabetu „{_short_alph(it.get('alphabet'))}”")
            elif k == "queries":
                ts = []
                for t in it.get("types") or []:
                    args = ", ".join(fld(f) for f in t.get("fields") or [])
                    ts.append(f"„{t['tag']}”" + (f" {args}" if args else ""))
                out.append(f"{pad}{it.get('count')} zapytań: " + "; ".join(ts))
            elif k == "lists":
                out.append(f"{pad}{it.get('count')} linii „k x₁ … x_k”: {fld(it['elem'], False)}")
            elif k == "block":
                out.append(f"{pad}{it.get('count')} testów (multitest), każdy:")
                rec(it.get("items") or [], indent + 1)
    rec(spec.get("items") or [], 0)
    return out


# ============================================================================================== generowanie
def _size_cap(size) -> int | None:
    if isinstance(size, int):
        return max(1, size)
    if isinstance(size, str) and size.isdigit():
        return max(1, int(size))
    return SIZES.get(size or "small", 10)


class _Gen:
    def __init__(self, spec, strategy, size, rng):
        self.spec = spec
        self.strategy = strategy if strategy in STRATEGY_IDS else "random"
        self.size = size
        self.cap = _size_cap(size)
        self.rng = rng
        self.vars = spec.get("vars") or {}
        self.count_vars = _count_vars(spec)
        self.graph_of = {}
        self.graph_by_n = {}
        self.grid_uniq = [it for it in _iter_items(spec.get("items") or []) if it.get("kind") == "grid" and it.get("unique")]

        def f(it, _d):
            if it.get("kind") == "graph":
                if isinstance(it.get("count"), str):
                    self.graph_of[it["count"]] = it
                if isinstance(it.get("n"), str):
                    self.graph_by_n[it["n"]] = it
        _walk_items(spec.get("items") or [], f)

    # ---- wartości
    def _span_hi(self, lo, hi):
        if self.strategy == "random":
            span = {"tiny": 5, "small": 10, "medium": 1000}.get(self.size if isinstance(self.size, str) else "", None)
            if isinstance(self.size, int):
                span = max(10, self.size)
            if span is not None:
                return min(hi, lo + span) if lo >= 0 or hi <= 0 else min(hi, span)
        return hi

    def value(self, lo, hi, idx=0, n=1, const=None):
        if lo is None:
            lo = 0
        if hi is None:
            hi = lo + 10
        if hi < lo:
            hi = lo
        s = self.strategy
        r = self.rng
        if s == "min":
            return lo
        if s == "max":
            return hi
        if s == "equal" and const is not None:
            return const
        if s == "alternating":
            return lo if idx % 2 == 0 else hi
        if s == "extreme":
            cands = [x for x in (lo, lo + 1, hi - 1, hi, 0, 1, -1) if lo <= x <= hi]
            return r.choice(cands)
        if s == "random":
            lo2 = lo
            if lo < 0 < hi:
                lo2 = max(lo, -10)
            return r.randint(lo2, max(lo2, self._span_hi(lo, hi)))
        return r.randint(lo, hi)

    def count_value(self, lo, hi, cap):
        if lo is None:
            lo = 0
        if hi is None:
            hi = max(lo, cap or 10)
        hi_eff = hi if cap is None else max(lo, min(hi, cap))
        s = self.strategy
        if s == "min":
            return lo
        if s in ("max",) or (cap is None):
            return hi_eff
        r = self.rng
        # trochę przewagi większych rozmiarów (więcej treści w teście)
        a, b = r.randint(lo, hi_eff), r.randint(lo, hi_eff)
        return max(a, b) if r.random() < 0.6 else a

    def field(self, f, env, idx=0, n=1, const=None):
        t = f.get("type", "int")
        if t == "int":
            lo, hi = _num_or_expr(f.get("min"), env), _num_or_expr(f.get("max"), env)
            lo = int(lo) if lo is not None else 0
            hi = int(hi) if hi is not None else lo + 10
            return self.value(lo, hi, idx, n, const)
        if t == "float":
            lo, hi = _num_or_expr(f.get("min"), env) or 0, _num_or_expr(f.get("max"), env)
            hi = 10 if hi is None else hi
            dec = int(f.get("decimals") or 2)
            if self.strategy == "min":
                v = lo
            elif self.strategy == "max":
                v = hi
            else:
                v = self.rng.uniform(lo, hi if self.strategy != "random" else min(hi, lo + 10))
            return f"{v:.{dec}f}"
        if t == "word":
            ch = f.get("choices") or ["a"]
            if self.strategy in ("min", "equal"):
                return ch[0]
            return self.rng.choice(ch)
        if t == "bigint":
            return self.bigint(f, env)
        alph = f.get("alphabet") or "ab"
        lo, hi = _int_expr(f.get("min_len"), env, 1), _int_expr(f.get("max_len"), env, 5)
        ln = self.count_value(lo, hi, self.cap)
        return self.string(ln, alph)

    # liczba z tysiącami cyfr — zawsze napis (nigdy int: str() inta > ~4300 cyfr rzuca ValueError w Pythonie 3.11+)
    BIGINT_CAP = {"tiny": 3, "small": 6, "medium": 30, "large": 3000}

    def bigint(self, f, env) -> str:
        r, s = self.rng, self.strategy
        D = max(1, _int_expr(f.get("max_len"), env, 19) or 19)
        lo = 1 if (f.get("min") or 0) >= 1 else 0
        cap = max(1, self.size) if isinstance(self.size, int) else self.BIGINT_CAP.get(self.size or "small")
        L_hi = D if cap is None else min(D, cap)        # rozmiar „max” = pełna długość; losowe testy są krótkie
        top = _bigint_max(f)
        if s == "min":
            return str(lo)
        if s == "max":
            if L_hi >= D and top is not None:
                return top                               # dokładnie maksimum z treści (np. 10^K)
            return "9" * (L_hi if top is None or L_hi < D else max(1, D - 1))
        L = max(r.randint(1, L_hi), r.randint(1, L_hi)) if r.random() < 0.6 else r.randint(1, L_hi)
        nz = "123456789"
        if s == "equal":
            out = r.choice(nz) * L
        elif s == "alternating":
            out = ("10" * L)[:L] if r.random() < 0.5 else ("90" * L)[:L]
        elif s == "extreme":
            out = r.choice(["1", "9" * L, "1" + "0" * (L - 1), "9" + "0" * (L - 1), "1" + "9" * (L - 1),
                            "1" + "".join(r.choices("01", k=L - 1))])
        elif s in ("sorted", "reversed"):
            body = sorted(r.choices("0123456789", k=L), reverse=s == "reversed")
            if s == "sorted":
                nzs = [c for c in body if c != "0"] or ["1"]
                body.remove(nzs[0]) if nzs[0] in body else body.pop()
                body = [nzs[0]] + body
            out = "".join(body)
            if out[0] == "0":
                out = "1" + out[1:]
        else:
            out = r.choice(nz) + "".join(r.choices("0123456789", k=L - 1))
        if lo == 0 and s == "random" and r.random() < 0.05:
            return "0"
        if top is not None and len(out) >= len(top) and (len(out) > len(top) or out > top):
            return top
        return out

    def string(self, ln, alph):
        r = self.rng
        s = self.strategy
        if s in ("random",) and len(alph) > 3:
            alph = alph[:3] if self.size in ("tiny", "small") else alph
        if s in ("min", "equal"):
            c = alph[0] if s == "min" else r.choice(alph)
            return c * ln
        if s == "max":
            return alph[-1] * ln
        if s == "alternating":
            return "".join(alph[i % min(2, len(alph))] for i in range(ln))
        out = "".join(r.choice(alph) for _ in range(ln))
        if s == "sorted":
            out = "".join(sorted(out))
        elif s == "reversed":
            out = "".join(sorted(out, reverse=True))
        return out

    def var_value(self, name, env, cap):
        vd = self.vars.get(name, {})
        lo = _int_expr(vd.get("min"), env, 0 if name in self.count_vars else 1)
        hi = _int_expr(vd.get("max"), env, None)
        for gi in self.grid_uniq:
            u = len(gi.get("unique") or "")
            other = gi.get("cols") if gi.get("rows") == name else (gi.get("rows") if gi.get("cols") == name else None)
            if other is not None and isinstance(env.get(other), int) and env[other] > 0:
                lo = max(lo, -(-u // env[other]))
            elif other is not None:
                lo = max(lo, 1 if u <= 1 else 2)
        if hi is None:
            hi = max(lo, 10)
        g = self.graph_of.get(name)
        gn = self.graph_by_n.get(name)
        if gn is not None and gn.get("simple") and isinstance(gn.get("count"), str) and gn["count"] in self.vars:
            try:
                m_lo = _int_expr(self.vars[gn["count"]].get("min"), env, 0) or 0
            except FormatError:
                m_lo = 0
            if m_lo > 0:
                need = 2
                while need * (need - 1) // (1 if gn.get("directed") else 2) < m_lo:
                    need += 1
                lo = max(lo, need)
                hi = max(hi, lo)
        if g is not None and g.get("n") != name:
            try:
                n = _int_expr(g.get("n"), env, None)
            except FormatError:
                n = None
            if n is not None:
                if vd.get("max_graph") or g.get("simple"):
                    mx = n * (n - 1) // (1 if g.get("directed") else 2) if g.get("simple") else hi
                    hi = min(hi, mx)
                if g.get("connected") or self.strategy in ("connected",):
                    lo = max(lo, n - 1)
                if self.strategy in ("path", "star", "random_tree") and lo <= n - 1 <= hi:
                    return n - 1
                hi = max(hi, lo)
                if self.strategy == "max" and cap is not None:
                    return max(lo, min(hi, cap * 2))
                if self.strategy == "min":
                    return lo
                return self.rng.randint(lo, min(hi, max(lo, (cap or hi) * 2)))
        if name in self.count_vars:
            return self.count_value(lo, hi, cap)
        return self.value(lo, hi)

    # ---- struktury
    def items(self, items, env, out, cap):
        for it in items:
            self.item(it, env, out, cap)

    def item(self, it, env, out, cap):
        k = it.get("kind")
        r = self.rng
        if k == "line":
            fields = it.get("fields") or []
            vals = [None] * len(fields)
            pending = list(range(len(fields)))
            for _pass in range(len(fields) + 1):
                nxt = []
                for i in pending:
                    f = fields[i]
                    try:
                        if f.get("var"):
                            v = self.var_value(f["var"], env, cap)
                            env[f["var"]] = v
                        else:
                            v = self.field(f, env)
                        vals[i] = v
                    except FormatError:
                        nxt.append(i)
                if not nxt or len(nxt) == len(pending):
                    pending = nxt
                    break
                pending = nxt
            for i in pending:       # zależność cykliczna / nieznana — wartość bez górnej granicy
                f = fields[i]
                vd = self.vars.get(f.get("var"), {}) if f.get("var") else f
                lo = _num_or_expr(vd.get("min"), env)
                v = self.value(int(lo) if lo is not None else 1, (int(lo) if lo is not None else 1) + 10)
                if f.get("var"):
                    env[f["var"]] = v
                vals[i] = v
            # granica „a ≤ b” z tej samej linii: popraw kolejność, jeśli trzeba
            for i, f in enumerate(fields):
                vd = self.vars.get(f.get("var"), {}) if f.get("var") else f
                try:
                    hi = _num_or_expr(vd.get("max"), env)
                except FormatError:
                    hi = None
                if isinstance(hi, (int, float)) and isinstance(vals[i], int) and vals[i] > hi:
                    vals[i] = int(hi)
                    if f.get("var"):
                        env[f["var"]] = vals[i]
            out.append(" ".join(_fmt(v) for v in vals))
        elif k == "array":
            n = self._len(it.get("len"), env, cap)
            vals = self.array_vals(it, n, env)
            if it.get("layout") == "column":
                out.extend(_fmt(v) for v in vals)
            else:
                out.append(" ".join(_fmt(v) for v in vals))
        elif k == "parents":
            n = _int_expr(it.get("n"), env, 1)
            base = it.get("base", 1)
            ps = []
            for i in range(2, n + 1):
                if self.strategy == "path":
                    p = i - 1
                elif self.strategy == "star":
                    p = 1
                else:
                    p = r.randint(max(1, i - 3), i - 1) if self.strategy == "random" and r.random() < 0.3 else r.randint(1, i - 1)
                ps.append(p - 1 + base)
            if n > 1:
                out.append(" ".join(map(str, ps)))
        elif k == "repeat":
            n = self._len(it.get("count"), env, cap)
            consts = [self.field(f, env) if f.get("type") == "int" else None for f in it.get("fields") or []]
            until = it["count"].get("until") if isinstance(it.get("count"), dict) else None
            for i in range(n):
                row = [self.field(f, env, i, n, consts[j]) for j, f in enumerate(it.get("fields") or [])]
                self._apply_pairs(row, it.get("pairs"))
                line = " ".join(_fmt(v) for v in row)
                if until is not None and line.split() == str(until).split():
                    continue
                out.append(line)
            if until is not None:
                out.append(str(until))
        elif k == "graph":
            out.extend(" ".join(map(str, e)) for e in self.graph(it, env, cap))
        elif k == "matrix":
            rr = _int_expr(it.get("rows"), env, 1)
            cc = _int_expr(it.get("cols"), env, 1)
            const = self.field(it["elem"], env)
            for i in range(rr):
                out.append(" ".join(_fmt(self.field(it["elem"], env, i + j, cc, const)) for j in range(cc)))
        elif k == "grid":
            rr = _int_expr(it.get("rows"), env, 1)
            cc = _int_expr(it.get("cols"), env, 1) if it.get("cols") is not None else self.count_value(1, 10, self.cap)
            alph = it.get("alphabet") or ".#"
            uniq = it.get("unique") or ""
            base_alph = "".join(c for c in alph if c not in uniq) or alph
            rows_g = [list(self.string(cc, base_alph)) for _ in range(rr)]
            cells = [(i, j) for i in range(rr) for j in range(cc)]
            if uniq and len(cells) >= len(uniq):
                for ch, (i, j) in zip(uniq, r.sample(cells, len(uniq))):
                    rows_g[i][j] = ch
            out.extend("".join(x) for x in rows_g)
        elif k == "string":
            if it.get("len") is not None:
                ln = _int_expr(it.get("len"), env, 1)
            else:
                ln = self.count_value(_int_expr(it.get("min_len"), env, 1), _int_expr(it.get("max_len"), env, 10), cap)
            out.append(self.string(max(0, ln), it.get("alphabet") or "ab") or (it.get("alphabet") or "a")[0])
        elif k == "queries":
            n = self._len(it.get("count"), env, cap)
            types = it.get("types") or []
            weights = [max(1, int(t.get("weight") or 1)) for t in types]
            for i in range(n):
                t = r.choices(types, weights=weights)[0] if types else {"tag": "?", "fields": []}
                row = [self.field(f, env, i, n) for f in t.get("fields") or []]
                self._apply_pairs(row, t.get("pairs"))
                out.append(" ".join([str(t["tag"])] + [_fmt(v) for v in row]))
        elif k == "lists":
            n = self._len(it.get("count"), env, cap)
            for i in range(n):
                lo = _int_expr(it.get("min_len"), env, 0)
                hi = _int_expr(it.get("max_len"), env, 5)
                ln = self.count_value(lo, hi, cap)
                vals = [self.field(it["elem"], env, j, ln) for j in range(ln)]
                out.append(" ".join([str(ln)] + [_fmt(v) for v in vals]))
        elif k == "block":
            cvar = it.get("count")
            t = _int_expr(cvar, env, 1)
            inner_cap = cap if cap is None else max(1, cap // max(1, min(t, 4)))
            if self.strategy == "max" and cap is None:
                inner_cap = None
            for _ in range(t):
                local = dict(env)
                self.items(it.get("items") or [], local, out, inner_cap)

    def _len(self, e, env, cap):
        if isinstance(e, dict):
            lo, hi = _int_expr(e.get("min"), env, 0), _int_expr(e.get("max"), env, None)
            return self.count_value(lo, hi if hi is not None else max(lo, 10), cap)
        return max(0, _int_expr(e, env, 0))

    def _apply_pairs(self, row, pairs):
        for a, b, rel in pairs or []:
            if a < len(row) and b < len(row) and isinstance(row[a], int) and isinstance(row[b], int):
                if row[a] > row[b]:
                    row[a], row[b] = row[b], row[a]
                if rel == "lt" and row[a] == row[b]:
                    row[b] += 1

    def array_vals(self, it, n, env):
        elem = it.get("elem") or {"type": "int"}
        order = it.get("order")
        r = self.rng
        s = self.strategy
        if order == "permutation":
            vals = list(range(1, n + 1))
            if s in ("sorted", "min"):
                return vals
            if s in ("reversed", "max"):
                return vals[::-1]
            r.shuffle(vals)
            return vals
        if elem.get("type") != "int":
            const = self.field(elem, env)
            return [self.field(elem, env, i, n, const) for i in range(n)]
        lo, hi = _num_or_expr(elem.get("min"), env), _num_or_expr(elem.get("max"), env)
        lo = int(lo) if lo is not None else 0
        hi = int(hi) if hi is not None else lo + 10
        if order == "distinct":
            span_hi = hi if hi - lo + 1 >= n else hi
            hi2 = self._span_hi(lo, span_hi) if s == "random" else span_hi
            if hi2 - lo + 1 < n:
                hi2 = min(hi, lo + n - 1) if hi - lo + 1 >= n else hi
            if s == "min":
                vals = list(range(lo, lo + n))
            elif s == "max":
                vals = list(range(hi, hi - n, -1))
            elif hi2 - lo + 1 >= n and hi2 - lo < 4 * 10 ** 6:
                vals = r.sample(range(lo, hi2 + 1), n)
            elif hi2 - lo + 1 >= n:
                seen_v: set = set()
                while len(seen_v) < n:
                    seen_v.add(r.randint(lo, hi2))
                vals = list(seen_v)
                r.shuffle(vals)
            else:
                vals = [r.randint(lo, hi) for _ in range(n)]
            if s == "sorted":
                vals.sort()
            elif s == "reversed":
                vals.sort(reverse=True)
            return vals
        const = self.value(lo, hi)
        vals = [self.field(elem, env, i, n, const) for i in range(n)]
        if s == "sorted" or order == "sorted":
            vals.sort()
        if s == "reversed" or order == "sorted_desc":
            vals.sort(reverse=True)
        if order == "sorted":
            vals.sort()
        return vals

    def graph(self, it, env, cap):
        n = _int_expr(it.get("n"), env, 1)
        base = it.get("base", 1)
        cnt = it.get("count")
        m = _int_expr(cnt, env, max(0, n - 1)) if not isinstance(cnt, dict) else max(0, n - 1)
        r = self.rng
        s = self.strategy
        edges: list[tuple[int, int]] = []
        directed = it.get("directed")
        simple = it.get("simple", True)
        need_tree = it.get("tree") or (it.get("connected") and m >= n - 1) or s in ("path", "star", "random_tree", "connected")
        perm = list(range(n))
        if s not in ("path",) or r.random() < 0.3:
            r.shuffle(perm)
        if n >= 2 and need_tree and m >= n - 1:
            if s == "path":
                edges = [(perm[i], perm[i + 1]) for i in range(n - 1)]
            elif s == "star":
                c = perm[0]
                edges = [(c, perm[i]) for i in range(1, n)]
            else:
                for i in range(1, n):
                    j = r.randint(max(0, i - 2), i - 1) if (s == "random" and r.random() < 0.3) else r.randint(0, i - 1)
                    edges.append((perm[j], perm[i]))
        if it.get("dag") or s == "dag":
            edges = [(a, b) if perm.index(a) < perm.index(b) else (b, a) for a, b in edges] if n <= 3000 else edges
        seen = set()
        for a, b in edges:
            seen.add((a, b) if directed else (min(a, b), max(a, b)))
        maxe = (n * (n - 1) // (1 if directed else 2)) if simple else 10 ** 18
        target = min(m, maxe) if simple else m
        tries = 0
        pos = {v: i for i, v in enumerate(perm)} if (it.get("dag") or s == "dag") else None
        if simple and target - len(edges) > maxe // 2 and n <= 2000:
            allp = [(a, b) for a in range(n) for b in range(n) if a != b and (directed or a < b)]
            allp = [p for p in allp if p not in seen]
            if pos is not None:
                allp = [p for p in allp if pos[p[0]] < pos[p[1]]] or allp
            r.shuffle(allp)
            for p in allp[: max(0, target - len(edges))]:
                edges.append(p)
                seen.add(p)
        while len(edges) < target and tries < target * 20 + 100:
            tries += 1
            a, b = r.randrange(n), r.randrange(n)
            if simple and a == b:
                continue
            if pos is not None and pos[a] > pos[b]:
                a, b = b, a
            key = (a, b) if directed else (min(a, b), max(a, b))
            if simple and key in seen:
                continue
            seen.add(key)
            edges.append((a, b))
        while len(edges) < m and n >= 1:    # awaryjnie (gdy spec niespójny) — dopełnij krawędziami
            a, b = r.randrange(n), r.randrange(n)
            edges.append((a, b))
        if s not in ("path",):
            r.shuffle(edges)
        if not directed:
            edges = [(b, a) if r.random() < 0.5 else (a, b) for a, b in edges]
        w = it.get("weight")
        res = []
        for i, (a, b) in enumerate(edges[:m]):
            row = [a + base, b + base]
            if w:
                row.append(self.field(w, env, i, m))
            res.append(row)
        return res


def generate(spec: dict, strategy: str = "random", size="small", rng: random.Random | None = None,
             seed: int | None = None) -> str:
    """Generuje jeden test zgodny ze specyfikacją. size: tiny|small|medium|large|max albo liczba (limit n)."""
    rng = rng or random.Random(seed)
    g = _Gen(spec, strategy, size, rng)
    out: list[str] = []
    g.items(spec.get("items") or [], {}, out, g.cap)
    return "\n".join(out) + "\n"


def generate_many(spec, strategies, count, seed=0, sizes=("tiny", "small", "small", "medium")) -> list[dict]:
    """Lista testów {input, strategy, size} — strategie i rozmiary na przemian."""
    rng = random.Random(seed)
    out = []
    strategies = [s for s in strategies if s in STRATEGY_IDS] or ["random"]
    for i in range(count):
        st = strategies[i % len(strategies)]
        sz = sizes[(i // len(strategies)) % len(sizes)] if st not in ("max", "min") else ("medium" if st == "max" else "tiny")
        out.append({"input": generate(spec, st, sz, rng), "strategy": st, "size": sz})
    return out


# ============================================================================================== shrinking
def shrink(spec: dict, input_text: str, predicate, max_evals: int = 400, time_budget: float = 30.0,
           on_progress=None) -> dict:
    """Minimalizuje wejście przy zachowaniu predicate(text) == True (np. „programy dają różne wyniki”).
    Zmniejsza liczności (usuwa elementy/wierzchołki/testy), potem wartości. Każdy kandydat jest poprawny
    względem spec (parse strict). Zwraca {input, original_size, size, evals, steps, improved, time}."""
    t0 = time.monotonic()
    try:
        cur = parse(spec, input_text, strict=False)
    except FormatError as e:
        return {"input": input_text, "original_size": len(input_text), "size": len(input_text), "evals": 0,
                "steps": 0, "improved": False, "error": f"Wejście nie pasuje do formatu: {e}", "time": 0.0}
    # oryginał może wychodzić poza zakresy ze specyfikacji (np. oszacowane z przykładów) — poszerzamy je
    spec = _widen_spec(spec, cur)
    strict_mode = validate(spec, input_text, strict=True)[0]
    cache: dict[str, bool] = {}
    state = {"evals": 0, "steps": 0}
    cur_text = render(spec, cur)
    best_text = input_text
    if normalize_sample(cur_text) != normalize_sample(input_text):
        if predicate(cur_text):
            best_text = cur_text
        state["evals"] += 1

    def budget_ok():
        return state["evals"] < max_evals and time.monotonic() - t0 < time_budget

    def test(cand: Parsed) -> bool:
        try:
            txt = render(spec, cand)
        except Exception:  # noqa: BLE001
            return False
        if len(txt) >= len(best_holder[0]) and txt != best_holder[0]:
            # nie większy niż obecny — dopuszczamy równe długości (np. mniejsze wartości)
            if len(txt) > len(best_holder[0]):
                return False
        ok, _ = validate(spec, txt, strict=strict_mode)
        if not ok:
            return False
        if txt in cache:
            return cache[txt]
        if not budget_ok():
            return False
        state["evals"] += 1
        try:
            res = bool(predicate(txt))
        except Exception:  # noqa: BLE001
            res = False
        cache[txt] = res
        if res:
            best_holder[0] = txt
            state["steps"] += 1
            if on_progress:
                try:
                    on_progress(len(txt), state["evals"])
                except Exception:  # noqa: BLE001
                    pass
        return res

    best_holder = [best_text]
    cur = parse(spec, best_text, strict=False)
    changed = True
    rounds = 0
    while changed and budget_ok() and rounds < 8:
        rounds += 1
        changed = False
        # 1) testy multitestu, liczności zmiennych
        for path in _shrink_targets(spec, cur):
            if not budget_ok():
                break
            res = _ddmin_target(spec, cur, path, test)
            if res is not None:
                cur = res
                changed = True
        # 2) wartości
        if budget_ok():
            res = _shrink_values(spec, cur, test, budget_ok)
            if res is not None:
                cur = res
                changed = True
    final = best_holder[0]
    return {"input": final, "original_size": len(input_text), "size": len(final), "evals": state["evals"],
            "steps": state["steps"], "improved": len(final) < len(input_text) or final != input_text,
            "time": round(time.monotonic() - t0, 3)}


def _widen_spec(spec, parsed):
    """Kopia spec z zakresami zmiennych poszerzonymi do wartości z danego wejścia."""
    sp = copy.deepcopy(spec)
    vals: dict[str, list] = {}

    def collect(env):
        for k, v in env.items():
            if isinstance(v, int):
                vals.setdefault(k, []).append(v)
    collect(parsed.env)
    for _items, _nodes, env, _outer, _blk in _scopes(sp, parsed):
        collect(env)
    for k, vs in vals.items():
        vd = sp.get("vars", {}).get(k)
        if not vd:
            continue
        if isinstance(vd.get("max"), int) and max(vs) > vd["max"]:
            vd["max"] = max(vs)
        if isinstance(vd.get("min"), int) and min(vs) < vd["min"]:
            vd["min"] = min(vs)
    return sp


def _scopes(spec, parsed):
    """Lista zakresów: (items, nodes, env_dict_do_modyfikacji, outer_env) — top-level i iteracje bloków."""
    out = [(spec.get("items") or [], parsed.nodes, parsed.env, None, None)]

    def rec(items, nodes, env):
        for it, nd in zip(items, nodes):
            if it.get("kind") == "block":
                for idx, itr in enumerate(nd["iters"]):
                    out.append((it.get("items") or [], itr["nodes"], itr["env"], env, (it, nd, idx)))
                    local = dict(env)
                    local.update(itr["env"])
                    rec(it.get("items") or [], itr["nodes"], local)
    rec(spec.get("items") or [], parsed.nodes, parsed.env)
    return out


def _shrink_targets(spec, parsed):
    """Cele dla ddmin: ("block", scope_idx, item_idx) oraz ("var", scope_idx, var)."""
    targets = []
    scopes = _scopes(spec, parsed)
    for si, (items, nodes, env, outer, _blk) in enumerate(scopes):
        for ii, (it, nd) in enumerate(zip(items, nodes)):
            if it.get("kind") == "block" and isinstance(it.get("count"), str):
                targets.append(("block", si, ii))
        for var in list(env.keys()):
            if (spec.get("vars") or {}).get(var, {}).get("count") or var in _count_vars(spec):
                targets.append(("var", si, var))
        for ii, it in enumerate(items):
            if isinstance(it.get("count"), dict) or isinstance(it.get("len"), dict):
                targets.append(("free", si, ii))
    # najpierw bloki (całe testy), potem zmienne od największych
    return targets


def _ddmin_target(spec, parsed, target, test):
    """Usuwa kawałki indeksów (połowy, ćwiartki, …, pojedyncze) — zwraca nowy Parsed albo None."""
    kind, si, key = target
    cur = parsed
    improved = False
    size = _target_size(spec, cur, target)
    if size is None or size <= 0:
        return None
    lo_req = _target_min(spec, cur, target)
    chunk = max(1, size // 2)
    while chunk >= 1:
        i = 0
        progressed = False
        while True:
            size = _target_size(spec, cur, target)
            if size is None or size - chunk < lo_req or i >= size:
                break
            rem = set(range(i, min(size, i + chunk)))
            cand = _remove_indices(spec, cur, target, rem)
            if cand is not None and test(cand):
                cur = cand
                improved = True
                progressed = True
                continue           # ten sam i — kolejny kawałek przesunął się na to miejsce
            i += chunk
        if chunk == 1:
            break
        chunk = chunk // 2 if not progressed else max(1, chunk // 2)
    return cur if improved else None


def _target_size(spec, parsed, target):
    kind, si, key = target
    scopes = _scopes(spec, parsed)
    if si >= len(scopes):
        return None
    items, nodes, env, outer, _ = scopes[si]
    if kind == "block":
        return len(nodes[key]["iters"])
    if kind == "free":
        nd = nodes[key]
        return len(nd.get("rows", nd.get("vals", [])))
    v = env.get(key)
    return v if isinstance(v, int) else None


def _target_min(spec, parsed, target):
    kind, si, key = target
    scopes = _scopes(spec, parsed)
    items, nodes, env, outer, _ = scopes[si]
    full_env = dict(outer or {})
    full_env.update(env)
    if kind == "block":
        cvar = items[key].get("count")
        vd = (spec.get("vars") or {}).get(cvar, {})
        return max(0, _int_expr(vd.get("min"), full_env, 1) or 0)
    if kind == "free":
        it = items[key]
        e = it.get("count") if isinstance(it.get("count"), dict) else it.get("len")
        return _int_expr(e.get("min"), full_env, 0) or 0
    vd = (spec.get("vars") or {}).get(key, {})
    try:
        lo = _int_expr(vd.get("min"), full_env, 0) or 0
    except FormatError:
        lo = 0
    try:
        if vd.get("min_graph"):
            pass    # sprawdzane przez validate
    except FormatError:
        pass
    return lo


def _remove_indices(spec, parsed, target, rem: set):
    kind, si, key = target
    cand = copy.deepcopy(parsed)
    scopes = _scopes(spec, cand)
    items, nodes, env, outer, blk = scopes[si]
    if kind == "block":
        nd = nodes[key]
        nd["iters"] = [x for i, x in enumerate(nd["iters"]) if i not in rem]
        cvar = items[key].get("count")
        # licznik testów jest zdefiniowany w tym samym zakresie (lub wyżej)
        if cvar in env:
            env[cvar] = len(nd["iters"])
        elif outer is not None and cvar in outer:
            return None
        return cand
    if kind == "free":
        nd = nodes[key]
        if "rows" in nd:
            nd["rows"] = [x for i, x in enumerate(nd["rows"]) if i not in rem]
        else:
            nd["vals"] = [x for i, x in enumerate(nd["vals"]) if i not in rem]
        return cand
    var = key
    k = env[var]
    full_env = dict(outer or {})
    full_env.update(env)
    newk = k - len(rem)
    keep = [i for i in range(k) if i not in rem]
    remap = {}           # stary indeks 0.. → nowy 0..
    for new, old in enumerate(keep):
        remap[old] = new
    handled = False
    # kolejne zakresy wewnątrz (bloki) też mogą zależeć od zmiennej — obsługujemy tylko ten zakres
    for it, nd in zip(items, nodes):
        r = _apply_removal(it, nd, var, k, rem, keep, remap, full_env)
        if r is False:
            return None
        handled = handled or r
    if not handled:
        return None
    env[var] = newk
    # graf z m jako zmienną — po usunięciu wierzchołków liczba krawędzi się zmienia
    for it, nd in zip(items, nodes):
        if it.get("kind") == "graph" and isinstance(it.get("count"), str) and it["count"] in env and it.get("n") == var:
            env[it["count"]] = len(nd["rows"])
    return cand


def _is_var(e, var):
    return isinstance(e, str) and e.replace(" ", "") == var


def _is_var_m1(e, var):
    return isinstance(e, str) and e.replace(" ", "") == f"{var}-1"


def _bounded_by(f, var):
    m = f.get("max")
    if _is_var(m, var):
        return 1
    if _is_var_m1(m, var):
        return 0
    return None


def _renumber(v, rem_sorted_vals, newk, base):
    """Wartość-indeks po usunięciu wierzchołków (wartości 1-based lub 0-based)."""
    import bisect
    less = bisect.bisect_left(rem_sorted_vals, v)
    nv = v - less
    return max(base, min(nv, newk - 1 + base))


def _apply_removal(it, nd, var, k, rem, keep, remap, env):
    """Zwraca True (zmieniono), None (bez związku), False (nie da się)."""
    kind = it.get("kind")
    newk = k - len(rem)
    changed = None
    rem_vals1 = sorted(i + 1 for i in rem)
    rem_vals0 = sorted(rem)

    def renum_fields(fields, rows, offset=0):
        nonlocal changed
        for j, f in enumerate(fields):
            b = _bounded_by(f, var)
            if b is None:
                continue
            for row in rows:
                v = row[j + offset]
                if isinstance(v, int):
                    row[j + offset] = _renumber(v, rem_vals1 if b == 1 else rem_vals0, newk, b)
            changed = True
    if kind == "array":
        if _is_var(it.get("len"), var):
            nd["vals"] = [v for i, v in enumerate(nd["vals"]) if i not in rem]
            changed = True
            if it.get("order") == "permutation":
                order = sorted(range(len(nd["vals"])), key=lambda i: nd["vals"][i])
                for rank, i in enumerate(order):
                    nd["vals"][i] = rank + 1
        elif _is_var_m1(it.get("len"), var):
            if len(rem) != 1 or k - 1 in rem:
                pass
            idxs = {i - 1 for i in rem if i >= 1} if rem else set()
            if 0 in rem and len(nd["vals"]) > 0:
                idxs.add(0)
            nd["vals"] = [v for i, v in enumerate(nd["vals"]) if i not in idxs][: max(0, newk - 1)]
            changed = True
        b = _bounded_by(it.get("elem") or {}, var)
        if b is not None and it.get("order") != "permutation":
            nd["vals"] = [_renumber(v, rem_vals1 if b == 1 else rem_vals0, newk, b) if isinstance(v, int) else v
                          for v in nd["vals"]]
            changed = True
    elif kind == "parents":
        if _is_var(it.get("n"), var):
            base = it.get("base", 1)
            par = [None, None] + [p - base + 1 for p in nd["vals"]]      # par[i] dla i=2..k (1-based)
            alive = [i for i in range(1, k + 1) if (i - 1) not in rem]
            if not alive:
                return False
            # wierzchołek usunięty → jego dzieci przechodzą do najbliższego żywego przodka
            alive_set = set(alive)

            def anc(x):
                while x is not None and x not in alive_set:
                    x = par[x] if x >= 2 else None
                return x
            root = alive[0]
            newid = {v: i + 1 for i, v in enumerate(alive)}
            newpar = []
            for v in alive[1:]:
                p = anc(par[v]) if v >= 2 else None
                if p is None or newid.get(p, 0) >= newid[v]:
                    p = root if root != v else None
                if p is None:
                    return False
                newpar.append(newid[p] - 1 + base)
            nd["vals"] = newpar
            changed = True
    elif kind in ("repeat", "queries", "lists"):
        if _is_var(it.get("count"), var):
            nd["rows"] = [r for i, r in enumerate(nd["rows"]) if i not in rem]
            changed = True
        if kind == "repeat":
            renum_fields(it.get("fields") or [], nd["rows"])
        elif kind == "queries":
            for t in it.get("types") or []:
                rows = [r for r in nd["rows"] if r[0] == t["tag"]]
                renum_fields(t.get("fields") or [], rows, offset=1)
        elif kind == "lists":
            b = _bounded_by(it.get("elem") or {}, var)
            if b is not None:
                nd["rows"] = [[_renumber(v, rem_vals1 if b == 1 else rem_vals0, newk, b) for v in r] for r in nd["rows"]]
                changed = True
            if _is_var(it.get("max_len"), var):
                nd["rows"] = [r[:newk] for r in nd["rows"]]
    elif kind == "graph":
        base = it.get("base", 1)
        if _is_var(it.get("n"), var):
            edges = [(r[0] - base, r[1] - base, r[2:]) for r in nd["rows"]]
            if it.get("tree") or it.get("connected"):
                edges = _contract(edges, k, rem)
                if edges is None:
                    return False
            else:
                edges = [e for e in edges if e[0] not in rem and e[1] not in rem]
            edges = [(remap[a], remap[b], w) for a, b, w in edges if a in remap and b in remap]
            if it.get("simple"):
                seen, ed2 = set(), []
                for a, b, w in edges:
                    key = (a, b) if it.get("directed") else (min(a, b), max(a, b))
                    if a == b or key in seen:
                        continue
                    seen.add(key)
                    ed2.append((a, b, w))
                edges = ed2
            nd["rows"] = [[a + base, b + base, *w] for a, b, w in edges]
            if _is_var_m1(it.get("count"), var) and len(nd["rows"]) != newk - 1:
                return False
            changed = True
        elif _is_var(it.get("count"), var):
            nd["rows"] = [r for i, r in enumerate(nd["rows"]) if i not in rem]
            changed = True
    elif kind == "matrix":
        if _is_var(it.get("rows"), var):
            nd["rows"] = [r for i, r in enumerate(nd["rows"]) if i not in rem]
            changed = True
        if _is_var(it.get("cols"), var):
            nd["rows"] = [[v for j, v in enumerate(r) if j not in rem] for r in nd["rows"]]
            changed = True
    elif kind == "grid":
        if _is_var(it.get("rows"), var):
            nd["rows"] = [r for i, r in enumerate(nd["rows"]) if i not in rem]
            changed = True
        if _is_var(it.get("cols"), var):
            nd["rows"] = ["".join(c for j, c in enumerate(r) if j not in rem) for r in nd["rows"]]
            changed = True
    elif kind == "string":
        if _is_var(it.get("len"), var):
            nd["val"] = "".join(c for i, c in enumerate(nd["val"]) if i not in rem)
            changed = True
    elif kind == "line":
        for j, f in enumerate(it.get("fields") or []):
            if f.get("var"):
                continue
            b = _bounded_by(f, var)
            if b is not None and isinstance(nd["vals"][j], int):
                nd["vals"][j] = _renumber(nd["vals"][j], rem_vals1 if b == 1 else rem_vals0, newk, b)
                changed = True
    elif kind == "block":
        for itr in nd["iters"]:
            if var in itr["env"]:
                continue        # przesłonięta
            for it2, nd2 in zip(it.get("items") or [], itr["nodes"]):
                r = _apply_removal(it2, nd2, var, k, rem, keep, remap, env)
                if r is False:
                    return False
                changed = changed or r
    else:
        # zależność od zmiennej przez wyrażenie, którego nie umiemy zmniejszyć
        pass
    if changed is None:
        for key in ("len", "count", "rows", "cols", "n"):
            e = it.get(key)
            if isinstance(e, str) and var in re.findall(r"[A-Za-z_]\w*", e):
                return False
    return changed


def _contract(edges, k, rem):
    """Ściąga usuwane wierzchołki do sąsiadów (zachowuje spójność / drzewiastość). edges: (a, b, w)."""
    par = list(range(k))

    def f(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    alive = [v for v in range(k) if v not in rem]
    if not alive:
        return None
    # łączymy usunięte wierzchołki z sąsiadami, preferując żywych
    adj: dict[int, list[int]] = {}
    for a, b, _ in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    target = {}
    pending = sorted(rem)
    for _ in range(len(pending) + 1):
        nxt = []
        for x in pending:
            nb = [y for y in adj.get(x, []) if y not in rem or y in target]
            if nb:
                y = nb[0]
                target[x] = target.get(y, y) if y in rem else y
            else:
                nxt.append(x)
        if not nxt or len(nxt) == len(pending):
            pending = nxt
            break
        pending = nxt
    for x in pending:
        target[x] = alive[0]

    def res(x):
        seen = 0
        while x in target and seen < k + 1:
            x = target[x]
            seen += 1
        return x
    out = []
    for a, b, w in edges:
        ra, rb = res(a), res(b)
        if ra == rb:
            continue
        out.append((ra, rb, w))
    # usuwamy duplikaty (krawędź zredukowana do tej samej pary)
    seen = set()
    uniq = []
    for a, b, w in out:
        key = (min(a, b), max(a, b))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((a, b, w))
    return uniq


def _simplest(lo, hi):
    if lo is None and hi is None:
        return 0
    if lo is None:
        return min(0, hi)
    if hi is None:
        return max(0, lo)
    if lo <= 0 <= hi:
        return 0
    return lo if lo > 0 else hi


def _value_slots(spec, parsed):
    """Grupy miejsc z wartościami do zmniejszania: lista (opis, getter/setter, lo, hi)."""
    groups = []
    for items, nodes, env, outer, _ in _scopes(spec, parsed):
        full_env = dict(outer or {})
        full_env.update(env)
        cvars = _count_vars(spec)
        for it, nd in zip(items, nodes):
            k = it.get("kind")
            slots = []
            if k == "line":
                for j, f in enumerate(it.get("fields") or []):
                    if f.get("var"):
                        if f["var"] in cvars:
                            continue
                        vd = (spec.get("vars") or {}).get(f["var"], {})
                        slots.append(("env", env, f["var"], vd.get("min"), vd.get("max")))
                    elif f.get("type") == "int":
                        slots.append(("list", nd["vals"], j, f.get("min"), f.get("max")))
            elif k == "array" and (it.get("elem") or {}).get("type") == "int" and it.get("order") != "permutation":
                f = it["elem"]
                slots += [("list", nd["vals"], j, f.get("min"), f.get("max")) for j in range(len(nd["vals"]))]
            elif k == "repeat":
                for row in nd["rows"]:
                    for j, f in enumerate(it.get("fields") or []):
                        if f.get("type") == "int" and _bounded_by(f, "") is None:
                            slots.append(("list", row, j, f.get("min"), f.get("max")))
            elif k == "graph" and it.get("weight") and it["weight"].get("type") == "int":
                f = it["weight"]
                slots += [("list", row, 2, f.get("min"), f.get("max")) for row in nd["rows"]]
            elif k == "matrix" and (it.get("elem") or {}).get("type") == "int":
                f = it["elem"]
                slots += [("list", row, j, f.get("min"), f.get("max")) for row in nd["rows"] for j in range(len(row))]
            elif k == "queries":
                types = {t["tag"]: t for t in it.get("types") or []}
                for row in nd["rows"]:
                    for j, f in enumerate(types.get(row[0], {}).get("fields") or []):
                        if f.get("type") == "int":
                            slots.append(("list", row, j + 1, f.get("min"), f.get("max")))
            elif k == "lists" and (it.get("elem") or {}).get("type") == "int":
                f = it["elem"]
                slots += [("list", row, j, f.get("min"), f.get("max")) for row in nd["rows"] for j in range(len(row))]
            if slots:
                groups.append((slots, full_env))
    return groups


def _shrink_values(spec, parsed, test, budget_ok):
    """Zmniejsza wartości: najpierw całe grupy (wszystkie → najprostsza, połowa), potem pojedynczo."""
    cur = parsed
    improved = False

    def try_assign(group_idx, assign):
        nonlocal cur, improved
        cand = copy.deepcopy(cur)
        groups = _value_slots(spec, cand)
        if group_idx >= len(groups):
            return False
        slots, env = groups[group_idx]
        any_change = False
        for si, newv in assign:
            kind, cont, key, lo_e, hi_e = slots[si]
            old = cont[key]
            if old == newv:
                continue
            cont[key] = newv
            any_change = True
        if not any_change:
            return False
        if test(cand):
            cur = cand
            improved = True
            return True
        return False
    ngroups = len(_value_slots(spec, cur))
    for gi in range(ngroups):
        if not budget_ok():
            break
        groups = _value_slots(spec, cur)
        if gi >= len(groups):
            break
        slots, env = groups[gi]

        def bounds(s):
            lo = _num_or_expr(s[3], env)
            hi = _num_or_expr(s[4], env)
            return (int(lo) if lo is not None else None, int(hi) if hi is not None else None)
        vals = [s[1][s[2]] for s in slots]
        if not all(isinstance(v, int) for v in vals):
            continue
        simp = [_simplest(*bounds(s)) for s in slots]
        # wszystkie naraz
        if any(v != t for v, t in zip(vals, simp)):
            if try_assign(gi, list(enumerate(simp))):
                continue
        # połowa wartości (w stronę najprostszej)
        half = [t + (v - t) // 2 for v, t in zip(vals, simp)]
        if any(h != v for h, v in zip(half, vals)) and try_assign(gi, list(enumerate(half))):
            groups = _value_slots(spec, cur)
            slots, env = groups[gi]
            vals = [s[1][s[2]] for s in slots]
        # pojedynczo (ograniczone)
        if len(slots) <= 40:
            for si in range(len(slots)):
                if not budget_ok():
                    break
                groups = _value_slots(spec, cur)
                slots, env = groups[gi]
                v = slots[si][1][slots[si][2]]
                t = simp[si]
                if not isinstance(v, int) or v == t:
                    continue
                step = 1 if v > t else -1
                cands_v = [t, t + (v - t) // 2]
                if abs(v - t) <= 10:
                    cands_v.append(v - step)
                for cand_v in cands_v:
                    if cand_v == v or (v > t and cand_v < t) or (v < t and cand_v > t):
                        continue
                    if try_assign(gi, [(si, cand_v)]):
                        break
    return cur if improved else None


# ============================================================================================== pomocnicze dla UI
def spec_summary(spec: dict) -> dict:
    return {"lines": describe(spec), "vars": spec.get("vars") or {}, "warnings": spec.get("warnings") or [],
            "confidence": spec.get("confidence")}


def clean_spec(spec: dict) -> dict:
    """Specyfikacja z UI → bezpieczna kopia (bez pól diagnostycznych)."""
    if not isinstance(spec, dict) or not isinstance(spec.get("items"), list):
        raise FormatError("Specyfikacja musi być obiektem z listą „items”.")
    s = copy.deepcopy(spec)
    for k in ("warnings", "confidence", "sample_parse"):
        s.pop(k, None)
    s.setdefault("vars", {})
    s.setdefault("version", 1)
    return s


# ============================================================================================== wczytywanie C++
_CPP_RESERVED = {
    "alignas", "alignof", "and", "asm", "auto", "bool", "break", "case", "catch", "char", "class", "const", "continue",
    "default", "delete", "do", "double", "else", "enum", "explicit", "export", "extern", "false", "float", "for",
    "friend", "goto", "if", "inline", "int", "long", "mutable", "namespace", "new", "not", "operator", "or", "private",
    "protected", "public", "register", "return", "short", "signed", "sizeof", "static", "struct", "switch", "template",
    "this", "throw", "true", "try", "typedef", "typename", "union", "unsigned", "using", "virtual", "void", "volatile",
    "while", "xor", "main", "cin", "cout", "endl", "std", "string", "vector", "pair", "min", "max", "abs", "ws",
    "y0", "y1", "j0", "j1", "jn", "yn", "gamma", "index", "rank", "count", "size", "begin", "end", "array", "list",
}


def cpp_reader(spec: dict, indent: str = "    ", ops_info: dict | None = None) -> str | None:
    """Wnętrze `main` w C++, które wczytuje dane w formacie `spec` (z `infer`) — do „Gotowego wczytywania danych”.
    None, gdy w specyfikacji jest coś, czego nie umiemy zapisać (wtedy wołający wybiera inny sposób)."""
    used: set[str] = set()
    known: set[str] = set()          # zmienne liczbowe, których można użyć w rozmiarach
    out: list[str] = []
    ops_info = ops_info or {}

    class _No(Exception):
        pass

    def fresh(base: str) -> str:
        b = re.sub(r"[^A-Za-z0-9_]", "", str(base or "")) or "x"
        if b[0].isdigit():
            b = "x" + b
        nm, i = b, 2
        while nm in used or nm in _CPP_RESERVED:
            nm, i = f"{b}{i}", i + 1
        used.add(nm)
        return nm

    def ctype(f: dict | None) -> str:
        t = (f or {}).get("type", "int")
        return {"int": "long long", "float": "double"}.get(t, "string")

    def cexpr(e) -> str:
        if isinstance(e, bool) or e is None or isinstance(e, dict):
            raise _No
        if isinstance(e, int):
            return str(e)
        s = str(e).strip().replace(" ", "")
        if "**" in s or "^" in s or not re.fullmatch(r"[A-Za-z0-9_+\-*/(),]+", s):
            raise _No
        for nm in re.findall(r"[A-Za-z_]\w*", s):
            if nm not in known and nm not in ("min", "max", "abs"):
                raise _No
        return re.sub(r"\b(min|max)\(", r"\1<long long>(", s)

    def plus1(e) -> str:
        c = cexpr(e)
        return f"{c} + 1" if re.fullmatch(r"\w+", c) else f"({c}) + 1"

    def until_lit(tok: str, f: dict) -> str:
        return tok if ctype(f) != "string" else '"' + tok.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def decl_group(ind: str, pairs: list[tuple[str, str]]) -> None:
        groups: dict[str, list[str]] = {}
        for nm, t in pairs:
            groups.setdefault(t, []).append(nm)
        for t, nms in groups.items():
            out.append(f"{ind}{t} {', '.join(nms)};")

    def field_names(fields, prefix="x") -> list[str]:
        names = []
        for j, f in enumerate(fields):
            base = f.get("var") or f.get("name") or (f"{prefix}{j + 1}" if len(fields) > 1 else prefix)
            names.append(fresh(base))
        return names

    def emit(items: list[dict], ind: str) -> None:
        for it in items:
            k = it.get("kind")
            if k == "line":
                fields = it.get("fields") or []
                if not fields:
                    continue
                names = field_names(fields)
                decl_group(ind, [(nm, ctype(f)) for nm, f in zip(names, fields)])
                out.append(f"{ind}cin >> {' >> '.join(names)};")
                known.update(nm for nm, f in zip(names, fields) if ctype(f) == "long long")
            elif k == "array":
                elem = it.get("elem") or {"type": "int"}
                t, nm = ctype(elem), fresh(elem.get("name") or "a")
                ln = it.get("len")
                if isinstance(ln, dict):          # długość nieznana: cała linia (row) albo do końca wejścia (column)
                    x = fresh("x")
                    out.append(f"{ind}vector<{t}> {nm};")
                    if it.get("layout") == "column":
                        out.append(f"{ind}for ({t} {x}; cin >> {x}; ) {nm}.push_back({x});   // do końca wejścia")
                    else:
                        lin, ss = fresh("linia"), fresh("ss")
                        out.append(f"{ind}{{   // cały wiersz wartości")
                        out.append(f"{ind}    string {lin};")
                        out.append(f"{ind}    getline(cin >> ws, {lin});")
                        out.append(f"{ind}    stringstream {ss}({lin});")
                        out.append(f"{ind}    for ({t} {x}; {ss} >> {x}; ) {nm}.push_back({x});")
                        out.append(f"{ind}}}")
                else:
                    x = fresh("x")
                    out.append(f"{ind}vector<{t}> {nm}({cexpr(ln)});")
                    out.append(f"{ind}for (auto &{x} : {nm}) cin >> {x};")
            elif k == "parents":
                n, nm, i = cexpr(it.get("n")), fresh("p"), fresh("i")
                out.append(f"{ind}vector<long long> {nm}({plus1(it.get('n'))});   // {nm}[i] = rodzic wierzchołka i")
                out.append(f"{ind}for (int {i} = 2; {i} <= {n}; {i}++) cin >> {nm}[{i}];")
            elif k in ("repeat", "graph"):
                emit_rows(it, ind)
            elif k == "matrix":
                elem = it.get("elem") or {"type": "int"}
                t, nm, r, x = ctype(elem), fresh("t"), fresh("wiersz"), fresh("x")
                out.append(f"{ind}vector<vector<{t}>> {nm}({cexpr(it.get('rows'))}, vector<{t}>({cexpr(it.get('cols'))}));")
                out.append(f"{ind}for (auto &{r} : {nm}) for (auto &{x} : {r}) cin >> {x};")
            elif k == "grid":
                nm, r = fresh("plansza"), fresh("wiersz")
                rows = it.get("rows")
                if isinstance(rows, dict):
                    out.append(f"{ind}vector<string> {nm};")
                    out.append(f"{ind}for (string {r}; cin >> {r}; ) {nm}.push_back({r});   // do końca wejścia")
                else:
                    out.append(f"{ind}vector<string> {nm}({cexpr(rows)});")
                    out.append(f"{ind}for (auto &{r} : {nm}) cin >> {r};")
            elif k == "string":
                nm = fresh(it.get("name") or "s")
                out.append(f"{ind}string {nm};")
                out.append(f"{ind}cin >> {nm};")
            elif k == "queries":
                emit_queries(it, ind)
            elif k == "lists":
                elem = it.get("elem") or {"type": "int"}
                t, nm, l_, kk, x = ctype(elem), fresh("listy"), fresh("l"), fresh("k"), fresh("x")
                out.append(f"{ind}vector<vector<{t}>> {nm}({cexpr(it.get('count'))});")
                out.append(f"{ind}for (auto &{l_} : {nm}) {{   // wiersz: k, potem k wartości")
                out.append(f"{ind}    int {kk};")
                out.append(f"{ind}    cin >> {kk};")
                out.append(f"{ind}    {l_}.resize({kk});")
                out.append(f"{ind}    for (auto &{x} : {l_}) cin >> {x};")
                out.append(f"{ind}}}")
            elif k == "block":
                cnt = it.get("count")
                ti = fresh("test")
                out.append(f"{ind}for (long long {ti} = 0; {ti} < {cexpr(cnt)}; {ti}++) {{   // każdy zestaw danych")
                saved_used, saved_known = set(used), set(known)
                emit(it.get("items") or [], ind + "    ")
                out.append(f"{ind}    // (tu są już wczytane dane jednego zestawu)")
                out.append(f"{ind}}}")
                used.clear(); used.update(saved_used | {ti})
                known.clear(); known.update(saved_known)
            else:
                raise _No

    def emit_rows(it: dict, ind: str) -> None:
        graph = it.get("kind") == "graph"
        if graph:
            w = it.get("weight")
            n = it.get("n")
            nm = fresh("g")
            base1 = it.get("base", 1) != 0
            cnt = it.get("count")
            a, b, c, i = fresh("a"), fresh("b"), fresh("w"), fresh("i")
            wt = ctype(w) if w else None
            out.append(f"{ind}vector<vector<{'pair<int, ' + wt + '>' if w else 'int'}>> {nm}("
                       f"{plus1(n) if base1 else cexpr(n)});   // lista sąsiedztwa")
            out.append(f"{ind}for (int {i} = 0; {i} < {cexpr(cnt)}; {i}++) {{")
            out.append(f"{ind}    int {a}, {b};")
            if w:
                out.append(f"{ind}    {wt} {c};")
                out.append(f"{ind}    cin >> {a} >> {b} >> {c};")
                out.append(f"{ind}    {nm}[{a}].push_back({{{b}, {c}}});")
                if not it.get("directed"):
                    out.append(f"{ind}    {nm}[{b}].push_back({{{a}, {c}}});")
            else:
                out.append(f"{ind}    cin >> {a} >> {b};")
                out.append(f"{ind}    {nm}[{a}].push_back({b});")
                if not it.get("directed"):
                    out.append(f"{ind}    {nm}[{b}].push_back({a});")
            out.append(f"{ind}}}")
            return
        fields = it.get("fields") or []
        if not fields:
            raise _No
        names = field_names(fields, "x")
        ts = [ctype(f) for f in fields]
        for nm, t in zip(names, ts):
            out.append(f"{ind}vector<{t}> {nm};")
        tmp = [fresh(nm + "_") for nm in names]
        cnt = it.get("count")
        push = [f"{ind}    {nm}.push_back({tv});" for nm, tv in zip(names, tmp)]
        if isinstance(cnt, dict) and cnt.get("until") is not None:
            stop = str(cnt["until"]).split()
            if len(stop) not in (1, len(fields)):
                raise _No
            out.append(f"{ind}while (true) {{")
            decl_group(ind + "    ", list(zip(tmp, ts)))
            if len(stop) == 1:
                out.append(f"{ind}    if (!(cin >> {tmp[0]}) || {tmp[0]} == {until_lit(stop[0], fields[0])}) break;"
                           "   // wiersz kończący dane")
                if len(tmp) > 1:
                    out.append(f"{ind}    cin >> {' >> '.join(tmp[1:])};")
            else:
                cond = " && ".join(f"{tv} == {until_lit(sv, f)}" for tv, sv, f in zip(tmp, stop, fields))
                out.append(f"{ind}    if (!(cin >> {' >> '.join(tmp)}) || ({cond})) break;   // wiersz kończący dane")
            out.extend(push)
            out.append(f"{ind}}}")
        elif isinstance(cnt, dict):
            out.append(f"{ind}while (true) {{   // wiersze do końca wejścia")
            decl_group(ind + "    ", list(zip(tmp, ts)))
            out.append(f"{ind}    if (!(cin >> {' >> '.join(tmp)})) break;")
            out.extend(push)
            out.append(f"{ind}}}")
        else:
            i = fresh("i")
            out.append(f"{ind}for (int {i} = 0; {i} < {cexpr(cnt)}; {i}++) {{")
            decl_group(ind + "    ", list(zip(tmp, ts)))
            out.append(f"{ind}    cin >> {' >> '.join(tmp)};")
            out.extend(push)
            out.append(f"{ind}}}")

    def emit_queries(it: dict, ind: str) -> None:
        cnt = it.get("count")
        qi, typ = fresh("qi"), fresh("typ")
        num = it.get("tag_type") == "int"
        if isinstance(cnt, dict):
            out.append(f"{ind}for ({'int' if num else 'string'} {typ}; cin >> {typ}; ) {{   // zapytania do końca wejścia")
        else:
            out.append(f"{ind}for (int {qi} = 0; {qi} < {cexpr(cnt)}; {qi}++) {{")
            out.append(f"{ind}    {'int' if num else 'string'} {typ};")
            out.append(f"{ind}    cin >> {typ};")
        reserved = set(used) | _CPP_RESERVED
        for j, t in enumerate(it.get("types") or []):
            tag = str(t.get("tag"))
            fields = t.get("fields") or []
            q = ops_info.get(tag) or {}
            names = [str(x) for x in q.get("params") or []]
            if len(names) != len(fields) or len(set(names)) != len(names) or \
                    not all(re.fullmatch(r"[A-Za-z_]\w{0,15}", x) for x in names):
                names = ["x"] if len(fields) == 1 else [f"x{i + 1}" for i in range(len(fields))]
            names = [x + "_" if x in reserved else x for x in names]
            if num and not re.fullmatch(r"-?\d+", tag):
                raise _No
            cond = f"{typ} == {tag}" if num else f'{typ} == "{tag}"'
            out.append(f"{ind}    {'if' if j == 0 else '} else if'} ({cond}) {{")
            if fields:
                groups: dict[str, list[str]] = {}
                for nm, f in zip(names, fields):
                    groups.setdefault(ctype(f), []).append(nm)
                for ty, nms in groups.items():
                    out.append(f"{ind}        {ty} {', '.join(nms)};")
                out.append(f"{ind}        cin >> {' >> '.join(names)};")
            mean = (q.get("meaning") or "").strip()
            note = (f" — {mean}" if mean else "") + (" (wypisuje odpowiedź)" if q.get("prints") else "")
            out.append(f"{ind}        // {' '.join([tag] + names)}{note}")
        if it.get("types"):
            out.append(f"{ind}    }}")
        out.append(f"{ind}}}")

    try:
        emit(spec.get("items") or [], indent)
    except _No:
        return None
    return "\n".join(out) if out else None
