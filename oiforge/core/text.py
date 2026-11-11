"""Przetwarzanie tekstu PL/EN: normalizacja, tokenizacja (także identyfikatorów kodu), lekki stemmer,
n-gramy znakowe, odległość Levenshteina. Bez zewnętrznych bibliotek."""
from __future__ import annotations

import functools
import re
import unicodedata

_PL_MAP = str.maketrans({"ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
                         "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n", "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z"})

STOPWORDS = set("""
a aby ale albo ani az bardzo bez bo by byc byl byla bylo byly bede beda bedzie ci cie co czy czyli dla do dlaczego
gdy gdzie go i ich ile im inny inna inne ja jak jaka jaki jakie jako je jego jej jest jestem jeszcze jesli jezeli juz
ktora ktore ktory ktorych kto ma mam mamy maja mi mie mna mnie moge moze mozna mu musi my na nad nam nas nie niech
nic nim niz no o od oraz po pod przez przy sa sie sobie sposob ta tak takze tam te tego tej ten to tu ty tylko tym
u w we wiec wszystko z za ze zeby zrobic robic prosze pls plz hej siema jakis jakos czym ktorej
the an of to in on for is are be how what why do does can i you it with and or by as at from this that use using
""".split())

_WORD_RE = re.compile(r"[a-z0-9_+#]+(?:::[a-z0-9_]+)*")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_+#]+(?:::[A-Za-z0-9_]+)*")


def strip_accents(s: str) -> str:
    s = s.translate(_PL_MAP)
    if s.isascii():
        return s
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize(s: str) -> str:
    """Małe litery, bez polskich znaków, uproszczone białe znaki. Zachowuje `c++`, `#`, `::`, `_`."""
    if not s:
        return ""
    s = strip_accents(s).lower()
    s = re.sub(r"[^\w+#:\s.\-/<>]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_identifier(tok: str) -> list[str]:
    """`lowerBound` → [lower, bound]; `lower_bound` → [lower, bound]; `std::set` → [std, set]."""
    parts = []
    for p in re.split(r"::|_+", tok):
        if not p:
            continue
        parts += [x for x in _CAMEL_RE.sub(" ", p).split() if x]
    return [p.lower() for p in parts]


def tokenize(s: str, keep_identifiers: bool = True) -> list[str]:
    """Tokeny słownikowe. Identyfikatory typu `lower_bound`, `std::set`, `camelCase` są też rozbijane."""
    if not s:
        return []
    raw = strip_accents(s)
    out: list[str] = []
    for m in _TOKEN_RE.finditer(raw):
        w = m.group(0)
        low = w.lower()
        if low in ("c++", "cpp", "c#"):
            out.append("cpp" if low != "c#" else "c#")
            continue
        low = low.strip("+#") or low
        if keep_identifiers and ("_" in w or "::" in w or _CAMEL_RE.search(w)):
            whole = low.replace("::", "_")
            out.append(whole)
            out += [p for p in split_identifier(w) if p != whole]
        else:
            out.append(low)
    return [t for t in out if t]


# Lekki stemmer: obcina typowe końcówki fleksyjne PL (po usunięciu ogonków) i EN.
_PL_SUFFIXES = sorted("""
owaniami owaniach owaniem owania owanie owaniu owanym owany owana owane owac owal
eniami eniach eniem enia enie eniu ania anie aniu aniem aniach aniami
ościach osciach osciami osci osc
ami ach ach owie owi owa owe owy ego emu ych ymi imi ej ow om ie ia iu ii ic ac ec yc
ny na ne nego nej nym
a e i o u y
""".split(), key=len, reverse=True)
_EN_SUFFIXES = ["ations", "ation", "ings", "ing", "ies", "ers", "er", "es", "ed", "ly", "s"]
_PROTECT = {"bfs", "dfs", "lca", "lis", "lcs", "dsu", "kmp", "nwd", "nww", "gcd", "lcm", "bit", "dp", "set", "map",
            "cin", "cout", "endl", "int", "long", "pair", "sort", "stl", "cpp", "vector", "queue", "stack", "deque",
            "bitset", "string", "mst", "scc", "fft", "ntt", "mod", "xor", "and", "max", "min", "abs", "pow", "sqrt",
            "unordered_map", "unordered_set", "multiset", "multimap", "priority_queue", "lower_bound", "upper_bound",
            "prim", "floyd", "dijkstra", "kruskal", "tarjan", "manacher", "trie", "treap", "hash", "mo", "sos", "z"}


@functools.lru_cache(maxsize=100_000)
def stem(tok: str) -> str:
    t = tok.lower()
    if len(t) <= 3 or t in _PROTECT or any(ch.isdigit() for ch in t) or "_" in t:
        return t
    if t.isascii() and re.fullmatch(r"[a-z]+", t):
        for suf in _PL_SUFFIXES:
            if t.endswith(suf) and len(t) - len(suf) >= 4:
                return t[: -len(suf)]
        for suf in _EN_SUFFIXES:
            if t.endswith(suf) and len(t) - len(suf) >= 4:
                return t[: -len(suf)]
    return t


def terms(s: str, drop_stopwords: bool = True) -> list[str]:
    """Stemy tokenów (bez stopwords) — podstawowa jednostka wyszukiwania (z cache dla powtarzanych tekstów)."""
    if not s:
        return []
    return list(_terms_cached(s, drop_stopwords))


@functools.lru_cache(maxsize=60_000)
def _terms_cached(s: str, drop_stopwords: bool) -> tuple:
    out = []
    for t in tokenize(s):
        if drop_stopwords and t in STOPWORDS:
            continue
        out.append(stem(t))
    return tuple(out)


def char_ngrams(s: str, n: int = 3, pad: bool = True) -> list[str]:
    s = normalize(s)
    if pad:
        s = f" {s} "
    if len(s) < n:
        return [s] if s.strip() else []
    return [s[i:i + n] for i in range(len(s) - n + 1)]


def levenshtein(a: str, b: str, limit: int | None = None) -> int:
    """Odległość edycyjna (z opcjonalnym wczesnym przerwaniem po przekroczeniu `limit`)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    if limit is not None and len(a) - len(b) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = i
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            # transpozycja (Damerau) — częsta literówka
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev[j - 2] + 1 if j >= 2 else cur[j])
            best = min(best, cur[j])
        if limit is not None and best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    """Podobieństwo 0..1: max(Jaccard trigramów, 1 - lev/len)."""
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ga, gb = set(char_ngrams(a)), set(char_ngrams(b))
    jac = len(ga & gb) / max(1, len(ga | gb))
    m = max(len(a), len(b))
    lev = 1.0 - levenshtein(a, b, limit=m) / m if m <= 64 else 0.0
    return max(jac, lev)


def sentences(text: str) -> list[str]:
    """Prosty podział na zdania (PL)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ0-9])", text)
    return [p.strip() for p in parts if p.strip()]
