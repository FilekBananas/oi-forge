"""Asystent OI Forge — czatbot nad lokalną bazą wiedzy, bez LLM i bez sieci.

Jak to działa (w skrócie):

1. **Jednostki wyszukiwania** — z każdego artykułu KB powstaje kilkadziesiąt jednostek: sam artykuł
   (tytuł/aliasy/tagi/streszczenie), sekcje `body`, pary QA (wszystkie warianty pytania), wpisy `functions`,
   `pitfalls`, a także snippety. Każda jednostka zna swój artykuł (`aid`) i kotwicę w dokumentacji.
2. **Ranking hybrydowy** — BM25F (`core.search`) z ekspansją zapytania (synonimy + skojarzenia NPMI z KB +
   odmiana + literówki) × priorytety rodzaju jednostki zależne od intencji + mała sieć neuronowa
   (`core.nn.HashedMLP`: haszowane uni/bigramy rdzeni i trigramy znaków → 64 neurony tanh → softmax nad
   artykułami) + podobieństwo trigramowe zapytania do wariantów pytań. Sieć trenuje się w tle przy starcie
   (wagi w `user_data/cache/nn_assistant_*.json`, klucz = podpis KB); do czasu jej gotowości działa sam BM25.
3. **Intencje** (reguły + cechy): jak zrobić / co to / złożoność / kod / porównanie X vs Y / błąd kompilatora /
   wklejony kod / treść zadania.
4. **Składanie odpowiedzi** — bloki (tekst, kod, złożoność, funkcje, pułapki, tabela porównawcza, …),
   źródła z linkami do dokumentacji, „Zobacz też”, sugestie pytań, poziom pewności, kontekst rozmowy.

Trasy: POST /api/assistant/ask, GET /api/assistant/suggest, GET /api/assistant/status,
GET /api/assistant/examples, GET /api/search/global.
"""
from __future__ import annotations

import hashlib
import math
import os
import random
import re
import threading
import time
from collections import defaultdict

from oiforge import config
from oiforge.core import kb
from oiforge.core import search as S
from oiforge.core import text as T
from oiforge.core.nn import HashedMLP, typo_variants, word_dropout
from oiforge.server_api import ApiError

try:  # piaskownica, komendy, „nie rozumiem”, profil (bez AI)
    from . import _assistant_tools as AT
except Exception:  # noqa: BLE001
    AT = None

# ============================================================================================ stałe
KIND_LABELS = {
    "qa": "Pytanie i odpowiedź", "article": "Artykuł", "code": "Przykład kodu", "text": "Sekcja",
    "tip": "Wskazówka", "warn": "Ostrzeżenie", "table": "Tabela", "function": "Funkcja", "pitfall": "Pułapka",
    "snippet": "Snippet",
}
KIND_PRIOR = {"qa": 1.0, "article": 0.95, "code": 0.72, "text": 0.66, "tip": 0.62, "warn": 0.62, "table": 0.6,
              "function": 0.7, "pitfall": 0.55, "snippet": 0.7}
INTENT_KIND = {
    "howto": {"qa": 1.15},
    "define": {"article": 1.35, "text": 1.15, "qa": 1.0},
    "complexity": {"article": 1.2, "function": 1.25, "table": 1.15, "qa": 1.05},
    "code": {"code": 1.45, "snippet": 1.45, "qa": 1.05},
    "compare": {"qa": 1.1, "article": 1.1},
    "error": {"pitfall": 1.3, "warn": 1.2, "qa": 1.05},
}
INTENT_LABELS = {
    "howto": "Jak to zrobić", "define": "Wyjaśnienie pojęcia", "complexity": "Złożoność", "code": "Kod",
    "compare": "Porównanie", "error": "Błąd", "compiler": "Komunikat kompilatora", "code_paste": "Wklejony kod",
    "task": "Treść zadania", "general": "Pytanie", "run": "Uruchomienie kodu", "command": "Komenda",
    "clarify": "Prościej", "tool": "Narzędzie",
}
LEVELS = {1: "podstawowy", 2: "średni", 3: "zaawansowany", 4: "ekspercki", 5: "ekspercki"}
CATEGORY_ORDER = ["C++ język", "STL kontenery", "STL algorytmy", "Wejście/wyjście", "Liczby i typy", "Pułapki",
                  "OI praktyka", "Grafy", "Drzewa", "Struktury danych", "Techniki", "Programowanie dynamiczne",
                  "Teksty", "Matematyka", "Geometria", "Teoria gier", "Narzędzia"]
FIELDS = {"q": 3.4, "title": 3.0, "tags": 2.0, "ctx": 1.25, "body": 1.0, "code": 0.35}
DOC_FIELDS = {"title": 4.0, "aliases": 3.5, "tags": 2.2, "summary": 1.2, "qa": 1.0}

# kara za niepokryte (ważone idf) słowa pytania — patrz retrieve()
ICOV_W = float(os.environ.get("OIFORGE_ICOV", "0.4"))

NN_HIDDEN = 64
NN_EPOCHS = 2.0
NN_WARM_EPOCHS = 1.0
NN_LR = 0.3
NN_CHUNK_CPU = float(os.environ.get("OIFORGE_NN_CHUNK", "6"))
DATA_VERSION = 5                       # zmień, gdy zmienia się sposób generowania danych treningowych
CACHE_DIR = config.CACHE_DIR

# Słowa „czynności” — nie wskazują tematu (do wykrywania pytań uzupełniających).
GENERIC = set(T.terms("""
jak co czy kiedy dlaczego ile gdzie ktory która które usun usunac usuwanie usuwac dodac dodawanie dodaj wstawic
wstawianie wstaw znalezc znajdz szukac szukanie wyszukac sprawdzic sprawdzanie sprawdz policzyc liczyc zliczyc
wypisac wypisywanie wypisz wczytac wczytywanie wczytaj posortowac sortowac odwrocic zamienic zmienic zmiana
uzyc uzywac uzycie zrobic robic zrobienie napisac pisac zaimplementowac implementacja kod kodu przyklad przyklady
przykladem zlozonosc zlozonosci szybko szybciej wolno dziala dzialanie element elementy elementu wartosc wartosci
pierwszy ostatni koniec poczatek rozmiar dlugosc pusty pusta czysc wyczyscic iterowac iteracja petla przejsc
wszystkie kazdy kazdego mozna trzeba lepiej najlepiej dobrze zle blad bledy pomoc help how to what why use a i
""")) | {"vs", "a", "i"}

FOLLOWUP_RX = re.compile(r"^(a|i|oraz|to|no to|a co|a jak|a czy|a w|a dla|a gdy|a kiedy|a ile|a jesli|a jezeli|a skoro|"
                         r"and|what about|how about|a teraz|a gdyby|a do|a z|a na|a po)\b")

# ============================================================================================ jednostki
_HEADING_RX = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$", re.M)


def _plain(md: str) -> str:
    """Markdown → zwykły tekst (do indeksowania i fragmentów)."""
    s = re.sub(r"```.*?```", " ", md or "", flags=re.S)
    s = re.sub(r"[`*_>#|]", " ", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def _first_sentence(s: str, limit: int = 160) -> str:
    s = _plain(s)
    parts = T.sentences(s)
    out = parts[0] if parts else s
    return out if len(out) <= limit else out[:limit].rsplit(" ", 1)[0] + "…"


def short_name(a: dict) -> str:
    """Krótka nazwa tematu artykułu („set”, „Dijkstra”) — do sugestii i kontekstu rozmowy."""
    title = a.get("title") or a.get("id", "")
    title = re.sub(r"^(Dokumentacja|Pułapka)\s*:\s*", "", title)   # „Dokumentacja: <cmath> — …” → „<cmath>”
    main = re.split(r"\s+[—–-]\s+|\s*\(|:\s", title, maxsplit=1)[0].strip()
    main = re.sub(r"^std::", "", main)
    al = [x for x in (a.get("aliases") or []) if isinstance(x, str)]
    if al and len(al[0]) <= 22 and (len(main) > 26 or not main):
        return al[0]
    return main if len(main) <= 34 else (al[0] if al else main[:34])


def _qa_variants(qa: dict) -> list[str]:
    q = qa.get("q")
    if isinstance(q, list):
        return [x for x in q if isinstance(x, str) and x.strip()]
    return [q] if isinstance(q, str) and q.strip() else []


def _method_names(sig: str) -> list[str]:
    return re.findall(r"(?:\.|::|^|\s|/)\s*([A-Za-z_]\w*)\s*\(", sig or "")


def build_units(articles: list[dict], snippets: list[dict]) -> list[dict]:
    """Dzieli bazę wiedzy na jednostki wyszukiwania (patrz docstring modułu)."""
    units: list[dict] = []
    for a in articles:
        aid = a["id"]
        title = a.get("title") or aid
        aliases = [x for x in (a.get("aliases") or []) if isinstance(x, str)]
        tags = [x for x in (a.get("tags") or []) if isinstance(x, str)]
        ctx = " ".join([title] + aliases[:8] + tags[:6])
        base = {"aid": aid, "atitle": title, "category": a.get("category", "Inne")}
        units.append(dict(base, uid=f"a:{aid}", kind="article", title=title, anchor="",
                          f={"q": " ".join(aliases), "title": title, "tags": " ".join(tags + [a.get("category", "")]),
                             "ctx": "", "body": a.get("summary", ""), "code": ""},
                          phrases=[title] + aliases, text=a.get("summary", "")))
        for i, b in enumerate(a.get("body") or []):
            if not isinstance(b, dict):
                continue
            typ = b.get("type", "text")
            anchor = f"b{i}"
            if typ == "code":
                ttl = b.get("title") or "Przykład"
                units.append(dict(base, uid=f"s:{aid}:{i}", kind="code", title=ttl, anchor=anchor,
                                  f={"q": "", "title": ttl, "tags": "", "ctx": ctx, "body": "", "code": b.get("code", "")},
                                  phrases=[ttl], text=b.get("code", ""), code=b.get("code", ""),
                                  lang=b.get("lang", "cpp"), full=("int main" in (b.get("code") or ""))))
            elif typ == "table":
                rows = b.get("rows") or []
                txt = " ".join(b.get("headers") or []) + " " + " ".join(" ".join(map(str, r)) for r in rows)
                units.append(dict(base, uid=f"s:{aid}:{i}", kind="table", title=b.get("title") or "Tabela",
                                  anchor=anchor, f={"q": "", "title": b.get("title", ""), "tags": "", "ctx": ctx,
                                                    "body": _plain(txt), "code": ""},
                                  phrases=[], text=_plain(txt), table={"headers": b.get("headers") or [], "rows": rows}))
            else:
                md = b.get("md", "")
                heads = _HEADING_RX.findall(md)
                ttl = heads[0] if heads else ""
                units.append(dict(base, uid=f"s:{aid}:{i}", kind=typ if typ in ("tip", "warn") else "text",
                                  title=ttl or _first_sentence(md, 80), anchor=anchor,
                                  f={"q": "", "title": ttl, "tags": "", "ctx": ctx, "body": _plain(md), "code": ""},
                                  phrases=[ttl] if ttl else [], text=md))
        for j, qa in enumerate(a.get("qa") or []):
            if not isinstance(qa, dict):
                continue
            vs = _qa_variants(qa)
            if not vs:
                continue
            units.append(dict(base, uid=f"q:{aid}:{j}", kind="qa", title=vs[0], anchor=f"qa-{j}",
                              f={"q": " | ".join(vs), "title": vs[0], "tags": "", "ctx": ctx,
                                 "body": _plain(qa.get("a", "")), "code": qa.get("code", "")},
                              phrases=vs, text=qa.get("a", ""), code=qa.get("code", ""), lang=qa.get("lang", "cpp"),
                              qa_index=j))
        for j, fn in enumerate(a.get("functions") or []):
            if not isinstance(fn, dict) or not fn.get("sig"):
                continue
            sig = fn["sig"]
            names = " ".join(_method_names(sig))
            units.append(dict(base, uid=f"f:{aid}:{j}", kind="function", title=sig, anchor="functions",
                              f={"q": names, "title": sig, "tags": "", "ctx": ctx,
                                 "body": " ".join(str(fn.get(k) or "") for k in ("desc", "returns", "complexity")),
                                 "code": fn.get("example", "")},
                              phrases=[sig, names] if names else [sig], text=fn.get("desc", ""), fn=fn))
        for j, p in enumerate(a.get("pitfalls") or []):
            if not isinstance(p, str):
                continue
            units.append(dict(base, uid=f"p:{aid}:{j}", kind="pitfall", title=_first_sentence(p, 90), anchor="pitfalls",
                              f={"q": "", "title": "", "tags": "", "ctx": ctx, "body": _plain(p), "code": ""},
                              phrases=[], text=p))
    by_id = {a["id"]: a for a in articles}
    for s in snippets:
        trig = s.get("trigger")
        if not trig:
            continue
        s = dict(s, code=_strip_placeholders(s.get("code", "")))
        aid = s.get("kb") if s.get("kb") in by_id else None
        name = s.get("name") or trig
        units.append({"uid": f"n:{trig}", "kind": "snippet", "aid": aid, "atitle": by_id[aid]["title"] if aid else name,
                      "category": by_id[aid].get("category") if aid else "Snippety", "title": name, "anchor": "",
                      "f": {"q": trig + " " + " ".join(s.get("tags") or []), "title": name, "tags": "",
                            "ctx": by_id[aid]["title"] if aid else "",
                            "body": (s.get("description") or "") + " " + (s.get("usage") or ""), "code": s.get("code", "")},
                      "phrases": [name, trig], "text": s.get("description") or "", "code": s.get("code", ""),
                      "lang": s.get("lang", "cpp"), "snippet": s})
    for u in units:
        u["tri"] = [_tri(p) for p in u.get("phrases") or [] if p]
    return units


def _strip_placeholders(code: str) -> str:
    """`${1:nazwa}` → `nazwa`, `$1` → `` (placeholdery tabstop snippetów)."""
    return re.sub(r"\$\d", "", re.sub(r"\$\{\d+:([^}]*)\}", r"\1", code or ""))


def _tri(s: str) -> frozenset:
    """Trigramy znakowe tekstu po normalizacji, usunięciu interpunkcji i sprowadzeniu słów do rdzeni
    („plecak 0/1” ≈ „plecak 0 1”, „lower_bound w secie” ≈ „set lower_bound”)."""
    words = re.sub(r"[^a-z0-9+#_]+", " ", T.normalize(s)).split()
    n = " ".join(S.root(T.stem(w)) for w in words)
    return frozenset(T.char_ngrams(n, 3)) if n else frozenset()


# ============================================================================================ dane treningowe sieci
def training_examples(articles: list[dict], snippets: list[dict], seed: int = 7) -> list[tuple[str, str]]:
    """Pary (tekst, id artykułu) dla sieci: tytuły, aliasy, tagi, warianty pytań, sygnatury funkcji, tytuły
    przykładów, streszczenia, pułapki + augmentacja (literówki, synonimy, pominięte słowa)."""
    rng = random.Random(seed)
    groups = S.synonym_groups()
    syn_index: dict[str, list[int]] = defaultdict(list)
    for gi, g in enumerate(groups):
        for ph in g:
            if len(ph.split()) == 1:
                syn_index[ph].append(gi)
    ex: list[tuple[str, str]] = []
    for a in articles:
        aid = a["id"]
        title = a.get("title") or aid
        sn = short_name(a)
        aliases = [x for x in (a.get("aliases") or []) if isinstance(x, str)]
        ex += [(title, aid), (sn, aid)]
        ex += [(x, aid) for x in aliases]
        ex += [(x, aid) for x in (a.get("tags") or []) if isinstance(x, str)]
        if a.get("summary"):
            ex.append((_first_sentence(a["summary"], 200), aid))
        firsts = []
        for qa in a.get("qa") or []:
            if isinstance(qa, dict):
                vs = _qa_variants(qa)
                ex += [(v, aid) for v in vs]
                if vs:
                    firsts.append(vs[0])
                    d = word_dropout(vs[0], rng)
                    if d:
                        ex.append((d, aid))
        for b in a.get("body") or []:
            if isinstance(b, dict) and b.get("type") == "code" and b.get("title"):
                ex.append((f"{sn} {b['title']}", aid))
        for fn in (a.get("functions") or [])[:12]:
            if isinstance(fn, dict) and fn.get("sig"):
                ex.append((f"{fn['sig']} {fn.get('desc', '')}"[:120], aid))
        for p in (a.get("pitfalls") or [])[:3]:
            if isinstance(p, str):
                ex.append((" ".join(_plain(p).split()[:14]), aid))
        for src in [title, sn] + aliases[:2] + firsts[:3]:
            for v in typo_variants(T.strip_accents(src.lower()), rng, 1):
                ex.append((v, aid))
        # synonimy: podmień jedno słowo na synonim z grupy (uczy sieć słownictwa spoza artykułu)
        for src in aliases[:3] + firsts[:3]:
            words = src.split()
            for wi, w in enumerate(words):
                st = " ".join(T.terms(w, drop_stopwords=False))
                gis = syn_index.get(st)
                if gis:
                    g = groups[rng.choice(gis)]
                    alt = rng.choice(g)
                    ex.append((" ".join(words[:wi] + [alt] + words[wi + 1:]), aid))
                    break
    by_id = {a["id"] for a in articles}
    for s in snippets:
        if s.get("kb") in by_id:
            ex.append((s.get("name") or s["trigger"], s["kb"]))
            ex += [(t, s["kb"]) for t in (s.get("tags") or []) if isinstance(t, str)]
    return ex


# ============================================================================================ stan silnika
class _State:
    """Niezmienna migawka indeksu (podmieniana atomowo przy zmianie KB)."""

    def __init__(self, sig, articles, snippets):
        t0 = time.perf_counter()
        self.sig = sig
        self.articles = {a["id"]: a for a in articles}
        self.order = [a["id"] for a in articles]
        self.units_list = build_units(articles, snippets)
        self.units = {u["uid"]: u for u in self.units_list}
        self.by_article: dict[str, list[dict]] = defaultdict(list)
        for u in self.units_list:
            if u.get("aid"):
                self.by_article[u["aid"]].append(u)
        idx = S.SearchIndex(FIELDS)
        for u in self.units_list:
            idx.add(u["uid"], u["f"], payload=u["kind"])
        self.index = idx.build()
        # indeks artykułów (całe artykuły jako dokumenty) — „temat” zapytania jako sygnał wspierający ranking jednostek
        didx = S.SearchIndex(DOC_FIELDS)
        for a in articles:
            qa = " ".join(v for x in a.get("qa") or [] if isinstance(x, dict) for v in _qa_variants(x))
            didx.add(a["id"], {"title": a.get("title", ""), "aliases": " ".join(x for x in a.get("aliases") or []
                                                                               if isinstance(x, str)),
                               "tags": " ".join(x for x in a.get("tags") or [] if isinstance(x, str)),
                               "summary": a.get("summary", ""), "qa": qa}, payload=None)
        self.doc_index = didx.build()
        # skojarzenia termów: warianty pytań QA + nagłówki artykułów
        ctxs = []
        for u in self.units_list:
            if u["kind"] == "qa":
                ctxs.append(T.terms(u["f"]["q"]))
            elif u["kind"] == "article":
                ctxs.append(T.terms(u["f"]["q"] + " " + u["title"] + " " + u["f"]["tags"]))
        self.graph = S.TermGraph().fit(ctxs)
        # słownictwo tematyczne: termy z tytułów/aliasów/tagów występujące w niewielu artykułach
        art_df: dict[str, int] = defaultdict(int)
        for a in articles:
            for t in set(T.terms(" ".join([a.get("title", "")] + list(a.get("aliases") or []) + list(a.get("tags") or [])))):
                art_df[S.root(t)] += 1
        lim = max(4, int(0.04 * max(1, len(articles))))
        self.topic_terms = {t for t, c in art_df.items() if c <= lim and t not in GENERIC and len(t) >= 2
                            and not t.isdigit()}
        self.phrases = _build_phrases(articles)
        # rdzenie słów tytułu i krótkiej nazwy — „czy pytanie nazywa ten temat”
        self.title_roots = {a["id"]: {S.root(t) for t in T.terms(f"{a.get('title', '')} {short_name(a)}")}
                            for a in articles}
        # znormalizowana „głowa” artykułu (tytuł + aliasy + tagi) — czy artykuł w ogóle mówi o danej czynności
        self.head_norm = {a["id"]: T.normalize(" ".join([a.get("title", "")] + [x for x in (a.get("aliases") or [])
                                                                                if isinstance(x, str)]
                                                         + [x for x in (a.get("tags") or []) if isinstance(x, str)]))
                          for a in articles}
        # rdzenie słów z pytań QA/aliasów/tagów → artykuły (tylko rzadkie: do „reszty” pytania obok obiektu)
        rr: dict[str, set[str]] = defaultdict(set)
        for a in articles:
            txt = " ".join([x for x in (a.get("aliases") or []) if isinstance(x, str)]
                           + [x for x in (a.get("tags") or []) if isinstance(x, str)]
                           + [v for qa in (a.get("qa") or []) if isinstance(qa, dict) for v in _qa_variants(qa)])
            for t in set(T.terms(txt)):
                if len(t) >= 3 and not t.isdigit() and t not in GENERIC:
                    rr[S.root(t)].add(a["id"])
        self.rare_roots = {r: v for r, v in rr.items() if len(v) <= 2}
        self.alias_map: dict[str, set[str]] = defaultdict(set)
        for a in articles:
            for x in [a.get("title", ""), short_name(a)] + [y for y in (a.get("aliases") or []) if isinstance(y, str)]:
                nx = T.normalize(x)
                if nx:
                    self.alias_map[nx].add(a["id"])
        self.build_ms = round(1000 * (time.perf_counter() - t0), 1)


def _build_phrases(articles: list[dict]) -> dict:
    """Frazy do autouzupełniania: tytuły, aliasy, warianty pytań (z wagą)."""
    items = []
    seen = set()
    for a in articles:
        aid = a["id"]
        cands = [(a.get("title", ""), 3.0, "article")] + [(x, 2.2, "alias") for x in (a.get("aliases") or [])[:8]]
        for qa in a.get("qa") or []:
            if isinstance(qa, dict):
                vs = _qa_variants(qa)
                cands += [(v, 2.0 if i == 0 else 1.2, "qa") for i, v in enumerate(vs)]
        for txt, w, kind in cands:
            if not isinstance(txt, str):
                continue
            norm = T.normalize(txt)
            if len(norm) < 2 or norm in seen:
                continue
            seen.add(norm)
            items.append({"text": txt, "norm": norm, "words": norm.split(), "w": w, "aid": aid, "kind": kind})
    word_index: dict[str, set[int]] = defaultdict(set)
    for i, it in enumerate(items):
        for wd in it["words"]:
            word_index[wd].add(i)
    return {"items": items, "word_index": dict(word_index), "vocab": sorted(word_index)}


class Engine:
    """Singleton: indeks jednostek + sieć neuronowa trenowana w tle."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state: _State | None = None
        self._nn: HashedMLP | None = None
        self._nn_key: str | None = None
        self._nn_status = {"state": "off", "message": "Sieć jeszcze nie startowała.", "progress": 0.0}
        self._nn_thread: threading.Thread | None = None
        self._nn_wanted: str | None = None
        self._last_check = 0.0
        self._rebuilding = False

    # ------------------------------------------------------------------ indeks
    @staticmethod
    def _signature() -> tuple:
        snips = kb.snippets()
        return (kb.signature(), len(snips), hash(tuple(s.get("trigger") for s in snips)))

    def state(self, train: bool = True, wait: bool = False) -> _State:
        """Aktualna migawka indeksu. Pierwsza budowa jest synchroniczna; po zmianie KB nowy indeks buduje się
        w tle, a do tego czasu odpowiadamy ze starego (bez blokowania żądań). `wait=True` wymusza świeżość."""
        now = time.monotonic()
        st = self._state
        if st is not None and now - self._last_check < 1.0 and not wait:
            return st
        self._last_check = now
        sig = self._signature()
        if st is None or (wait and st.sig != sig):
            with self._lock:
                st = self._state
                if st is None or st.sig != sig:
                    st = _State(sig, kb.articles(), kb.snippets())
                    self._state = st
        elif st.sig != sig:
            self._rebuild_async(sig)
        if train:
            self._ensure_nn(st)
        return st

    def _rebuild_async(self, sig) -> None:
        if self._rebuilding:
            return
        self._rebuilding = True

        def work():
            try:
                with self._lock:
                    cur = self._signature()
                    if self._state is None or self._state.sig != cur:
                        self._state = _State(cur, kb.articles(), kb.snippets())
                self._ensure_nn(self._state)
            except Exception:  # noqa: BLE001 — zostaje poprzedni indeks
                pass
            finally:
                self._rebuilding = False
        threading.Thread(target=work, name="oiforge-assistant-rebuild", daemon=True).start()

    # ------------------------------------------------------------------ sieć neuronowa
    def nn_key(self, st: _State) -> str:
        h = hashlib.sha1(repr((st.sig[0], DATA_VERSION, NN_HIDDEN, NN_EPOCHS)).encode("utf-8")).hexdigest()
        return h[:16]

    def nn(self) -> HashedMLP | None:
        return self._nn

    def nn_status(self) -> dict:
        d = dict(self._nn_status)
        d["ready"] = self._nn is not None
        d["fresh"] = self._nn is not None and self._state is not None and self._nn_key == self.nn_key(self._state)
        return d

    def _ensure_nn(self, st: _State) -> None:
        if os.environ.get("OIFORGE_NO_NN"):
            return
        key = self.nn_key(st)
        if self._nn_key == key and self._nn_status.get("state") == "ready":
            return
        self._nn_wanted = key
        if self._nn_thread is None or not self._nn_thread.is_alive():
            self._nn_thread = threading.Thread(target=self._nn_worker, name="oiforge-nn", daemon=True)
            self._nn_thread.start()

    def wait_nn(self, timeout: float = 120.0, fresh: bool = True, complete: bool = True) -> bool:
        """Czeka na sieć (używane w testach): `fresh` — wytrenowaną na aktualnej KB, `complete` — do końca
        harmonogramu (a nie tylko po pierwszym kawałku treningu). Zwraca True, gdy warunek spełniony."""
        t_end = time.monotonic() + timeout
        self.state()
        while time.monotonic() < t_end:
            s = self.nn_status()
            if s["ready"] and (not fresh or s["fresh"]) and (not complete or s.get("state") == "ready"):
                return True
            time.sleep(0.2)
        return False

    def _cache_path(self, key: str):
        return CACHE_DIR / f"nn_assistant_{key}.json"

    def _nn_worker(self) -> None:
        while True:
            key = self._nn_wanted
            st = self._state
            if not key or st is None or self.nn_key(st) != key:
                return
            try:
                self._train_for(st, key)
            except Exception as e:  # noqa: BLE001 — sieć jest opcjonalna, asystent działa bez niej
                self._nn_status = {"state": "error", "message": f"Błąd treningu sieci: {e}", "progress": 0.0}
                return
            if self._nn_wanted == key:
                return

    def _train_for(self, st: _State, key: str) -> None:
        path = self._cache_path(key)
        model = HashedMLP.load(path, key=key)
        labels = list(st.order)
        if model is not None and model.labels == labels and model.meta.get("complete"):
            self._nn, self._nn_key = model, key
            self._nn_status = {"state": "ready", "message": "Sieć wczytana z pamięci podręcznej.", "progress": 1.0,
                               "meta": model.meta.get("train")}
            return
        examples = training_examples(list(st.articles.values()), [u["snippet"] for u in st.units_list
                                                                   if u["kind"] == "snippet"])
        epochs = NN_EPOCHS
        if model is None or model.labels != labels:
            model = HashedMLP(labels, hidden=NN_HIDDEN)
            prev = self._latest_cache(exclude=path)
            if prev is not None:
                n = model.warm_start(prev)
                if n:
                    epochs = NN_WARM_EPOCHS
                    model.meta["warm_from"] = n
        model.meta["target_epochs"] = epochs
        epochs = model.meta.get("target_epochs", epochs)
        self._nn_status = {"state": "training", "message": "Trenuję sieć neuronową…", "progress": 0.0}
        while True:
            def stop():
                return self._nn_wanted != key

            def prog(p, msg):
                self._nn_status = {"state": "training", "message": msg, "progress": round(p, 3)}

            stats = model.fit(examples, epochs=epochs, lr=NN_LR, max_seconds=NN_CHUNK_CPU, should_stop=stop,
                              progress=prog)
            if stop():
                return
            model.meta["complete"] = bool(stats["done"])
            model.meta["examples_total"] = len(examples)
            try:
                model.save(path, key=key)
                self._cleanup_cache(keep=path)
            except OSError:
                pass
            # Model używalny po każdym kawałku treningu (nawet niedouczony jest lepszy niż żaden).
            self._nn, self._nn_key = model, key
            if stats["done"]:
                self._nn_status = {"state": "ready", "message": "Sieć wytrenowana.", "progress": 1.0, "meta": stats}
                return
            self._nn_status = {"state": "training", "progress": round(stats["step"] / max(1, stats["planned"]), 3),
                               "message": f"Douczanie sieci: epoka {stats['epochs']}/{epochs:g}"}
            time.sleep(0.05)

    def _latest_cache(self, exclude=None) -> HashedMLP | None:
        try:
            files = sorted(CACHE_DIR.glob("nn_assistant_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return None
        for p in files:
            if exclude is not None and p == exclude:
                continue
            m = HashedMLP.load(p)
            if m is not None and m.hidden == NN_HIDDEN:
                return m
        return None

    def _cleanup_cache(self, keep, max_files: int = 3) -> None:
        files = sorted(CACHE_DIR.glob("nn_assistant_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[max_files:]:
            if p != keep:
                try:
                    p.unlink()
                except OSError:
                    pass

    def nn_probs(self, text_: str, st: _State) -> dict[str, float]:
        m = self._nn
        if m is None or not text_.strip():
            return {}
        p = m.predict_proba(text_)
        return {k: v for k, v in p.items() if k in st.articles}


_ENGINE = Engine()


def engine() -> Engine:
    return _ENGINE


# ============================================================================================ intencje
_RX_DEFINE = re.compile(r"^(co to( jest| sa| znaczy| oznacza)?|czym (jest|sa)|co oznacza|co znaczy|what is|what's|whats|"
                        r"definicja|wyjasnij|wytlumacz|objasnij|o co chodzi|jak dziala|na czym polega|opowiedz o|"
                        r"explain)\b|\b(co to( jest)?|czym jest|definicja|to co)\s*\??$")
_RX_COMPLEX = re.compile(r"\b(zlozonosc\w*|zlozonos\w*|complexity|big ?o|notacj\w* o|jak szybk\w*|ile (trwa|kosztuje|zajmuje)|"
                         r"w jakim czasie|czas dzialania|koszt\w* operacj\w*)\b|\bo\s*\(\s*(n|1|log|m|v|e)")
_RX_CODE = re.compile(r"\b(kod|kodu|kodzik|code|implementacj\w*|implementation|implement|zaimplementuj\w*|zaimplementowac|"
                      r"przyklad\w*|example|szablon\w*|template|napisz|pokaz|gotowiec|gotowy|wzorzec|snippet\w*)\b")
_RX_COMPARE = re.compile(r"\b(vs\.?|versus|kontra|roznic\w*|porown\w*|lepsz\w*|szybsz\w*|co wybrac|czy lepiej|zamiast)\b")
_RX_HOWTO = re.compile(r"^(jak|how|w jaki sposob|sposob na|czy (da sie|mozna)|co zrobic|czego uzyc|kiedy uzywac|"
                       r"jakiej? funkcji|jaka funkcja)\b")
_RX_ERRORKW = re.compile(r"\b(blad\w*|error\w*|segfault|sigsegv|sigabrt|sigfpe|crash\w*|wywal\w*|nie dziala|"
                         r"nie kompiluje|exception|wyjatek|runtime error|core dumped|undefined reference|"
                         r"segmentation fault|naruszenie ochrony)\b")
_RX_COMPILER = re.compile(r"(^|\n)\s*[^\s:]*:\d+:(\d+:)?\s*(fatal\s+)?(error|warning|note|blad|ostrzezenie)\b"
                          r"|\berror:\s|undefined reference to|collect2:|ld: |terminate called after|AddressSanitizer|"
                          r"runtime error:|was not declared in this scope|expected .{1,20} before|no matching function|"
                          r"Segmentation fault \(core dumped\)|In function ['‘]", re.I)
_RX_TASK = re.compile(r"\b(mam zadani\w*|dostalem zadani\w*|tresc zadania|jak (to )?rozwiazac(?! (uklad|rownani|rekurencj|problem \w+ ?w c))(?=\s*(to|ten|te|tego|zadani\w*|\?|$))|jaki algorytm\w*|"
                      r"jakiego algorytmu|zadanie:|wejscie\b.*\bwyjscie|input\b.*\boutput)", re.S)
_RX_CONSTRAINT = re.compile(r"\b([nmkq])\s*(<=|≤|<|=<)\s*(\d[\d\s·*^.,e+]*)", re.I)


def looks_like_code(s: str) -> bool:
    lines = [l for l in (s or "").splitlines() if l.strip()]
    if re.search(r"#include\s*<|int\s+main\s*\(|using\s+namespace\s+std|^\s*def\s+\w+\(.*\):", s or "", re.M):
        return True
    if len(lines) < 3:
        return False
    codey = sum(1 for l in lines if re.search(r"[;{}]\s*$|^\s*(for|while|if|return|cout|cin|int|long|auto|vector)\b", l))
    return codey >= max(2, int(0.5 * len(lines)))


def detect_intent(q: str) -> dict:
    """Rozpoznaje intencję pytania (reguły + cechy). Zwraca {id, label, score, flags}."""
    raw = q or ""
    n = T.normalize(raw)
    flags = {"error_kw": bool(_RX_ERRORKW.search(n)), "multiline": "\n" in raw.strip()}
    if _RX_COMPILER.search(raw) and (flags["multiline"] or len(raw) > 40 or "error" in n):
        return {"id": "compiler", "label": INTENT_LABELS["compiler"], "score": 4.0, "flags": flags}
    if looks_like_code(raw):
        return {"id": "code_paste", "label": INTENT_LABELS["code_paste"], "score": 4.0, "flags": flags}
    if (len(raw) > 350 and not looks_like_code(raw)) or _RX_TASK.search(n) or \
            (_RX_CONSTRAINT.search(raw) and len(n.split()) > 6):
        return {"id": "task", "label": INTENT_LABELS["task"], "score": 3.0, "flags": flags}
    scores = {"howto": 0.5}
    if _RX_DEFINE.search(n):
        scores["define"] = 2.2
    if _RX_COMPLEX.search(n):
        scores["complexity"] = 2.4
    if _RX_CODE.search(n):
        scores["code"] = 2.0
    if _RX_COMPARE.search(n):
        scores["compare"] = 2.6
    if _RX_HOWTO.search(n):
        scores["howto"] = 1.6
    if flags["error_kw"]:
        scores["error"] = 1.4
    if re.search(r"\bjak (dziala|to dziala)\b", n):
        scores["define"] = max(scores.get("define", 0), 2.3)
    best = max(scores.items(), key=lambda kv: kv[1])
    return {"id": best[0], "label": INTENT_LABELS[best[0]], "score": best[1], "flags": flags,
            "scores": scores}


_STRIP_INTENT = re.compile(r"^(co to( jest| sa| znaczy)?|czym (jest|sa)|jak dziala|na czym polega|what is|wyjasnij|"
                           r"wytlumacz|definicja|pokaz|napisz|daj|podaj)\s+|\s+(co to( jest)?|definicja)\s*$")


_STRIP_LANG = re.compile(r"\s*\b(w |dla |na )?(c\+\+(1[147]|20|23)?|cpp|stl)(?=\s|$)")


def _topic_query(q: str) -> str:
    """Zapytanie bez słów intencji („co to”, „pokaż”) i bez „w c++” — do rozpoznawania tematu."""
    n = T.normalize(q)
    t = _STRIP_INTENT.sub(" ", n).strip() or n
    return _STRIP_LANG.sub(" ", t).strip() or t


# ============================================================================================ czynność + obiekt
# Pytania „jak zrobić X z Y” (posortować vector par, odwrócić tablicę, usunąć duplikaty z wektora): czynność X
# wskazuje artykuł o ALGORYTMIE (std::sort, reverse, unique), a obiekt Y tylko typ danych. Bez tej wiedzy BM25 często
# wybierał artykuł o obiekcie (std::pair), bo jego nazwa pasuje do większej liczby słów pytania.
# Rodzaj czynności: "algo" — funkcja z <algorithm> działająca na dowolnym ciągu (wygrywa artykuł czynności),
# "method" — metoda kontenera (set.erase, map.find): dla kontenerów asocjacyjnych wygrywa artykuł o obiekcie.
ACTIONS = [
    ("sort", "algo", r"\b(posort\w*|sortow\w*|sortuj\w*|sort|sorted|uporzadkow\w*|ustaw\w* (je |ich )?(wedlug|po|rosnaco|"
                     r"malejaco|w kolejnosci)|uloz\w* (je |ich )?(wedlug|po|rosnaco|malejaco|w kolejnosci|od)|rosnaco|"
                     r"malejaco|od najwieksz\w*|od najmniejsz\w*)\b",
     {"stl-sort": None, "pit-comparator": None}),
    ("reverse", "algo", r"\b(odwroc\w*|odwrac\w*|odwrotn\w* kolejnosc\w*|reverse|od tylu|wspak)\b",
     {"stl-reverse-rotate": None, "str-basic-reverse": {"string"}}),
    ("rotate", "algo", r"\b(cykliczn\w*|rotate|rotacj\w*)\b", {"stl-reverse-rotate": None}),
    ("dedupe", "algo", r"\b(duplikat\w*|powtorzen\w*|powtarzajac\w*|unikaln\w*|unique|bez powtorzen)\b",
     {"stl-unique": {None, "vector", "tablica", "string"}}),
    ("remove", "method", r"\b(usun\w*|usuwa\w*|wyrzuc\w*|skasow\w*|erase|remove|wymaz\w*)\b",
     {"stl-remove-erase": {"vector", "tablica", "string"}}),
    ("insert", "method", r"\b(wstaw\w*|dodaj\w*|dodac|dodawan\w*|insert|push_back|dopisa\w*)\b", {}),
    ("find", "method", r"\b(znalez\w*|znajd\w*|szuka\w*|wyszuka\w*|find|zawiera\w*|czy (jest|wystepuje) w)\b",
     {"stl-count-find": {"vector", "tablica"}}),
    ("count", "algo", r"\b(zlicz\w*|ile razy|wystapien\w*|czestosc\w*|count)\b",
     {"stl-count-find": {"vector", "tablica"}}),
    ("sum", "algo", r"\b(sum[ay] (elementow|wszystkich|liczb) (w |z )?(vector|wektor|tablic)\w*|zsumowa\w*|"
                    r"accumulate)\b", {"stl-accumulate": None}),
    ("minmax", "algo", r"\b(max(imum)? element\w*|min(imum)? element\w*|max_element|min_element|najwieksz\w* element\w*|"
                       r"najmniejsz\w* element\w*)\b", {"stl-min-max": {None, "vector", "tablica"}}),
    ("fill", "algo", r"\b(wypelni\w*|wyzerow\w*|zerowan\w*|fill|memset)\b", {"stl-iota-fill": None, "stl-memset": None}),
    ("shuffle", "algo", r"\b(przetasow\w*|potasow\w*|shuffle|wymiesza\w*)\b", {"stl-shuffle-random": None}),
    ("merge", "algo", r"\b(scal\w*|merge|polacz\w* (dwa|dwie) posortowan\w*)\b", {"stl-merge": None}),
]
OBJECTS = [  # (id, wyrażenie, artykuł o obiekcie)
    ("multiset", r"\b(multiset\w*|multisecie)\b", "stl-multiset"),
    ("umap", r"\b(unordered_map\w*|unordered map\w*|hashmap\w*)\b", "stl-unordered-map"),
    ("uset", r"\b(unordered_set\w*|unordered set\w*)\b", "stl-unordered-set"),
    ("pq", r"\b(priority_queue|priority queue|kolejk\w* priorytetow\w*|kopiec|kopcu|kopca)\b", "stl-priority-queue"),
    ("set", r"\b(set|seta|secie|setu|sety|setem|setach|zbior|zbiorze|zbioru)\b", "stl-set"),
    ("map", r"\b(map|mapa|mapie|mapy|mape|mapa|mapach|slownik\w*)\b", "stl-map"),
    ("vector2d", r"\b(2d|dwuwymiarow\w*|vector vectorow|tablic\w* 2d)\b", "stl-vector-2d"),
    ("pair", r"\b(para|pary|par|parami|parach|pare|pair\w*)\b", "stl-pair"),
    ("string", r"\b(string\w*|napis\w*|slowo|slowa|wyraz\w*|tekst\w*|znak\w*|liter\w*)\b", "stl-string"),
    ("vector", r"\b(vector\w*|wektor\w*|vektor\w*)\b", "stl-vector"),
    ("tablica", r"\b(tablic\w*|array\w*|ciag\w*|liczb)\b", None),
    ("struct", r"\b(struct\w*|struktur\w*)\b", "cpp-struct"),
    ("deque", r"\b(deque\w*)\b", "stl-deque"),
    ("queue", r"\b(queue|kolejk\w*)\b", "stl-queue"),
    ("stack", r"\b(stack|stos\w*)\b", "stl-stack"),
]
_ACTION_RX = [(aid, kind, re.compile(rx), arts) for aid, kind, rx, arts in ACTIONS]
_OBJECT_RX = [(oid, re.compile(rx), art) for oid, rx, art in OBJECTS]
OWN_ORDER = {"set", "multiset", "map", "pq", "umap", "uset"}     # kontenery z własnym porządkiem/metodami
ACT_BOOST, OBJ_DAMP, OBJ_BOOST = 1.22, 0.86, 1.15
UNKNOWN_CONF = 0.22            # poniżej tej pewności odpowiadamy szczerze „nie wiem”
NON_ACTION_DAMP = 0.92          # artykuł, w którego nazwie/aliasach/tagach nie ma tej czynności
RESIDUAL_BOOST = 1.12           # artykuł nazwany przez resztę pytania (algorytm obok kontenera)
OBJ_SOFT_BOOST = 1.06           # pytanie tylko o obiekt („jak zrobić vector 2d”)


def action_object(q: str) -> dict:
    """Rozpoznaje czynność i obiekt pytania; zwraca {action, kind, object, factors: {aid: mnożnik}}.
    „jak posortować vector par po drugim” → sort/pair: std::sort ×1.22, std::pair ×0.86."""
    n = T.normalize(q)
    act = next(((aid, kind, arts) for aid, kind, rx, arts in _ACTION_RX if rx.search(n)), None)
    objs = [(oid, art) for oid, rx, art in _OBJECT_RX if rx.search(n)]
    # „multiset” to nie „set”, „unordered_map” to nie „map”, „kolejka priorytetowa” to nie „kolejka”
    ids = {o for o, _ in objs}
    if "multiset" in ids or "uset" in ids:
        objs = [x for x in objs if x[0] != "set"]
    if "umap" in ids:
        objs = [x for x in objs if x[0] != "map"]
    if "pq" in ids:
        objs = [x for x in objs if x[0] != "queue"]
    if "vector2d" in ids:
        objs = [x for x in objs if x[0] not in ("vector", "tablica")]
    obj = objs[0] if objs else None
    rest = n
    for _o, rx, _a in _OBJECT_RX:
        rest = rx.sub(" ", rest)
    for _a, _k, rx, _x in _ACTION_RX:
        rest = rx.sub(" ", rest)
    out = {"action": act[0] if act else None, "kind": act[1] if act else None,
           "object": obj[0] if obj else None, "objects": [o for o, _ in objs], "factors": {},
           "object_articles": [a for _, a in objs if a],
           "rx": next((rx for aid, kind, rx, arts in _ACTION_RX if act and aid == act[0]), None),
           "residual": [t for t in T.terms(rest) if t not in GENERIC and not t.isdigit() and len(t) >= 3]}
    if not act:
        return out
    aid, kind, arts = act
    f = out["factors"]
    oid = obj[0] if obj else None
    own = oid in OWN_ORDER
    for art, allowed in arts.items():
        if allowed is None or oid in allowed:
            if not (own and kind == "method"):
                f[art] = ACT_BOOST
    if own and obj[1]:
        # set/map/kolejka mają własne metody i własny porządek (set z komparatorem, a nie std::sort)
        f[obj[1]] = OBJ_BOOST
        for art in arts:
            f.pop(art, None)
    elif kind == "algo" and f:
        for o, art in objs:
            if art and art not in f:
                f[art] = OBJ_DAMP
    return out


# ============================================================================================ wyszukiwanie
def _kind_boost(intent: str, topic_aid: str | None, topic_factor: float):
    ik = INTENT_KIND.get(intent, {})

    def boost(did, kind):
        b = KIND_PRIOR.get(kind, 0.6) * ik.get(kind, 1.0)
        if topic_aid and _uid_article(did) == topic_aid:
            b *= topic_factor
        return b
    return boost


def _uid_article(uid: str) -> str | None:
    """`q:stl-set:3` → `stl-set` (snippety `n:…` nie mają artykułu w identyfikatorze)."""
    parts = uid.split(":")
    return parts[1] if len(parts) >= 2 and parts[0] != "n" else None


def retrieve(st: _State, query: str, intent: str = "howto", topic_aid: str | None = None,
             topic_factor: float = 1.0, k: int = 60, use_nn: bool = True, nn_text: str | None = None,
             art_factor: dict | None = None) -> dict:
    """Hybrydowy ranking jednostek. Zwraca {units: [(fused, unit, hit)], articles: [(score, aid)], nn: {...}}.
    `art_factor` — mnożniki dla artykułów (np. z `action_object`: czynność ważniejsza od obiektu)."""
    eng = _ENGINE
    act_rx = None
    if art_factor is None and intent in ("howto", "code", "general", "error"):
        ao = action_object(query)
        art_factor = dict(ao["factors"])
        if ao["kind"] == "algo" and art_factor:
            act_rx = ao["rx"]            # artykuły, które nie mówią o tej czynności, lekko w dół
        elif not ao["action"] and ao["object_articles"] and ao["residual"]:
            # „dijkstra z kolejką priorytetową”: obiekt (kontener) jest tylko narzędziem — temat to algorytm
            # słowa, które artykuł o obiekcie sam „zna” (set.lower_bound), nie wskazują innego tematu
            own_terms = set()
            for oa in ao["object_articles"]:
                if oa in st.articles:
                    art = st.articles[oa]
                    own_terms |= {S.root(t) for t in T.terms(st.head_norm.get(oa, "") + " " + " ".join(
                        str(fn.get("sig", "")) for fn in (art.get("functions") or []) if isinstance(fn, dict)))}
            res_roots = {S.root(t) for t in ao["residual"]} - own_terms
            owners = set()
            for r in res_roots:
                if r in st.topic_terms:          # nazwa tematu z tytułu („dijkstra”)
                    owners |= {a for a, roots in st.title_roots.items() if r in roots}
                else:                            # rzadkie słowo z pytań QA („wysp” → BFS na siatce)
                    own = st.rare_roots.get(r, set())
                    if 0 < len(own) <= 2 and not own & set(ao["object_articles"]):
                        owners |= own
            owners -= set(ao["object_articles"])
            if 0 < len(owners) <= 3:
                for a in owners:
                    art_factor[a] = RESIDUAL_BOOST
                for a in ao["object_articles"]:
                    art_factor[a] = OBJ_DAMP
            elif not owners and intent == "howto":
                # „jak zrobić vector 2d” — pytanie o sam obiekt: jego artykuł lekko w górę
                art_factor[ao["object_articles"][0]] = OBJ_SOFT_BOOST
        elif not ao["action"] and ao["object_articles"] and intent == "howto":
            art_factor[ao["object_articles"][0]] = OBJ_SOFT_BOOST
    art_factor = {a: f for a, f in (art_factor or {}).items() if a in st.articles}
    qterms = T.terms(query)
    present = [t for t in qterms if st.index.has_term(t)]
    extra = st.graph.expand(present, weight=0.22, k=3) if present else []
    hits = st.index.search(query, k=max(k, 80), boost=_kind_boost(intent, topic_aid, topic_factor), extra_terms=extra)
    nn = eng.nn_probs(nn_text or query, st) if use_nn else {}
    pmax = max(nn.values()) if nn else 0.0
    dhits = st.doc_index.search(nn_text or query, k=40)
    dmax = dhits[0]["score"] if dhits else 1.0
    doc_rel = {h["id"]: h["score"] / dmax for h in dhits}
    smax = hits[0]["score"] if hits else 1.0
    qtri = _tri(_topic_query(query))
    qtri_full = _tri(query)
    # słowa pytania + ich synonimy (z informacją, które słowo pytania „pokrywają”) — do pokrycia tytułu
    qset = set(T.terms(_topic_query(query)))
    qexp = [(S.root(t), set(o) or {t}) for t, w, o in S._expand(_topic_query(query)) if w >= 0.6]
    # „przegląd tematu”: pytanie zawiera tylko temat + ogólne słowa („jak użyć seta”, „set cpp”, „kolejka”)
    overview = intent in ("howto", "general") and 0 < len(qterms) <= 3 and \
        all(t in GENERIC or t in ("cpp", "c", "std", "stl", "tutorial", "podstaw", "przyklad", "uzyc", "uzyw", "uzyci")
            or S.root(t) in st.topic_terms for t in qterms) and any(S.root(t) in st.topic_terms for t in qterms)
    fused: dict[str, tuple[float, dict, dict]] = {}
    tcache: dict = {}
    # ważone idf pokrycie słów pytania: jednostka, która pomija rzadkie, kluczowe słowo („wysp”), traci
    qw = st.index.query_term_weights(query)
    qw_tot = sum(qw.values())

    ik = INTENT_KIND.get(intent, {})

    def score_unit(u, h):
        sim = max((max(S.jaccard(qtri, t), S.jaccard(qtri_full, t)) for t in u["tri"]), default=0.0)
        bm = (h["score"] / smax) if h else 0.0
        # względne p sieci, ale tłumione, gdy sieć sama nie jest pewna (pmax < 0.6)
        p_rel = (nn.get(u.get("aid"), 0.0) / max(pmax, 0.6)) if pmax > 0 else 0.0
        # prawie identyczne sformułowanie (np. wariant pytania QA) wygrywa niezależnie od długości pola
        exact = max(0.0, (sim - 0.5) / 0.5) ** 1.5 * KIND_PRIOR.get(u["kind"], 0.6) * ik.get(u["kind"], 1.0)
        d_rel = doc_rel.get(u.get("aid"), 0.0)
        tcov = _title_cover(qset, qexp, st.title_roots.get(u.get("aid")), tcache, u.get("aid"))
        if nn:
            f = 0.44 * bm + 0.14 * d_rel + 0.16 * p_rel * min(1.0, 0.3 + bm) + 0.14 * sim + 0.34 * exact + 0.18 * tcov
        else:
            f = 0.54 * bm + 0.18 * d_rel + 0.18 * sim + 0.34 * exact + 0.18 * tcov
        if overview and u.get("aid") and u["kind"] in ("article", "qa") and u.get("qa_index", 0) == 0:
            f *= 1.18
        if topic_aid and u.get("aid") == topic_aid:
            f *= min(1.25, topic_factor)
        if art_factor and u.get("aid") in art_factor:
            f *= art_factor[u["aid"]]
        elif act_rx is not None and u.get("aid") and not act_rx.search(st.head_norm.get(u["aid"], "")) \
                and not (u["kind"] == "qa" and act_rx.search(T.normalize(u["f"]["q"]))):
            f *= NON_ACTION_DAMP
        if qw_tot > 0 and len(qw) >= 2:
            cov = set(h.get("covered") or ()) if h else set()
            icov = sum(w for t, w in qw.items() if t in cov) / qw_tot if h else max(0.0, sim)
            f *= 1.0 - ICOV_W * (1.0 - icov)
        return f, sim, p_rel

    def info(h, sim, p_rel, u):
        return dict(h, sim=round(sim, 3), nn=round(p_rel, 3), doc=round(doc_rel.get(u.get("aid"), 0.0), 3))

    for h in hits:
        u = st.units[h["id"]]
        f, sim, p_rel = score_unit(u, h)
        fused[u["uid"]] = (f, u, info(h, sim, p_rel, u))
    # sieć może „podpowiedzieć” artykuł, którego BM25 nie znalazł (inne słownictwo)
    if nn:
        top_nn = sorted(nn.items(), key=lambda kv: -kv[1])[:3]
        for aid, p in top_nn:
            if p < 0.12:
                continue
            for u in st.by_article.get(aid, []):
                if u["kind"] in ("article", "qa") and u["uid"] not in fused:
                    f, sim, p_rel = score_unit(u, None)
                    if u["kind"] == "article" or sim > 0.25:
                        fused[u["uid"]] = (f * KIND_PRIOR.get(u["kind"], 0.6), u,
                                           {"id": u["uid"], "score": 0.0, "matched": [], "cover": 0.0,
                                            "sim": round(sim, 3), "nn": round(p_rel, 3)})
    ranked = sorted(fused.values(), key=lambda x: -x[0])[:k]
    agg: dict[str, list[float]] = defaultdict(list)
    for f, u, _ in ranked:
        if u.get("aid"):
            agg[u["aid"]].append(f)
    # temat = artykuł z najlepszą jednostką; kolejne jednostki tego samego artykułu dają tylko małą premię
    # (inaczej popularny artykuł wygrywa samą liczbą częściowo pasujących pytań — „mapie” → std::map)
    arts = sorted(((v[0] + 0.06 * (v[1] if len(v) > 1 else 0.0)
                    + 0.012 * sum(min(1.0, x / v[0]) ** 2 for x in v[1:4]) if v[0] > 0 else 0.0, aid)
                   for aid, v in agg.items()), reverse=True)
    return {"units": ranked, "articles": arts, "nn": nn, "hits": len(hits), "extra": extra, "overview": overview}


def _title_cover(qset: set, qexp: list, troots: set | None, cache: dict, aid) -> float:
    """Jaka część słów pytania (bezpośrednio albo przez synonim) występuje w tytule/krótkiej nazwie artykułu.
    Porównanie po rdzeniach z tolerancją prefiksu („modul” ≈ „modular”); „największy wspólny dzielnik” pokrywa
    tytuł z „NWD”, bo są w jednej grupie synonimów."""
    if aid in cache:
        return cache[aid]
    if not troots or not qset:
        cache[aid] = 0.0
        return 0.0
    covered: set = set()
    for r, origin in qexp:
        if r in troots or (len(r) >= 4 and any(len(x) >= 4 and (x.startswith(r) or r.startswith(x)) for x in troots)):
            covered |= origin
    cache[aid] = len(covered & qset) / len(qset)
    return cache[aid]


def _confidence(res: dict, qterms: list[str]) -> float:
    units, arts = res["units"], res["articles"]
    if not units or not arts:
        return 0.0
    f1, u1, h1 = units[0]
    cover = h1.get("cover", 0.0)
    sim = h1.get("sim", 0.0)
    a1 = arts[0][0]
    a2 = arts[1][0] if len(arts) > 1 else 0.0
    margin = (a1 - a2) / a1 if a1 > 0 else 0.0
    p = res["nn"].get(u1.get("aid"), 0.0) if res["nn"] else None
    # ręcznie skalibrowana regresja logistyczna: pokrycie słów, podobieństwo frazy, przewaga nad 2. tematem, sieć
    z = -2.3 + 2.4 * cover + 1.6 * sim + 1.1 * margin
    if p is not None:
        z += 1.4 * min(1.0, p * 2.0) - 0.3
    if not qterms:
        z -= 1.0
    if h1.get("score", 0) == 0:
        z -= 0.8
    return 1.0 / (1.0 + math.exp(-z))


# ============================================================================================ składanie odpowiedzi
def _cap(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    s = s[0].upper() + s[1:]
    if re.match(r"^(jak|co|czy|ile|dlaczego|kiedy|gdzie|który|która|które|jaki|jaka|jakie|czym|po co|w jaki)\b", s, re.I) \
            and not s.endswith("?"):
        s += "?"
    return s


def _relevant(items: list, qterms: set, key=lambda x: x, k: int = 3) -> list:
    scored = []
    for i, it in enumerate(items):
        ts = set(T.terms(_plain(key(it))))
        scored.append((len(ts & qterms), -i, it))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [it for _, _, it in scored[:k]]


def _source(u: dict, h: dict | None, qterms: list[str]) -> dict:
    snippet = S.highlight(_plain(u.get("text") or u["f"].get("body", "")) or u["title"], (h or {}).get("matched") or qterms, 180)
    return {"id": u.get("aid"), "uid": u["uid"], "kind": u["kind"], "kind_label": KIND_LABELS.get(u["kind"], u["kind"]),
            "title": u["title"] if u["kind"] != "article" else u["atitle"], "article": u.get("atitle"),
            "category": u.get("category"), "anchor": u.get("anchor") or "", "snippet": snippet,
            "score": round((h or {}).get("score", 0.0), 3)}


def _article_link(a: dict) -> dict:
    return {"id": a["id"], "title": a.get("title"), "category": a.get("category"), "summary": _first_sentence(a.get("summary", ""), 140),
            "level": a.get("level")}


def _suggestions(st: _State, a: dict, used_uid: str | None, intent: str, qterms: set, k: int = 5,
                 q: str = "") -> list[str]:
    """3–5 pytań uzupełniających: sąsiednie QA z artykułu, kod/złożoność tematu, powiązany temat."""
    out: list[str] = []
    sn = short_name(a)
    qtri = _tri(q)
    # bez pytań niemal identycznych z zadanym
    qa_units = [u for u in st.by_article.get(a["id"], []) if u["kind"] == "qa" and u["uid"] != used_uid
                and max((S.jaccard(qtri, t) for t in u["tri"]), default=0.0) < 0.7]
    # najpierw pytania „obok” bieżącego (inne słowa niż w pytaniu), w kolejności z artykułu
    qa_units.sort(key=lambda u: (len(set(T.terms(u["title"])) & qterms) > 1, u.get("qa_index", 0)))
    for u in qa_units[:3]:
        out.append(_cap(u["title"]))
    if intent != "code" and any(u["kind"] in ("code", "snippet") for u in st.by_article.get(a["id"], [])):
        out.append(f"{sn} — pokaż kod")
    if intent != "complexity" and a.get("complexity"):
        out.append(f"Złożoność {sn}")
    for rid in a.get("related") or []:
        r = st.articles.get(rid)
        if r and len(out) < k + 1:
            rs = short_name(r)
            if intent != "compare" and r.get("category") == a.get("category") and len(out) < k:
                out.append(f"{sn} vs {rs}")
            else:
                out.append(f"Co to {rs}?")
            break
    seen, res = set(), []
    for s in out:
        key = T.normalize(s)
        if key not in seen:
            seen.add(key)
            res.append(s)
    return res[:k]


def _pitfalls_block(a: dict, qterms: set, k: int = 3) -> dict | None:
    pits = [p for p in a.get("pitfalls") or [] if isinstance(p, str)]
    if not pits:
        return None
    return {"type": "pitfalls", "items": _relevant(pits, qterms, k=k)}


def _functions_for(st: _State, a: dict, res_units: list, qterms: set, k: int = 5) -> list[dict]:
    fns = [f for f in a.get("functions") or [] if isinstance(f, dict) and f.get("sig")]
    if not fns:
        return []
    matched_idx = []
    for f, u, h in res_units:
        if u["kind"] == "function" and u["aid"] == a["id"] and h.get("score", 0) > 0:
            matched_idx.append(int(u["uid"].rsplit(":", 1)[1]))
    chosen = [fns[i] for i in matched_idx[:k] if i < len(fns)]
    if not chosen:
        chosen = _relevant(fns, qterms, key=lambda f: f.get("sig", "") + " " + f.get("desc", ""), k=k)
    return [{"sig": f.get("sig"), "desc": f.get("desc", ""), "complexity": f.get("complexity", ""),
             "example": f.get("example", "")} for f in chosen]


def _best_unit(st: _State, res_units: list, aid: str, kinds: tuple, pred=None):
    for f, u, h in res_units:
        if u.get("aid") == aid and u["kind"] in kinds and (pred is None or pred(u)):
            return f, u, h
    return None


def compose(st: _State, res: dict, intent: str, q: str, conf: float) -> dict:
    """Składa odpowiedź (bloki) dla najlepszego artykułu."""
    qterms = set(T.terms(q))
    arts = res["articles"]
    units = res["units"]
    aid = arts[0][1]
    a = st.articles[aid]
    top = units[0]
    blocks: list[dict] = []
    used_uid = None
    title = a.get("title")
    best_qa = _best_unit(st, units, aid, ("qa",))
    best_art = _best_unit(st, units, aid, ("article",))
    if top[1]["kind"] == "snippet" and top[1].get("aid") is None:
        best_qa = None
    if intent == "code":
        cu = _best_unit(st, units, aid, ("code", "snippet"), pred=lambda u: u.get("full")) or \
            _best_unit(st, units, aid, ("code", "snippet"))
        if cu is None:
            # kod z artykułu, nawet jeśli BM25 nie trafił w samą sekcję
            for u in st.by_article.get(aid, []):
                if u["kind"] == "code" and u.get("full"):
                    cu = (0.0, u, {})
                    break
        intro = a.get("summary", "")
        if best_qa and best_qa[0] >= 0.75 * top[0] and best_qa[1].get("code"):
            u = best_qa[1]
            used_uid = u["uid"]
            blocks.append({"type": "text", "md": u["text"]})
            blocks.append({"type": "code", "code": u["code"], "lang": u.get("lang", "cpp"), "title": _cap(u["title"])})
            if cu and cu[1].get("full") and cu[1]["code"] != u["code"]:
                blocks.append({"type": "code", "code": cu[1]["code"], "lang": cu[1].get("lang", "cpp"),
                               "title": f"Pełny program: {cu[1]['title']}"})
        elif cu:
            u = cu[1]
            used_uid = u["uid"]
            blocks.append({"type": "text", "md": f"**{a.get('title')}** — {_first_sentence(intro, 220)}"})
            blocks.append({"type": "code", "code": u["code"], "lang": u.get("lang", "cpp"), "title": u["title"]})
            if u["kind"] == "snippet":
                blocks.append({"type": "notice", "tone": "info",
                               "md": f"W edytorze wpisz `{u['snippet']['trigger']}` i naciśnij **Tab**, aby wstawić ten szablon."})
        elif best_qa:
            used_uid = best_qa[1]["uid"]
            blocks += _qa_blocks(best_qa[1])
        else:
            blocks.append({"type": "text", "md": intro})
    elif intent == "define":
        blocks.append({"type": "text", "md": f"**{a.get('title')}** — {a.get('summary', '')}"})
        first_text = next((b for b in a.get("body") or [] if isinstance(b, dict) and b.get("type") == "text"), None)
        if first_text and _plain(first_text.get("md", "")) != _plain(a.get("summary", "")):
            blocks.append({"type": "text", "md": first_text.get("md", "")})
        if best_qa and re.match(r"^(co to|czym|what|jak dziala|po co|kiedy)", T.normalize(best_qa[1]["title"])):
            used_uid = best_qa[1]["uid"]
            blocks.append({"type": "text", "md": best_qa[1]["text"]})
        code = _short_code(st, aid)
        if code:
            blocks.append(code)
    elif intent == "complexity":
        if a.get("complexity"):
            blocks.append({"type": "complexity", "text": a["complexity"], "title": f"Złożoność — {short_name(a)}"})
        if best_qa and best_qa[0] >= 0.8 * top[0] and qterms & set(T.terms(best_qa[1]["f"]["q"])):
            used_uid = best_qa[1]["uid"]
            blocks += _qa_blocks(best_qa[1])
        elif not a.get("complexity"):
            blocks.append({"type": "text", "md": a.get("summary", "")})
    else:
        main = None
        if best_qa and (best_art is None or best_qa[0] >= 0.72 * best_art[0] or top[1]["kind"] == "qa"):
            main = best_qa
        if main is None and top[1]["kind"] in ("text", "tip", "warn", "table", "function", "pitfall", "code") \
                and top[1].get("aid") == aid:
            main = top
        if main is not None:
            u = main[1]
            used_uid = u["uid"]
            if u["kind"] == "qa":
                blocks += _qa_blocks(u)
            elif u["kind"] == "table":
                blocks.append({"type": "text", "md": f"**{a.get('title')}**"})
                blocks.append({"type": "table", **u["table"]})
            elif u["kind"] == "code":
                blocks.append({"type": "code", "code": u["code"], "lang": u.get("lang", "cpp"), "title": u["title"]})
            elif u["kind"] == "function":
                fn = u["fn"]
                blocks.append({"type": "text", "md": f"`{fn['sig']}` — {fn.get('desc', '')}"
                                                     + (f" Złożoność: **{fn['complexity']}**." if fn.get("complexity") else "")})
                if fn.get("example"):
                    blocks.append({"type": "code", "code": fn["example"], "lang": "cpp", "title": "Przykład"})
            else:
                blocks.append({"type": "warn" if u["kind"] in ("warn", "pitfall") else "text", "md": u["text"]})
        else:
            blocks.append({"type": "text", "md": f"**{a.get('title')}** — {a.get('summary', '')}"})
            code = _short_code(st, aid)
            if code:
                blocks.append(code)
    # pytanie „jak…/kod” bez przykładu w odpowiedzi → dołóż najlepiej pasujący kod z tego artykułu
    if intent in ("howto", "code", "general") and not any(b["type"] == "code" for b in blocks):
        cu = _best_unit(st, units, aid, ("qa", "code", "snippet"),
                        pred=lambda u: bool(u.get("code")) and u["uid"] != used_uid)
        if cu is not None:
            u = cu[1]
            blocks.append({"type": "code", "code": u["code"], "lang": u.get("lang", "cpp"),
                           "title": _cap(u["title"]) if u["kind"] == "qa" else u["title"]})
        else:
            code = _short_code(st, aid)
            if code:
                blocks.append(code)
    # funkcje, gdy pytanie dotyczy metod lub złożoności
    fns = _functions_for(st, a, units, qterms, k=6 if intent == "complexity" else 4)
    if fns and (intent == "complexity" or any(u["kind"] == "function" and u["aid"] == aid and h.get("score", 0) > 0
                                              for _, u, h in units[:8])):
        blocks.append({"type": "functions", "items": fns})
    if intent != "complexity" and a.get("complexity"):
        blocks.append({"type": "complexity", "text": a["complexity"]})
    pit = _pitfalls_block(a, qterms, k=3 if intent in ("error", "howto", "define") else 2)
    if pit:
        blocks.append(pit)
    return {"article": a, "blocks": blocks, "used_uid": used_uid, "title": title}


def _qa_blocks(u: dict) -> list[dict]:
    out = [{"type": "text", "md": u["text"]}]
    if u.get("code"):
        out.append({"type": "code", "code": u["code"], "lang": u.get("lang", "cpp"), "title": ""})
    return out


def _short_code(st: _State, aid: str, max_lines: int = 40) -> dict | None:
    best = None
    for u in st.by_article.get(aid, []):
        if u["kind"] in ("code", "qa") and u.get("code"):
            n = u["code"].count("\n") + 1
            if n <= max_lines and (best is None or (u["kind"] == "code") > (best["kind"] == "code")):
                best = u
                if u["kind"] == "code":
                    break
    if best is None:
        return None
    return {"type": "code", "code": best["code"], "lang": best.get("lang", "cpp"),
            "title": best["title"] if best["kind"] == "code" else "Przykład"}


# ============================================================================================ porównanie X vs Y
_CMP_PATTERNS = [
    re.compile(r"^(?:jaka jest |czym sie rozni |czym rozni sie )?(?:roznic\w*|porownanie|porownaj)\s+(?:miedzy|pomiedzy|between)?\s*"
               r"(.+?)\s+(?:a|i|oraz|and|vs\.?|versus|z)\s+(.+)$"),
    re.compile(r"^czym\s+(?:sie\s+)?rozni\s+(?:sie\s+)?(.+?)\s+od\s+(.+)$"),
    re.compile(r"^(?:co (?:jest )?(?:lepsze|szybsze|wybrac)|lepiej|co lepiej)\s*:?\s*(.+?)\s+(?:czy|albo|or|vs\.?)\s+(.+)$"),
    re.compile(r"^(.+?)\s+(?:vs\.?|versus|kontra|czy|or|albo)\s+(.+?)(?:\s+(?:roznica|co lepsze|co wybrac|lepsze|szybsze|"
               r"co szybsze|porownanie))?$"),
]


def split_compare(q: str) -> tuple[str, str] | None:
    n = T.normalize(q).rstrip("?!. ")
    n = re.sub(r"^(a|i)\s+", "", n)
    for rx in _CMP_PATTERNS:
        m = rx.match(n)
        if m:
            x, y = m.group(1).strip(), m.group(2).strip()
            x = re.sub(r"^(co lepsze|co wybrac|lepiej|uzyc|uzywac)\s+", "", x)
            y = re.sub(r"\s+(roznica|co lepsze|co wybrac|lepsze|szybsze|co szybsze|w c\+\+|cpp)$", "", y)
            if rx is _CMP_PATTERNS[-1] and re.match(r"^(jak|co|czy|gdzie|kiedy|dlaczego|ile|ktor\w*|jak\w+|how|what|why)\b", x):
                continue
            if x and y and x != y and len(T.terms(x)) <= 4 and len(T.terms(y)) <= 4:
                return x, y
    return None


def resolve_topic(st: _State, text_: str, min_cover: float = 0.99, min_sim: float = 0.3) -> tuple[str, float] | None:
    """Artykuł odpowiadający krótkiej nazwie tematu („set”, „lista sąsiedztwa”) albo None.

    Najpierw dokładne dopasowanie do aliasu/tytułu/krótkiej nazwy, potem BM25 po jednostkach-artykułach
    (wymagane pokrycie wszystkich słów i podobieństwo trigramowe do aliasów), na końcu sieć neuronowa."""
    norm = T.normalize(text_)
    exact = st.alias_map.get(norm)
    if exact and len(exact) == 1:
        return next(iter(exact)), 1.0
    hits = st.index.search(text_, k=8, filters=lambda did, kind: kind == "article")
    nn = _ENGINE.nn_probs(text_, st)
    qtri = _tri(text_)
    best = None
    for h in hits:
        if h["cover"] < min_cover:
            continue
        aid = h["id"][2:]
        if exact and aid not in exact:
            continue
        u = st.units[h["id"]]
        sim = max((S.jaccard(qtri, t) for t in u["tri"]), default=0.0)
        if sim < min_sim and not exact:
            continue
        s = h["score"] * (0.5 + sim) * (1.0 + (nn.get(aid, 0.0) if nn else 0.0))
        if best is None or s > best[1]:
            best = (aid, s)
    if best is None and nn and not exact:
        aid, p = max(nn.items(), key=lambda kv: kv[1])
        if p >= 0.6:
            best = (aid, p)
    return best


def compare_table(a: dict, b: dict) -> dict:
    def fnmap(art):
        out = {}
        for f in art.get("functions") or []:
            if not isinstance(f, dict):
                continue
            names = _method_names(f.get("sig", ""))
            comps = [c.strip() for c in str(f.get("complexity", "")).split(" / ")]
            for i, nm in enumerate(names):
                if nm in ("T", "s", "v", "a", "b"):
                    continue
                c = comps[i] if len(comps) == len(names) else (f.get("complexity") or "")
                out.setdefault(nm, c or "—")
        return out

    fa, fb = fnmap(a), fnmap(b)
    na, nb = short_name(a), short_name(b)
    rows = [["Czym jest", _first_sentence(a.get("summary", ""), 170), _first_sentence(b.get("summary", ""), 170)],
            ["Złożoność", a.get("complexity") or "—", b.get("complexity") or "—"],
            ["Poziom", LEVELS.get(a.get("level"), "—"), LEVELS.get(b.get("level"), "—")]]
    common = [m for m in fa if m in fb][:7]
    for m in common:
        rows.append([f"`{m}()`", fa[m], fb[m]])
    only_a = [m for m in fa if m not in fb][:6]
    only_b = [m for m in fb if m not in fa][:6]
    if only_a or only_b:
        rows.append(["Tylko tutaj", ", ".join(f"`{m}`" for m in only_a) or "—", ", ".join(f"`{m}`" for m in only_b) or "—"])
    return {"type": "table", "title": f"{na} vs {nb}", "headers": ["", na, nb], "rows": rows}


# ============================================================================================ specjalne tryby
def _try_errors(stderr: str) -> list[dict] | None:
    try:
        from oiforge.tools import errors as _errors  # moduł agenta B1 (opcjonalny)
        items = _errors.translate(stderr, None)
        return items if isinstance(items, list) else None
    except Exception:  # noqa: BLE001
        return None


_FALLBACK_ERRORS = [
    (r"was not declared in this scope", "Nazwa nie została zadeklarowana",
     "Używasz zmiennej/funkcji, której kompilator nie zna w tym miejscu: literówka, brak `#include`, brak "
     "`using namespace std;` albo deklaracja dopiero niżej."),
    (r"expected ['‘]?;['’]? before", "Brakuje średnika", "Dodaj `;` na końcu poprzedniej instrukcji."),
    (r"no matching function for call", "Brak pasującej funkcji",
     "Typy argumentów nie pasują do żadnej wersji funkcji — sprawdź typy (np. `int` vs `long long` w `max`)."),
    (r"undefined reference to", "Niezdefiniowane odwołanie (linker)",
     "Funkcja jest zadeklarowana, ale nie ma definicji (albo brakuje `main`)."),
    (r"invalid conversion from", "Niepoprawna konwersja typów", "Przypisujesz wartość niezgodnego typu."),
    (r"Segmentation fault|SIGSEGV", "Naruszenie ochrony pamięci (segfault)",
     "Program sięgnął poza tablicę/vector, wyłuskał pusty wskaźnik albo przepełnił stos głęboką rekurencją."),
    (r"terminate called after throwing", "Nieobsłużony wyjątek",
     "Np. `out_of_range` z `.at()`/`substr` albo `bad_alloc` przy zbyt dużej alokacji."),
]


def _fallback_errors(stderr: str) -> list[dict]:
    out = []
    for line in stderr.splitlines():
        for rx, title, expl in _FALLBACK_ERRORS:
            if re.search(rx, line):
                m = re.search(r":(\d+):(?:(\d+):)?", line)
                out.append({"line": int(m.group(1)) if m else None, "col": int(m.group(2)) if m and m.group(2) else None,
                            "severity": "error", "title": title, "explanation": expl, "fix": "", "original": line.strip()})
                break
    return out[:8]


def _try_algodetect(code: str) -> list[dict]:
    try:
        from oiforge.core import algodetect  # moduł agenta B2 (opcjonalny)
        res = algodetect.detect(code)
        return res if isinstance(res, list) else []
    except Exception:  # noqa: BLE001
        return []


_GENERIC_CODE = {"iostream", "bits/stdc++.h", "sync_with_stdio", "cstdio", "using", "namespace", "std", "main", "cin",
                 "cout", "include", "int", "vector", "endl"}


def _strong_reason(why: str) -> bool:
    """Czy powód dopasowania programu ucznia jest konkretny (temat rundy, nazwa zadania, istotny kod)?"""
    w = why.lower()
    if w.startswith(("temat", "nazwa zadania", "identyfikatory")):
        return True
    if w.startswith("w kodzie:"):
        items = {x.strip() for x in w.split(":", 1)[1].split(",")}
        return bool(items - _GENERIC_CODE)
    return False


def _programs(q: str, k: int = 3, strict: bool = True) -> list[dict]:
    """„Twoje rozwiązania”: programy ucznia (moduł `programs`, jeśli jest) pasujące do pytania.
    strict=True zostawia tylko trafienia z konkretnym uzasadnieniem (nie same słowa z treści zadania)."""
    try:
        from oiforge.tools import programs as _progs  # moduł agenta F (opcjonalny)
        res = _progs.search(q, k=k + 3) or []
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], set()
    for r in res:
        if not isinstance(r, dict):
            continue
        why = [str(x) for x in (r.get("why") or [])]
        if strict and why and not any(_strong_reason(x) for x in why):
            continue
        key = (r.get("problem_name"), r.get("contest_name"))
        if key in seen:
            continue
        seen.add(key)
        item = {k2: r.get(k2) for k2 in ("id", "problem_name", "problem_short", "contest_name", "path", "tags", "round",
                                         "topic", "relevance")}
        item["why"] = next((x for x in why if _strong_reason(x)), why[0] if why else "")
        out.append(item)
        if len(out) >= k:
            break
    return out


_SCALE = [  # (maks. n, złożoność, przykłady)
    (11, "O(n!) lub O(n·n!)", "permutacje, pełny przegląd"),
    (22, "O(2ⁿ·n)", "maski bitowe, DP po podzbiorach"),
    (40, "O(2^(n/2))", "meet in the middle"),
    (500, "O(n³)", "Floyd–Warshall, DP przedziałowe"),
    (5000, "O(n²)", "DP kwadratowe, pary elementów"),
    (300_000, "O(n log n) lub O(n√n)", "sortowanie, drzewa przedziałowe, wyszukiwanie binarne"),
    (5_000_000, "O(n) lub O(n log n)", "sumy prefiksowe, dwa wskaźniki, sito"),
    (100_000_000, "O(n) ze stałą ~1", "proste pętle, sito liniowe"),
    (10 ** 19, "O(log n) lub O(√n)", "matematyka, szybkie potęgowanie, wyszukiwanie binarne po wyniku"),
]


def _parse_num(s: str) -> int | None:
    try:
        from oiforge.core import statement  # agent E2 (opcjonalny)
        v = statement.parse_value(s)
        if isinstance(v, (int, float)):
            return int(v)
    except Exception:  # noqa: BLE001
        pass
    s = s.replace(" ", "").replace(",", "").rstrip(".")
    m = re.fullmatch(r"(\d+)?(?:[·*x])?10\^(\d+)", s)
    if m:
        return int(m.group(1) or 1) * 10 ** int(m.group(2))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)e(\d+)", s)
    if m:
        return int(float(m.group(1)) * 10 ** int(m.group(2)))
    return int(s) if s.isdigit() else None


def constraint_advice(text_: str) -> dict | None:
    """Ograniczenia typu `n ≤ 200 000` → oczekiwana złożoność (doradca na skróty)."""
    found = {}
    for m in _RX_CONSTRAINT.finditer(text_ or ""):
        v = _parse_num(m.group(3).strip())
        if v:
            var = m.group(1).lower()
            found[var] = max(found.get(var, 0), v)
    if not found:
        return None
    var, nmax = max(found.items(), key=lambda kv: kv[1])
    for lim, comp, ex in _SCALE:
        if nmax <= lim:
            return {"var": var, "max": nmax, "complexity": comp, "examples": ex, "all": found}
    return None


# ============================================================================================ kontekst rozmowy
def resolve_context(st: _State, q: str, history: list) -> dict:
    """Pytanie uzupełniające („a jak usunąć?”) → dokleja temat poprzedniej odpowiedzi.
    Zwraca {query, topic_aid, factor, used, note}."""
    out = {"query": q, "topic_aid": None, "factor": 1.0, "used": False, "note": None}
    prev = None
    for h in reversed(history or []):
        if isinstance(h, dict) and (h.get("topic") or h.get("q")):
            prev = h
            break
    if not prev:
        return out
    ptopic = prev.get("topic")
    if isinstance(ptopic, dict):
        ptopic = ptopic.get("id")
    pa = st.articles.get(ptopic) if ptopic else None
    n = T.normalize(q)
    terms_ = T.terms(q)
    starts = bool(FOLLOWUP_RX.match(n))
    content = [t for t in terms_ if t not in GENERIC]
    # czy pytanie samo nazywa (inny) temat? — „a z vectora?” tak, „a jak usunąć?” nie
    hit = resolve_topic(st, " ".join(content), min_cover=0.5) if content and len(content) <= 4 else None
    topical = hit is not None and (not pa or hit[0] != pa["id"])
    if pa and not topical and (starts or len(terms_) <= 4):
        sn = short_name(pa)
        out.update(query=f"{q} {sn}", topic_aid=pa["id"], factor=1.5 if starts else 1.3, used=True,
                   note=f"Rozumiem, że nadal chodzi o: **{sn}**.")
        return out
    if starts and topical and len(content) <= 3 and prev.get("q"):
        # zmiana tematu z zachowaniem czynności: „jak usunąć z seta” → „a z vectora?”
        old_names = _name_roots(pa) if pa else set()
        prev_terms = [w for w in T.normalize(prev["q"]).split()
                      if not ((T.terms(w) or [""])[0] not in GENERIC and S.root((T.terms(w) or [""])[0]) in old_names)]
        if prev_terms:
            na = st.articles.get(hit[0])
            out.update(query=f"{q} {' '.join(prev_terms)}", topic_aid=hit[0], factor=1.3, used=True,
                       note=f"Łączę z poprzednim pytaniem (*{prev['q'].strip()}*), ale dla: **{short_name(na) if na else hit[0]}**.")
    return out


def _name_roots(a: dict) -> set[str]:
    """Rdzenie nazw tematu (tytuł, krótka nazwa, pierwsze aliasy) — do usuwania starego tematu z pytania."""
    txt = " ".join([a.get("title", ""), short_name(a)] + [x for x in (a.get("aliases") or [])[:6] if isinstance(x, str)])
    return {S.root(t) for t in T.terms(txt)}


# ============================================================================================ główna funkcja
def ask(q: str, history: list | None = None, programs: bool = False) -> dict:
    """Odpowiedź na pytanie. `programs=True` dołącza od razu „Twoje rozwiązania” (wolniejsze, ~200 ms); domyślnie
    zwracamy tylko `programs_query`, a widok dociąga programy osobnym żądaniem (/api/assistant/programs)."""
    t0 = time.perf_counter()
    q = (q or "").strip()
    if not q:
        raise ApiError("Zadaj pytanie, np. „jak użyć seta” albo „dijkstra kod”.")
    if len(q) > 60_000:
        q = q[:60_000]
    st = _ENGINE.state()
    intent = detect_intent(q)
    resp: dict = {"q": q, "intent": {"id": intent["id"], "label": intent["label"]}, "blocks": [], "sources": [],
                  "see_also": [], "suggestions": [], "actions": [], "programs": [], "alternatives": [],
                  "did_you_mean": None, "topic": None, "context": None}
    if AT is not None and _tools_dispatch(st, q, history or [], intent, resp):
        pass
    elif intent["id"] == "compiler":
        _answer_compiler(st, q, resp)
    elif intent["id"] == "code_paste":
        _answer_code(st, q, resp)
    elif intent["id"] == "task":
        _answer_task(st, q, resp)
    else:
        _answer_question(st, q, history or [], intent, resp)
    pq = resp.pop("_programs_q", None)
    if pq:
        resp["programs_query"] = pq
        if programs:
            resp["programs"] = _programs(pq)
    nn_st = _ENGINE.nn_status()
    resp["nn"] = {"state": nn_st.get("state"), "ready": nn_st["ready"]}
    resp["timing_ms"] = round(1000 * (time.perf_counter() - t0), 1)
    return resp


def _answer_question(st: _State, q: str, history: list, intent: dict, resp: dict) -> None:
    iid = intent["id"]
    # 1) korekta pisowni
    corr = st.index.correct(q)
    query = corr["text"] if corr["changed"] else q
    if corr["changed"]:
        resp["did_you_mean"] = {"text": corr["text"], "fixes": corr["fixes"]}
    # 2) porównanie X vs Y
    sides = split_compare(query)
    if sides:
        explicit = iid == "compare"
        ra, rb = resolve_topic(st, sides[0]), resolve_topic(st, sides[1])
        # artykuły-dokumentacje („Dokumentacja: <iterator>”) to katalogi funkcji, nie strony porównania
        ref = any(x and x[0].startswith(("stl-ref-", "cpp-ref-")) for x in (ra, rb))
        if ra and rb and ra[0] != rb[0] and not ref and (explicit or not _has_near_qa(st, query)):
            _answer_compare(st, query, st.articles[ra[0]], st.articles[rb[0]], resp)
            return
    if iid == "compare":
        iid = "howto"
        resp["intent"] = {"id": "howto", "label": INTENT_LABELS["howto"]}
    # 3) kontekst rozmowy
    ctx = resolve_context(st, query, history)
    if ctx["used"]:
        resp["context"] = {"note": ctx["note"], "query": ctx["query"]}
    search_q = ctx["query"]
    # 4) ranking
    res = retrieve(st, search_q, intent=iid, topic_aid=ctx["topic_aid"], topic_factor=ctx["factor"],
                   nn_text=_topic_query(search_q))
    if not res["units"] and corr["changed"]:
        res = retrieve(st, q, intent=iid)
    qterms = T.terms(search_q)
    conf = _confidence(res, qterms) if res["units"] else 0.0
    # poprawiona pisownia = mniejsza pewność (szczególnie przy odległości 2)
    for d in corr.get("distances") or []:
        conf *= 0.92 if d <= 1 else 0.75
    # słowa, których baza wiedzy w ogóle nie zna („makaron”, „australii”) — im więcej, tym mniejsza pewność
    solid, allq = st.index.known_terms(search_q)
    unknown_words = sorted(allq - solid)
    solid_frac = len(solid) / len(allq) if allq else 1.0
    if solid_frac < 1.0:
        conf *= 0.5 + 0.5 * solid_frac
    resp["confidence"] = round(conf, 3)
    resp["confidence_label"] = "wysoka" if conf >= 0.7 else ("średnia" if conf >= 0.42 else "niska")
    # „nie wiem” tylko przy realnym braku dowodów: nieznane słowa albo bardzo niska pewność przy nieznanych słowach
    if not res["units"] or not res["articles"] or (conf < UNKNOWN_CONF and solid_frac < 0.75) or \
            (solid_frac <= 0.5 and conf < 0.5):
        _answer_unknown(st, search_q, res, resp, unknown_words=unknown_words)
        return
    comp = compose(st, res, iid, search_q, conf)
    a = comp["article"]
    blocks = comp["blocks"]
    if ctx["used"] and ctx["note"]:
        blocks.insert(0, {"type": "notice", "tone": "info", "md": ctx["note"]})
    if conf < 0.42:
        blocks.insert(0, {"type": "notice", "tone": "warn",
                          "md": "Nie jestem pewien, czy dobrze rozumiem pytanie — oto najbliższy temat, jaki znalazłem. "
                                "Spróbuj doprecyzować albo wybierz jeden z tematów poniżej."})
        resp["alternatives"] = [_article_link(st.articles[x]) for _, x in res["articles"][:4] if x in st.articles][:3]
    if intent["flags"].get("error_kw"):
        resp["actions"].append({"label": "Tłumacz błędów", "icon": "languages", "view": "errors", "params": {}})
        resp["actions"].append({"label": "Sprawdzarka kodu", "icon": "bug", "view": "checker", "params": {}})
    resp["blocks"] = blocks
    resp["answer_title"] = comp["title"]
    resp["topic"] = {"id": a["id"], "title": a.get("title"), "name": short_name(a), "category": a.get("category")}
    resp["sources"] = _sources(res, qterms)
    resp["see_also"] = _see_also(st, a, res)
    resp["suggestions"] = _suggestions(st, a, comp["used_uid"], iid, set(qterms), q=query)
    code = next((b["code"] for b in blocks if b["type"] == "code"), None)
    resp["actions"].insert(0, {"label": "Otwórz w dokumentacji", "icon": "book-open", "view": "docs",
                               "params": {"id": a["id"]}})
    if code:
        resp["actions"].insert(1, {"label": "Otwórz w Playground", "icon": "play", "view": "playground",
                                   "params": {"code": code}})
    resp["_programs_q"] = search_q
    resp["ranking"] = [x for _, x in res["articles"][:5]]
    resp["used_uid"] = comp.get("used_uid")
    if AT is not None and _personal():
        _personalize(st, q, a, resp)


def _has_near_qa(st: _State, q: str, thr: float = 0.72) -> bool:
    """Czy w KB jest pytanie QA sformułowane niemal identycznie (wtedy odpowiadamy nim zamiast tabelą)."""
    qtri = _tri(q)
    for h in st.index.search(q, k=40, filters=lambda did, kind: kind == "qa"):
        if max((S.jaccard(qtri, t) for t in st.units[h["id"]]["tri"]), default=0.0) >= thr:
            return True
    return False


def _sources(res: dict, qterms: list[str], k: int = 4) -> list[dict]:
    out, seen = [], set()
    for f, u, h in res["units"]:
        key = (u.get("aid"), u["kind"], u.get("anchor"))
        if key in seen:
            continue
        seen.add(key)
        s = _source(u, h, qterms)
        s["relevance"] = round(f, 3)
        out.append(s)
        if len(out) >= k:
            break
    return out


def _see_also(st: _State, a: dict, res: dict, k: int = 5) -> list[dict]:
    out, seen = [], {a["id"]}
    for _, aid in res["articles"][1:4]:
        if aid not in seen and aid in st.articles:
            seen.add(aid)
            out.append(_article_link(st.articles[aid]))
    for rid in a.get("related") or []:
        if rid not in seen and rid in st.articles and len(out) < k:
            seen.add(rid)
            out.append(_article_link(st.articles[rid]))
    return out[:k]


def _answer_unknown(st: _State, q: str, res: dict, resp: dict, unknown_words: list | None = None) -> None:
    """Szczere „nie wiem”: nie udajemy odpowiedzi z przypadkowego artykułu. Pokazujemy, których słów nie znamy,
    najbliższe tematy (jeśli w ogóle coś pasuje) i podpowiedzi, jak zapytać inaczej."""
    resp["confidence"] = min(resp.get("confidence") or 0.0, 0.25)
    resp["confidence_label"] = "niska"
    near, seen = [], set()
    for _s, aid in (res.get("articles") or [])[:3]:
        if aid in st.articles and aid not in seen:
            seen.add(aid)
            near.append(_article_link(st.articles[aid]))
    nn = res.get("nn") or {}
    for aid, p in sorted(nn.items(), key=lambda kv: -kv[1])[:3]:
        if p >= 0.2 and aid not in seen and len(near) < 3:
            seen.add(aid)
            near.append(_article_link(st.articles[aid]))
    md = "**Nie wiem.** Nie znalazłem odpowiedzi na to pytanie w lokalnej bazie wiedzy (algorytmy, C++, STL, " \
         "zadania olimpijskie) — wolę to powiedzieć wprost, niż zgadywać."
    words = [w for w in (unknown_words or []) if len(w) >= 3][:6]
    if words:
        md += " Nie rozpoznaję słów: " + ", ".join(f"*{w}*" for w in words) + "."
    blocks = [{"type": "notice", "tone": "warn", "md": md}]
    if near:
        blocks.append({"type": "text", "md": "Może chodzi Ci o któryś z tych tematów?"})
        blocks.append({"type": "topics", "items": near})
    blocks.append({"type": "text", "md": "Spróbuj zapytać inaczej, np. nazwą struktury lub algorytmu („kolejka "
                                         "priorytetowa”, „dijkstra”), czynnością i obiektem („posortuj vector par po "
                                         "second”) albo wklej kod lub komunikat błędu."})
    resp["blocks"] = blocks
    resp["alternatives"] = near or [_article_link(st.articles[x]) for x in st.order[:3]]
    resp["topic"] = None
    resp["ranking"] = [x["id"] for x in near]
    resp["answer_title"] = "Nie wiem"
    resp["did_you_mean"] = None          # „poprawka” słów spoza bazy („sosem” → „stosem”) tylko by myliła
    resp["suggestions"] = ["Jak użyć seta?", "Dijkstra kod", "Jak wczytać do końca pliku?", "Co to lazy propagation?"]


def _answer_compare(st: _State, q: str, a: dict, b: dict, resp: dict) -> None:
    resp["intent"] = {"id": "compare", "label": INTENT_LABELS["compare"]}
    qterms = T.terms(q)
    res = retrieve(st, q, intent="compare")
    blocks = []
    # pytanie QA, które porusza oba tematy (np. „set vs multiset”) — jako bezpośrednia odpowiedź
    for f, u, h in res["units"][:12]:
        if u["kind"] == "qa" and u.get("aid") in (a["id"], b["id"]) and h.get("cover", 0) >= 0.66:
            blocks += _qa_blocks(u)
            break
    if not blocks:
        blocks.append({"type": "text", "md": f"Porównanie: **{a.get('title')}** i **{b.get('title')}**."})
    blocks.append(compare_table(a, b))
    for art in (a, b):
        pits = [p for p in art.get("pitfalls") or [] if isinstance(p, str)]
        if pits:
            blocks.append({"type": "pitfalls", "items": _relevant(pits, set(qterms), k=1),
                           "title": f"Uwaga ({short_name(art)})"})
    resp["blocks"] = blocks
    resp["confidence"] = 0.85
    resp["confidence_label"] = "wysoka"
    resp["answer_title"] = f"{short_name(a)} vs {short_name(b)}"
    resp["ranking"] = [a["id"], b["id"]]
    resp["topic"] = {"id": a["id"], "title": a.get("title"), "name": short_name(a), "category": a.get("category")}
    resp["sources"] = [_source(st.units[f"a:{x['id']}"], None, qterms) for x in (a, b)]
    resp["see_also"] = [_article_link(st.articles[r]) for r in (a.get("related") or []) + (b.get("related") or [])
                        if r in st.articles and r not in (a["id"], b["id"])][:4]
    sa, sb = short_name(a), short_name(b)
    resp["suggestions"] = [f"Jak użyć {sa}?", f"Jak użyć {sb}?", f"Złożoność {sa}", f"{sb} — pokaż kod"]
    resp["actions"] = [{"label": f"Dokumentacja: {sa}", "icon": "book-open", "view": "docs", "params": {"id": a["id"]}},
                       {"label": f"Dokumentacja: {sb}", "icon": "book-open", "view": "docs", "params": {"id": b["id"]}}]


def _answer_compiler(st: _State, q: str, resp: dict) -> None:
    items = _try_errors(q)
    source = "errors"
    if not items:
        items = _fallback_errors(q)
        source = "fallback"
    blocks = [{"type": "text", "md": "Wygląda na komunikat kompilatora lub błąd wykonania. "
                                     + ("Oto tłumaczenie najważniejszych komunikatów:" if items else
                                        "Nie rozpoznałem szczegółów — otwórz **Tłumacz błędów**, żeby zobaczyć pełną analizę.")}]
    if items:
        blocks.append({"type": "errors", "items": items[:8], "source": source})
    # powiązana wiedza z KB (np. segfault → pułapki z pamięcią)
    first = (items[0].get("title") or "") if items else ""
    kbq = first or " ".join(re.findall(r"error:\s*([^\n]{0,80})", q)[:1]) or q[:120]
    res = retrieve(st, kbq, intent="error")
    resp["intent"] = {"id": "compiler", "label": INTENT_LABELS["compiler"]}
    resp["confidence"] = 0.8 if items else 0.4
    resp["confidence_label"] = "wysoka" if items else "niska"
    resp["blocks"] = blocks
    resp["sources"] = _sources(res, T.terms(kbq), k=3) if res["units"] else []
    resp["actions"] = [{"label": "Otwórz w Tłumaczu błędów", "icon": "languages", "view": "errors", "params": {"stderr": q}},
                       {"label": "Sprawdzarka kodu", "icon": "bug", "view": "checker", "params": {}}]
    resp["suggestions"] = ["Co to segfault?", "Jak uniknąć przepełnienia inta?", "Wyjście poza tablicę"]


def _split_code_and_text(s: str) -> tuple[str, str]:
    lines = s.splitlines()
    text_lines, code_lines = [], []
    started = False
    for l in lines:
        if not started and l.strip() and not re.search(r"[;{}#()<>=]", l) and len(code_lines) == 0:
            text_lines.append(l)
        else:
            started = True
            code_lines.append(l)
    return " ".join(text_lines).strip(), "\n".join(code_lines).strip()


_FALLBACK_ALGOS = [
    (r"priority_queue.*(dist|odl)|dijkstra", "dijkstra"), (r"lower_bound|upper_bound|while\s*\(\s*\w+\s*<\s*\w+\s*\)\s*\{[^}]*mid",
                                                           "wyszukiwanie binarne"),
    (r"queue\s*<.*>.*\.(push|pop)\(|bfs", "bfs"), (r"\bdfs\s*\(", "dfs"), (r"\bfind\s*\(.*\)\s*\{|parent\[|rep\[", "find union"),
    (r"dp\s*\[", "programowanie dynamiczne"), (r"\btree\s*\[\s*2\s*\*|seg|\bt\[2\s*\*", "drzewo przedziałowe"),
    (r"&\s*-\s*\w+|\w+\s*&\s*\(-\w+\)", "drzewo fenwicka"), (r"sort\s*\(", "sortowanie"),
]


def _answer_code(st: _State, q: str, resp: dict) -> None:
    text_part, code = _split_code_and_text(q)
    n_lines = code.count("\n") + 1
    lang = "python" if re.search(r"^\s*def\s+\w+\(|^\s*import\s+\w+|print\(", code, re.M) and ";" not in code else "C++"
    blocks = [{"type": "text", "md": f"Wkleiłeś kod ({lang}, {n_lines} linii). Mogę go przekazać do narzędzi:"
                                     " **Sprawdzarka** znajdzie typowe błędy (overflow, wyjście poza tablicę, niezainicjowane "
                                     "zmienne), **Objaśniacz** opisze, co robi kod, a **Złożoność** oszacuje czas działania."}]
    algos = _try_algodetect(code)
    found = []
    if algos:
        for al in algos[:4]:
            name = al.get("name") or al.get("id")
            hit = resolve_topic(st, str(al.get("name") or al.get("id", "")).replace("_", " "), min_cover=0.5)
            found.append({"name": name, "confidence": al.get("confidence"), "id": hit[0] if hit else None})
    else:
        for rx, phrase in _FALLBACK_ALGOS:
            if re.search(rx, code, re.I | re.S):
                hit = resolve_topic(st, phrase, min_cover=0.5)
                if hit and all(f["id"] != hit[0] for f in found):
                    found.append({"name": st.articles[hit[0]]["title"], "confidence": None, "id": hit[0]})
            if len(found) >= 3:
                break
    if found:
        lines = []
        for f in found:
            lines.append(f"- **{f['name']}**" + (f" — zobacz [{st.articles[f['id']]['title']}](kb:{f['id']})"
                                                if f.get("id") else ""))
        blocks.append({"type": "text", "md": "Rozpoznane techniki w kodzie:\n" + "\n".join(lines)})
        resp["see_also"] = [_article_link(st.articles[f["id"]]) for f in found if f.get("id")]
    if text_part:
        res = retrieve(st, text_part, intent="howto")
        if res["units"] and res["articles"]:
            comp = compose(st, res, "howto", text_part, 0.6)
            blocks.append({"type": "notice", "tone": "info", "md": f"Twoje pytanie: *{text_part}*"})
            blocks += comp["blocks"][:3]
            resp["sources"] = _sources(res, T.terms(text_part), k=3)
    resp["blocks"] = blocks
    resp["intent"] = {"id": "code_paste", "label": INTENT_LABELS["code_paste"]}
    resp["confidence"] = 0.75
    resp["confidence_label"] = "wysoka"
    resp["actions"] = [{"label": "Sprawdzarka", "icon": "bug", "view": "checker", "params": {"code": code}},
                       {"label": "Objaśniacz", "icon": "message-square", "view": "explain", "params": {"code": code}},
                       {"label": "Złożoność", "icon": "gauge", "view": "complexity", "params": {"code": code}},
                       {"label": "Playground", "icon": "play", "view": "playground", "params": {"code": code}}]
    resp["suggestions"] = ["Jak znaleźć błąd w kodzie?", "Co to segfault?", "Jak przyspieszyć wczytywanie?"]


def _answer_task(st: _State, q: str, resp: dict) -> None:
    blocks = [{"type": "text", "md": "Wygląda na treść zadania. Pełną analizę zrobi **Doradca algorytmów** "
                                     "(rozpozna ograniczenia, typ zadania i zaproponuje podejście), a **Objaśniacz zadań** "
                                     "rozłoży treść na części. Szybka podpowiedź poniżej:"}]
    adv = constraint_advice(q)
    if adv:
        blocks.append({"type": "complexity", "title": "Wymagana złożoność",
                       "text": f"{adv['var']} ≤ {adv['max']:,}".replace(",", " ") +
                               f" → celuj w **{adv['complexity']}** ({adv['examples']})."})
    res = retrieve(st, q[:2000], intent="howto")
    topics = [_article_link(st.articles[aid]) for _, aid in res["articles"][:4] if aid in st.articles]
    if topics:
        blocks.append({"type": "text", "md": "Tematy z bazy wiedzy, które mogą się przydać:"})
        blocks.append({"type": "topics", "items": topics})
    resp["blocks"] = blocks
    resp["intent"] = {"id": "task", "label": INTENT_LABELS["task"]}
    resp["confidence"] = 0.6 if topics else 0.3
    resp["confidence_label"] = "średnia" if topics else "niska"
    resp["actions"] = [{"label": "Doradca algorytmów", "icon": "lightbulb", "view": "suggest", "params": {"text": q}},
                       {"label": "Objaśnij zadanie", "icon": "file-text", "view": "taskexplain", "params": {"text": q}},
                       {"label": "Przypadki brzegowe", "icon": "triangle-alert", "view": "edgecases", "params": {"text": q}}]
    resp["see_also"] = topics
    resp["_programs_q"] = q[:300]
    resp["suggestions"] = ["Jak dobrać złożoność do ograniczeń?", "Wyszukiwanie binarne po wyniku", "Co to sumy prefiksowe?"]


# ============================================================================================ narzędzia w czacie
_tls = threading.local()


def _personal() -> bool:
    """Profil ucznia (personalizacja) tylko dla pytań z okna czatu — testy i skrypty go nie czytają ani nie zmieniają."""
    return bool(getattr(_tls, "personal", False))


def _code_ok_for_run(code: str) -> bool:
    """Fragment w jednej linii uruchamiamy tylko, gdy wygląda na kompletną instrukcję (średnik / print(...))."""
    if not code:
        return False
    if AT.detect_lang(code) == "python":
        return bool(re.search(r"print\s*\(.*\)", code, re.S))
    return ";" in code or "{" in code


def _last_topic(history: list) -> dict | None:
    for h_ in reversed(history or []):
        if isinstance(h_, dict) and h_.get("topic"):
            return h_
    return None


def _tools_dispatch(st: _State, q: str, history: list, intent: dict, resp: dict) -> bool:
    """Komendy (/uruchom…), prośby o prostsze wyjaśnienie i wklejony kod do uruchomienia. True = obsłużone."""
    cmd = AT.parse_command(q)
    if cmd:
        _answer_command(st, cmd[0], cmd[1], history, resp)
        return True
    mode = AT.clarify_mode(q)
    if mode and _last_topic(history):
        _answer_clarify(st, mode, history, resp)
        return True
    if intent["id"] == "compiler":
        return False
    text, code, stdin = AT.split_message(q)
    if not code:
        return False
    multi = "\n" in code.strip()
    if not multi and not _code_ok_for_run(code):
        return False
    name = AT.natural_command(text) if text else None
    if name is None and not AT.has_runnable_code(code):
        return False                                  # np. sama funkcja bez wypisywania → dotychczasowa analiza kodu
    _answer_code_tools(st, text, code, stdin, name, resp)
    return True


_INPUT_RX = re.compile(r"\bcin\s*>>|\bscanf\s*\(|\bgetline\s*\(|\binput\s*\(|sys\.stdin|\bgetchar\s*\(")


def _answer_code_tools(st: _State, text: str, code: str, stdin: str, name: str | None, resp: dict) -> None:
    lang = AT.detect_lang(code)
    blocks: list[dict] = []
    actions: list[dict] = []
    ran = None
    if name and name not in ("uruchom",):
        tb, ta = AT.tool_blocks(name, text, code, "python" if lang == "python" else "cpp")
        blocks += tb
        actions += ta
    if name in (None, "uruchom", "sprawdz") and AT.has_runnable_code(code):
        ran = AT.run_code(code, stdin, lang)
        rb = AT.run_block(ran, "Uruchomiłem Twój kod" if name != "sprawdz" else "Uruchomienie")
        if name == "sprawdz":
            blocks.append(rb)
        else:
            blocks.insert(0, rb)
        if ran.get("stage") == "run" and not stdin and _INPUT_RX.search(code):
            blocks.append({"type": "notice", "tone": "info",
                           "md": "Program wczytuje dane, a nie podałeś wejścia — uruchomiłem go z pustym wejściem. "
                                 "Dopisz pod kodem linię `wejście:` i dane, np.\n```\nwejście: 3 1 2 3\n```"})
        for h_ in AT.output_hints(code, ran, lambda x: x in st.articles):
            aid = h_.get("article")
            blocks.append({"type": "notice", "tone": "info",
                           "md": h_["md"] + (f" Więcej: [{st.articles[aid]['title']}](kb:{aid})" if aid else "")})
            if aid:
                resp["see_also"].append(_article_link(st.articles[aid]))
    elif name is None:
        _answer_code(st, (text + "\n" + code) if text else code, resp)
        return
    if AT.FULL and name is None and lang == "cpp":
        full_src, was_wrapped = AT.wrap_cpp(code)
        fix_line = AT._line_mapper(full_src, code) if was_wrapped else (lambda x: x)
        r = AT.call_tool("checker", "POST /api/checker/analyze", {"code": full_src}) or {}
        issues = [i for i in (r.get("issues") or []) if i.get("severity") in ("error", "warning")][:3]
        if issues:
            blocks.append({"type": "text", "md": "Przy okazji **Sprawdzarka** zauważyła:"})
            blocks.append({"type": "errors", "source": "checker", "items": [
                {"line": fix_line(i.get("line")), "col": i.get("col"), "severity": i.get("severity"), "title": i.get("title"),
                 "explanation": i.get("message"), "fix": i.get("fix")} for i in issues]})
    if name is None and code.count("\n") >= 3:
        # dłuższy program: dodatkowo rozpoznane techniki (jak przy zwykłym wklejeniu kodu)
        tmp = {"see_also": [], "blocks": []}
        try:
            _answer_code(st, code, tmp)
        except Exception:  # noqa: BLE001
            tmp = {"see_also": [], "blocks": []}
        blocks += [b for b in tmp["blocks"] if b.get("type") == "text" and "Rozpoznane techniki" in (b.get("md") or "")]
        resp["see_also"] += tmp.get("see_also") or []
    if text and name in (None, "uruchom") and len(T.terms(text)) >= 3 and not AT.natural_command(text):
        res = retrieve(st, text, intent="howto")
        if res["units"] and res["articles"]:
            comp = compose(st, res, "howto", text, 0.6)
            blocks.append({"type": "notice", "tone": "info", "md": f"Twoje pytanie: *{text}*"})
            blocks += comp["blocks"][:3]
            resp["sources"] = _sources(res, T.terms(text), k=3)
    if not blocks:
        blocks.append({"type": "notice", "tone": "warn", "md": "Nie udało się użyć tego narzędzia na tym kodzie."})
    resp["blocks"] = blocks
    resp["intent"] = {"id": "run" if name in (None, "uruchom") else "tool",
                      "label": INTENT_LABELS["run"] if name in (None, "uruchom") else
                      (AT.COMMANDS.get(name, ("", "Narzędzie"))[1].capitalize() if name else "Narzędzie")}
    resp["confidence"] = 0.95 if ran and ran.get("stage") == "run" else 0.8
    resp["confidence_label"] = "wysoka"
    if AT.FULL and lang == "cpp":
        actions += [{"label": "Sprawdzarka", "icon": "bug", "view": "checker", "params": {"code": code}},
                    {"label": "Złożoność", "icon": "gauge", "view": "complexity", "params": {"code": code}}]
    actions.append({"label": "Otwórz w edytorze", "icon": "file-code", "view": "workspace",
                    "params": {"code": ran.get("source") if ran and ran.get("wrapped") else code,
                               "name": "czat.py" if lang == "python" else "czat.cpp"}})
    seen = set()
    resp["actions"] = [a for a in actions if not (a["label"] in seen or seen.add(a["label"]))][:5]
    resp["suggestions"] = (["/sprawdz", "/zlozonosc", "/wyjasnij"] if AT.FULL and lang == "cpp" else []) + \
        ["Co to kody ASCII?", "/pomoc"]
    if _personal():
        try:
            AT.note_question(text or "", None, code_lang=lang)
        except Exception:  # noqa: BLE001
            pass


def _answer_command(st: _State, name: str, arg: str, history: list, resp: dict) -> None:
    resp["intent"] = {"id": "command", "label": INTENT_LABELS["command"]}
    resp["confidence"] = 1.0
    resp["confidence_label"] = "wysoka"
    if name == "?":
        resp["blocks"] = [{"type": "notice", "tone": "warn", "md": f"Nie znam komendy `/{arg}`."}] + _help_blocks()
        return
    if name == "full-only":
        resp["blocks"] = [{"type": "notice", "tone": "info",
                           "md": f"Komenda `/{arg}` ({AT.COMMANDS[arg][1]}) jest w pełnym OI Forge. Tutaj mogę kod uruchomić: `/uruchom`."}] + _help_blocks()
        return
    if name == "pomoc":
        resp["blocks"] = _help_blocks()
        resp["suggestions"] = ["/uruchom cout << 'a' + 1;", "Co to kody ASCII?", "nie rozumiem"]
        return
    if name == "clear":
        resp["clear"] = True
        resp["blocks"] = [{"type": "notice", "tone": "success", "md": "Wyczyściłem rozmowę i kontekst — zaczynamy od nowa."}]
        return
    if name == "programy":
        progs = _programs(arg, k=8, strict=False) if arg else []
        resp["programs"] = progs
        resp["blocks"] = [{"type": "text", "md": (f"Twoje programy pasujące do „{arg}”:" if progs else
                                                 f"Nie znalazłem w Twoich programach nic dla „{arg}”.") if arg else
                           "Napisz, czego szukać, np. `/programy dijkstra`."}]
        resp["actions"] = [{"label": "Otwórz Moje programy", "icon": "folder", "view": "programs", "params": {"q": arg}}]
        return
    if name == "docs":
        hit = resolve_topic(st, arg, min_cover=0.5) if arg else None
        if not hit and arg:
            res = retrieve(st, arg, intent="define")
            hit = (res["articles"][0][1], 1.0) if res["articles"] else None
        a = st.articles.get(hit[0]) if hit else None
        if not a:
            resp["blocks"] = [{"type": "notice", "tone": "warn", "md": f"Nie znalazłem tematu „{arg}”."}]
            return
        resp["topic"] = {"id": a["id"], "title": a.get("title"), "name": short_name(a), "category": a.get("category")}
        resp["blocks"] = [{"type": "text", "md": f"**{a.get('title')}** — {a.get('summary') or ''}"}]
        resp["actions"] = [{"label": "Otwórz w dokumentacji", "icon": "book-open", "view": "docs", "params": {"id": a["id"]}}]
        return
    needs = AT.COMMANDS[name][2]
    if needs == "code":
        text, code, stdin = AT.split_message(arg)
        if not code and arg.strip():
            code, text = arg.strip(), ""
        if not code:
            resp["blocks"] = [{"type": "notice", "tone": "info", "md": f"Wklej kod po komendzie, np.\n```\n/{name} cout << 'a' + 1;\n```"}]
            return
        _answer_code_tools(st, text, code, stdin, name, resp)
        return
    if not arg.strip():
        resp["blocks"] = [{"type": "notice", "tone": "info", "md": f"Wklej tekst po komendzie: `/{name} …` ({AT.COMMANDS[name][1]})."}]
        return
    blocks, actions = AT.tool_blocks(name, arg, "", "cpp")
    resp["blocks"] = blocks or [{"type": "notice", "tone": "warn", "md": "To narzędzie nie zwróciło wyniku."}]
    resp["actions"] = actions
    resp["intent"] = {"id": "tool", "label": AT.COMMANDS[name][1].capitalize()}


def _help_blocks() -> list[dict]:
    rows = [[f"`/{c['name']}`", c["desc"], ", ".join(f"`/{a}`" for a in c["aliases"][:3])] for c in AT.available_commands()]
    return [{"type": "text", "md": "**Komendy asystenta** (wpisz na początku wiadomości):"},
            {"type": "table", "headers": ["Komenda", "Co robi", "Inaczej"], "rows": rows},
            {"type": "tip", "md": "Kod możesz też po prostu wkleić albo napisać *„co wypisze cout << 'a' + 1;”* — uruchomię go "
                                  "w piaskownicy. Dane dla programu podaj w linii `wejście: …`. Gdy coś jest niejasne, "
                                  "napisz **„nie rozumiem”** — wytłumaczę prościej."}]


def _answer_clarify(st: _State, mode: str, history: list, resp: dict) -> None:
    prev = _last_topic(history) or {}
    aid = prev.get("topic")
    if isinstance(aid, dict):
        aid = aid.get("id")
    a = st.articles.get(aid)
    resp["intent"] = {"id": "clarify", "label": INTENT_LABELS["clarify"]}
    if not a:
        resp["blocks"] = [{"type": "notice", "tone": "info", "md": "Napisz proszę, czego dokładnie nie rozumiesz — np. nazwę "
                                                                  "funkcji albo tematu — a wytłumaczę to prościej."}]
        return
    level = int(prev.get("clarify") or 0) + 1
    if _personal():
        AT.note_confused(a["id"])
    sn = short_name(a)
    used = {prev.get("uid")} | set(prev.get("used") or [])
    intro = {"example": f"Przykład do tematu: **{sn}**.", "steps": f"Po kolei — temat: **{sn}**."}.get(
        mode, f"Spróbuję {'jeszcze ' if level > 1 else ''}prościej — temat: **{sn}**.")
    blocks = [{"type": "notice", "tone": "info", "md": intro}]
    analogy = AT.ANALOGIES.get(a["id"])
    summary = _first_sentence(_plain(a.get("summary") or ""), 240)
    m_uid = re.match(r"^q:[\w-]+:(\d+)$", str(prev.get("uid") or ""))
    examples_ = AT.code_examples(a, first_qa=int(m_uid.group(1)) if m_uid else None)
    shown_examples = int(prev.get("examples") or 0)

    def add_example(idx: int, label: str) -> int:
        if not examples_:
            return 0
        title, code, lang = examples_[idx % len(examples_)]
        blocks.append({"type": "text", "md": f"**{label}** ({title}):"})
        blocks.append({"type": "code", "lang": "python" if lang in ("python", "py") else "cpp", "code": code})
        if AT.has_runnable_code(code) and _code_ok_for_run(code):
            ran = AT.run_code(code, "", "python" if lang in ("python", "py") else "cpp")
            if ran.get("stage") == "run" and ran.get("ok"):
                out = (ran.get("stdout") or "").strip()
                blocks.append({"type": "run", "title": "Uruchomiłem ten przykład", **{k: ran.get(k) for k in (
                    "lang", "ok", "stage", "verdict", "stdout", "stderr", "time", "wrapped", "sandbox")}})
                if out and len(out) < 200:
                    blocks.append({"type": "text", "md": f"Czyli ten kod wypisuje `{out.splitlines()[0][:80]}`"
                                                        + (" (i dalej kolejne linie)." if "\n" in out else ".")})
        return 1

    if mode == "example":
        resp["examples"] = shown_examples + add_example(shown_examples, "Przykład" if not shown_examples else "Inny przykład")
        if not examples_:
            blocks.append({"type": "text", "md": f"Do tego tematu nie mam przykładu kodu. W skrócie: {summary}"})
    elif mode == "steps":
        steps = AT.simple_steps(a, k=7)
        if steps:
            blocks.append({"type": "text", "md": "**Krok po kroku:**\n" + "\n".join(f"{i + 1}. {s_}" for i, s_ in enumerate(steps))})
        if AT.FULL and examples_ and examples_[0][2] not in ("python", "py"):
            tb, _ = AT.tool_blocks("wyjasnij", "", examples_[0][1], "cpp")
            if tb:
                blocks.append({"type": "text", "md": "Przykładowy kod, linia po linii:"})
                blocks.append({"type": "code", "lang": "cpp", "code": examples_[0][1]})
                blocks += [b for b in tb if b.get("type") == "text"][1:2]
        resp["examples"] = shown_examples
    elif level == 1:
        if analogy:
            blocks.append({"type": "text", "md": f"**Na chłopski rozum:** {analogy}"})
        if summary:
            blocks.append({"type": "text", "md": f"**W jednym zdaniu:** {summary}"})
        steps = AT.simple_steps(a, k=4)
        if steps:
            blocks.append({"type": "text", "md": "**Po kolei:**\n" + "\n".join(f"{i + 1}. {s_}" for i, s_ in enumerate(steps))})
        resp["examples"] = shown_examples + add_example(shown_examples, "Najprostszy przykład")
    elif level == 2:
        qas = [(i, qa) for i, qa in enumerate(a.get("qa") or []) if f"q:{a['id']}:{i}" not in used and qa.get("a")]
        qas.sort(key=lambda x: len(x[1]["a"]))
        for _, qa in qas[:2]:
            blocks.append({"type": "text", "md": f"**{_cap((qa.get('q') or [''])[0])}?** {qa['a']}"})
        lv = a.get("level") or 2
        pre = [st.articles[r] for r in (a.get("related") or []) if r in st.articles and (st.articles[r].get("level") or 2) < lv][:3]
        if pre:
            blocks.append({"type": "text", "md": "Może pomoże najpierw przejrzeć prostsze tematy:"})
            blocks.append({"type": "topics", "items": [_article_link(x) for x in pre]})
        resp["examples"] = shown_examples + add_example(shown_examples, "Jeszcze jeden przykład")
    else:
        blocks.append({"type": "text", "md": f"Widzę, że **{sn}** sprawia kłopot — to zupełnie normalne, ten temat często "
                                            "wymaga kilku podejść. Co możesz zrobić:"})
        blocks.append({"type": "text", "md": "1. Otwórz pełny artykuł w dokumentacji i przeczytaj go od góry.\n"
                                            "2. Zapytaj o konkretny fragment, np. jedną funkcję albo linijkę kodu.\n"
                                            "3. Zobacz, jak użyłeś tego w swoich programach (poniżej, jeśli są)."})
        resp["_programs_q"] = sn
        resp["examples"] = shown_examples
    resp["blocks"] = blocks
    resp["clarify"] = level if mode == "simple" else int(prev.get("clarify") or 0)
    resp["topic"] = {"id": a["id"], "title": a.get("title"), "name": sn, "category": a.get("category")}
    resp["confidence"] = 0.9
    resp["confidence_label"] = "wysoka"
    resp["actions"] = [{"label": "Otwórz w dokumentacji", "icon": "book-open", "view": "docs", "params": {"id": a["id"]}}]
    resp["suggestions"] = ["Pokaż przykład", "Krok po kroku", "Nadal nie rozumiem", f"Kiedy używać: {sn}?"]


def _personalize(st: _State, q: str, a: dict, resp: dict) -> None:
    """Personalizacja bez AI: profil (tematy, poziom, „nie rozumiem”, język) → prostszy start, ostrzeżenie o poziomie,
    przykład w Pythonie, „w skrócie” przy długich odpowiedziach."""
    try:
        p = AT.profile()
    except Exception:  # noqa: BLE001
        return
    blocks = resp["blocks"]
    pos = 1 if blocks and blocks[0].get("type") == "notice" else 0
    text_len = sum(len(b.get("md") or "") for b in blocks if b.get("type") == "text")
    summary = _first_sentence(_plain(a.get("summary") or ""), 220)
    if p["confused"].get(a["id"]):
        analogy = AT.ANALOGIES.get(a["id"])
        md = "Ostatnio ten temat był niejasny, więc najpierw najprościej: " + (analogy or summary)
        blocks.insert(pos, {"type": "notice", "tone": "info", "md": md})
    elif text_len > 650 and summary:
        blocks.insert(pos, {"type": "text", "md": f"**W skrócie:** {summary}"})
    lv = a.get("level") or 1
    if lv >= AT.user_level(p) + 2:
        pre = [st.articles[r] for r in (a.get("related") or []) if r in st.articles and (st.articles[r].get("level") or 2) < lv][:3]
        blocks.append({"type": "notice", "tone": "info",
                       "md": f"To temat na poziomie **{LEVELS.get(lv, 'zaawansowany')}**. Jeśli coś jest niejasne, napisz "
                             "**„nie rozumiem”** — wytłumaczę prościej" + (", albo zacznij od prostszych tematów:" if pre else ".")})
        if pre:
            blocks.append({"type": "topics", "items": [_article_link(x) for x in pre]})
    if AT.prefers_python(p) and not any(b.get("type") == "code" and b.get("lang") == "python" for b in blocks):
        py = [e for e in AT.code_examples(a) if e[2] in ("python", "py")]
        if py:
            blocks.append({"type": "text", "md": "Ponieważ często piszesz w Pythonie — to samo w Pythonie:"})
            blocks.append({"type": "code", "lang": "python", "code": py[0][1]})
        elif "oi-py-cpp-equivalents" in st.articles:
            blocks.append({"type": "notice", "tone": "info",
                           "md": "Piszesz często w Pythonie — odpowiedniki z C++ znajdziesz w "
                                 "[Python ↔ C++](kb:oi-py-cpp-equivalents)."})
    try:
        AT.note_question(q, resp.get("topic"), lv)
    except Exception:  # noqa: BLE001
        pass


def handle_run(body: dict) -> dict:
    """Uruchomienie kodu z czatu (przycisk ▶ przy przykładzie) — w piaskownicy."""
    if AT is None:
        raise ApiError("Uruchamianie kodu z czatu jest niedostępne.")
    code = str(body.get("code") or "")
    if not code.strip():
        raise ApiError("Brak kodu do uruchomienia.")
    lang = body.get("lang") if body.get("lang") in ("cpp", "python") else None
    return {"run": AT.run_block(AT.run_code(code, str(body.get("input") or ""), lang))}


def handle_commands(_q: dict) -> dict:
    return {"commands": AT.available_commands() if AT is not None else [], "full": bool(AT and AT.FULL)}


# ============================================================================================ autouzupełnianie
def suggest(q: str, k: int = 8) -> list[dict]:
    """Podpowiedzi pełnych pytań/tematów dla wpisywanego tekstu (prefiksy słów, potem fuzzy)."""
    st = _ENGINE.state()
    n = T.normalize(q)
    if len(n) < 1:
        return []
    P = st.phrases
    words = n.split()
    complete, last = words[:-1], words[-1]
    vocab = P["vocab"]
    wi = P["word_index"]

    def ids_with_prefix(p: str, limit: int = 400) -> set[int]:
        import bisect
        i = bisect.bisect_left(vocab, p)
        out: set[int] = set()
        while i < len(vocab) and vocab[i].startswith(p) and len(out) < 4000:
            out |= wi[vocab[i]]
            i += 1
            if limit and len(out) > limit * 10:
                break
        return out

    cand: set[int] | None = None
    for w in complete:
        if w in T.STOPWORDS and len(complete) > 1:
            continue
        s = ids_with_prefix(w, 0) if len(w) >= 3 else wi.get(w, set())
        cand = s if cand is None else cand & s
        if not cand:
            break
    last_ids = ids_with_prefix(last)
    cand = last_ids if cand is None else cand & last_ids
    items = P["items"]
    scored = []
    for i in cand or ():
        it = items[i]
        s = it["w"]
        if it["norm"].startswith(n):
            s += 3.0
        s -= 0.02 * len(it["norm"])
        scored.append((s, i))
    scored.sort(reverse=True)
    out, seen = [], set()
    for _, i in scored:
        it = items[i]
        if it["norm"] in seen:
            continue
        seen.add(it["norm"])
        a = st.articles.get(it["aid"], {})
        out.append({"text": _cap(it["text"]) if it["kind"] == "qa" else it["text"], "kind": it["kind"], "id": it["aid"],
                    "title": a.get("title")})
        if len(out) >= k:
            break
    if len(out) < 3 and len(n) >= 3:
        for h in st.index.search(n, k=12, filters=lambda did, kind: kind in ("qa", "article")):
            u = st.units[h["id"]]
            key = T.normalize(u["title"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"text": _cap(u["title"]) if u["kind"] == "qa" else u["atitle"], "kind": u["kind"], "id": u["aid"],
                        "title": u["atitle"]})
            if len(out) >= k:
                break
    return out


# ============================================================================================ wyszukiwarka globalna
def global_search(q: str, k: int = 20) -> dict:
    st = _ENGINE.state()
    q = (q or "").strip()
    if not q:
        return {"results": [], "groups": {}}
    corr = st.index.correct(q)
    query = corr["text"] if corr["changed"] else q
    res = retrieve(st, query, intent=detect_intent(query)["id"] if len(query) < 200 else "howto", k=60)
    qterms = T.terms(query)
    results = []
    seen_art = set()
    art_score = {aid: s for s, aid in res["articles"]}
    for f, u, h in res["units"]:
        matched = h.get("matched") or qterms
        if u.get("aid") and u["aid"] not in seen_art:
            seen_art.add(u["aid"])
            a = st.articles[u["aid"]]
            results.append({"type": "article", "id": a["id"], "article_id": a["id"], "title": a.get("title"),
                            "subtitle": a.get("category"), "category": a.get("category"),
                            "snippet": S.highlight(_plain(u.get("text") or "") or a.get("summary", ""), matched, 160),
                            "score": round(art_score.get(a["id"], f), 3), "view": "docs",
                            "params": {"id": a["id"], "anchor": u.get("anchor") or ""}, "icon": "book-open"})
        if u["kind"] == "qa":
            results.append({"type": "qa", "id": u["uid"], "article_id": u["aid"], "q": u["title"], "title": _cap(u["title"]),
                            "subtitle": u["atitle"], "category": u.get("category"),
                            "snippet": S.highlight(_plain(u["text"]), matched, 160), "score": round(f * 0.95, 3),
                            "view": "assistant", "params": {"q": u["title"]}, "icon": "message-square"})
        elif u["kind"] == "snippet":
            sn = u["snippet"]
            results.append({"type": "snippet", "id": sn["trigger"], "trigger": sn["trigger"], "article_id": u.get("aid"),
                            "title": u["title"], "subtitle": f"snippet `{sn['trigger']}`", "category": "Snippet",
                            "snippet": S.highlight(sn.get("description") or "", matched, 140), "score": round(f, 3),
                            "view": "snippets", "params": {"trigger": sn["trigger"]}, "icon": "braces"})
        elif u["kind"] == "function" and h.get("score", 0) > 0:
            results.append({"type": "function", "id": u["uid"], "article_id": u["aid"], "title": u["title"],
                            "subtitle": u["atitle"], "category": u.get("category"),
                            "snippet": S.highlight(u["fn"].get("desc", ""), matched, 140), "score": round(f * 0.8, 3),
                            "view": "docs", "params": {"id": u["aid"], "anchor": "functions"}, "icon": "square-function"})
    for i, p in enumerate(_programs(query, k=4)):
        rel = p.get("relevance")
        rel = float(rel) if isinstance(rel, (int, float)) else 1.0 - 0.1 * i
        results.append({"type": "program", "id": p.get("id") or p.get("path"), "title": p.get("problem_name"),
                        "subtitle": p.get("contest_name"), "category": "Moje programy",
                        "snippet": p.get("why") or ", ".join(str(t) for t in (p.get("tags") or [])[:5]),
                        "score": round(0.55 * rel, 3), "view": "programs",
                        "params": {"path": p.get("path"), "id": p.get("id")}, "icon": "folder"})
    # różnorodność: maksymalnie po kilka wyników każdego typu
    limits = {"article": 8, "qa": 6, "snippet": 4, "function": 3, "program": 4}
    groups: dict[str, list] = defaultdict(list)
    for r in sorted(results, key=lambda r: -r["score"]):
        if len(groups[r["type"]]) < limits.get(r["type"], 4):
            groups[r["type"]].append(r)
    merged = sorted((r for g in groups.values() for r in g), key=lambda r: -r["score"])[:k]
    return {"results": merged, "groups": dict(groups),
            "did_you_mean": {"text": corr["text"], "fixes": corr["fixes"]} if corr["changed"] else None}


# ============================================================================================ przykłady i status
_EXAMPLES = ["Jak użyć seta?", "Dijkstra kod", "Jak zaokrąglić do 2 miejsc po przecinku?", "multiset vs set",
             "Co to lazy propagation?", "Jak wczytać do końca pliku?", "long long czy int", "Sort malejąco",
             "Zaokrąglenie w górę dzielenia", "Segfault co to?", "Vector usuń element", "Złożoność drzewa przedziałowego"]


def examples(k: int = 8) -> list[dict]:
    """Przykładowe pytania na pusty stan czatu: kilka stałych + losowe pierwsze warianty QA z różnych kategorii."""
    st = _ENGINE.state(train=False)
    rng = random.Random(int(time.time() // 3600))
    out = [{"text": t, "category": "Popularne"} for t in rng.sample(_EXAMPLES, min(4, len(_EXAMPLES)))]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for u in st.units_list:
        if u["kind"] == "qa" and u.get("qa_index", 9) < 2:
            by_cat[u["category"]].append(u)
    cats = list(by_cat)
    rng.shuffle(cats)
    for c in cats:
        if len(out) >= k:
            break
        u = rng.choice(by_cat[c])
        out.append({"text": _cap(u["title"]), "category": c, "id": u["aid"]})
    return out


def status() -> dict:
    st = _ENGINE.state()
    kinds: dict[str, int] = defaultdict(int)
    for u in st.units_list:
        kinds[u["kind"]] += 1
    return {"articles": len(st.articles), "units": len(st.units_list), "kinds": dict(kinds), "build_ms": st.build_ms,
            "nn": _ENGINE.nn_status(), "terms": len(st.index.vocabulary()), "graph_terms": len(st.graph.neighbors)}


# ============================================================================================ HTTP
def handle_ask(body: dict) -> dict:
    hist = body.get("history") or []
    if not isinstance(hist, list):
        hist = []
    q = str(body.get("q") or "")
    _tls.personal = True
    try:
        res = ask(q, hist[-6:])
    finally:
        _tls.personal = False
    try:  # dziennik postępów (moduł journal, jeśli jest)
        from oiforge.tools import journal
        journal.log_event("assistant_q", {"q": q[:200], "topic": (res or {}).get("topic")})
    except Exception:  # noqa: BLE001
        pass
    return res


def handle_suggest(q: dict) -> dict:
    try:
        k = max(1, min(20, int(q.get("k") or 8)))
    except ValueError:
        k = 8
    return {"suggestions": suggest(q.get("q") or "", k=k)}


def handle_global(q: dict) -> dict:
    try:
        k = max(1, min(50, int(q.get("k") or 20)))
    except ValueError:
        k = 20
    return global_search(q.get("q") or "", k=k)


def handle_status(_q: dict) -> dict:
    return status()


def handle_examples(q: dict) -> dict:
    return {"examples": examples(int(q.get("k") or 8) if str(q.get("k") or "8").isdigit() else 8)}


def handle_programs(q: dict) -> dict:
    """„Twoje rozwiązania” dla pytania — osobno, żeby nie spowalniać samej odpowiedzi."""
    return {"programs": _programs(str(q.get("q") or "")[:300])}


ROUTES = {
    "POST /api/assistant/ask": handle_ask,
    "GET /api/assistant/programs": handle_programs,
    "GET /api/assistant/suggest": handle_suggest,
    "GET /api/assistant/status": handle_status,
    "GET /api/assistant/examples": handle_examples,
    "GET /api/search/global": handle_global,
    "POST /api/assistant/run": handle_run,
    "GET /api/assistant/commands": handle_commands,
}


def _warmup() -> None:
    """Buduje indeks i rozgrzewa pamięci podręczne (słownik korekty pisowni, ekspansja synonimów, indeks programów
    ucznia), żeby już pierwsze pytanie w aplikacji dostało odpowiedź w ułamku sekundy."""
    try:
        _ENGINE.state()
        for q in ("jak posortować vector par po drugim elemencie", "co to jest kopiec", "dijkstra kod"):
            ask(q)
        _programs("dijkstra")
    except Exception:  # noqa: BLE001
        pass


# Indeks i sieć budują się w tle zaraz po imporcie modułu (przy starcie serwera), żeby pierwsze pytanie było szybkie.
if not os.environ.get("OIFORGE_NO_WARMUP"):
    threading.Thread(target=_warmup, name="oiforge-assistant-warmup", daemon=True).start()
