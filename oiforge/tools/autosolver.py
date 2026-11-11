"""(f) Auto-solver: „pisze rozwiązania — często błędne, ale może prostsze mu się udadzą”.

Potok: parsowanie treści → klasyfikacja typu (sygnały, kształt wejścia, doradca algorytmów) → kandydaci:
  (a) szablony klasycznych zadań z `data/tasks/*.json` (C++, parametryzowane: TAK/NIE, modulo, znaki planszy…),
  (b) kod podobnych zadań ucznia (`user_data/programs`),
→ WERYFIKACJA: kandydaci kompilowani równolegle (pula 4, najlepiej pasujący najpierw, postęp na żywo), potem
  uruchamiani na przykładach z treści (`core.runner`); przy imporcie modułu rozgrzewka w tle kompiluje wszystkie
  szablony (maks. 2 kompilacje naraz, ustępuje bieżącemu rozwiązywaniu), więc później wszystko jest w cache,
→ ranking po liczbie zaliczonych przykładów + pewności klasyfikacji,
→ gdy żaden szablon nie pasuje: synteza programu (`core.synth`) — wyliczanie programów z języka typowych rozwiązań
  siłowych, sprawdzanych w interpreterze na przykładach, potem w C++ i na losowych testach; jeśli mieści się w
  limitach, jest rozwiązaniem, a jeśli nie — zweryfikowanym brute force (status „brute”),
→ wynik: najlepszy kod z nazwami zmiennych z treści albo szczere „nie znalazłem” + gotowe wczytywanie danych
  (osobno, jako `reader` — to nie jest rozwiązanie; nigdy nie udajemy wyniku „wynik = 0”).
Bez LLM — tylko reguły, TF-IDF, wyliczanie programów i uruchamianie kodu."""
from __future__ import annotations

import concurrent.futures as cf
import contextlib
import json
import os
import re
import threading
import time

from .. import config
from ..core import jobs, runner
from ..core import statement as ST
from ..core import text as T
from ..server_api import ApiError

try:
    from . import suggest as SG
except ImportError:  # pragma: no cover
    SG = None
try:
    from ..core import synth as SY
except ImportError:  # pragma: no cover
    SY = None

TASKS_DIR = config.DATA_DIR / "tasks"
_lock = threading.Lock()
_tpl_cache: dict = {"sig": None, "items": None}

HEADER = "#include <bits/stdc++.h>\nusing namespace std;\ntypedef long long ll;\n"

# domyślne wartości wstawiane do szablonów (najczęstsze w treściach) — z nimi rozgrzewamy cache binarek
DEFAULT_PH = {"YES": "TAK", "NO": "NIE", "NONE": "-1", "MOD": "1000000007", "PREC": "2", "START": "S", "END": "E",
              "WALL": "#", "LAND": "#", "SRC": "*", "FUEL": "."}

# kolejność „kanonicznych” zmiennych w kształtach (do zmiany nazw na nazwy z treści)
CANON = {
    "S1": ["n"], "S2": ["a", "b"], "S3": ["a", "b", "m"], "NA": ["n", "a"], "NKA": ["n", "k", "a"],
    "G": ["n", "m"], "GW": ["n", "m"], "T": ["n"], "NA-Q2": ["n", "a", "q"], "NA-Q1": ["n", "a", "q"],
    "NQA-Q2": ["n", "q"], "MAT": ["n", "m"], "NWR2": ["n", "W"], "NR2": ["n"], "NR1": ["n"],
    "GST": ["n", "m", "s", "t"], "GWST": ["n", "m", "s", "t"], "NA-S1": ["n", "a", "x"],
}


# ============================================================================================ szablony
def _tasks_signature() -> tuple:
    try:
        return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in TASKS_DIR.glob("*.json")))
    except OSError:
        return ()


def load_templates() -> list[dict]:
    """Płaska lista kandydatów (szablony + ich warianty), ze skompilowanymi wzorcami sygnałów."""
    sig = _tasks_signature()
    with _lock:
        if _tpl_cache["sig"] == sig and _tpl_cache["items"] is not None:
            return _tpl_cache["items"]
    items = []
    for p in sorted(TASKS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for t in data.get("templates", []):
            base = {k: t.get(k) for k in ("id", "name", "description", "algos", "shapes", "signals", "weight", "globals",
                                          "solve", "brute", "example", "kb")}
            base["chunk"] = data.get("chunk", p.stem)
            base["base_id"] = t["id"]
            base["_rx"] = _compile_signals(t.get("signals") or {})
            items.append(base)
            for v in t.get("variants") or []:
                var = dict(base)
                var.update({"id": v["id"], "name": v.get("name", v["id"]), "solve": v.get("solve", base["solve"]),
                            "variant_of": t["id"], "example": v.get("example"),
                            "brute": v.get("brute")})        # brute bazowego szablonu nie pasuje do wariantu
                var["weight"] = (base.get("weight") or 1.0) * 0.92
                items.append(var)
    with _lock:
        _tpl_cache.update(sig=sig, items=items)
    return items


def _compile_signals(s: dict) -> dict:
    return {"any": [re.compile(x) for x in s.get("any", [])],
            "all": [[re.compile(x) for x in grp] for grp in s.get("all", [])],
            "none": [re.compile(x) for x in s.get("none", [])]}


# ============================================================================================ cechy zadania
def shape_tags(fmt: list[dict]) -> set[str]:
    """Kształt wejścia jako znaczniki (NA = n + ciąg, G = graf n m + krawędzie, STR = napis…)."""
    tags = {"ANY"}
    if not fmt:
        return tags
    items = fmt
    mt = items[0].get("kind") == "multitest"
    if mt:
        items = [x for x in fmt if x.get("per_test")]
    sig = []
    for it in items:
        k = it.get("kind")
        rep = str(it.get("repeat") or "").replace(" ", "")
        if k == "scalars":
            sig.append(f"S{len(it.get('vars') or [])}")
        elif k == "array":
            sig.append("A")
        elif k == "string":
            sig.append("STR")
        elif k == "rows":
            if it.get("length"):
                sig.append("MATROWS")
            elif "string" in (it.get("types") or []):
                sig.append(f"RS{len(it.get('vars') or [])}:{rep}")      # wiersze „imię punkty”
            else:
                sig.append(f"R{len(it.get('vars') or [])}:{rep}")
        elif k == "grid":
            sig.append("GRID")
        elif k == "lines":
            sig.append("LINES")
        elif k == "queries":
            sig.append("Q")
    s = sig
    first = items[0] if items else {}
    fv = [ST.base_var(v) for v in (first.get("vars") or [])]

    def rep_is(x, var_idx):
        return len(fv) > var_idx and x.split(":")[-1] == fv[var_idx]

    t: set[str] = set()
    if s == ["S1"]:
        t.add("S1")
    elif s == ["S2"]:
        t.add("S2")
    elif s == ["S3"]:
        t.add("S3")
    elif len(s) == 1 and re.fullmatch(r"S\d+", s[0]):
        t.add("SK")
    if len(s) == 2 and s[0] == "S4" and len(fv) >= 4 and s[1].split(":")[-1] == fv[1]:
        if s[1].startswith("R2:"):
            t.add("GST")                # „n m s t”, potem m krawędzi
        elif s[1].startswith("R3:"):
            t.add("GWST")
    if len(s) == 2 and s[0] == "S1" and s[1] in ("Q", "LINES"):
        t.add("NQ")                     # q operacji („PUSH x”, „POP”…)
    if len(s) == 2 and s[0] == "S1" and s[1].startswith("RS2:") and rep_is(s[1], 0):
        t.add("NRS2")                   # n wierszy „napis liczba”
    if s[:2] == ["S1", "A"]:
        if len(s) == 2:
            t.add("NA")
        elif len(s) == 3 and s[2] == "S1":
            t.add("NA-S1")
        elif len(s) == 4 and s[2] == "S1" and s[3] == "A":
            t.update({"NA-NA", "NA-Q1"})
        elif len(s) == 4 and s[2] == "S1" and s[3].startswith("R2"):
            t.add("NA-Q2")
        elif len(s) == 4 and s[2] == "S1" and s[3].startswith("R1"):
            t.add("NA-Q1")
        elif len(s) == 3 and s[2] == "A":
            t.add("NA-A")
    if s[:2] == ["S2", "A"]:
        if len(s) == 2:
            t.add("NKA")
        elif len(s) == 3 and s[2].startswith("R2"):
            t.add("NQA-Q2")
        elif len(s) == 3 and s[2] == "A":
            t.add("NKA-A")
        elif len(s) == 3 and s[2] == "Q":
            t.add("NQA-Q")
    if s and s[0] == "S2" and len(s) >= 2:
        if s[1].startswith("R2:") and rep_is(s[1], 1):
            t.add("G" if len(s) == 2 else ("G+S2" if s[2:] == ["S2"] else ("G-Q2" if s[2:3] == ["S1"] else "G+")))
        if s[1].startswith("R3:") and rep_is(s[1], 1) and len(s) == 2:
            t.add("GW")
        if s[1].startswith("R2:") and rep_is(s[1], 0) and len(s) == 2:
            t.add("NWR2")
        if s[1] == "GRID" and len(s) == 2:
            t.add("GRID")
        if s[1] == "MATROWS" and len(s) == 2:
            t.add("MAT")
        if s[1] == "LINES" and len(s) == 2:
            t.add("GRID")
    if s and s[0] == "S1" and len(s) == 2:
        r = s[1]
        if r.startswith("R2:") and r.endswith("-1"):
            t.add("T")
        elif r.startswith("R3:") and r.endswith("-1"):
            t.add("TW")
        elif r.startswith("R2:"):
            t.add("NR2")
        elif r.startswith("R3:"):
            t.add("NR3")
        elif r.startswith("R1:"):
            t.update({"NR1", "NA"})
        elif r == "LINES":
            t.add("NLINES")
        elif r == "STR":
            t.add("NSTR")
    if s == ["STR"]:
        t.add("STR")
    elif s == ["STR", "STR"]:
        t.add("STR2")
    if mt:
        t = {"T*" + x for x in t} | {"MT"}
    return tags | t


def placeholders(parsed: dict) -> dict:
    """Wartości wstawiane do szablonów: słowa TAK/NIE, odpowiedź „brak”, modulo, precyzja, znaki planszy."""
    sec = parsed.get("sections") or {}
    out_txt = sec.get("wyjscie", "") + " " + sec.get("tresc", "")
    samples = parsed.get("samples") or []
    outs = " ".join(s.get("output") or "" for s in samples)
    toks = set(outs.split())
    words = set(re.findall(r"\b[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]+\b", out_txt))
    ph = dict(DEFAULT_PH)
    pairs = [("TAK", "NIE"), ("YES", "NO"), ("Tak", "Nie"), ("tak", "nie"), ("T", "N"), ("PRAWDA", "FAŁSZ"),
             ("TRUE", "FALSE"), ("true", "false"), ("Yes", "No"), ("ON", "OFF"), ("1", "0")]
    for a, b in pairs:
        if a in toks or b in toks:
            if (a in words or b in words or a in toks and b in toks) or a not in ("1", "T"):
                ph["YES"], ph["NO"] = a, b
                break
    low = T.strip_accents(out_txt).lower()
    m = re.search(r"(?:wypisz|wypisac|wypisuje|odpowiedzia (?:jest|powinno byc))\s+(?:jedno slowo |slowo |liczbe )?"
                  r"[„\"'‘]?(-1|nie|brak|niemozliwe|impossible|no)\b[^.]{0,80}(?:jesli|gdy|jezeli)", low) or \
        re.search(r"(?:jesli|gdy|jezeli|w przeciwnym)[^.]{0,120}?(?:wypisz|wypisac|nalezy wypisac|odpowiedzia jest|"
                  r"powinien wypisac|wypisujemy)\s+(?:jedno slowo |slowo |liczbe )?[„\"'‘]?(-1|nie|brak|niemozliwe|"
                  r"impossible|no|0)\b", low)
    if m:
        w = m.group(1)
        orig = re.search(r"\b" + re.escape(w) + r"\b", out_txt, re.I)
        ph["NONE"] = orig.group(0) if orig else w
        if ph["NONE"].lower() == "nie" and "NIE" in out_txt:
            ph["NONE"] = "NIE"
        if ph["NONE"].lower() == "brak" and "BRAK" in out_txt:
            ph["NONE"] = "BRAK"
    else:
        # „… albo słowo PUSTO, jeśli kolejka była pusta”, „wypisz NIEMOŻLIWE, gdy …”
        wy = sec.get("wyjscie", "")
        m2 = re.search(r"(?:albo|lub|wypisz|wypisa[cć]|wypisuje|komunikat|s[lł]owo|napis)\s+(?:s[lł]owo\s+|napis\s+|"
                       r"komunikat\s+)?[„\"'‘]?([A-ZĄĆĘŁŃÓŚŹŻ][A-ZĄĆĘŁŃÓŚŹŻ_!?.]+|-1)[”\"'’]?\s*,?\s*(?:je[sś]li|je[zż]eli|"
                       r"gdy|w przypadku|kiedy)", wy)
        if m2 and m2.group(1) not in (ph["YES"],) and m2.group(1).rstrip(".") not in ("TAK", "YES"):
            ph["NONE"] = m2.group(1).rstrip(".")
        elif "NIE" in toks:
            ph["NONE"] = "NIE"
        elif "BRAK" in toks:
            ph["NONE"] = "BRAK"
    full = T.strip_accents(" ".join(sec.values())).lower()
    mm = re.search(r"modulo\s*(\d[\d ^+*·]*\d|\d+)", full) or re.search(r"reszt\w* z dzielenia (?:jej |tej liczby |wyniku )?"
                                                                      r"przez\s*(\d[\d ^+*·]*\d|\d+)", full)
    if mm:
        v = ST.parse_value(mm.group(1).strip())
        if isinstance(v, int) and 1 < v < 2**62:
            ph["MOD"] = str(v)
    elif "998244353" in full:
        ph["MOD"] = "998244353"
    mp = re.search(r"(\d+|dw[oó]ch|trzech|jednego|czterech|sze[sś]ciu)\s+miejsc\w* po (?:przecinku|kropce)", full)
    if mp:
        w = mp.group(1)
        ph["PREC"] = {"dwoch": "2", "dwóch": "2", "trzech": "3", "jednego": "1", "czterech": "4", "szesciu": "6",
                      "sześciu": "6"}.get(w, w if w.isdigit() else "2")
    else:
        for s in samples:
            mdec = re.search(r"-?\d+\.(\d+)", s.get("output") or "")
            if mdec:
                ph["PREC"] = str(len(mdec.group(1)))
                break
    # znaki planszy z przykładów
    fmt = parsed.get("input_format") or []
    gi = [i for i, it in enumerate(fmt) if it.get("kind") in ("grid", "lines")]
    if gi and samples:
        cnt: dict = {}
        once: list = []
        c = ST.check_format(fmt, samples[0].get("input") or "")
        if c.get("ok") and gi[0] in c.get("spans", {}):
            a, b = c["spans"][gi[0]]
            rows = [ln for ln in (samples[0]["input"].split("\n")) if ln.strip()][a:b]
            for r in rows:
                for ch in r.strip():
                    cnt[ch] = cnt.get(ch, 0) + 1
            once = [ch for ch, k in cnt.items() if k == 1]
            if once:
                order = [ch for r in rows for ch in r.strip() if ch in once]
                # kolejność wg pierwszej wzmianki w treści („mysz (litera M) … ser (litera S)”), potem wg położenia
                full_txt = " ".join((parsed.get("sections") or {}).get(k, "") for k in ("tresc", "wejscie"))

                def mention(ch):
                    mm = re.search(r"(?<![A-Za-z0-9])[„\"'‘(]?" + re.escape(ch) + r"[”\"'’)]?(?![A-Za-z0-9])", full_txt)
                    return mm.start() if mm and ch.isalpha() else 10**9
                pos0 = {ch: i for i, ch in enumerate(order)}
                order.sort(key=lambda ch: (mention(ch), pos0[ch]))
                ph["START"] = order[0]
                ph["END"] = order[1] if len(order) > 1 else order[0]
            common = sorted((k, ch) for ch, k in cnt.items() if ch not in once)
            if common:
                wall_cands = [ch for k, ch in common if ch in "#X*x@"]
                ph["WALL"] = wall_cands[0] if wall_cands else (common[0][1] if len(common) > 1 else "#")
                land = [ch for k, ch in common if ch not in ".-_"]
                ph["LAND"] = land[0] if land else common[-1][1]
            # BFS z wielu źródeł: źródło = najrzadszy znak różny od „.”/„#”, „paliwo” = najczęstszy z pozostałych
            letters = sorted((k, ch) for ch, k in cnt.items() if ch not in ".#")
            if letters:
                ph["SRC"] = letters[0][1]
                rest = sorted(((k, ch) for ch, k in cnt.items() if ch != ph["SRC"] and ch != "#"), reverse=True)
                ph["FUEL"] = rest[0][1] if rest else "."
    return ph


def _fill(code: str, ph: dict) -> str:
    for k, v in ph.items():
        code = code.replace("{{" + k + "}}", v.replace("\\", "\\\\").replace("\"", "\\\"") if k not in ("MOD", "PREC")
                            else v)
    return code


def render(tpl: dict, ph: dict, multitest: bool = False, brute: bool = False) -> str:
    body = tpl.get("brute") if brute and tpl.get("brute") else tpl.get("solve") or ""
    glob = tpl.get("globals") or ""
    main = "    int T_; cin >> T_;\n    while (T_--) solve();\n" if multitest else "    solve();\n"
    code = (HEADER + (glob + "\n" if glob else "") + "\nvoid solve() {\n" + _indent(body, "    ") + "\n}\n\n"
            "int main() {\n    ios::sync_with_stdio(false);\n    cin.tie(nullptr);\n" + main + "    return 0;\n}\n")
    return _fill(code, ph)


def _indent(s: str, ind: str) -> str:
    return "\n".join((ind + ln) if ln.strip() else ln for ln in s.split("\n"))


# ============================================================================================ punktacja kandydatów
def template_score(tpl: dict, ntext: str, tags: set, algo_rank: dict) -> tuple[float, list]:
    rx = tpl["_rx"]
    reasons = []
    if any(r.search(ntext) for r in rx["none"]):
        return -1.0, ["wykluczony przez słowa w treści"]
    sig = 0.0
    hits = [r.pattern for r in rx["any"] if r.search(ntext)]
    if hits:
        sig += min(2.0, 0.9 + 0.55 * (len(hits) - 1))
        reasons.append("słowa w treści: " + ", ".join(_pretty_pat(h) for h in hits[:3]))
    if rx["all"]:
        if all(any(r.search(ntext) for r in grp) for grp in rx["all"]):
            sig += 1.5
            reasons.append("pasują wszystkie wymagane słowa")
        elif not rx["any"]:
            sig -= 0.5
    shapes = set(tpl.get("shapes") or [])
    inner = {x[2:] for x in tags if x.startswith("T*")}
    shape = 0.0
    if shapes & tags:
        shape = 2.0
        reasons.append("pasuje kształt wejścia")
    elif shapes & inner:
        shape = 1.7
        reasons.append("pasuje kształt wejścia (w każdym zestawie)")
    elif "ANY" in shapes:
        shape = 0.4
    algo = sum(algo_rank.get(a, 0.0) for a in tpl.get("algos") or [])
    if algo > 0.2:
        reasons.append("zgodny z doradcą algorytmów")
    score = (tpl.get("weight") or 1.0) * (shape + sig + 0.9 * min(1.0, algo))
    return score, reasons


def story_text(p: dict) -> str:
    """Tytuł + właściwa treść (bez opisu wejścia/wyjścia, gdzie „pierwszy wiersz” itp. to tylko formułki)."""
    t = T.strip_accents(" ".join([p.get("title") or "", (p.get("sections") or {}).get("tresc", "")])).lower()
    return re.sub(r"\s+", " ", t)


def sig_strength(tpl: dict, ntext: str, story: str = "", title: str = "") -> bool:
    """Czy szablon jest związany z treścią MOCNO: ≥ 2 trafione sygnały, komplet słów wymaganych, trafiona fraza
    wielowyrazowa („wspólny podciąg”, „ile razy”) albo słowo-klucz w tytule / powtórzone w samej historyjce
    („Suma ciągu … oblicz sumę”). Pojedyncze słowo z formułki („pierwszy wiersz” → „pierwsz”) bywa przypadkiem."""
    rx = tpl["_rx"]
    hit = [r for r in rx["any"] if r.search(ntext)]
    ms = [r.search(ntext) for r in hit]
    all_ok = bool(rx["all"]) and all(any(r.search(ntext) for r in grp) for grp in rx["all"])
    if len(ms) >= 2 or all_ok or any(" " in m.group(0).strip() for m in ms):
        return True
    title = T.strip_accents(title or "").lower()
    return any((title and r.search(title)) or len(r.findall(story)) >= 2 for r in hit)


def norm_text(p: dict) -> str:
    """Tekst zadania do dopasowań sygnałów: tytuł + sekcje, bez polskich znaków, małe litery."""
    t = T.strip_accents(" ".join([p.get("title") or ""] + list((p.get("sections") or {}).values()))).lower()
    return re.sub(r"\s+", " ", t)


def choose_templates(ntext: str, tags: set, algo_rank: dict | None = None, max_templates: int = 18,
                     shape_extra: int = 6) -> list[tuple]:
    """[(punkty, szablon, uzasadnienia)] — szablony pasujące słowami (≥ 1.2) + kilka pasujących samym kształtem."""
    scored = []
    for tpl in load_templates():
        sc, why = template_score(tpl, ntext, tags, algo_rank or {})
        if sc <= 0:
            continue
        scored.append((sc, tpl, why))
    scored.sort(key=lambda x: -x[0])
    chosen = [x for x in scored if x[0] >= 1.2][:max_templates]
    inner = tags | {t[2:] for t in tags if t.startswith("T*")}
    shape_only = [x for x in scored if x not in chosen and set(x[1].get("shapes") or []) & inner
                  and "ANY" not in (x[1].get("shapes") or [])][:shape_extra]
    return chosen + shape_only


def _pretty_pat(p: str) -> str:
    s = re.sub(r"\\w\*|\\b|\\s|\(\?:|\)|\(|\[|\]|\\|\?|\*|\+|\.|\{.*?\}", "", p)
    return s.split("|")[0][:24]


# ============================================================================================ rozgrzewka kompilatora
# Pierwsze użycie po wyczyszczeniu cache kompilowało ~25 kandydatów (minuty). Przy imporcie modułu (start serwera)
# kompilujemy w tle wszystkie szablony z domyślnymi wartościami — potem `runner.compile_cpp` trafia w cache binarek.
WARMUP_PARALLEL = 2
_warm: dict = {"started": False, "running": False, "done": 0, "total": 0, "compiled": 0, "failed": [], "t0": None,
               "t1": None}
_active_solves = [0]
_active_lock = threading.Lock()


@contextlib.contextmanager
def _solving():
    """Na czas rozwiązywania rozgrzewka ustępuje miejsca (czeka między kompilacjami)."""
    with _active_lock:
        _active_solves[0] += 1
    try:
        yield
    finally:
        with _active_lock:
            _active_solves[0] -= 1


def warmup_sources() -> list[str]:
    """Źródła do rozgrzewki: każdy szablon i jego brute, z domyślnymi wartościami (TAK/NIE, -1, 10^9+7…)."""
    out, seen = [], set()
    tpls = load_templates()
    for t in sorted(tpls, key=lambda x: (bool(x.get("variant_of")), x["id"])):
        for brute in (False, True):
            if brute and not t.get("brute"):
                continue
            code = render(t, DEFAULT_PH, brute=brute)
            if code not in seen:
                seen.add(code)
                out.append(code)
    return out


def _warm_worker() -> None:
    _warm.update(running=True, t0=time.time())
    try:
        srcs = warmup_sources()
        _warm["total"] = len(srcs)

        def one(code):
            while _active_solves[0] > 0:          # niski priorytet: nie konkurujemy z bieżącym rozwiązywaniem
                time.sleep(0.5)
            try:
                r = runner.compile_cpp(code, low_priority=True)
                if not r.cached:
                    _warm["compiled"] += 1
                if not r.ok:
                    _warm["failed"].append(code.split("void solve()")[0][-80:])
            except Exception:  # noqa: BLE001 — rozgrzewka nigdy nie może wywrócić serwera
                pass
            _warm["done"] += 1

        with cf.ThreadPoolExecutor(max_workers=WARMUP_PARALLEL, thread_name_prefix="as-warm") as ex:
            list(ex.map(one, srcs))
    finally:
        _warm.update(running=False, t1=time.time())


def start_warmup(force: bool = False) -> bool:
    """Startuje rozgrzewkę w wątku w tle (raz na proces). Wyłączenie: zmienna środowiskowa OIFORGE_NO_WARMUP=1."""
    if (_warm["started"] and not force) or _warm["running"]:
        return False
    if os.environ.get("OIFORGE_NO_WARMUP") or not config.CXX:
        return False
    _warm["started"] = True
    threading.Thread(target=_warm_worker, name="autosolver-warmup", daemon=True).start()
    return True


def warmup_status() -> dict:
    w = dict(_warm)
    w["failed"] = len(_warm["failed"])
    w["ready"] = bool(w["total"]) and w["done"] >= w["total"]
    return w


# ============================================================================================ uruchamianie
def _float_eq(a: str, b: str) -> bool:
    return runner.outputs_equal(a, b, float_eps=1e-6 if re.search(r"\d\.\d", b) else None)


BIG_SAMPLE = getattr(ST, "BIG_SAMPLE", 100_000)


def is_big(s: dict) -> bool:
    """Przykład z bardzo długim wejściem (np. test „ocen” z liczbą o milionie cyfr)."""
    return len(s.get("input") or "") > BIG_SAMPLE


def run_on_samples(code: str, samples: list[dict], timeout: float = 2.0, lang: str | None = None,
                   prog: runner.Program | None = None, skip_big: bool = False) -> dict:
    """Uruchamia kod na przykładach. Duże przykłady idą na końcu i są pomijane (SKIP), gdy `skip_big` (wolne
    kandydaty, np. brute) albo gdy kandydat już oblał któryś mały przykład — nie blokujemy aplikacji."""
    prog = prog or runner.prepare(code, lang=lang)
    if not prog.ok:
        return {"compiled": False, "passed": 0, "total": len(samples), "results": [],
                "compile_error": (prog.compile.stderr if prog.compile else prog.error)[:3000]}
    res: list = [None] * len(samples)
    passed = skipped = 0
    failed = False
    order = sorted(range(len(samples)), key=lambda i: (is_big(samples[i]), i))
    for i in order:
        s = samples[i]
        if is_big(s) and (skip_big or failed):
            skipped += 1
            res[i] = {"name": s.get("name"), "ok": None, "verdict": "SKIP", "skipped": True, "got": "",
                      "expected": s["output"][:2000], "time": 0.0, "source": s.get("source")}
            continue
        rr = runner.run(prog.cmd, s["input"], timeout=timeout)
        ok = rr.verdict == "OK" and _float_eq(rr.stdout, s["output"])
        passed += ok
        failed = failed or not ok
        res[i] = {"name": s.get("name"), "ok": ok, "verdict": rr.verdict if not (rr.verdict == "OK" and not ok) else "WA",
                  "got": rr.stdout[:2000], "expected": s["output"][:2000], "time": rr.time, "source": s.get("source")}
    return {"compiled": True, "passed": passed, "total": len(samples) - skipped, "skipped": skipped, "results": res}


# ============================================================================================ siła dowodu
def _trivial_out(o: str) -> bool:
    o = (o or "").strip()
    return len(o.split()) <= 1 and len(o) <= 3


def weak_evidence(c: dict) -> list[str]:
    """Dlaczego zgodność kandydata z przykładami to SŁABY dowód (pusta lista = dowód wystarczający):
    „shape” — brak związku słownego z treścią (pasuje tylko kształtem wejścia/wyjścia),
    „trivial” — sprawdzony tylko na jednym przykładzie, którego wynik to pojedyncza mała liczba/słowo, a związek
    z treścią nie jest mocny."""
    verified = [r for r in c.get("results") or [] if r and r.get("ok")]
    reasons = []
    if not c.get("sig_hit"):
        reasons.append("shape")
    elif len(verified) <= 1 and all(_trivial_out(r.get("expected")) for r in verified) and not c.get("sig_strong"):
        reasons.append("trivial")
    return reasons


def weak_message(c: dict, reasons: list[str], what: str = "Kandydat") -> str:
    verified = [r for r in c.get("results") or [] if r and r.get("ok")]
    out = (verified[0].get("expected") or "").strip() if verified else ""
    if "shape" in reasons:
        msg = (f"{what} „{c.get('name')}” przechodzi przykłady ({c.get('passed')}/{c.get('total')}), ale nie ma żadnego "
               "związku z treścią — pasuje tylko kształtem wejścia i wyjścia.")
        if len(verified) <= 1:
            msg += f" Sprawdzony na jednym przykładzie (wynik „{out}”)."
        return msg + " To za mało, żeby ufać — sprawdź testerką."
    return (f"Pasuje do jedynego przykładu (wynik „{out}”), ale to za mało, żeby ufać — sprawdź testerką. "
            f"({what} „{c.get('name')}” tylko luźno pasuje do słów treści.)")


# ============================================================================================ ładny kod
_ID_OK = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,15}")


def _rename(code: str, tags: set, fmt: list[dict]) -> tuple[str, dict]:
    """Zmienia kanoniczne nazwy (n, k, a…) na nazwy z treści, jeśli to bezpieczne."""
    tag = next((t for t in CANON if t in tags or ("T*" + t) in tags), None)
    if not tag:
        return code, {}
    items = [x for x in fmt if not x.get("per_test")] if not any(t.startswith("T*") for t in tags) else \
        [x for x in fmt if x.get("per_test")]
    names = []
    for it in items:
        if it.get("kind") in ("scalars", "array"):
            names += [ST.cpp_name(v) for v in it.get("vars") or []]
    canon = CANON[tag]
    mapping = {}
    for c, nm in zip(canon, names):
        if c == nm or not _ID_OK.fullmatch(nm) or nm in ST._CPP_KEYWORDS or len(nm) > 12:
            continue
        if re.search(r"\b" + re.escape(nm) + r"\b", code):
            continue
        if nm in mapping.values():
            continue
        mapping[c] = nm
    if not mapping:
        return code, {}
    head, sep, body = code.partition("void solve() {")
    if not sep:
        return code, {}
    for c, nm in mapping.items():
        body = re.sub(r"(?<![\w.'\"])" + re.escape(c) + r"(?![\w'\"(])", nm, body)
    return head + sep + body, mapping


def _decorate(code: str, title: str, name: str, passed: int, total: int, source: str, weak: str | None = None) -> str:
    head = [f"// Zadanie: {title}" if title else "// Zadanie: (bez tytułu)",
            f"// Rozwiązanie z auto-solvera OI Forge — {source}: {name}",
            f"// Przechodzi przykłady z treści: {passed}/{total}. Przykłady to nie wszystko — sprawdź testy brzegowe!"]
    if weak:
        head.append("// UWAGA — SŁABY DOWÓD: " + weak)
    return "\n".join(head) + "\n" + code


def reader_and_hints(parsed: dict, advice: dict | None) -> tuple[str | None, list[str]]:
    """Gotowe wczytywanie danych (kompletny program, który TYLKO czyta wejście) + wskazówki doradcy jako tekst."""
    reader = SY.reader_program(parsed) if SY is not None else None
    if reader is None and SY is None:      # synteza sprawdza, czy wczytywanie się kompiluje — bez niej: wprost z formatu
        fmt = parsed.get("input_format") or []
        samples = [s for s in parsed.get("samples") or [] if s.get("input")]
        if fmt or samples:
            rd = ST.cpp_reader(fmt, samples=samples)
            reader = ("// Gotowe wczytywanie danych wygenerowane z formatu wejścia (to NIE jest rozwiązanie).\n"
                      "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios::sync_with_stdio(false);\n"
                      "    cin.tie(nullptr);\n" + rd["code"] + "\n    return 0;\n}\n")
    hints = []
    cx = (advice or {}).get("complexity") or {}
    if cx.get("complexity"):
        hints.append(f"Dopuszczalna złożoność: {cx['complexity']} ({cx.get('var')} ≤ {cx.get('max_fmt')}).")
    algos = (advice or {}).get("algorithms") or []
    if algos:
        hints.append("Doradca sugeruje: " + ", ".join(a["name"] for a in algos[:3]) + ".")
        if algos[0].get("when"):
            hints.append(f"{algos[0]['name']}: {algos[0]['when']}")
    out = (parsed.get("sections") or {}).get("wyjscie", "")
    if out and ST._sentences(out):
        hints.append("Co wypisać: " + ST._sentences(out)[0][:200])
    return reader, hints


# ============================================================================================ główny algorytm
def _trivial_samples(samples: list[dict]) -> bool:
    outs = [s["output"].strip() for s in samples]
    return len(samples) <= 1 and all(len(o.split()) <= 1 and len(o) <= 3 for o in outs)


def solve(statement: str, job=None, *, exclude_keys: set | None = None, exclude_similar_above: float | None = None,
          max_templates: int = 18, use_user_code: bool = True, timeout: float = 2.0, parallel: int = 4,
          use_synth: bool = True) -> dict:
    t0 = time.time()

    def prog(f, msg):
        if job is not None:
            job.progress(f, msg)

    prog(0.02, "Analizuję treść…")
    p = ST.parse(statement)
    samples = [s for s in p.get("samples") or [] if s.get("input") is not None and s.get("output")]
    advice = None
    if SG is not None:
        try:
            advice = SG.analyze(statement, parsed=p, exclude_keys=exclude_keys, with_code=False)
        except Exception:  # noqa: BLE001
            advice = None
    algo_rank = {a["id"]: a["relative"] for a in (advice or {}).get("algorithms", [])[:6] if a.get("relative")}
    tags = shape_tags(p.get("input_format") or [])
    ph = placeholders(p)
    ntext = norm_text(p)
    story = story_text(p)
    multitest = any(x.startswith("T*") for x in tags)
    # --- kandydaci z szablonów
    chosen = choose_templates(ntext, tags, algo_rank, max_templates=max_templates)
    candidates = []
    for sc, tpl, why in chosen:
        shapes = set(tpl.get("shapes") or [])
        inner_match = multitest and bool(shapes & {t[2:] for t in tags if t.startswith("T*")}) and \
            not any(s.startswith("T*") for s in shapes)
        code = render(tpl, ph, multitest=inner_match)
        sig_hit = any(w.startswith("słowa w treści") or w.startswith("pasują wszystkie") for w in why)
        candidates.append({"kind": "template", "id": tpl["id"], "name": tpl["name"], "score": round(sc, 3),
                           "reasons": why, "code": code, "algos": tpl.get("algos"), "multitest_wrap": inner_match,
                           "sig_hit": sig_hit, "sig_strong": sig_hit and sig_strength(tpl, ntext, story, p.get("title"))})
    # --- kandydaci z kodu ucznia
    if use_user_code and SG is not None:
        try:
            sims = SG.similar_tasks(SG.doc_text(p), k=4, with_code=True, exclude=exclude_keys)
        except Exception:  # noqa: BLE001
            sims = []
        for s in sims:
            if exclude_similar_above is not None and s["similarity"] >= exclude_similar_above:
                continue
            if s["similarity"] < 0.18 or not s.get("code"):
                continue
            label = "Twoje rozwiązanie tego zadania" if s["same_task"] else f"Twój kod z zadania «{s['title']}»"
            candidates.append({"kind": "user", "id": s["key"], "name": label, "score": round(3.5 * s["similarity"], 3),
                               "sig_hit": s["similarity"] >= 0.35,
                               "sig_strong": bool(s["same_task"]) or s["similarity"] >= 0.5,
                               "reasons": [f"podobieństwo treści {round(100 * s['similarity'])}%"], "code": s["code"],
                               "path": s.get("path"), "similarity": s["similarity"], "same_task": s["same_task"],
                               "sio2_score": s.get("score")})
    if not samples:
        res = _finish(p, advice, candidates, [], ph, tags, t0, no_samples=True)
        return res
    # --- weryfikacja: (1) kompilacja równolegle, najlepiej pasujący najpierw, (2) uruchomienie na przykładach
    verify_candidates(candidates, samples, job=job, timeout=timeout, parallel=parallel, f1=0.8 if use_synth else 0.95)
    res = _finish(p, advice, candidates, samples, ph, tags, t0, job=job, timeout=timeout)
    if use_synth:
        if res.get("status") == "solved":
            res = with_challenge(res, p, statement, advice, job=job, timeout=timeout)
        res = with_synth(res, p, statement, job=job, timeout=timeout)
        res["time"] = round(time.time() - t0, 2)
    return res


def with_challenge(res: dict, p: dict, statement: str, advice: dict | None, job=None, timeout: float = 2.0) -> dict:
    """Rozwiązanie z szablonu / podobnego zadania przechodzi przykłady — ale czy inny wiarygodny program (zgodny ze
    słowami treści i przykładami) nie liczy czegoś innego? Jeśli tak, przykłady nie rozstrzygają: wynik „weak”."""
    best = res.get("best") or {}
    # Twój własny program zaakceptowany na SIO2 dla tego samego zadania to wzorzec — inny program zgodny z krótkim
    # przykładem nie może go podważyć.
    if SY is None or best.get("kind") == "synth" or (best.get("kind") == "user" and (best.get("same_task") or best.get("confidence", 0) >= 0.9)):
        return res
    if job is not None:
        job.progress(0.82, "Sprawdzam, czy inny program zgodny z przykładami nie liczy czegoś innego…")
    try:
        ch = SY.challenge(p, statement, best["code"], words=res.get("placeholders"), timeout=timeout)
    except Exception:  # noqa: BLE001
        return res
    res["challenge"] = {k: ch.get(k) for k in ("checked", "tests", "agree", "time")}
    if not ch.get("competitors"):
        return res
    comp = ch["competitors"]
    other = ("inny program zgodny z przykładami i treścią ({}) daje" if len(comp) == 1 else
             "inne programy zgodne z przykładami i treścią (np. {}) dają").format(comp[0]["desc"])
    msg = (f"„{best.get('name')}” przechodzi przykłady, ale {other} inne wyniki na losowym teście — przykłady nie "
           "rozstrzygają, który jest właściwy. Sprawdź na wejściu poniżej.")
    best = dict(best, weak=True, confidence=round(min(best.get("confidence", 0.5), 0.35), 3),
                code="// UWAGA — NIEPEWNE: " + msg + "\n" + best["code"])
    reader, hints = reader_and_hints(p, advice)
    res.update(ok=False, status="weak", best=best, weak_reasons=["ambiguous"], message=msg, competitors=comp,
               reader=reader, hints=hints, challenge_failed=True)
    return res


def _synth_summary(syn: dict) -> dict:
    return {k: syn.get(k) for k in ("status", "desc", "family", "cx", "fast", "reasons", "message", "candidates",
                                    "matches", "cross", "passed", "total", "slow", "competitors", "random_tests", "time")}


def with_synth(res: dict, p: dict, statement: str, job=None, timeout: float = 2.0, budget: float = 5.0) -> dict:
    """Gdy szablony nie dały pewnego rozwiązania: synteza programu. Zweryfikowany i szybki → rozwiązanie;
    zweryfikowany, ale za wolny → status „brute” (uczciwie: szybkiego nie znalazłem); niejednoznaczny → „weak”."""
    if SY is None or res.get("status") in ("solved", "no_samples") or res.get("challenge_failed"):
        return res
    try:
        syn = SY.synthesize(p, statement, words=res.get("placeholders"), job=job, budget=budget, want="solution",
                            f0=0.8, f1=0.99, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — synteza nigdy nie może wywrócić auto-solvera
        res["synth"] = {"status": "error", "message": f"Synteza przerwana: {e}"}
        return res
    res["synth"] = _synth_summary(syn)
    st = syn.get("status")
    if st not in ("verified", "weak") or not syn.get("code"):
        if res.get("status") == "unsolved" and syn.get("candidates"):
            res["message"] += (f" Synteza sprawdziła też {syn['candidates']} programów (wzory, pary, fragmenty, "
                               "podzbiory, grafy…) — żaden nie daje wyników z przykładów.")
        return res
    head = (f"// Przechodzi przykłady z treści: {syn['passed']}/{syn['total']}"
            + (f" (na {syn['slow']} za wolny)" if syn.get("slow") else "")
            + f"; zgodny z interpreterem na {syn['cross']} losowych testach.\n")
    best = {"kind": "synth", "id": "synth:" + (syn.get("family") or ""), "name": "Synteza: " + syn["desc"],
            "code": head + syn["code"], "passed": syn["passed"], "total": syn["total"], "renamed": {}, "path": None,
            "cx": syn.get("cx"), "fast": syn.get("fast")}
    if st == "verified":
        if syn.get("fast"):
            best["confidence"] = 0.8
            res.update(ok=True, status="solved", best=best,
                       message=f"Przechodzi przykłady {syn['passed']}/{syn['total']} ✔ — program znaleziony przez syntezę "
                               f"(„{syn['desc']}”), zgodny z interpreterem na {syn['cross']} losowych testach.",
                       warning=("Przykłady sprawdzają tylko małe przypadki — przetestuj jeszcze na testach brzegowych."))
        else:
            best.update(confidence=0.6, brute=True)
            res.update(ok=False, status="brute", best=best,
                       message=f"Mam zweryfikowany brute force: „{syn['desc']}” (O({syn.get('cx')})) — przechodzi przykłady "
                               f"i zgadza się z interpreterem na {syn['cross']} losowych testach, ale jest za wolny na "
                               "limity z treści. Szybkiego rozwiązania nie znalazłem.",
                       warning="Użyj go w testerce jako wzorca do sprawdzania własnego szybkiego rozwiązania.")
    elif res.get("status") == "unsolved":
        best.update(confidence=0.3, weak=True)
        res.update(ok=False, status="weak", best=best, message=syn["message"], weak_reasons=syn.get("reasons"),
                   competitors=syn.get("competitors"))
    else:
        res["competitors"] = syn.get("competitors")
    return res


def verify_candidates(candidates: list[dict], samples: list[dict], job=None, timeout: float = 2.0,
                      parallel: int = 4, f0: float = 0.08, f1: float = 0.95, skip_big: bool = False) -> None:
    """Kompiluje kandydatów (pula `parallel`, w kolejności malejącej punktacji) z postępem na żywo, potem uruchamia
    każdego na przykładach. Uzupełnia w kandydatach: passed, total, compiled, results, compile_error."""
    total = len(candidates)
    if not total:
        return
    lock = threading.Lock()
    state = {"done": 0, "cold": 0}
    order = sorted(range(total), key=lambda i: -(candidates[i].get("score") or 0))
    progs: dict[int, runner.Program] = {}
    mid = f0 + 0.75 * (f1 - f0)

    def prog_msg(f, msg):
        if job is not None:
            job.progress(f, msg)

    def compile_one(i):
        if job is not None and job.cancelled:
            return
        c = candidates[i]
        lang = runner.detect_lang(c["code"]) if c.get("kind") == "user" else "cpp"
        pr = runner.prepare(c["code"], lang=lang)
        with lock:
            progs[i] = pr
            state["done"] += 1
            if pr.compile is not None and not pr.compile.cached and pr.lang == "cpp":
                state["cold"] += 1
            msg = f"Kompiluję kandydatów {state['done']}/{total}"
            if state["cold"]:
                msg += " — pierwsze uruchomienie trwa dłużej (kompilacja szablonów, potem będą w cache)"
            prog_msg(f0 + (mid - f0) * state["done"] / total, msg)
        if job is not None:
            job.push({"type": "compiled", "id": c["id"], "name": c["name"], "ok": pr.ok,
                      "cached": bool(pr.compile and pr.compile.cached), "done": state["done"], "total": total})

    prog_msg(f0, f"Kompiluję kandydatów 0/{total}…")
    state["ran"] = 0

    def run_one(i):
        c = candidates[i]
        pr = progs.get(i)
        if pr is None or (job is not None and job.cancelled):
            return
        if pr.ok and pr.lang == "cpp" and pr.cmd and not os.path.exists(pr.cmd[0]):
            # binarka zdążyła wypaść z cache (limit liczby plików) między kompilacją a uruchomieniem — skompiluj ponownie
            pr = runner.prepare(c["code"], lang="cpp")
        r = run_on_samples(c["code"], samples, timeout=timeout, prog=pr, skip_big=skip_big)
        c.update({"passed": r["passed"], "total": r["total"], "compiled": r["compiled"], "results": r["results"],
                  "compile_error": r.get("compile_error"), "skipped": r.get("skipped", 0)})
        with lock:
            state["ran"] += 1
            prog_msg(mid + (f1 - mid) * state["ran"] / total,
                     f"Sprawdzam kandydatów na przykładach: {state['ran']}/{total}")
        if job is not None:
            job.push({"type": "candidate", "id": c["id"], "name": c["name"], "kind": c.get("kind"),
                      "passed": c["passed"], "total": c["total"], "compiled": c["compiled"]})

    with _solving(), cf.ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        list(ex.map(compile_one, order))
        list(ex.map(run_one, order))


def _finish(p, advice, candidates, samples, ph, tags, t0, no_samples=False, job=None, timeout=2.0) -> dict:
    for c in candidates:
        c.setdefault("passed", 0)
        c.setdefault("total", len(samples))
        ratio = c["passed"] / c["total"] if c["total"] else 0.0
        conf = ratio * (0.55 + 0.45 * min(1.0, c["score"] / 4.0))
        if c["kind"] == "user" and c.get("same_task"):
            conf = max(conf, 0.9 * ratio)
        if ratio == 1.0 and _trivial_samples(samples):
            conf *= 0.6
        if not c.get("sig_hit"):
            conf *= 0.45          # pasuje tylko kształtem wejścia, nie słowami treści — łatwo o przypadek
        c["confidence"] = round(conf, 3)
    ranked = sorted(candidates, key=lambda c: (-(c["passed"] / c["total"] if c["total"] else 0), -int(bool(c.get("sig_hit"))),
                                               -c["confidence"], -c["score"]))
    best = ranked[0] if ranked and ranked[0]["passed"] == ranked[0]["total"] and ranked[0]["total"] > 0 else None
    result = {
        "title": p.get("title"),
        "samples": samples,
        "shape": sorted(tags),
        "placeholders": ph,
        "classification": {"topics": (advice or {}).get("topics", [])[:3],
                            "algorithms": [{"id": a["id"], "name": a["name"], "confidence": a["confidence"]}
                                           for a in (advice or {}).get("algorithms", [])[:4]],
                            "complexity": ((advice or {}).get("complexity") or {}).get("complexity")},
        "candidates": [{k: c.get(k) for k in ("kind", "id", "name", "score", "confidence", "passed", "total", "compiled",
                                              "reasons", "results", "compile_error", "path", "similarity", "code", "sig_hit",
                                              "sig_strong", "skipped")}
                       for c in ranked],
        "time": None,
    }
    if no_samples:
        reader, hints = reader_and_hints(p, advice)
        result.update(ok=False, status="no_samples",
                      message="Treść nie ma przykładów — nie mam na czym sprawdzić kandydatów.",
                      reader=reader, hints=hints, best=None)
        result["time"] = round(time.time() - t0, 2)
        return result
    cross = None
    if best:
        passing = [c for c in ranked if c["total"] and c["passed"] == c["total"]]
        cross = _cross_check(p, passing[:5], timeout) if len(passing) > 1 else None
        if cross and cross["disagree"]:
            best["confidence"] = round(best["confidence"] * 0.7, 3)
    # Twój program zaakceptowany na SIO2 dla tego samego zadania: wzorzec, nie zgadywanka
    own = bool(best and best["kind"] == "user" and best.get("same_task"))
    if own:
        best["confidence"] = 0.97
    if best:
        code = best["code"]
        mapping = {}
        weak = [] if own else weak_evidence(best)
        if best["kind"] == "template":
            pretty, mapping = _rename(code, tags, p.get("input_format") or [])
            if mapping:
                chk = run_on_samples(pretty, samples, timeout=timeout)
                if chk["passed"] == chk["total"]:
                    code = pretty
                else:
                    mapping = {}
            code = _decorate(code, p.get("title"), best["name"], best["passed"], best["total"], "szablon",
                             weak=weak_message(best, weak) if weak else None)
        if weak:
            # zgodność z przykładami to za słaby dowód — nie ogłaszamy sukcesu (np. √n „pasujące” do jednego wyniku)
            best["confidence"] = round(min(best["confidence"], 0.3), 3)
            reader, hints = reader_and_hints(p, advice)
            result.update(ok=False, status="weak", message=weak_message(best, weak), weak_reasons=weak,
                          reader=reader, hints=hints,
                          best={"kind": best["kind"], "id": best["id"], "name": best["name"], "code": code,
                                "confidence": best["confidence"], "passed": best["passed"], "total": best["total"],
                                "renamed": mapping, "path": best.get("path"), "weak": True},
                          warning=_warning(best, samples, cross))
        else:
            result.update(ok=True, status="solved",
                          message=(("Twój program dla tego zadania" + (f" ({best['sio2_score']} pkt na SIO2)" if best.get("sio2_score") else "")
                                    + " — przechodzi") if own else "Przechodzi") +
                          f" przykłady {best['passed']}/{best['total']} ✔" + (
                              f" (pominięto {best['skipped']} bardzo duży przykład)" if best.get("skipped") else ""),
                          best={"kind": best["kind"], "id": best["id"], "name": best["name"], "code": code,
                                "confidence": best["confidence"], "passed": best["passed"], "total": best["total"],
                                "renamed": mapping, "path": best.get("path"), "same_task": own},
                          warning=None if own else _warning(best, samples, cross))
        alts = [c for c in ranked[1:] if c["passed"] == c["total"]]
        result["alternatives"] = [{"id": c["id"], "name": c["name"], "kind": c["kind"], "code": c["code"]} for c in alts[:3]]
        result["cross_check"] = cross
    else:
        partial = ranked[0] if ranked and ranked[0]["passed"] > 0 else None
        reader, hints = reader_and_hints(p, advice)
        result.update(ok=False, status="unsolved",
                      message="Nie znalazłem pewnego rozwiązania — żaden kandydat nie przechodzi wszystkich przykładów.",
                      best=None, reader=reader, hints=hints,
                      closest=({"id": partial["id"], "name": partial["name"], "passed": partial["passed"],
                                "total": partial["total"], "code": partial["code"]} if partial else None))
    result["time"] = round(time.time() - t0, 2)
    return result


def _warning(best: dict, samples: list[dict], cross: dict | None) -> str | None:
    msgs = []
    if _trivial_samples(samples):
        msgs.append("Przykład jest bardzo krótki — zgodność mogła wyjść przypadkiem.")
    if best.get("skipped"):
        msgs.append(f"Pominięto {best['skipped']} bardzo duży przykład (np. test „ocen” z ogromnym wejściem) — "
                    "uruchom go sam.")
    if not best.get("sig_hit"):
        msgs.append("Ten kandydat pasuje do przykładów i kształtu wejścia, ale nie do słów w treści — wynik może być "
                    "przypadkowy.")
    if cross and cross["disagree"]:
        msgs.append(f"Inne kandydaty, które też przechodzą przykłady, dają inne wyniki na {cross['disagree']} z "
                    f"{cross['tests']} testów brzegowych — co najmniej jeden z nich jest błędny.")
    if not msgs:
        return None
    return " ".join(msgs) + " Sprawdź rozwiązanie na testach brzegowych!"


def _cross_check(p: dict, passing: list[dict], timeout: float) -> dict | None:
    """Uruchamia kandydatów zgodnych z przykładami na testach brzegowych i porównuje ich wyniki między sobą."""
    try:
        from . import edgecases as EC
        tests = [t["input"] for t in EC.edge_tests(p)][:8]
    except Exception:  # noqa: BLE001
        return None
    if not tests:
        return None
    progs = []
    for c in passing:
        pr = runner.prepare(c["code"], lang=runner.detect_lang(c["code"]) if c["kind"] == "user" else "cpp")
        if pr.ok:
            progs.append((c, pr))
    if len(progs) < 2:
        return None
    disagree = 0
    details = []
    for tin in tests:
        outs = []
        for c, pr in progs:
            rr = runner.run(pr.cmd, tin, timeout=timeout)
            outs.append(rr.stdout if rr.verdict == "OK" else f"<{rr.verdict}>")
        if any(not runner.outputs_equal(outs[0], o) for o in outs[1:]):
            disagree += 1
            details.append({"input": tin[:300], "outputs": {c["name"]: o[:120] for (c, _), o in zip(progs, outs)}})
    return {"tests": len(tests), "disagree": disagree, "candidates": [c["name"] for c, _ in progs], "details": details[:3]}


# ============================================================================================ HTTP
def handle_solve(body: dict) -> dict:
    st = (body.get("statement") or body.get("text") or "").strip()
    if not st:
        raise ApiError("Wklej treść zadania (albo upuść PDF).")
    opts = {"use_user_code": body.get("use_user_code", True) not in (False, "0", "false"),
            "timeout": max(0.2, min(float(body.get("timeout") or 2.0), 10.0))}
    return {"job": jobs.submit(lambda job: solve(st, job, **opts), title="Auto-solver")}


def handle_templates(_q: dict) -> dict:
    out = []
    for t in load_templates():
        out.append({"id": t["id"], "name": t["name"], "chunk": t["chunk"], "algos": t.get("algos"),
                    "shapes": t.get("shapes"), "variant_of": t.get("variant_of"), "has_brute": bool(t.get("brute"))})
    return {"templates": out, "count": len(out)}


def handle_render(body: dict) -> dict:
    tid = body.get("id")
    t = next((x for x in load_templates() if x["id"] == tid), None)
    if not t:
        raise ApiError("Nie ma takiego szablonu.", 404)
    ph = placeholders(ST.parse(body.get("statement") or "")) if body.get("statement") else placeholders({})
    return {"code": render(t, ph, multitest=bool(body.get("multitest"))), "placeholders": ph}


def handle_warmup(_q: dict) -> dict:
    return {"warmup": warmup_status()}


ROUTES = {
    "POST /api/autosolver/solve": handle_solve,
    "GET /api/autosolver/templates": handle_templates,
    "POST /api/autosolver/render": handle_render,
    "GET /api/autosolver/warmup": handle_warmup,
}

# rozgrzewka cache binarek przy pierwszym imporcie (serwer importuje moduł przy starcie)
start_warmup()
