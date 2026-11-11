"""Silnik wyszukiwania pełnotekstowego OI Forge (czysty Python, bez zależności).

Składniki:
- **BM25F** — ranking nad dokumentami z wieloma polami o różnych wagach (title, aliases, body, code…),
- **ekspansja zapytania** — synonimy z `data/synonyms.json` (frazy PL/EN/skróty/potoczne/literówki),
- **morfologia** — `root()` skleja odmiany polskie i angielskie, których lekki stemmer nie łączy
  (`seta`/`secie`/`set`, `vectorze`/`vector`, `mapie`/`map`, `kolejce`/`kolejka`),
- **tolerancja literówek** — trigramy znaków + Levenshtein (z transpozycją) nad słownikiem indeksu,
- **prefiksy** — `przedzial` znajduje `przedzialow…`,
- **korekta pisowni** — `SearchIndex.correct()` („czy chodziło Ci o…”) ze słownika słów indeksu i synonimów,
- **skojarzenia termów** — `TermGraph` (NPMI ze współwystępowania) do ostrożnej ekspansji zapytań.

Publiczne API jest stabilne (docs/CONTRACT.md 3.4): `SearchIndex(fields)`, `.add()`, `.build()`,
`.search(query, k, filters)`, `expand_query(q)`, `highlight(text, terms)`. Nowe elementy są dodatkami
(argumenty wyłącznie nazwane, nowe metody), więc stary kod działa bez zmian.
"""
from __future__ import annotations

import bisect
import functools
import json
import math
import re
import threading
from collections import Counter, defaultdict
from typing import Iterable

from .. import config
from . import text as T

SYN_PATH = config.DATA_DIR / "synonyms.json"
_syn_lock = threading.Lock()
_syn_cache: dict = {"mtime": None, "groups": [], "phrase_index": {}, "words": {}, "word_tri": {}, "nwords": {}}

# Wagi źródeł dopasowania termów (mnożone przez wagę termu zapytania).
W_SYNONYM = 0.6
W_ROOT = 0.85
W_PREFIX = 0.55
W_FUZZY1 = 0.75
W_FUZZY2 = 0.5
# Jaka część „nadmiarowych” dopasowań tego samego słowa pytania (synonimy/odmiany ponad najlepsze) się liczy.
SYNONYM_SPILL = 0.3


# ============================================================================================ morfologia
_ROOT_SUFFIXES = ("owego", "owej", "owym", "owych", "owemu", "ami", "ach", "owa", "owe", "owy", "ow", "ego", "emu",
                  "ej", "ym", "em", "om", "ie", "ia", "iu", "a", "e", "i", "o", "u", "y")


def root(term: str) -> str:
    """Agresywniejszy „rdzeń” termu (po `text.stem`) do sklejania odmian.

    Działa na termach już znormalizowanych/stemowanych. Zasady są celowo proste i *spójne* — ważne jest,
    by różne formy tego samego słowa dawały ten sam rdzeń, a nie poprawność językowa:
    `seta → set`, `seci(e) → set`, `vectorz(e) → vector`, `mapi(e) → map`, `kolejc(e) → kolejk`,
    `przedzialow(ego) → przedzial`, `stringi → string`.
    """
    t = term
    if len(t) <= 3 or not t.isascii() or not t.isalpha():
        return t
    if len(t) <= 5 and t.endswith("ci"):          # secie → seci → set ; kocie → koci → kot
        return t[:-2] + "t"
    for suf in _ROOT_SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            t = t[: -len(suf)]
            break
    if len(t) >= 5 and t.endswith("rz"):           # vectorze → vectorz → vector ; wektorze → wektor
        t = t[:-1]
    elif len(t) >= 4 and t.endswith("dz"):         # kodzie → kodz → kod
        t = t[:-1]
    elif len(t) >= 5 and t.endswith("c") and t[-2] not in "aeiouy":   # kolejce → kolejc → kolejk
        t = t[:-1] + "k"
    return t


# ============================================================================================ synonimy
def _load_synonyms() -> tuple[list[list[str]], dict[str, list[int]]]:
    """(grupy znormalizowanych fraz, indeks: klucz-rdzeni-frazy → [nr grup]). Przeładowuje się po zmianie pliku."""
    try:
        mt = SYN_PATH.stat().st_mtime_ns
    except OSError:
        return [], {}
    with _syn_lock:
        if _syn_cache["mtime"] == mt:
            return _syn_cache["groups"], _syn_cache["phrase_index"]
        try:
            raw = json.loads(SYN_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        groups = raw.get("groups", []) if isinstance(raw, dict) else raw
        norm_groups, index = [], defaultdict(list)
        words: Counter = Counter()
        nwords: dict[str, int] = {}
        word_groups: dict[str, set[int]] = defaultdict(set)
        canon: set[str] = set()
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, list):
                continue
            ng = []
            gi_next = len(norm_groups)
            for pos, x in enumerate(g):
                if not isinstance(x, str):
                    continue
                ph = " ".join(T.terms(x, drop_stopwords=False))
                if ph and ph not in ng:
                    ng.append(ph)
                    # liczba „prawdziwych” słów frazy (std::set = 1 słowo, choć daje 3 termy)
                    nwords[ph] = min(nwords.get(ph, 99), max(1, len([w for w in x.split()
                                                                       if T.normalize(w) not in T.STOPWORDS])))
                for w in T.tokenize(x):
                    if len(w) >= 3:
                        words[w] += 1
                        word_groups[w].add(gi_next)
                        if pos == 0:
                            canon.add(w)
            if len(ng) < 2:
                continue
            gi = len(norm_groups)
            norm_groups.append(ng)
            for ph in ng:
                key = _phrase_key(ph)
                if gi not in index[key]:
                    index[key].append(gi)
        tri: dict[str, set[str]] = defaultdict(set)
        for w in words:
            if len(w) >= 4 and w.isascii():
                for gram in T.char_ngrams(w, 3):
                    tri[gram].add(w)
        _syn_cache.update(mtime=mt, groups=norm_groups, phrase_index=dict(index), words=dict(words),
                          word_tri=dict(tri), nwords=nwords, word_groups=dict(word_groups), canon=canon)
        return norm_groups, _syn_cache["phrase_index"]


def _phrase_key(phrase: str) -> str:
    return " ".join(root(t) for t in phrase.split())


def synonym_groups() -> list[list[str]]:
    """Znormalizowane grupy synonimów (listy fraz stemów)."""
    return _load_synonyms()[0]


def synonym_words() -> dict[str, int]:
    """Słowa (znormalizowane, bez stemowania) występujące w synonimach — do korekty pisowni."""
    _load_synonyms()
    return _syn_cache["words"]


def _synonym_word_groups() -> tuple[dict[str, set[int]], set[str]]:
    """(słowo → numery grup, słowa z pierwszej — kanonicznej — frazy każdej grupy)."""
    _load_synonyms()
    return _syn_cache.get("word_groups", {}), _syn_cache.get("canon", set())


def synonyms_of(phrase: str) -> list[str]:
    """Wszystkie frazy z grup, do których należy `phrase` (bez niej samej)."""
    groups, index = _load_synonyms()
    key = _phrase_key(" ".join(T.terms(phrase, drop_stopwords=False)))
    out: list[str] = []
    for gi in index.get(key, []):
        for ph in groups[gi]:
            if _phrase_key(ph) != key and ph not in out:
                out.append(ph)
    return out


def _expand(query: str, max_phrase: int = 4) -> tuple[tuple[str, float, tuple[str, ...]], ...]:
    """Jak `expand_query`, ale z „pochodzeniem” termu: krotką termów zapytania, które go wygenerowały.
    Wynik jest cache'owany (to samo zapytanie bywa rozwijane kilka razy na jedno pytanie)."""
    _load_synonyms()
    return _expand_cached(query, max_phrase, _syn_cache["mtime"])


@functools.lru_cache(maxsize=2048)
def _expand_cached(query: str, max_phrase: int, _mtime) -> tuple[tuple[str, float, tuple[str, ...]], ...]:
    qterms = T.terms(query)
    if not qterms:
        qterms = T.terms(query, drop_stopwords=False)
    out: dict[str, tuple[float, tuple[str, ...]]] = {}

    def put(t: str, w: float, origin: tuple[str, ...]):
        if t not in out or out[t][0] < w:
            out[t] = (w, origin)

    for t in qterms:
        put(t, 1.0, (t,))
    groups, index = _load_synonyms()
    nwords = _syn_cache.get("nwords", {})
    if groups:
        allterms = T.terms(query, drop_stopwords=False)
        qset = set(qterms)
        seen_groups: set[int] = set()
        for n in range(min(max_phrase, len(allterms)), 0, -1):
            for i in range(len(allterms) - n + 1):
                span = allterms[i:i + n]
                origin = tuple(x for x in span if x in qset) or tuple(span)
                if n == 1 and span[0] in T.STOPWORDS:
                    continue
                key = " ".join(root(x) for x in span)
                for gi in index.get(key, []):
                    if gi in seen_groups:
                        continue
                    seen_groups.add(gi)
                    for other in groups[gi]:
                        if _phrase_key(other) == key:
                            continue
                        words = [x for x in other.split() if x not in T.STOPWORDS]
                        if not words:
                            continue
                        # wielowyrazowa fraza „rozkłada” wagę — pojedyncze ogólne słowo nie może przeważyć
                        nw = nwords.get(other, len(words))
                        w = max(W_SYNONYM / math.sqrt(nw), 0.36) if nw > 1 else W_SYNONYM
                        for t in words:
                            put(t, w, origin)
    return tuple((t, w, o) for t, (w, o) in sorted(out.items(), key=lambda kv: -kv[1][0]))


def expand_query(query: str, max_phrase: int = 4) -> list[tuple[str, float]]:
    """Termy zapytania z wagami: oryginalne (1.0) + synonimy (≤ 0.6). Rozpoznaje frazy do `max_phrase` słów
    (także w odmianie: „w secie” trafia w grupę „set”)."""
    return [(t, w) for t, w, _ in _expand(query, max_phrase)]


def _transposed(a: str, b: str) -> bool:
    """Czy słowa różnią się dokładnie jednym przestawieniem sąsiednich liter (odległość Damerau = 1)."""
    if len(a) != len(b) or a == b:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def _cover_terms(query: str) -> set[str]:
    """Słowa pytania, których pokrycie się liczy. Pojedyncze litery („n na m”, „a do potęgi b”) to zwykle nazwy
    zmiennych, a nie treść pytania — pomijamy je, chyba że nic innego nie zostało."""
    ts = set(T.terms(query)) or set(T.terms(query, drop_stopwords=False))
    long_ = {t for t in ts if len(t) > 1 or t.isdigit()}
    return long_ or ts


# ============================================================================================ skojarzenia termów
class TermGraph:
    """Skojarzenia termów z ich współwystępowania w kontekstach (NPMI).

    `fit([[term, ...], ...])` — każdy kontekst to zbiór termów (np. wszystkie warianty jednego pytania QA
    albo tytuł + aliasy + tagi artykułu). `related(t)` zwraca termy o wysokim znormalizowanym PMI —
    używane do ostrożnej ekspansji zapytania (np. `zaokragl` ↔ `setprecision`, `przecink`).
    """

    def __init__(self, min_count: int = 2, min_npmi: float = 0.35, max_neighbors: int = 8,
                 max_df_ratio: float = 0.08, max_context_terms: int = 40):
        self.min_count = min_count
        self.min_npmi = min_npmi
        self.max_neighbors = max_neighbors
        self.max_df_ratio = max_df_ratio
        self.max_context_terms = max_context_terms
        self.n_contexts = 0
        self.df: Counter = Counter()
        self.neighbors: dict[str, list[tuple[str, float]]] = {}

    def fit(self, contexts: Iterable[Iterable[str]]) -> "TermGraph":
        ctxs = []
        for c in contexts:
            s = {t for t in c if t and len(t) >= 2 and t not in T.STOPWORDS and not t.isdigit()}
            if s:
                ctxs.append(s)
        n = len(ctxs)
        self.n_contexts = n
        df: Counter = Counter()
        for s in ctxs:
            df.update(s)
        self.df = df
        if n < 5:
            self.neighbors = {}
            return self
        max_df = max(3, int(self.max_df_ratio * n))
        pair: Counter = Counter()
        for s in ctxs:
            terms_ = sorted(t for t in s if self.min_count <= df[t] <= max_df)
            if len(terms_) > self.max_context_terms:
                terms_ = sorted(terms_, key=lambda t: df[t])[: self.max_context_terms]
                terms_.sort()
            for i in range(len(terms_)):
                a = terms_[i]
                for j in range(i + 1, len(terms_)):
                    pair[(a, terms_[j])] += 1
        nb: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for (a, b), c in pair.items():
            if c < self.min_count:
                continue
            p_ab = c / n
            pmi = math.log(p_ab / ((df[a] / n) * (df[b] / n)))
            npmi = pmi / -math.log(p_ab) if p_ab < 1 else 1.0
            # tłumienie przypadkowych par z małą liczbą wystąpień
            npmi *= min(1.0, c / 3.0) ** 0.5
            if npmi >= self.min_npmi:
                nb[a].append((b, npmi))
                nb[b].append((a, npmi))
        self.neighbors = {t: sorted(v, key=lambda x: -x[1])[: self.max_neighbors] for t, v in nb.items()}
        return self

    def related(self, term: str, k: int = 6) -> list[tuple[str, float]]:
        return self.neighbors.get(term, [])[:k]

    def expand(self, terms_: Iterable[str], weight: float = 0.3, k: int = 3,
               exclude: Iterable[str] = ()) -> list[tuple[str, float]]:
        """Dodatkowe termy (term, waga ≤ weight) dla listy termów zapytania."""
        terms_ = list(terms_)
        skip = set(terms_) | set(exclude)
        out: dict[str, float] = {}
        for t in terms_:
            for r, s in self.related(t, k):
                if r in skip:
                    continue
                out[r] = max(out.get(r, 0.0), weight * s)
        return sorted(out.items(), key=lambda kv: -kv[1])


# ============================================================================================ indeks BM25F
class SearchIndex:
    """Indeks BM25F nad dokumentami z wieloma polami (np. title, aliases, body, code).

    >>> idx = SearchIndex({"title": 3.0, "body": 1.0})
    >>> idx.add("a", {"title": "std::set", "body": "zbiór uporządkowany"}); idx.build()
    >>> idx.search("zbior")[0]["id"]
    'a'
    """

    def __init__(self, fields: dict[str, float] | None = None, k1: float = 1.3, b: float = 0.72):
        self.fields = fields or {"title": 3.0, "body": 1.0}
        self.k1, self.b = k1, b
        self.docs: dict[str, dict] = {}
        self.payloads: dict[str, object] = {}
        self.raw: dict[str, dict] = {}
        self._postings: dict[str, dict[str, dict[str, int]]] = {}
        self._pre: dict[str, dict[str, float]] = {}
        self._lens: dict[str, dict[str, int]] = {}
        self._avg: dict[str, float] = {}
        self._idf: dict[str, float] = {}
        self._trigrams: dict[str, set[str]] = {}
        self._vocab_sorted: list[str] = []
        self._roots: dict[str, list[str]] = {}
        self._top_norm: dict[str, str] = {}
        self._surface: Counter | None = None
        self._cands: Counter = Counter()
        self._display: dict[str, str] = {}
        self._surface_tri: dict[str, set[str]] = {}
        self._built = False

    # ------------------------------------------------------------------ budowa
    def add(self, doc_id: str, fields: dict[str, str], payload=None) -> None:
        self.raw[doc_id] = {f: (fields.get(f) or "") for f in self.fields}
        self.payloads[doc_id] = payload
        self._built = False

    def __len__(self) -> int:
        return len(self.raw)

    def __contains__(self, doc_id: str) -> bool:
        return doc_id in self.raw

    def build(self) -> "SearchIndex":
        postings: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
        lens: dict[str, dict[str, int]] = {}
        tot: dict[str, int] = defaultdict(int)
        memo: dict[str, list[str]] = {}            # te same teksty (np. kontekst artykułu) tokenizujemy raz
        for did, fields in self.raw.items():
            lens[did] = {}
            for f, txt in fields.items():
                ts = memo.get(txt)
                if ts is None:
                    ts = T.terms(txt)
                    if len(txt) < 4000:
                        memo[txt] = ts
                lens[did][f] = len(ts)
                tot[f] += len(ts)
                for t, c in Counter(ts).items():
                    postings[t][did][f] = c
        n = max(1, len(self.raw))
        self._avg = {f: max(1.0, tot[f] / n) for f in self.fields}
        self._postings = {t: dict(d) for t, d in postings.items()}
        self._lens = lens
        self._idf = {t: math.log(1 + (n - len(d) + 0.5) / (len(d) + 0.5)) for t, d in self._postings.items()}
        # Wkład BM25F (idf · nasycone tf) jest stały dla pary (term, dokument) — liczymy go raz przy budowie,
        # wtedy szukanie to tylko sumowanie wag (kilkukrotnie szybciej).
        k1, b, fw, avg = self.k1, self.b, self.fields, self._avg
        pre: dict[str, dict[str, float]] = {}
        for t, docs in self._postings.items():
            idf = self._idf[t]
            row = {}
            for did, fcounts in docs.items():
                L = lens[did]
                tf = 0.0
                for f, c in fcounts.items():
                    tf += fw[f] * c / (1 - b + b * L.get(f, 0) / avg[f])
                row[did] = idf * tf * (k1 + 1) / (tf + k1)
            pre[t] = row
        self._pre = pre
        tri: dict[str, set[str]] = defaultdict(set)
        roots: dict[str, list[str]] = defaultdict(list)
        for t in self._postings:
            if len(t) >= 3 and t.isascii():
                for g in T.char_ngrams(t, 3):
                    tri[g].add(t)
            roots[root(t)].append(t)
        self._trigrams = dict(tri)
        self._roots = {r: v for r, v in roots.items() if len(v) > 1 or v[0] != r}
        self._vocab_sorted = sorted(self._postings)
        top_field = max(self.fields, key=self.fields.get)
        self._top_norm = {did: T.normalize(f.get(top_field, "")) for did, f in self.raw.items()}
        self._surface = None
        self._built = True
        return self

    def _ensure(self) -> None:
        if not self._built:
            self.build()

    # ------------------------------------------------------------------ statystyki
    def has_term(self, term: str) -> bool:
        self._ensure()
        return term in self._postings

    def df(self, term: str) -> int:
        """Liczba dokumentów zawierających term (stem)."""
        self._ensure()
        return len(self._postings.get(term, ()))

    def idf(self, term: str) -> float:
        self._ensure()
        return self._idf.get(term, 0.0)

    def vocabulary(self) -> list[str]:
        """Posortowana lista termów (stemów) indeksu."""
        self._ensure()
        return list(self._vocab_sorted)

    def doc_terms(self, doc_id: str, field: str | None = None) -> list[str]:
        """Termy dokumentu (opcjonalnie tylko z jednego pola)."""
        f = self.raw.get(doc_id) or {}
        if field:
            return T.terms(f.get(field, ""))
        return [t for txt in f.values() for t in T.terms(txt)]

    # ------------------------------------------------------------------ dopasowanie termów
    def _prefix_terms(self, t: str, limit: int = 12) -> list[str]:
        i = bisect.bisect_left(self._vocab_sorted, t)
        out = []
        while i < len(self._vocab_sorted) and self._vocab_sorted[i].startswith(t) and len(out) < limit:
            if self._vocab_sorted[i] != t:
                out.append(self._vocab_sorted[i])
            i += 1
        return out

    def _fuzzy_terms(self, t: str, limit: int = 6) -> list[tuple[str, int]]:
        if len(t) < 4 or not t.isascii():
            return []
        cand: dict[str, int] = defaultdict(int)
        grams = T.char_ngrams(t, 3)
        for g in grams:
            for v in self._trigrams.get(g, ()):
                cand[v] += 1
        maxd = 1 if len(t) <= 5 else 2
        scored = []
        for v, c in sorted(cand.items(), key=lambda kv: -kv[1])[:80]:
            if abs(len(v) - len(t)) > maxd or c < max(1, len(grams) // 3):
                continue
            d = T.levenshtein(t, v, limit=maxd)
            if d > 1 and _transposed(t, v):          # „binrane” ↔ „binarne”: przestawienie liter = 1 błąd
                d = 1
            if 0 < d <= maxd and (d == 1 or v[0] == t[0]):
                scored.append((v, d))
        scored.sort(key=lambda x: (x[1], -len(self._postings.get(x[0], {}))))
        return scored[:limit]

    def _resolve(self, query: str, extra_terms=None) -> list[tuple[str, float, str, tuple[str, ...]]]:
        """[(term_w_indeksie, waga, źródło, pochodzenie)] — źródło: exact|synonym|root|prefix|fuzzy|extra."""
        self._ensure()
        out: dict[str, tuple[float, str, tuple[str, ...]]] = {}

        def put(term, w, src, origin):
            if term in self._postings and (term not in out or out[term][0] < w):
                out[term] = (w, src, origin)

        for t, w, origin in _expand(query):
            src = "exact" if w >= 1.0 else "synonym"
            if t in self._postings:
                put(t, w, src, origin)
            for v in self._roots.get(root(t), ()):
                if v != t:
                    put(v, w * W_ROOT, "root", origin)
            if len(t) >= 3:
                for p in self._prefix_terms(t, limit=10):
                    put(p, w * W_PREFIX * min(1.0, (len(t) + 1) / len(p)), "prefix", origin)
            if t not in self._postings and root(t) not in self._roots:
                for v, d in self._fuzzy_terms(t):
                    put(v, w * (W_FUZZY1 if d == 1 else W_FUZZY2), "fuzzy", origin)
        for t, w in extra_terms or ():
            put(t, w, "extra", ())
        return [(t, w, s, o) for t, (w, s, o) in out.items()]

    def query_term_weights(self, query: str) -> dict[str, float]:
        """Waga (idf) każdego słowa zapytania — przez najlepsze jego dopasowanie w indeksie (dokładne, odmiana,
        synonim…). Słowo nieznane indeksowi dostaje wagę 0 (nie da się go „pokryć”, więc nie karze dokumentów)."""
        q_terms = _cover_terms(query)
        # rzadkość POJĘCIA, a nie konkretnej formy: „mapie” jest rzadkie jako forma, ale „map/mapa/mapy” — nie;
        # bierzemy więc najmniejsze idf wśród form dokładnych/odmian, a dopiero gdy ich brak — najlepsze z reszty
        forms: dict[str, float] = {}
        other: dict[str, float] = {}
        for term, w, src, origin in self._resolve(query):
            idf = self._idf.get(term, 0.0)
            for o in origin:
                if o not in q_terms:
                    continue
                if src in ("exact", "root"):
                    forms[o] = min(forms.get(o, idf), idf)
                else:
                    other[o] = max(other.get(o, 0.0), idf * min(1.0, w + 0.15))
        return {t: forms.get(t, other.get(t, 0.0)) for t in q_terms}

    def known_terms(self, query: str) -> tuple[set[str], set[str]]:
        """(słowa pytania „pewnie znane” indeksowi, wszystkie słowa pytania). Pewnie znane = dopasowane dokładnie,
        przez odmianę, synonim albo prefiks; samo dopasowanie literówkowe (fuzzy) się nie liczy — „makaron”
        nie staje się znanym słowem tylko dlatego, że przypomina „makro”."""
        q_terms = _cover_terms(query)
        solid: set[str] = set()
        for _term, w, src, origin in self._resolve(query):
            # literówka o 1 znak (w tym przestawienie: „binrane” → „binarne”) w dłuższym słowie też się liczy
            typo1 = src == "fuzzy" and w >= 0.7 and all(len(o) >= 5 and not o.isdigit() for o in origin)
            if src != "fuzzy" or typo1:
                solid.update(o for o in origin if o in q_terms)
        return solid, q_terms

    def resolve_terms(self, query: str) -> list[tuple[str, float, str]]:
        """[(term_w_indeksie, waga, źródło)] — źródło: exact | synonym | root | prefix | fuzzy."""
        return [(t, w, s) for t, w, s, _ in self._resolve(query)]

    # ------------------------------------------------------------------ szukanie
    def search(self, query: str, k: int = 10, filters=None, boost=None, *,
               extra_terms: list[tuple[str, float]] | None = None,
               candidates: Iterable[str] | None = None) -> list[dict]:
        """Zwraca [{id, score, payload, matched, cover}] posortowane malejąco.

        filters: callable(doc_id, payload) -> bool. boost: callable(doc_id, payload) -> mnożnik.
        extra_terms: dodatkowe (term, waga), np. z `TermGraph.expand`. candidates: ogranicz do tych id.
        `cover` = jaka część słów zapytania (z ich synonimami/odmianami) została dopasowana w dokumencie."""
        self._ensure()
        resolved = self._resolve(query, extra_terms)
        if not resolved:
            return []
        allowed = set(candidates) if candidates is not None else None
        scores: dict[str, float] = defaultdict(float)
        matched: dict[str, set[str]] = defaultdict(set)
        covered: dict[str, set[str]] = defaultdict(set)
        # Termy z tym samym „pochodzeniem” (jedno słowo pytania + jego synonimy, odmiany, prefiksy, literówki)
        # NIE sumują się w pełni: liczy się najlepsze dopasowanie + ułamek pozostałych. Inaczej słowo z wieloma
        # synonimami („policzyć” → zliczać, count, histogram, częstość…) przytłacza rzadkie, kluczowe słowo
        # pytania („wysp”) — dokument zbierał punkty za samo bogactwo synonimów.
        groups: dict[tuple, list] = defaultdict(list)
        for term, w, _src, origin in resolved:
            groups[origin or ("\x00extra", term)].append((term, w))
        for origin, items in groups.items():
            best: dict[str, float] = {}
            total: dict[str, float] = defaultdict(float)
            for term, w in items:
                row = self._pre[term]
                if allowed is not None:
                    row = {d: v for d, v in row.items() if d in allowed}
                for did, v in row.items():
                    c = w * v
                    total[did] += c
                    if c > best.get(did, 0.0):
                        best[did] = c
                    matched[did].add(term)
                if origin and origin[0] != "\x00extra":
                    for did in row:
                        covered[did].update(origin)
            # fraza z wielu słów pytania („największy wspólny dzielnik” → nwd, gcd) to mocny, konkretny sygnał —
            # jej dopasowania sumujemy w całości; ograniczamy tylko „rozmnażanie się” pojedynczego słowa
            spill = 1.0 if len(origin) >= 2 and origin[0] != "\x00extra" else SYNONYM_SPILL
            for did, b in best.items():
                scores[did] += b + spill * (total[did] - b)
        # premia za pokrycie wszystkich słów zapytania i za frazę w polu o najwyższej wadze
        q_terms = _cover_terms(query)
        qnorm = T.normalize(query)
        res = []
        for did, s in scores.items():
            payload = self.payloads.get(did)
            if filters and not filters(did, payload):
                continue
            cover = len(q_terms & covered[did]) / max(1, len(q_terms)) if q_terms else 0.0
            s *= 0.5 + 0.5 * cover
            if qnorm and len(qnorm) >= 3:
                tf_txt = self._top_norm.get(did, "")
                if qnorm == tf_txt:
                    s *= 1.6
                elif tf_txt and qnorm in tf_txt:
                    s *= 1.25
            if boost:
                s *= boost(did, payload)
            if s <= 0:
                continue
            res.append({"id": did, "score": round(s, 4), "payload": payload,
                        "matched": sorted(matched[did]), "cover": round(cover, 3),
                        "covered": sorted(q_terms & covered[did])})
        res.sort(key=lambda r: -r["score"])
        return res[:k]

    def suggest(self, prefix: str, k: int = 8) -> list[str]:
        """Podpowiedzi termów dla autouzupełniania."""
        self._ensure()
        norm = T.normalize(prefix)
        t = T.stem(norm.split()[-1]) if norm else ""
        if not t:
            return []
        cands = ([t] if t in self._postings else []) + self._prefix_terms(t, limit=50)
        cands.sort(key=lambda v: -len(self._postings.get(v, {})))
        return cands[:k]

    # ------------------------------------------------------------------ korekta pisowni
    def _build_surface(self) -> None:
        surface: Counter = Counter()
        display: dict[str, Counter] = defaultdict(Counter)
        word_re = re.compile(r"[A-Za-z0-9_+#ĄĆĘŁŃÓŚŹŻąćęłńóśźż]+")
        for fields in self.raw.values():
            for txt in fields.values():
                if not txt:
                    continue
                for m in word_re.finditer(txt):
                    w = m.group(0)
                    low = w.lower()
                    n = T.strip_accents(low)
                    if len(n) < 2 or n.isdigit():
                        continue
                    surface[n] += 1
                    if low != n:
                        display[n][low] += 1
        # kandydaci do poprawek: słowa z indeksu + słowa kanonicznych fraz synonimów (bez wariantów-literówek)
        wg, canon = _synonym_word_groups()
        cands: Counter = Counter(surface)
        for w in canon:
            cands[w] += 1
        tri: dict[str, set[str]] = defaultdict(set)
        for w in cands:
            if len(w) >= 3 and w.isascii() and w.isalpha():
                for g in T.char_ngrams(w, 3):
                    tri[g].add(w)
        self._surface = surface
        self._cands = cands
        self._display = {n: c.most_common(1)[0][0] for n, c in display.items()}
        self._surface_tri = dict(tri)

    def _known_word(self, n: str) -> bool:
        if n in self._surface or n in T.STOPWORDS:
            return True
        st = T.stem(n)
        return st in self._postings or root(st) in self._roots

    def correct_word(self, word: str) -> str | None:
        """Najbliższe słowo ze słownika indeksu dla nieznanego słowa (albo None, gdy słowo znane / brak kandydata).

        Słowo spoza indeksu, ale obecne w synonimach (np. potoczne „przedziałówka”), jest poprawiane tylko na
        słowo z tej samej grupy synonimów (np. literówka „dijsktra” → „dijkstra”)."""
        self._ensure()
        if self._surface is None:
            self._build_surface()
        n = T.strip_accents(word.lower())
        if len(n) < 4 or not n.isalpha() or not n.isascii() or self._known_word(n):
            return None
        wg, canon = _synonym_word_groups()
        syn_groups = wg.get(n)
        if syn_groups and n in canon:
            return None
        cand: Counter = Counter()
        grams = T.char_ngrams(n, 3)
        for g in grams:
            for v in self._surface_tri.get(g, ()):
                cand[v] += 1
        maxd = 1 if len(n) <= 5 else 2
        best = None
        for v, c in cand.most_common(80):
            if abs(len(v) - len(n)) > maxd or c < max(1, len(grams) // 3):
                continue
            if syn_groups and not (wg.get(v, set()) & syn_groups):
                continue
            d = T.levenshtein(n, v, limit=maxd)
            if d > 1 and _transposed(n, v):
                d = 1
            # odległość 2 tylko dla długich słów i przy tej samej pierwszej literze („makaron” ≠ „makro”)
            if d == 0 or d > maxd or (d == 2 and (v[0] != n[0] or len(n) < 8)):
                continue
            key = (d, -math.log1p(self._cands[v]), len(v))
            if best is None or key < best[0]:
                best = (key, v)
        if not best:
            return None
        v = best[1]
        return self._display.get(v, v)

    def correct(self, query: str) -> dict:
        """Korekta pisowni zapytania: {"text", "changed", "fixes": [[stare, nowe], ...], "distances": [odl., ...]}.
        Zmienia tylko słowa nieznane indeksowi; reszta tekstu (wielkość liter, ogonki) zostaje bez zmian."""
        fixes: list[list[str]] = []
        dists: list[int] = []

        def repl(m: re.Match) -> str:
            w = m.group(0)
            fix = self.correct_word(w)
            if fix and T.strip_accents(fix) != T.strip_accents(w.lower()):
                fixes.append([w, fix])
                dists.append(T.levenshtein(T.strip_accents(w.lower()), T.strip_accents(fix), limit=3))
                return fix
            return w

        text_ = re.sub(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{4,}", repl, query or "")
        return {"text": text_, "changed": bool(fixes), "fixes": fixes, "distances": dists}


# ============================================================================================ fragmenty
def highlight(text_: str, terms_: list[str], width: int = 220) -> str:
    """Fragment tekstu wokół pierwszego trafienia z **pogrubionymi** słowami (markdown)."""
    if not text_:
        return ""
    plain = re.sub(r"\s+", " ", text_).strip()
    low = T.strip_accents(plain).lower()
    pos = -1
    for t in terms_:
        if not t:
            continue
        m = re.search(r"\b" + re.escape(t), low)
        if m and (pos < 0 or m.start() < pos):
            pos = m.start()
    start = max(0, pos - width // 3) if pos >= 0 else 0
    if start > 0:                                  # nie tnij w połowie słowa
        sp = plain.find(" ", start)
        if 0 <= sp < start + 20:
            start = sp + 1
    frag = plain[start:start + width]
    if start + width < len(plain):
        sp = frag.rfind(" ")
        if sp > width * 0.7:
            frag = frag[:sp]
        frag += "…"
    if start > 0:
        frag = "…" + frag

    def bold(m):
        return f"**{m.group(0)}**"

    for t in sorted(set(terms_), key=len, reverse=True):
        if len(t) < 3 or not t.replace("_", "").isalnum():
            continue
        # dopasuj słowa zaczynające się od stemu (również z polskimi znakami); nie wchodź w już pogrubione
        pat = re.compile(r"(?<![\w*])(" + "".join(f"[{re.escape(c)}{_accent_variants(c)}]" for c in t) + r")\w*", re.I)
        frag = pat.sub(bold, frag)
    return frag.replace("****", "")


_ACC = {"a": "ą", "c": "ć", "e": "ę", "l": "ł", "n": "ń", "o": "ó", "s": "ś", "z": "źż"}


def _accent_variants(c: str) -> str:
    v = _ACC.get(c.lower(), "")
    return v + v.upper()


def trigram_set(s: str) -> frozenset:
    """Zbiór trigramów znakowych znormalizowanego tekstu (do szybkiego podobieństwa Jaccarda)."""
    return frozenset(T.char_ngrams(s, 3))


def jaccard(a: frozenset | set, b: frozenset | set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter) if inter else 0.0


__all__ = ["SearchIndex", "TermGraph", "expand_query", "highlight", "root", "synonym_groups", "synonym_words",
           "synonyms_of", "trigram_set", "jaccard"]
