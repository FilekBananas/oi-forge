"""Moje programy — magazyn zaakceptowanych rozwiązań (SIO2 i importy) + „MEGA SMART” wyszukiwarka.

Bez AI: BM25F (`core.search`) nad polami nazwa/temat rundy/tagi/STL/komentarze/identyfikatory/treść +
warstwa *pojęć* (algorytmy i tematy z tolerancją literówek: „binserch” → wyszukiwanie binarne), sygnały
z tematu rundy, nagłówka treści („NWD i NWW”), nazwy zadania i heurystyk na kodzie (albo `core.algodetect`),
dosłowne szukanie po kodzie (`#define int long long`, `lower_bound`) oraz podobieństwo programów
(shingle tokenów + Jaccard, TF-IDF treści, wspólne tematy).

Python API (dla innych modułów):
    search(query, k=10, filters=None) -> list[dict]   # problem_name, problem_short, contest_name, round, path,
                                                       # score, tags, snippet, url (+ why, relevance, id, …)
    similar_tasks(statement_text, k=5) -> list[dict]
    get(program_id) -> dict | None
    similar(program_id, k=8) -> list[dict]
    stats() -> dict ; list_programs(filters=None) -> list[dict]

Format danych = `user_data/programs/index.json` ({fetched_at, programs: [...]}) — tylko DODAJEMY pola.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..core import search as S
from ..core import text as T
from ..server_api import ApiError
from . import _sio2_parse as P

try:
    from ..core import cpp_lexer as L
except ImportError:  # pragma: no cover - lekser jest w rdzeniu, ale bądźmy odporni
    L = None

INDEX_NAME = "index.json"
BACKUP_NAME = "index.backup.json"
_io_lock = threading.RLock()
_root_override: Path | None = None


# ============================================================================================ magazyn
def programs_dir() -> Path:
    return Path(_root_override) if _root_override else config.PROGRAMS_DIR


def set_root(path) -> None:
    """Zmienia katalog programów (testy). None = domyślny `config.PROGRAMS_DIR`."""
    global _root_override
    _root_override = Path(path) if path else None
    invalidate()


def now_iso() -> str:
    n = datetime.now(timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


class IndexCorrupted(Exception):
    pass


def load_index(root=None, strict: bool = False) -> dict:
    """{fetched_at, programs: [...]}. Przy uszkodzonym pliku: strict → wyjątek (nie nadpisujemy danych!)."""
    root = Path(root or programs_dir())
    p = root / INDEX_NAME
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"fetched_at": None, "programs": []}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        if strict:
            raise IndexCorrupted(f"Plik {p} jest uszkodzony ({e}). Nie zapisuję, żeby nie stracić danych.") from e
        return {"fetched_at": None, "programs": [], "_error": str(e)}
    if isinstance(data, list):
        data = {"fetched_at": None, "programs": data}
    if not isinstance(data, dict):
        data = {"fetched_at": None, "programs": []}
    if not isinstance(data.get("programs"), list):
        data["programs"] = []
    return data


def save_index(data: dict, root=None) -> None:
    """Atomowy zapis index.json (przy pierwszym zapisie robi kopię index.backup.json)."""
    root = Path(root or programs_dir())
    with _io_lock:
        root.mkdir(parents=True, exist_ok=True)
        p = root / INDEX_NAME
        bak = root / BACKUP_NAME
        if p.exists() and not bak.exists():
            try:
                shutil.copy2(p, bak)
            except OSError:
                pass
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    invalidate()


def record_key(rec: dict) -> tuple[str, str]:
    return (str(rec.get("source") or ""), str(rec.get("submission_id") or ""))


def program_id(rec: dict) -> str:
    return f"{rec.get('source') or 'local'}:{rec.get('submission_id') or rec.get('path')}"


def merge_programs(new_records: list[dict], root=None, update_existing: bool = True) -> dict:
    """Dopisuje rekordy (bez duplikatów po (source, submission_id)). Zwraca {added, updated, total}."""
    root = Path(root or programs_dir())
    with _io_lock:
        data = load_index(root, strict=True)
        progs = data["programs"]
        pos = {record_key(r): i for i, r in enumerate(progs)}
        added = updated = 0
        for rec in new_records:
            k = record_key(rec)
            if k in pos:
                if update_existing:
                    old = progs[pos[k]]
                    changed = False
                    for kk, vv in rec.items():
                        if vv not in (None, "") and old.get(kk) in (None, ""):
                            old[kk] = vv
                            changed = True
                    updated += changed
                continue
            pos[k] = len(progs)
            progs.append(rec)
            added += 1
        if added or updated:
            data["fetched_at"] = now_iso()
            save_index(data, root)
        return {"added": added, "updated": updated, "total": len(progs)}


_SAFE_PART = re.compile(r"^[\w.\-]+$")


def safe_part(s: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", str(s or "").strip(), flags=re.A).strip("._") or "x"
    return s[:80]


def inside(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def statement_dir(root: Path, source: str, contest_id: str) -> Path:
    return Path(root) / safe_part(source) / safe_part(contest_id) / "statements"


def find_statement(root: Path, source: str, contest_id: str, short: str) -> dict:
    """{txt, html, pdf} → ścieżki (lub None). Skrót porównywany także małymi literami („BIB” → „bib”)."""
    d = statement_dir(root, source, contest_id)
    out = {"txt": None, "html": None, "pdf": None}
    if not short or not d.is_dir():
        return out
    names = [short, short.lower()] if short != short.lower() else [short]
    for ext in ("txt", "html", "pdf"):
        for nm in names:
            p = d / f"{safe_part(nm)}.{ext}"
            if p.is_file():
                out[ext] = p
                break
    return out


def load_problems(root: Path, source: str) -> dict:
    p = Path(root) / safe_part(source) / "problems.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def save_problems(root: Path, source: str, problems: dict) -> None:
    p = Path(root) / safe_part(source) / "problems.json"
    with _io_lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(problems, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    invalidate()


# ============================================================================================ tematy i pojęcia
_PL_CHARS = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
_fold_map: dict[int, str] = {}


def fold(s: str) -> str:
    """Zamienia znaki spoza ASCII (poza polskimi literami) na odpowiedniki ASCII albo spacje: ligatury z PDF
    („ﬁ” → „fi”), indeksy górne, „≤”, cudzysłowy… Dzięki temu `text.terms` działa na szybkiej ścieżce."""
    if not s or s.isascii():
        return s or ""
    table = {}
    for ch in set(s):
        o = ord(ch)
        if o < 128 or ch in _PL_CHARS:
            continue
        r = _fold_map.get(o)
        if r is None:
            r = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
            if not r.isascii() or not r.strip():
                r = " "
            _fold_map[o] = r
        table[o] = r
    return s.translate(table) if table else s


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", T.strip_accents(s or "").lower()).strip()


def words_of(s: str) -> list[str]:
    s = T.strip_accents(s or "").lower()
    s = s.replace("c++", "cpp")
    return [w for w in re.split(r"[^a-z0-9_#+]+", s) if w and w not in ("+", "#")]


def _root_word(w: str) -> str:
    st = T.stem(w)
    try:
        return S.root(st)
    except Exception:  # noqa: BLE001 - starsza wersja search bez root()
        return st


_GENERIC_TOPIC = re.compile(r"^(warsztaty|kontest|contest|piaskownica|sprawdzenie|runda|round|zadania dodatkowe|"
                            r"dodatkowe|powtorka|egzamin|probny|trening|sesja|dzien\s*\d*$|day\s*\d*$|test$|zawody|"
                            r"final|eliminacje|etap)", re.I)


def topic_of(round_name: str | None) -> str | None:
    """Temat z nazwy rundy: „Dzień 5 - Dijkstra” → „Dijkstra”, „Wyszukiwanie binarne na 12.02.2026” → „Wyszukiwanie
    binarne”, „grafy 11.09” → „Grafy”. None dla braku rundy."""
    t = P.clean_round(round_name) if round_name else None
    if not t:
        return None
    t = re.sub(r"^\s*(dzień|dzien|day|lekcja|zajęcia|zajecia|tydzień|tydzien|week)\s*\d+\s*[-–—:.]\s*", "", t, flags=re.I)
    t = re.sub(r"\s+na\s+\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\s*(?::\)|;\)|:D)?\s*$", "", t)
    t = re.sub(r"\s*(?::\)|;\)|:D)\s*", " ", t)
    t = re.sub(r"\s*[-–—,]?\s*\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\s*$", "", t)
    t = re.sub(r"\s+na\s+\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\s*$", "", t)
    t = re.sub(r"\s+cz(?:ęść|esc|\.)?\s*\d+\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -–—:,.")
    if not t:
        return None
    return t[0].upper() + t[1:]


def topic_is_generic(topic: str | None) -> bool:
    return not topic or bool(_GENERIC_TOPIC.match(norm(topic)))


def statement_topic(statement: str) -> str | None:
    """Temat z nagłówka PDF-a (format części serwerów SIO2): linia „NWD i NWW Limit pamięci: 64 MiB” → „NWD i NWW”."""
    if not statement:
        return None
    for line in statement.splitlines()[:12]:
        m = re.match(r"^\s*(.*?)\s*Limit pami[eę]ci", line)
        if m:
            t = m.group(1).strip(" –—-")
            if not t or re.search(r"\d", t) or norm(t) in ("contest", "kontest", "zadanie", "zawody"):
                return None
            if re.match(r"^(dzie[nń]|warsztaty|contest|runda|etap)\b", norm(t)):
                return None
            return t
    return None


# Pojęcia: id, nazwa (PL), aliasy do zapytań (PL/EN/potoczne/literówki), regex tematu (na znormalizowanym tekście
# bez ogonków) oraz rodzice {id: waga} — „Problem plecakowy” jest też „programowaniem dynamicznym”.
CONCEPTS: list[tuple[str, str, list[str], str | None, dict]] = [
    ("binary_search", "Wyszukiwanie binarne",
     ["wyszukiwanie binarne", "binsearch", "binary search", "bin search", "binsercz", "binserach", "bisekcja", "bs",
      "przeszukiwanie binarne", "wyszukiwanie połówkowe", "szukanie binarne", "binsearch po wyniku",
      "wyszukiwanie binarne po wyniku", "binary search on answer", "bs po wyniku", "lower_bound", "upper_bound",
      "lower bound", "upper bound", "binarne wyszukiwanie", "binsercz po wyniku", "bin search po wyniku",
      "wyszukiwanie binarne po odpowiedzi"],
     r"binarn(?!y spacer)|binsearch|\bbin ?search|^bs$|bisekc|polowkow", {}),
    ("ternary_search", "Wyszukiwanie ternarne",
     ["wyszukiwanie ternarne", "ternary search", "ternarne", "trójdzielne", "ternsearch", "funkcja unimodalna"],
     r"ternarn|ternary|trojdziel", {"binary_search": 0.4}),
    ("prefix_sums", "Sumy prefiksowe",
     ["sumy prefiksowe", "suma prefiksowa", "prefix sum", "prefix sums", "prefiksy", "sumy częściowe",
      "partial_sum", "tablica prefiksowa", "prefiksowe", "prefsumy", "pref sum", "sumy na przedziale",
      "tablica różnicowa", "difference array"],
     r"prefiks|prefix|sumy czesciow|roznicow", {}),
    ("two_pointers", "Gąsienica (dwa wskaźniki)",
     ["gąsienica", "gasienica", "metoda gąsienicy", "dwa wskaźniki", "two pointers", "okno przesuwne",
      "sliding window", "gąsieniczka", "dwa wskazniki"],
     r"gasienic|two pointers|dwa wskaz|okno przesuw|sliding window", {}),
    ("sorting", "Sortowanie",
     ["sortowanie", "sort", "sortować", "posortować", "std::sort", "sortowanie bąbelkowe", "sortowanie przez scalanie",
      "merge sort", "quicksort", "quick sort", "sortowanie szybkie", "counting sort", "sortowanie kubełkowe",
      "sortowanie przez zliczanie", "sortowanie przez wybieranie", "bubble sort", "komparator"],
     r"^(?!.*topolog).*(sortow|\bsort\b|scalani|babelk|kubelk|quicksort)", {}),
    ("greedy", "Algorytmy zachłanne",
     ["zachłanny", "zachłanne", "zachłan", "zachłany", "greedy", "algorytm zachłanny", "zachlany", "zachlanne"],
     r"zachlan|greedy", {}),
    ("dp", "Programowanie dynamiczne",
     ["programowanie dynamiczne", "dp", "dynamik", "dynamiki", "dynamic programming", "dynamika", "dynamiczne",
      "dynamiczny", "spamiętywanie", "memoizacja"],
     r"dynami|\bdp\b|programowanie dynamiczne|memoiz", {}),
    ("knapsack", "Problem plecakowy",
     ["problem plecakowy", "plecak", "plecakowy", "knapsack", "plecak dyskretny", "wydawanie reszty", "coin change",
      "problem wydawania reszty"],
     r"plecak|knapsack|wydawanie reszty", {"dp": 0.9}),
    ("tree_dp", "Dynamiki na drzewach",
     ["dp na drzewie", "dynamik na drzewie", "dynamiki na drzewach", "tree dp", "programowanie dynamiczne na drzewie"],
     r"dynami\w* na drzew|tree dp|dp na drzew", {"dp": 0.9, "tree": 0.9}),
    ("graph_dp", "Dynamiki na grafach",
     ["dp na grafie", "dynamiki na grafach", "dp na dagu", "dynamik na grafie"],
     r"dynami\w* na graf|dp na (graf|dag)", {"dp": 0.9, "graph": 0.8}),
    ("bfs", "BFS (przeszukiwanie wszerz)",
     ["bfs", "przeszukiwanie wszerz", "breadth first search", "wszerz", "bfs na siatce", "bfsa", "bfsem"],
     r"\bbfs|wszerz|breadth", {"graph": 0.8, "shortest_path": 0.65}),
    ("dfs", "DFS (przeszukiwanie w głąb)",
     ["dfs", "przeszukiwanie w głąb", "depth first search", "w głąb", "dfsa", "dfsem", "preorder", "postorder"],
     r"\bdfs|w glab|depth first|preorder|postorder", {"graph": 0.8}),
    ("dijkstra", "Algorytm Dijkstry",
     ["dijkstra", "dijkstry", "dikstra", "dijkstr", "dijkstrę", "algorytm dijkstry", "dijkstre", "dijkstrą",
      "najkrótsza ścieżka z wagami", "shortest path weighted", "dijsktra", "djikstra"],
     r"dijkstr|dikstr|djikstr|dijsktr", {"graph": 0.85, "shortest_path": 0.95}),
    ("bellman_ford", "Bellman-Ford",
     ["bellman ford", "bellman-ford", "bellmana forda", "algorytm bellmana forda", "ujemne wagi", "ujemne cykle"],
     r"bellman", {"graph": 0.85, "shortest_path": 0.9}),
    ("floyd", "Floyd-Warshall",
     ["floyd", "floyd warshall", "floyda warshalla", "floyd-warshall", "algorytm floyda", "wszystkie pary odległości"],
     r"floyd|warshall", {"graph": 0.85, "shortest_path": 0.9}),
    ("shortest_path", "Najkrótsze ścieżki",
     ["najkrótsza ścieżka", "najkrótsze ścieżki", "najkrótsza droga", "shortest path", "odległość w grafie",
      "najmniejsza odległość", "odległości w grafie", "najkrótszej ścieżki", "najkrótszą ścieżkę"],
     r"najkrotsz|shortest path", {"graph": 0.8}),
    ("topo_sort", "Sortowanie topologiczne",
     ["sortowanie topologiczne", "toposort", "topological sort", "topo", "porządek topologiczny", "topologiczne",
      "toposorta", "topsort", "sort topologiczny"],
     r"topolog|toposort|topsort|\btopo\b", {"graph": 0.85}),
    ("dsu", "Find & Union (zbiory rozłączne)",
     ["find union", "find and union", "find & union", "find&union", "dsu", "union find", "union-find",
      "disjoint set union", "zbiory rozłączne", "find-union", "fu", "f&u", "find unia", "lider zbioru"],
     r"find.{0,5}union|union.{0,3}find|\bdsu\b|zbiory rozlaczn|\bf ?& ?u\b", {}),
    ("mst", "Minimalne drzewo rozpinające",
     ["minimalne drzewo rozpinające", "mst", "minimum spanning tree", "kruskal", "prim", "drzewo rozpinające",
      "algorytm kruskala", "algorytm prima"],
     r"rozpinaj|\bmst\b|kruskal|\bprim(a)?\b", {"graph": 0.85}),
    ("scc", "Silnie spójne składowe",
     ["silnie spójne składowe", "scc", "strongly connected components", "tarjan", "kosaraju"],
     r"silnie spojn|\bscc\b|kosaraju|tarjan", {"graph": 0.85}),
    ("euler", "Cykl Eulera",
     ["cykl eulera", "ścieżka eulera", "euler", "eulerowski", "graf eulerowski"],
     r"euler", {"graph": 0.85}),
    ("graph", "Grafy",
     ["graf", "grafy", "graph", "grafie", "grafów", "lista sąsiedztwa", "macierz sąsiedztwa", "wierzchołki",
      "krawędzie", "graf skierowany", "grafy skierowane", "spójne składowe"],
     r"\bgraf|graph|sasiedztw|wierzcholk", {}),
    ("tree", "Drzewa",
     ["drzewo", "drzewa", "tree", "drzewko", "ukorzenione drzewo", "drzewie", "średnica drzewa", "przodek"],
     r"^(?!.*(przedzial|rozpinaj|fenwick|potegow|trie|prefiksow|segment)).*(\bdrzew|\btree\b|preorder|postorder)",
     {"graph": 0.6}),
    ("lca", "LCA (najniższy wspólny przodek)",
     ["lca", "najniższy wspólny przodek", "lowest common ancestor", "binary lifting", "skoki potęgowe",
      "wspólny przodek"],
     r"\blca\b|wspolny przodek|binary lifting|skoki potegow", {"tree": 0.9}),
    ("segment_tree", "Drzewo przedziałowe",
     ["drzewo przedziałowe", "drzewa przedziałowe", "segment tree", "segtree", "seg tree", "przedziałówka",
      "drzewo przedziałów", "drzewko przedziałowe", "drzewo punkt przedział", "drzewo przedziałowe punkt przedział",
      "przedzialowe", "drzewo przedzialowe", "drzewo segmentowe"],
     r"przedzial|segment tree|segtree", {"data_structures": 0.6}),
    ("lazy_segment_tree", "Drzewo przedziałowe z leniwą propagacją",
     ["lazy propagation", "leniwa propagacja", "lazy", "przedział przedział", "drzewo przedział przedział",
      "leniwe drzewo przedziałowe"],
     r"\blazy|leniw|przedzial przedzial", {"segment_tree": 0.95}),
    ("fenwick", "Drzewo Fenwicka (potęgowe)",
     ["drzewo fenwicka", "fenwick", "binary indexed tree", "drzewo potęgowe", "fenwik", "bit fenwick"],
     r"fenwick|fenwik|drzew\w* potegow|binary indexed", {"data_structures": 0.6}),
    ("sparse_table", "Sparse table (RMQ)",
     ["sparse table", "tablica rzadka", "rmq", "range minimum query"], r"sparse|\brmq\b|tablica rzadk",
     {"data_structures": 0.5}),
    ("heap", "Kopiec / kolejka priorytetowa",
     ["kopiec", "kopca", "kopce", "kopcu", "heap", "priority_queue", "kolejka priorytetowa", "pq", "priority queue",
      "kopiec binarny", "make_heap"],
     r"kop(iec|ca|cu|ce)|heap|priorytet", {"data_structures": 0.6}),
    ("stack", "Stos",
     ["stos", "stack", "std::stack", "lifo", "odwrotna notacja polska", "onp", "stos monotoniczny",
      "monotonic stack"], r"\bstos|stack|notacja polska|\bonp\b", {"data_structures": 0.4}),
    ("queue", "Kolejka / deque",
     ["kolejka", "queue", "std::queue", "fifo", "deque", "kolejka dwukierunkowa", "kolejka dwustronna",
      "kolejka monotoniczna"], r"^(?!.*priorytet).*(kolejk|queue|deque)", {"data_structures": 0.4}),
    ("stl_assoc", "Map / set (kontenery asocjacyjne)",
     ["map", "mapa", "set", "zbiór", "multiset", "multimap", "kontenery asocjacyjne", "std::map", "std::set",
      "unordered_map", "unordered_set", "słownik", "mapy", "sety", "hashmapa"],
     r"\bmap(a|y)?\b|\bset\b|asocjac|multiset|unordered", {"stl": 0.7}),
    ("stl", "STL",
     ["stl", "podstawy stl", "biblioteka standardowa", "standard template library"], r"\bstl\b", {}),
    ("structs", "Pary, krotki, struktury",
     ["pair", "para", "pary", "tuple", "krotki", "krotka", "struct", "struktura", "struktury", "pary krotki struktury"],
     r"\bpary\b|krotk|^struktury?$|\bstruct\b", {}),
    ("data_structures", "Struktury danych",
     ["struktury danych", "data structures", "struktura danych"], r"struktur\w* danych|data structur", {}),
    ("recursion", "Rekurencja",
     ["rekurencja", "recursion", "rekurencyjnie", "rekursja", "funkcja rekurencyjna", "rekurencyjna"],
     r"rekuren|rekurs|recurs", {}),
    ("divide_conquer", "Dziel i zwyciężaj",
     ["dziel i zwyciężaj", "divide and conquer", "dziel i rządź", "divide & conquer"], r"dziel i (zwyc|rzadz)|divide",
     {"recursion": 0.6}),
    ("math", "Matematyka / teoria liczb",
     ["matematyka", "teoria liczb", "number theory", "math", "matma", "liczby", "kombinatoryka"],
     r"matemat|teori\w* liczb|\bmath|kombinatory", {}),
    ("gcd", "NWD / NWW (Euklides)",
     ["nwd", "nww", "gcd", "lcm", "euklides", "algorytm euklidesa", "największy wspólny dzielnik",
      "najmniejsza wspólna wielokrotność", "__gcd"], r"\bnwd\b|\bnww\b|\bgcd\b|\blcm\b|euklid", {"math": 0.85}),
    ("sieve", "Liczby pierwsze, sito, dzielniki",
     ["sito", "sito eratostenesa", "sieve", "liczby pierwsze", "pierwszość", "test pierwszości", "faktoryzacja",
      "dzielniki", "rozkład na czynniki", "dzielniki pierwsze", "eratostenes", "primes"],
     r"\bsit(o|a)\b|eratosten|sieve|pierwsz|faktoryz|dzielnik", {"math": 0.85}),
    ("fast_pow", "Szybkie potęgowanie",
     ["szybkie potęgowanie", "fast pow", "binpow", "potęgowanie modulo", "fast exponentiation", "potęgowanie",
      "potegowanie binarne", "power mod"], r"potegow|binpow|fast pow|exponent", {"math": 0.85}),
    ("bignum", "Duże liczby (arytmetyka na napisach)",
     ["duże liczby", "dodawanie dużych liczb", "big integer", "bignum", "arytmetyka dużych liczb",
      "mnożenie dużych liczb", "długa arytmetyka", "bigint"], r"duz\w* liczb|bignum|big ?int|dlug\w* arytm",
     {"math": 0.6, "strings": 0.4}),
    ("strings", "Napisy i znaki",
     ["napisy", "teksty", "string", "stringi", "znaki", "tekst", "szyfr cezara", "szyfr", "char", "palindrom",
      "napis", "znaki i teksty"], r"tekst|napis|\bznak|string|szyfr|palindrom|cezar", {}),
    ("hashing", "Haszowanie",
     ["hashowanie", "haszowanie", "hashing", "hash", "hasz", "rolling hash", "hash stringów"], r"hash|hasz", {"strings": 0.5}),
    ("kmp", "KMP / funkcja prefiksowa",
     ["kmp", "knuth morris pratt", "funkcja prefiksowa", "prefix function", "wyszukiwanie wzorca"],
     r"\bkmp\b|funkcj\w* prefiksow|wzorc", {"strings": 0.8}),
    ("geometry", "Geometria",
     ["geometria", "geometry", "iloczyn wektorowy", "otoczka wypukła", "pole wielokąta", "punkty na płaszczyźnie",
      "cross product", "convex hull", "geometryczne"], r"geometr|otoczk|wielokat|convex", {"math": 0.5}),
    ("bitmask", "Operacje bitowe / maski",
     ["operacje bitowe", "maski bitowe", "maska bitowa", "xor", "bitmask", "bity", "bitowo", "popcount",
      "dp na maskach"], r"bitow|bitmask|\bxor\b|\bbity\b", {}),
    ("arrays", "Tablice i wektory",
     ["tablice", "tablica", "vector", "wektor", "tablica dwuwymiarowa", "t[][]", "macierz", "vectory"],
     r"tablic|vector|\bt\[\]\[\]|wektor|macierz(?! sasiedztw)", {}),
    ("basics", "Podstawy (pętle, warunki)",
     ["pętla", "pętle", "petla", "instrukcja warunkowa", "warunki", "if else", "for", "while", "podstawy",
      "pętla for"], r"\bpetl|warunkow|^podstaw(?!y stl)", {}),
    ("simulation", "Symulacja", ["symulacja", "simulation", "symulowanie"], r"symulac|simulat", {}),
    ("interactive", "Zadania interaktywne", ["interaktywne", "interactive", "zadanie interaktywne"],
     r"interaktyw|interactive", {}),
    ("testing", "Testowanie i debugowanie",
     ["testowanie", "debugowanie", "testy", "debug", "brut", "brute force", "testerka"], r"testowan|debug", {}),
    ("coordinate_compression", "Kompresja współrzędnych",
     ["kompresja współrzędnych", "coordinate compression", "skalowanie", "przenumerowanie"],
     r"kompresj|skalowani|przenumer", {}),
    ("backtracking", "Nawroty (backtracking)",
     ["backtracking", "nawroty", "przeszukiwanie z nawrotami", "wszystkie możliwości"], r"nawrot|backtrack",
     {"recursion": 0.7}),
    ("bipartite", "Graf dwudzielny", ["graf dwudzielny", "bipartite", "dwudzielność", "kolorowanie grafu"],
     r"dwudziel|bipartit", {"graph": 0.8}),
]

CONCEPTS += [
    ("bitmask_dp", "DP na maskach bitowych", ["dp na maskach", "bitmask dp", "dynamik na maskach", "maski dp"],
     r"dp na mask|dynami\w* na mask|bitmask dp", {"dp": 0.9, "bitmask": 0.8}),
    ("grid", "Plansza (siatka 2D)", ["plansza", "siatka", "grid", "labirynt", "mapa 2d", "plansza 2d"],
     r"\bplansz|\bsiatk|labirynt|\bgrid\b", {}),
    ("convex_hull", "Otoczka wypukła", ["otoczka wypukła", "convex hull", "otoczka", "graham", "andrew"],
     r"otoczk|convex hull", {"geometry": 0.9}),
    ("combinatorics", "Kombinatoryka", ["kombinatoryka", "dwumian newtona", "symbol newtona", "permutacje",
                                         "ile sposobów", "zliczanie"], r"kombinator|dwumian|newton", {"math": 0.8}),
    ("mod_inverse", "Odwrotność modularna", ["odwrotność modularna", "modular inverse", "odwrotność modulo"],
     r"odwrotno\w* modul", {"math": 0.8}),
    ("matrix_pow", "Potęgowanie macierzy", ["potęgowanie macierzy", "matrix exponentiation", "matrix power"],
     r"potegowanie macierzy|matrix pow", {"math": 0.8, "fast_pow": 0.6}),
    ("trie", "Drzewo trie", ["trie", "drzewo trie", "drzewo prefiksowe", "prefix tree"], r"\btrie\b|drzew\w* prefiks",
     {"strings": 0.8}),
    ("z_function", "Funkcja Z", ["funkcja z", "z function", "z-function", "algorytm z"], r"funkcj\w* z\b",
     {"strings": 0.8}),
    ("manacher", "Algorytm Manachera", ["manacher", "palindromy manacher"], r"manacher", {"strings": 0.8}),
    ("bridges", "Mosty i punkty artykulacji", ["mosty", "bridges", "punkty artykulacji", "articulation points"],
     r"\bmost(y|ow)\b|artykulac", {"graph": 0.85}),
    ("flow", "Maksymalny przepływ", ["przepływ", "max flow", "maksymalny przepływ", "dinic", "flow"],
     r"przeplyw|\bflow\b|dinic", {"graph": 0.85}),
    ("matching", "Skojarzenia", ["skojarzenie", "skojarzenia", "matching", "kuhn", "hopcroft karp"],
     r"skojarz|matching", {"graph": 0.85}),
    ("mo", "Algorytm Mo", ["algorytm mo", "mo's algorithm", "mo"], r"algorytm mo\b", {}),
    ("sqrt_decomp", "Dekompozycja pierwiastkowa", ["dekompozycja pierwiastkowa", "sqrt decomposition",
                                                    "bloki sqrt", "pierwiastki"], r"pierwiastkow|sqrt decomp",
     {"data_structures": 0.5}),
    ("meet_in_middle", "Meet in the middle", ["meet in the middle", "spotkanie w połowie", "mitm"],
     r"meet in the middle", {}),
    ("bitset", "Bitset", ["bitset", "std::bitset", "bitsety"], r"\bbitset", {"bitmask": 0.6}),
]
_AD_MAP = {"string_processing": "strings", "sliding_window": "two_pointers", "monotonic_stack": "stack"}
_ORDER = {c[0]: i for i, c in enumerate(CONCEPTS)}
_GENERAL = {"graph", "dp", "math", "data_structures", "stl", "tree", "recursion", "strings", "arrays", "basics",
            "structs", "shortest_path", "sorting"}
CONCEPT_BY_ID = {c[0]: {"id": c[0], "name": c[1], "aliases": c[2], "re": re.compile(c[3]) if c[3] else None,
                        "parents": c[4]} for c in CONCEPTS}

_GENERIC_TERMS = {T.stem(w) for w in ("algorytm", "algorytmy", "problem", "zadanie", "metoda", "struktura", "liczby",
                                      "algorithm", "na", "po", "z")}
_COMMENT_SKIP = {"basics", "arrays", "structs", "stl", "stl_assoc", "testing", "simulation", "math", "strings", "queue",
                 "stack", "data_structures", "graph", "tree", "sorting", "recursion", "grid", "combinatorics"}
_NAME_SKIP = {"basics", "arrays", "structs", "testing", "math", "stl_assoc", "stl", "simulation"}

# Wskazówki w treści zadania (słabe — tylko jako dodatkowy sygnał dla zapytań typu „najkrótsza ścieżka”).
STATEMENT_HINTS = [
    ("shortest_path", r"najkrotsz\w* (droga|drogi|sciezk|tras|czas)|najmniejsz\w* (odleglosc|koszt\w* dojazd)"),
    ("graph", r"wierzcholk|krawedzi|krawedz\w* (laczac|pomiedzy)|graf\w*\b"),
    ("tree", r"\bdrzew(o|a|ie|em)\b(?! przedzial)"),
    ("strings", r"\bnapis\w*|slow(o|a) (sklad|zlozon)"),
    ("geometry", r"punkt\w* na plaszczyz|wspolrzedn\w* (x|punkt)|wielokat"),
]

# Słowa kluczowe STL/kodu wyciągane do pola „stl” (pokazywane jako „użyte w kodzie”).
STL_NAMES = """vector map set multiset multimap unordered_map unordered_set priority_queue queue deque stack pair tuple
bitset string array list sort stable_sort lower_bound upper_bound binary_search equal_range next_permutation reverse
unique accumulate partial_sum max_element min_element nth_element swap fill memset iota __gcd gcd lcm
__builtin_popcount __builtin_popcountll __builtin_clz __builtin_ctz greater less make_pair emplace_back push_back
emplace insert erase find count getline setprecision fixed sync_with_stdio tie make_heap push_heap pop_heap
sqrt pow abs llabs log2 to_string stoi stoll substr mt19937 shuffle iterator auto lambda function""".split()
_STL_SET = set(STL_NAMES)
_CODE_STOP = set("""i j k n m x y a b c t q s l r p v w u d e f g h o z ans res wynik main int long double char bool
void return for while if else cin cout endl std using namespace include bits stdc define const auto true false
signed unsigned short printf scanf size begin end first second push_back""".split())


def _compact(s: str) -> str:
    return "".join(words_of(s))


class _ConceptMatcher:
    """Rozpoznaje pojęcia w zapytaniu: frazy (także w odmianie) + tolerancja literówek na zwartej formie."""

    def __init__(self):
        self.aliases: list[tuple[str, str, str, int, str]] = []   # (cid, alias, compact, nwords, stemkey)
        seen = set()
        extra: dict[str, list[str]] = defaultdict(list)
        # wzbogacenie aliasów o grupy z data/synonyms.json, gdy któraś fraza grupy jest aliasem pojęcia
        try:
            raw = json.loads((config.DATA_DIR / "synonyms.json").read_text(encoding="utf-8"))
            groups = raw.get("groups", []) if isinstance(raw, dict) else raw
        except (OSError, json.JSONDecodeError):
            groups = []
        alias_owner = {}
        for cid, c in CONCEPT_BY_ID.items():
            for a in c["aliases"]:
                alias_owner.setdefault(_compact(a), cid)
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, list):
                continue
            owners = Counter(alias_owner[_compact(x)] for x in g if isinstance(x, str) and _compact(x) in alias_owner)
            if owners:
                cid = owners.most_common(1)[0][0]
                for x in g:
                    if isinstance(x, str) and _compact(x) not in alias_owner:
                        extra[cid].append(x)
        for cid, c in CONCEPT_BY_ID.items():
            for a in list(c["aliases"]) + extra.get(cid, []):
                ws = words_of(a)
                comp = "".join(ws)
                if len(comp) < 2:
                    continue
                key = (cid, comp)
                if key in seen:
                    continue
                seen.add(key)
                self.aliases.append((cid, a, comp, len(ws), " ".join(_root_word(w) for w in ws)))
        self.single_terms: dict[str, set[str]] = defaultdict(set)   # term → pojęcia, dla których jest 1-wyrazowym aliasem
        for cid, a, comp, n, sk in self.aliases:
            if n == 1:
                for t in T.terms(a):
                    self.single_terms[t].add(cid)
        self.by_compact: dict[str, list[int]] = defaultdict(list)
        self.by_stem: dict[str, list[int]] = defaultdict(list)
        for i, (_cid, _a, comp, _n, sk) in enumerate(self.aliases):
            self.by_compact[comp].append(i)
            self.by_stem[sk].append(i)

    @staticmethod
    def _maxd(n: int) -> int:
        return 0 if n <= 3 else 1 if n <= 7 else 2 if n <= 14 else 3

    def match(self, query: str, vocab_known=None, exact_only: bool = False) -> list[dict]:
        """[{id, name, alias, span:(i,j), fuzzy: bool, weight, matched_text}] — najdłuższe dopasowania, bez nakładek."""
        ws = words_of(query)
        if not ws:
            return []
        used = [False] * len(ws)
        out: list[dict] = []
        have = set()
        for n in range(min(5, len(ws)), 0, -1):
            for i in range(0, len(ws) - n + 1):
                if any(used[i:i + n]):
                    continue
                span = ws[i:i + n]
                comp = "".join(span)
                sk = " ".join(_root_word(w) for w in span)
                hits = [(0, j) for j in self.by_compact.get(comp, [])] + [(0, j) for j in self.by_stem.get(sk, [])]
                if not hits and len(comp) >= 4 and not exact_only:
                    md = self._maxd(len(comp))
                    if vocab_known and n == 1 and vocab_known(span[0]):
                        md = min(md, 1) if len(comp) >= 7 else 0
                    if md:
                        for j, (_cid, _a, acomp, an, _sk) in enumerate(self.aliases):
                            if len(acomp) < 4 or abs(len(acomp) - len(comp)) > md or abs(an - n) > 1:
                                continue
                            if acomp[0] != comp[0] and len(comp) < 9:
                                continue
                            d = T.levenshtein(comp, acomp, limit=md)
                            if d <= md:
                                hits.append((d, j))
                if not hits:
                    continue
                hits.sort(key=lambda h: (h[0], -self.aliases[h[1]][3]))
                best_d = hits[0][0]
                cids_here = []
                for d, j in hits:
                    if d != best_d:
                        continue
                    cid = self.aliases[j][0]
                    if cid in cids_here:
                        continue
                    cids_here.append(cid)
                    if cid in have:
                        continue
                    have.add(cid)
                    out.append({"id": cid, "name": CONCEPT_BY_ID[cid]["name"], "alias": self.aliases[j][1],
                                "span": (i, i + n), "fuzzy": best_d > 0, "text": " ".join(span),
                                "weight": 1.0 if best_d == 0 else (0.9 if best_d == 1 else 0.8)})
                for k in range(i, i + n):
                    used[k] = True
        return out

    def in_text(self, text_norm: str) -> list[str]:
        """Pojęcia, których regex tematu pasuje do (znormalizowanego) tekstu."""
        res = []
        for cid, c in CONCEPT_BY_ID.items():
            rx = c["re"]
            if rx is not None and rx.search(text_norm):
                res.append(cid)
        return res


_matcher: _ConceptMatcher | None = None
_matcher_lock = threading.Lock()


def matcher() -> _ConceptMatcher:
    global _matcher
    with _matcher_lock:
        if _matcher is None:
            _matcher = _ConceptMatcher()
        return _matcher


def concepts_in_topic(topic: str | None) -> list[str]:
    if not topic:
        return []
    t = norm(topic)
    found = matcher().in_text(t)
    # „Drzewa przedziałowe” to nie „drzewa” w sensie grafowym; „Sortowanie topologiczne” to nie „sortowanie”
    return found


# ============================================================================================ cechy kodu
_RX = lambda p, f=0: re.compile(p, f | re.M)  # noqa: E731

_R_LB = _RX(r"\b(lower_bound|upper_bound|binary_search|equal_range|partition_point)\s*\(")
_R_MID = _RX(r"\b(\w+)\s*=\s*\(?\s*\(?\s*(\w+)\s*\+\s*(\w+)\s*\)?\s*(?:/\s*2|>>\s*1)\b")
_R_MID2 = _RX(r"\b(\w+)\s*=\s*(\w+)\s*\+\s*\(\s*(\w+)\s*-\s*\w+\s*\)\s*(?:/\s*2|>>\s*1)")
_R_WHILE_LR = _RX(r"while\s*\(\s*\w+\s*(?:<|<=)\s*\w+\s*\)|while\s*\(\s*\w+\s*-\s*\w+\s*>\s*1\s*\)|"
                  r"while\s*\(\s*\w+\s*\+\s*1\s*<\s*\w+\s*\)")
_R_PQ = _RX(r"\bpriority_queue\s*<")
_R_GREATER = _RX(r"greater\s*<|push\s*\(\s*\{?\s*-|make_pair\s*\(\s*-|emplace\s*\(\s*-")
_R_RELAX = _RX(r"\b\w+\s*\[[^\]\n]{1,40}\]\s*\+\s*\w+(?:\.\w+)?(?:\s*\[[^\]\n]{1,40}\])?\s*<\s*\w+\s*\[|"
               r"\b\w+\s*\[[^\]\n]{1,40}\]\s*>\s*\w+\s*\[[^\]\n]{1,40}\]\s*\+")
_R_DISTNAME = _RX(r"\b(dist|dis|odl|odleg\w*|odleglosc\w*|d)\s*\[", re.I)
_R_ADJ = _RX(r"\b\w+\s*\[\s*[\w+\-]+\s*\]\s*\.\s*(?:push_back|emplace_back)\s*\(")
_R_ADJ_DECL = _RX(r"vector\s*<\s*(?:int|long long|ll|pair\s*<[^<>]*>|edge|kraw\w*)\s*>\s*\w+\s*\[")
_R_QUEUE = _RX(r"\bqueue\s*<")
_R_FRONT = _RX(r"\.\s*front\s*\(\s*\)")
_R_DEQUE = _RX(r"\bdeque\s*<")
_R_STACK = _RX(r"\bstack\s*<")
_R_FOR_NEIGH = _RX(r"for\s*\(\s*(?:auto|int|long long|pair\s*<[^>]*>)\s*&?\s*\[?\s*[\w, ]+\]?\s*:\s*\w+\s*\[")
_R_VIS = _RX(r"\b(vis\w*|odw\w*|visited|byl\w*|odwiedz\w*|used)\s*\[\s*\w+\s*\]\s*=\s*(true|1)\b", re.I)
_R_INDEG = _RX(r"\b(indeg\w*|in_deg\w*|deg_in|stopien\w*|stop\w*|wch\w*|deg|in)\s*\[[^\]]+\]\s*(--|==\s*0|-=\s*1)|"
               r"--\s*(indeg\w*|in_deg\w*|deg\w*|stopien\w*|stop\w*|wch\w*|in)\s*\[", re.I)
_R_DSU_PC = _RX(r"return\s+(\w+)\s*\[\s*(\w+)\s*\]\s*=\s*\w+\s*\(\s*\1\s*\[\s*\2\s*\]\s*\)")
_R_DSU_ROOT = _RX(r"if\s*\(\s*(\w+)\s*\[\s*(\w+)\s*\]\s*==\s*\2\s*\)\s*\{?\s*return\s+\2|"
                  r"while\s*\(\s*(\w+)\s*\[\s*(\w+)\s*\]\s*!=\s*\4\s*\)")
_R_DSU_NAME = _RX(r"\b(find|Find|fin|znajdz|Znajdz|szukaj|lider|repr|FIND|findd|f)\s*\(\s*\w+\s*\)")
_R_UNION = _RX(r"\b(union\w*|Union\w*|unia|Unia|polacz\w*|Polacz\w*|lacz\w*|merge|unite|onion|join)\s*\(", re.I)
_R_SEG = _RX(r"\b\w+\s*\*\s*2\s*\+\s*1\b|\b2\s*\*\s*\w+\s*\+\s*1\b|<<\s*1\s*\|\s*1|\(\s*\w+\s*<<\s*1\s*\)\s*\+\s*1")
_R_SEG_NAME = _RX(r"\b(tree|seg\w*|drzewo|drzew|tr|st|t|d|tab|przedz\w*|segtree|segment)\s*\[\s*(\w+\s*\*\s*2|2\s*\*"
                  r"\s*\w+|\w+\s*<<\s*1)", re.I)
_R_SEG_BU = _RX(r"\b(\w+)\s*(?:/=\s*2|>>=\s*1)|\b(\w+)\s*=\s*\2\s*/\s*2\b")
_R_BASE = _RX(r"\b(base|BASE|M|pot|potega|R|size|SIZE|rozm)\s*=\s*1\s*<<|\bwhile\s*\(\s*\w+\s*<\s*\w+\s*\)\s*\w+\s*\*=\s*2")
_R_LAZY = _RX(r"\b(lazy|laz|push|propag\w*|spych\w*|leniw\w*|odloz\w*|add)\s*[\[(]", re.I)
_R_FEN = _RX(r"\b\w+\s*&\s*\(?\s*-\s*\w+\s*\)?|\b\w+\s*&\s*\(\s*~\s*\w+\s*\+\s*1\s*\)")
_R_PREF = _RX(r"\b(\w+)\s*\[\s*(\w+)\s*\]\s*=\s*\1\s*\[\s*\2\s*-\s*1\s*\]\s*\+|\b(\w+)\s*\[\s*(\w+)\s*\+\s*1\s*\]\s*="
              r"\s*\3\s*\[\s*\4\s*\]\s*\+|\b(\w+)\s*\[\s*(\w+)\s*\]\s*\+=\s*\5\s*\[\s*\6\s*-\s*1\s*\]|\bpartial_sum\s*\(")
_R_PREF_NAME = _RX(r"\b(pref\w*|prefix\w*|sumy|sum_pref|ps|psum)\s*\[", re.I)
_R_SORT = _RX(r"\b(sort|stable_sort|qsort)\s*\(")
_R_SWAPCALL = _RX(r"\bswap\s*\(")
_R_DP = _RX(r"\b(dp|DP|dyn|best|opt|memo|f|F)\w*\s*\[[^\]]*\](?:\s*\[[^\]]*\])?\s*=\s*(?:std::)?(max|min)\s*\(|"
            r"\b(dp|DP|dyn|memo)\w*\s*\[")
_R_KNAP = _RX(r"for\s*\(\s*(?:int|long long|ll)?\s*\w+\s*=\s*\w+\s*;\s*\w+\s*>=\s*\w+(?:\s*\[\s*\w+\s*\])?\s*;\s*"
              r"\w+\s*--|\b(waga|wagi|ciezar\w*|pojemn\w*|plecak\w*|W)\s*\[", re.I)
_R_HEAP = _RX(r"\b(make_heap|push_heap|pop_heap)\s*\(|\b(heap|kopiec|kopca|kop)\s*\[", re.I)
_R_ASSOC = _RX(r"\b(map|set|multiset|multimap|unordered_map|unordered_set)\s*<")
_R_GCD = _RX(r"\b(__gcd|gcd|nwd|NWD|lcm|nww|NWW|euklides)\s*\(")
_R_EUCL = _RX(r"while\s*\(\s*\w+\s*(?:!=\s*0|>\s*0)?\s*\)\s*\{[^}]*%[^}]*\}", re.S)
_R_SIEVE = _RX(r"for\s*\([^;]*?(\w+)\s*=\s*(\w+)\s*\*\s*\2\s*;[^;]*;\s*\1\s*\+=\s*\2\s*\)|"
               r"for\s*\([^;]*?(\w+)\s*=\s*(?:2\s*\*\s*(\w+)|(\w+)\s*\+\s*\5)\s*;[^;]*;\s*\3\s*\+=\s*(?:\4|\5)\s*\)")
_R_SIEVE_NAME = _RX(r"\b(sito|sieve|prime\w*|pierwsz\w*|erat\w*|is_prime|composite)\s*\[", re.I)
_R_POW = _RX(r"\b(\w+)\s*=\s*\(?\s*\1\s*\*\s*\1\s*\)?\s*%|\b(\w+)\s*\*=\s*\2\s*;|(?:&\s*1\b|%\s*2\s*==\s*1)")
_R_POW_NAME = _RX(r"\b(pot\w*|power|pw|binpow|fpow|qpow|fastpow|modpow|potega)\s*\(", re.I)
_R_BIGNUM = _RX(r"-\s*'0'|\+\s*'0'")
_R_CARRY = _RX(r"\b(carry|przen\w*|reszta|nad\w*|przeniesienie)\b", re.I)
_R_STRCH = _RX(r"[-+]\s*'[aA]'|\b(toupper|tolower|isalpha|isdigit|isupper|islower)\s*\(|\bgetline\s*\(")
_R_GEO = _RX(r"\b(cross|iloczyn\w*|wektor\w*|det|orient\w*|atan2|hypot|ccw|otoczk\w*)\s*\(|struct\s+(Point|Punkt|"
             r"punkt|pkt|P|pt)\b", re.I)
_R_BITS = _RX(r"__builtin_popcount|\(\s*1\s*<<\s*\w+\s*\)|\b1\s*<<\s*\w+|\b\w+\s*>>\s*\w+\s*&\s*1|\^=")
_R_RECURSIVE_FN = None  # wyliczane z find_functions
_R_TWO_PTR = _RX(r"while\s*\(\s*\w+\s*<\s*\w+\s*&&|\b(lewy|prawy|left|right|lo|hi|pocz|kon|glowa|ogon|head|tail)\b"
                 r"\s*(\+\+|--|\+=)", re.I)
_R_LCA = _RX(r"\b(lca|LCA)\s*\(|\b(up|jump|skok\w*|anc|par|przodek|jmp)\s*\[\s*\w+\s*\]\s*\[\s*\w+\s*\]\s*=\s*\2\s*\[")
_R_FLOYD = _RX(r"\[\s*(\w+)\s*\]\s*\[\s*(\w+)\s*\]\s*\+\s*\w+\s*\[\s*\2\s*\]\s*\[\s*(\w+)\s*\]")
_R_DEFINE = _RX(r"^\s*#\s*define\s+(\w+)(\([^)]*\))?\s+(.*?)\s*$")
_R_TYPEDEF = _RX(r"^\s*(?:typedef\s+(.+?)\s+(\w+)\s*;|using\s+(\w+)\s*=\s*(.+?)\s*;)")
_R_INCLUDE = _RX(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]")
_BOILERPLATE = re.compile(r"online c\+\+ compiler|code, compile, run and debug|welcome to gdb online|"
                          r"onlinegdb|write your code in this editor", re.I)


def _strip(code: str, toks=None) -> tuple[str, list[str]]:
    """(kod bez komentarzy i literałów, lista komentarzy)."""
    comments = []
    if L is not None:
        try:
            if toks is None:
                toks = L.tokenize(code)
            out = list(code)
            for t in toks:
                if t.kind == "comment":
                    comments.append(t.text)
                    rng = range(t.pos, t.end)
                elif t.kind in ("str", "char"):
                    rng = range(t.pos + 1, t.end - 1)
                else:
                    continue
                for k in rng:
                    if out[k] != "\n":
                        out[k] = " "
            return "".join(out), comments
        except Exception:  # noqa: BLE001
            pass
    comments = re.findall(r"//[^\n]*|/\*.*?\*/", code, re.S)
    nc = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), code, flags=re.S)
    nc = re.sub(r"//[^\n]*", "", nc)
    return nc, comments


def _functions(code: str, toks=None) -> list[dict]:
    """Definicje funkcji z listy tokenów (lżej niż `cpp_lexer.find_functions`): {name, params, body_start, body_end}."""
    if L is None:
        return []
    try:
        if toks is None:
            toks = L.tokenize(code)
        sig = [t for t in toks if t.kind not in ("comment", "pp")]
        pairs = L.match_brackets(sig)
    except Exception:  # noqa: BLE001
        return []
    res = []
    n = len(sig)
    i = 0
    while i < n - 1:
        t = sig[i]
        if t.kind == "id" and sig[i + 1].text == "(" and (i == 0 or sig[i - 1].kind in ("kw", "id")
                                                          or sig[i - 1].text in ("*", "&", ">", "::", "&&")):
            close = pairs.get(i + 1)
            if close is not None:
                j = close + 1
                while j < n and sig[j].text in ("const", "noexcept", "override", "final", "mutable"):
                    j += 1
                if j < n and sig[j].text == "{" and j in pairs:
                    end = pairs[j]
                    res.append({"name": t.text, "params": " ".join(x.text for x in sig[i + 2:close]),
                                "body_start": sig[j].pos, "body_end": sig[end].pos, "line": t.line})
                    i = end + 1
                    continue
        i += 1
    return res


def _line_of(code: str, pos: int) -> int:
    return code.count("\n", 0, pos) + 1


def detect_fallback(code: str, nc: str | None = None, fns: list | None = None) -> list[dict]:
    """Prosty detektor algorytmów na wzorcach (gdy brak `core.algodetect`). [{id, name, confidence, evidence, lines}]."""
    toks = None
    if nc is None or fns is None:
        try:
            toks = L.tokenize(code) if L is not None else None
        except Exception:  # noqa: BLE001
            toks = None
    if nc is None:
        nc, _ = _strip(code, toks)
    found: dict[str, dict] = {}

    def add(cid, conf, ev, m=None):
        cur = found.get(cid)
        line = _line_of(nc, m.start()) if m is not None else None
        if cur is None:
            found[cid] = {"id": cid, "name": CONCEPT_BY_ID.get(cid, {}).get("name", cid), "confidence": 0.0,
                          "evidence": [], "lines": []}
            cur = found[cid]
        cur["confidence"] = min(1.0, cur["confidence"] + conf)
        if ev and ev not in cur["evidence"]:
            cur["evidence"].append(ev)
        if line and line not in cur["lines"]:
            cur["lines"].append(line)

    if fns is None:
        fns = _functions(code, toks)
    recursive = []
    for f in fns:
        if f["name"] == "main":
            continue
        body = nc[f["body_start"] + 1:f["body_end"]]
        if re.search(r"\b" + re.escape(f["name"]) + r"\s*\(", body):
            recursive.append(f)

    # --- wyszukiwanie binarne
    m = _R_LB.search(nc)
    if m:
        add("binary_search", 0.65, m.group(1), m)
    mids = list(_R_MID.finditer(nc)) + list(_R_MID2.finditer(nc))
    seg_like = bool(_R_SEG_NAME.search(nc)) or (bool(_R_SEG.search(nc)) and any(re.search(r"\b(l|r|lo|hi|pocz|kon)\b",
                                                                                          f["params"]) for f in recursive))
    for mm in mids:
        mid = mm.group(1)
        window = nc[max(0, mm.start() - 400): mm.end() + 600]
        if not _R_WHILE_LR.search(window):
            continue
        if re.search(r"\b\w+\s*=\s*" + re.escape(mid) + r"\s*([+-]\s*1)?\s*;", window):
            add("binary_search", 0.75 if not seg_like else 0.5, f"pętla z połową przedziału ({mid} = (…)/2)", mm)
            break
    # --- grafy
    adj = _R_ADJ.search(nc)
    neigh = _R_FOR_NEIGH.search(nc)
    if adj:
        add("graph", 0.5 if len(_R_ADJ.findall(nc)) >= 2 else 0.4, "lista sąsiedztwa", adj)
    if _R_ADJ_DECL.search(nc):
        add("graph", 0.2, "tablica wektorów sąsiadów", _R_ADJ_DECL.search(nc))
    if neigh and (adj or _R_ADJ_DECL.search(nc)):
        add("graph", 0.2, "pętla po sąsiadach", neigh)
    pq = _R_PQ.search(nc)
    relax = _R_RELAX.search(nc)
    if pq and (relax or _R_DISTNAME.search(nc)) and (adj or neigh):
        conf = 0.45 + (0.2 if relax else 0) + (0.15 if _R_GREATER.search(nc) else 0)
        add("dijkstra", conf, "priority_queue + relaksacja odległości", pq)
    q = _R_QUEUE.search(nc)
    if q and _R_FRONT.search(nc) and not pq:
        if adj or neigh or re.search(r"\b(dx|dy|ruchy|kier\w*)\s*\[", nc):
            add("bfs", 0.7, "kolejka (queue) + odwiedzanie sąsiadów", q)
        else:
            add("queue", 0.5, "queue", q)
    elif q:
        add("queue", 0.4, "queue", q)
    if not q and not pq and (adj or _R_ADJ_DECL.search(nc)):
        kq = re.search(r"\b(kolejk\w*|queue\w*|que|kol)\s*(\.\s*push_back|\[)", nc, re.I)
        if kq:
            add("bfs", 0.55, "kolejka na wektorze + sąsiedzi", kq)
    if _R_INDEG.search(nc) and (q or _R_DEQUE.search(nc) or re.search(r"\bstack\s*<|vector\s*<\s*int\s*>\s*\w*kol", nc)):
        add("topo_sort", 0.75, "stopnie wejściowe + kolejka", _R_INDEG.search(nc))
    for f in recursive:
        body = nc[f["body_start"] + 1:f["body_end"]]
        if _R_FOR_NEIGH.search(body) or re.search(r"\b(adj|graf|g|G|v|V|sasiedzi|kraw\w*)\s*\[", body):
            add("dfs", 0.7, f"rekurencyjna funkcja {f['name']}() po sąsiadach", None)
            if _R_DP.search(body) or re.search(r"\b(sum|size|sz|rozm|ile|poddrzew\w*|sub\w*)\s*\[\s*\w+\s*\]\s*\+=",
                                               body):
                add("tree_dp", 0.35, f"wartości liczone w {f['name']}() z dzieci", None)
            break
    st = _R_STACK.search(nc)
    if st:
        add("stack", 0.7, "stack", st)
    if _R_FLOYD.search(nc) and len(re.findall(r"\bfor\s*\(", nc)) >= 3:
        add("floyd", 0.7, "potrójna pętla d[i][k] + d[k][j]", _R_FLOYD.search(nc))
    m = _R_LCA.search(nc)
    if m:
        add("lca", 0.65, "skoki potęgowe / lca()", m)
    # --- find & union
    m = _R_DSU_PC.search(nc)
    if m:
        add("dsu", 0.85, "find() z kompresją ścieżek", m)
    m = _R_DSU_ROOT.search(nc)
    if m and (_R_DSU_NAME.search(nc) or _R_UNION.search(nc)):
        add("dsu", 0.6, "szukanie reprezentanta zbioru", m)
    if pq and re.search(r"\b(in_?tree|w_?drzewie|wDrzewie|inMST|w_mst|dodany|prim\w*)\b", nc, re.I) and (adj or neigh):
        add("mst", 0.55, "Prim: kopiec krawędzi + zbiór wierzchołków w drzewie", pq)
    if found.get("dsu") and _R_SORT.search(nc) and re.search(r"\b(kraw\w*|edges?|e)\s*\[|struct\s+\w*(edge|kraw)", nc,
                                                             re.I):
        add("mst", 0.6, "sortowanie krawędzi + find & union (Kruskal)", None)
    # --- drzewa przedziałowe / Fenwick
    segn = _R_SEG_NAME.search(nc)
    heapish = re.search(r"\b(kopiec|kopca|kopcu|heap\w*|kop_\w+|heapify|przesi\w*|w_gore|w_dol|sift\w*)\b", nc, re.I)
    if heapish and (_R_SEG.search(nc) or segn) and not re.search(r"\b(tree|seg\w*|drzew\w*|przedz\w*)\s*\[", nc, re.I):
        add("heap", 0.7, "kopiec w tablicy (dzieci 2i, 2i+1)", heapish)
    elif segn or (_R_SEG.search(nc) and (recursive or _R_SEG_BU.search(nc))):
        conf = 0.45 + (0.25 if segn else 0) + (0.15 if _R_BASE.search(nc) else 0) + (0.1 if _R_SEG_BU.search(nc) else 0)
        add("segment_tree", conf, "drzewo w tablicy (2·v, 2·v+1)", segn or _R_SEG.search(nc))
        if _R_LAZY.search(nc) and re.search(r"\b(lazy|laz|propag|spych|leniw|odloz)", nc, re.I):
            add("lazy_segment_tree", 0.7, "leniwa propagacja", _R_LAZY.search(nc))
    m = _R_FEN.search(nc)
    if m and re.search(r"\+=\s*\(?\s*\w+\s*&|-=\s*\(?\s*\w+\s*&|=\s*\w+\s*[+-]\s*\(?\s*\w+\s*&\s*\(?-", nc):
        add("fenwick", 0.8, "i & -i (drzewo Fenwicka)", m)
    # --- sumy prefiksowe
    m = _R_PREF.search(nc)
    if m:
        add("prefix_sums", 0.7, "p[i] = p[i-1] + …", m)
    m = _R_PREF_NAME.search(nc)
    if m:
        add("prefix_sums", 0.3, f"tablica {m.group(1)}[]", m)
    # --- sortowanie
    m = _R_SORT.search(nc)
    if m:
        add("sorting", 0.6, m.group(1) + "()", m)
    else:
        for sw in _R_SWAPCALL.finditer(nc):
            if len(re.findall(r"\bfor\s*\(", nc[max(0, sw.start() - 400):sw.start()])) >= 2:
                add("sorting", 0.5, "zagnieżdżone pętle ze swap (sortowanie ręczne)", sw)
                break
    # --- DP
    m = _R_DP.search(nc)
    if m:
        add("dp", 0.6, "tablica dp[] z max/min" if m.group(2) else "tablica dp[]", m)
        if _R_KNAP.search(nc):
            add("knapsack", 0.45, "pętla po pojemności / wagi", _R_KNAP.search(nc))
    # --- kopiec / STL
    if pq:
        add("heap", 0.7, "priority_queue", pq)
    m = _R_HEAP.search(nc)
    if m:
        add("heap", 0.6, "operacje na kopcu", m)
    m = _R_ASSOC.search(nc)
    if m:
        add("stl_assoc", 0.65, m.group(1), m)
    if _R_DEQUE.search(nc):
        add("queue", 0.5, "deque", _R_DEQUE.search(nc))
    # --- matematyka
    m = _R_GCD.search(nc)
    if m:
        add("gcd", 0.7, m.group(1) + "()", m)
    elif _R_EUCL.search(nc) and re.search(r"%\s*\w+\s*;", nc):
        add("gcd", 0.35, "pętla z resztą z dzielenia", _R_EUCL.search(nc))
    m = _R_SIEVE.search(nc)
    if m:
        add("sieve", 0.75, "sito (j = i·i; j += i)", m)
    m = _R_SIEVE_NAME.search(nc)
    if m:
        add("sieve", 0.25, f"tablica {m.group(1)}[]", m)
    m = _R_POW_NAME.search(nc)
    if m and re.search(r"%|/\s*2|>>=?\s*1", nc):
        add("fast_pow", 0.6, f"funkcja {m.group(1)}()", m)
    elif len(_R_POW.findall(nc)) >= 2 and re.search(r"/=\s*2|>>=\s*1", nc):
        add("fast_pow", 0.5, "kwadrat podstawy + połowienie wykładnika", None)
    if _R_BIGNUM.search(nc) and (_R_CARRY.search(nc) or re.search(r"string\b", nc)) and re.search(r"%\s*10|/\s*10", nc):
        add("bignum", 0.6, "cyfry w stringu + przeniesienie", _R_BIGNUM.search(nc))
    m = _R_STRCH.search(nc)
    if m:
        add("strings", 0.45, "operacje na znakach", m)
    m = _R_GEO.search(nc)
    if m:
        add("geometry", 0.55, "punkty / iloczyn wektorowy", m)
    if len(_R_BITS.findall(nc)) >= 2:
        add("bitmask", 0.5, "przesunięcia i maski bitowe", _R_BITS.search(nc))
    recursive = [f for f in recursive if not re.match(r"(?i)(find|fin|znajdz|szukaj|lider|repr|f|gcd|nwd|pot\w*|pw|power"
                                                      r"|binpow|fpow|qpow|query|update|build|zapytanie|aktualizuj)$",
                                                      f["name"])]
    if recursive and not any(k in found for k in ("dfs", "segment_tree", "dsu", "fast_pow", "gcd")):
        add("recursion", 0.55, f"funkcja rekurencyjna {recursive[0]['name']}()", None)
    m = _R_TWO_PTR.search(nc)
    if m and not found.get("binary_search") and re.search(r"while\s*\(", nc):
        add("two_pointers", 0.35, "dwa indeksy przesuwane w pętli", m)
    out = [d for d in found.values() if d["confidence"] >= 0.45]
    for d in out:
        d["confidence"] = round(d["confidence"], 2)
        d["evidence"] = ", ".join(d["evidence"][:3])
    out.sort(key=lambda d: -d["confidence"])
    return out


_algodetect_mod = None
_algodetect_checked = 0.0


def _algodetect():
    """Moduł `oiforge.core.algodetect` (pisze inny agent) — ładowany leniwie, ponawiane co 60 s."""
    global _algodetect_mod, _algodetect_checked
    if _algodetect_mod is not None:
        return _algodetect_mod
    if time.time() - _algodetect_checked < 60:
        return None
    _algodetect_checked = time.time()
    try:
        mod = importlib.import_module("oiforge.core.algodetect")
        if hasattr(mod, "detect"):
            _algodetect_mod = mod
    except Exception:  # noqa: BLE001
        _algodetect_mod = None
    return _algodetect_mod


def detect_algorithms(code: str, nc: str | None = None, fns: list | None = None) -> list[dict]:
    """algodetect (jeśli jest) + własny detektor; łączone po id (maksimum pewności)."""
    res: dict[str, dict] = {}
    mod = _algodetect()
    if mod is not None:
        try:
            for d in mod.detect(code) or []:
                if not isinstance(d, dict) or not d.get("id"):
                    continue
                conf = float(d.get("confidence") or 0.5)
                ev = d.get("evidence")
                if isinstance(ev, list):
                    ev = ", ".join(str(x) for x in ev[:3])
                res[d["id"]] = {"id": d["id"], "name": d.get("name") or CONCEPT_BY_ID.get(d["id"], {}).get("name",
                                                                                                         d["id"]),
                                "confidence": round(min(1.0, conf), 2), "evidence": str(ev or ""),
                                "lines": d.get("lines") or [], "via": "algodetect"}
        except Exception:  # noqa: BLE001 - cudzy moduł nie może zepsuć indeksu
            pass
    fb = detect_fallback(code, nc, fns)
    for d in fb:
        d["via"] = "heurystyka"
    return merge_algos(fb, [dict(v, id=_AD_MAP.get(v["id"], v["id"])) for v in res.values()])


_feat_cache: dict[str, dict] = {}
_feat_lock = threading.Lock()
FEAT_VERSION = 4
_disk: dict = {"loaded": False, "features": {}, "html": {}, "tf": {}, "ad": {}, "dirty": False}


def _cache_file() -> Path:
    return config.CACHE_DIR / "programs_features.json"


def _algo_sig() -> str:
    """Podpis wersji własnych detektorów (zmiana tego pliku = przeliczenie cech kodu)."""
    try:
        own = int(Path(__file__).stat().st_mtime)
    except OSError:
        own = 0
    return f"fb{FEAT_VERSION}-{own}"


def _ad_sig() -> str | None:
    """Podpis wersji `core.algodetect` (None = modułu brak)."""
    mod = _algodetect()
    if mod is None:
        return None
    try:
        return f"ad-{int(Path(mod.__file__).stat().st_mtime)}"
    except (OSError, TypeError, AttributeError):
        return "ad"


def _ad_mode() -> str:
    """background (domyślnie: wyniki algodetect liczone w tle i dołączane po chwili) | sync | off."""
    return os.environ.get("OIFORGE_ALGODETECT", "background")


def _normalize_ad(res) -> list[dict]:
    out = []
    for d in res or []:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        ev = d.get("evidence")
        if isinstance(ev, list):
            ev = ", ".join(str(x) for x in ev[:3])
        cid = _AD_MAP.get(d["id"], d["id"])
        out.append({"id": cid, "name": d.get("name") or cid, "confidence": round(min(1.0, float(d.get("confidence")
                                                                                              or 0.5)), 2),
                    "evidence": str(ev or ""), "lines": [x for x in (d.get("lines") or []) if isinstance(x, int)][:20],
                    "via": "algodetect"})
    return out


def _ad_cached(h: str) -> list[dict] | None:
    sig = _ad_sig()
    if sig is None:
        return None
    e = _disk["ad"].get(h)
    if isinstance(e, dict) and e.get("sig") == sig:
        return e.get("algos") or []
    return None


def merge_algos(fb: list[dict], ad: list[dict] | None) -> list[dict]:
    res = {a["id"]: dict(a) for a in (ad or [])}
    for a in fb:
        cur = res.get(a["id"])
        if cur is None or cur["confidence"] < a["confidence"] - 0.15:
            res[a["id"]] = dict(a)
    return sorted(res.values(), key=lambda d: -d["confidence"])


_ad_worker_state = {"thread": None, "done": 0, "total": 0}


def _ad_worker(items: dict[str, str]) -> None:
    mod = _algodetect()
    sig = _ad_sig()
    if mod is None or sig is None:
        return
    _ad_worker_state.update(done=0, total=len(items))
    for i, (h, code) in enumerate(items.items()):
        try:
            res = _normalize_ad(mod.detect(code))
        except Exception:  # noqa: BLE001
            res = []
        with _feat_lock:
            _disk["ad"][h] = {"sig": sig, "algos": res}
            _disk["dirty"] = True
            if i % 25 == 24:
                _disk_save()
        _ad_worker_state["done"] = i + 1
        time.sleep(0.002)               # oddaj GIL serwerowi
    with _feat_lock:
        _disk_save()
    invalidate()
    try:
        engine()                         # przebuduj indeks z nowymi tagami (w tle)
    except Exception:  # noqa: BLE001
        pass


def _start_ad_worker(items: dict[str, str]) -> None:
    t = _ad_worker_state.get("thread")
    if t is not None and t.is_alive():
        return
    t = threading.Thread(target=_ad_worker, args=(items,), daemon=True, name="programs-algodetect")
    _ad_worker_state["thread"] = t
    t.start()


def _disk_load() -> None:
    if _disk["loaded"]:
        return
    _disk["loaded"] = True
    try:
        d = json.loads(_cache_file().read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("version") == FEAT_VERSION:
            for k in ("features", "html", "tf", "ad"):
                if isinstance(d.get(k), dict):
                    _disk[k] = d[k]
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass


def _disk_save() -> None:
    if not _disk["dirty"]:
        return
    try:
        p = _cache_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": FEAT_VERSION, "features": _disk["features"], "html": _disk["html"],
                                   "tf": _disk["tf"], "ad": _disk["ad"]}, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        os.replace(tmp, p)
        _disk["dirty"] = False
    except OSError:
        pass


def _feats_from_disk(h: str, code: str, sig: str) -> dict | None:
    d = _disk["features"].get(h)
    if not isinstance(d, dict) or d.get("sig") != sig:
        return None
    try:
        f = {k: d[k] for k in ("comments", "headers", "defines", "idents", "stl", "algos", "ntokens")}
        f["shingles"] = frozenset(d.get("shingles") or ())
    except KeyError:
        return None
    f["code_lower"] = code.lower()
    f["code_tight"] = _tight(f["code_lower"])
    f["sha1"] = h
    return f


def code_features(code: str) -> dict:
    """Cechy kodu: komentarze, identyfikatory, STL, nagłówki, makra, algorytmy, shingle (cache po SHA1,
    w pamięci i na dysku w user_data/cache/programs_features.json)."""
    h = hashlib.sha1(code.encode("utf-8", "replace")).hexdigest()
    with _feat_lock:
        if h in _feat_cache:
            return _feat_cache[h]
        _disk_load()
        sig = _algo_sig()
        f = _feats_from_disk(h, code, sig)
        if f is not None:
            _feat_cache[h] = f
            return f
    toks = []
    if L is not None:
        try:
            toks = L.tokenize(code)
        except Exception:  # noqa: BLE001
            toks = []
    nc, comments = _strip(code, toks or None)
    fns = _functions(code, toks) if toks else []
    comments = [c for c in comments if not _BOILERPLATE.search(c)]
    ctext = " ".join(re.sub(r"^\s*(//+|/\*+)|\*+/\s*$", " ", c) for c in comments)
    headers = _R_INCLUDE.findall(code)
    defines = []
    for m in _R_DEFINE.finditer(code):
        defines.append(f"#define {m.group(1)}{m.group(2) or ''} {m.group(3)}".strip())
    for m in _R_TYPEDEF.finditer(nc):
        if m.group(1):
            defines.append(f"typedef {m.group(1)} {m.group(2)}")
        else:
            defines.append(f"using {m.group(3)} = {m.group(4)}")
    idents: Counter = Counter()
    stl: Counter = Counter()
    toks_norm: list[str] = []
    if toks:
        for t in toks:
            if t.kind == "comment" or t.kind == "pp":
                continue
            if t.kind == "id":
                if t.text in _STL_SET:
                    stl[t.text] += 1
                    toks_norm.append(t.text)
                else:
                    if t.text.lower() not in _CODE_STOP and len(t.text) >= 2:
                        idents[t.text] += 1
                    toks_norm.append("ID")
            elif t.kind == "kw":
                toks_norm.append(t.text)
            elif t.kind == "num":
                toks_norm.append("N")
            elif t.kind in ("str", "char"):
                toks_norm.append("S")
            else:
                toks_norm.append(t.text)
    else:
        for w in re.findall(r"[A-Za-z_]\w*|\d+|\S", nc):
            if w in _STL_SET:
                stl[w] += 1
                toks_norm.append(w)
            elif re.match(r"[A-Za-z_]", w):
                if w.lower() not in _CODE_STOP and len(w) >= 2:
                    idents[w] += 1
                toks_norm.append("ID")
            else:
                toks_norm.append("N" if w.isdigit() else w)
    shingles = frozenset(zlib.crc32(" ".join(toks_norm[i:i + 5]).encode("utf-8")) for i in range(max(0, len(toks_norm)
                                                                                                    - 4)))
    algos = detect_fallback(code, nc, fns)
    for a in algos:
        a["via"] = "heurystyka"
    feats = {"comments": fold(ctext.strip()), "headers": headers, "defines": defines[:20],
             "idents": [w for w, _ in idents.most_common(80)], "stl": [w for w, _ in stl.most_common(40)],
             "algos": algos, "shingles": shingles, "ntokens": len(toks_norm),
             "code_lower": code.lower(), "code_tight": _tight(code.lower()), "sha1": h}
    with _feat_lock:
        if len(_feat_cache) > 20000:
            _feat_cache.clear()
        _feat_cache[h] = feats
        if code:
            _disk["features"][h] = {"sig": sig, **{k: feats[k] for k in ("comments", "headers", "defines", "idents",
                                                                           "stl", "algos", "ntokens")},
                                    "shingles": sorted(shingles)}
            _disk["dirty"] = True
    return feats


def _tight(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    return re.sub(r"\s*([^\w\s])\s*", r"\1", s).strip()


_stmt_cache: dict[str, tuple[float, str]] = {}


def read_statement(paths: dict) -> tuple[str, str | None]:
    """(tekst, rodzaj) — rodzaj: txt | html | pdf (bez tekstu) | None."""
    if paths.get("txt"):
        try:
            return paths["txt"].read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n"), "txt"
        except OSError:
            pass
    if paths.get("html"):
        p = paths["html"]
        try:
            mt = p.stat().st_mtime
            key = str(p)
            c = _stmt_cache.get(key)
            if c and c[0] == mt:
                return c[1], "html"
            _disk_load()
            dc = _disk["html"].get(key)
            if isinstance(dc, list) and len(dc) == 2 and dc[0] == mt:
                _stmt_cache[key] = (mt, dc[1])
                return dc[1], "html"
            txt = P.html_to_text(p.read_text(encoding="utf-8", errors="replace"))
            _stmt_cache[key] = (mt, txt)
            _disk["html"][key] = [mt, txt]
            _disk["dirty"] = True
            return txt, "html"
        except OSError:
            pass
    if paths.get("pdf"):
        return "", "pdf"
    return "", None


# ============================================================================================ silnik
FIELDS = {"title": 4.0, "topic": 3.5, "tags": 3.0, "stl": 1.6, "contest": 1.0, "comments": 1.4, "idents": 1.0,
          "statement": 1.0}
FIELD_LABELS = {"title": "nazwa zadania", "topic": "temat rundy", "tags": "tagi", "stl": "w kodzie (STL)",
                "contest": "konkurs", "comments": "komentarz w kodzie", "idents": "identyfikatory w kodzie",
                "statement": "w treści"}
SOURCE_LABEL = {"round": "temat rundy", "statement_topic": "temat w treści", "name": "nazwa zadania",
                "code": "w kodzie", "statement": "w treści", "import": "folder importu", "comment": "komentarz w kodzie"}
_generation = 0
_engine = None
_engine_lock = threading.Lock()


def invalidate() -> None:
    global _generation
    _generation += 1


def _signature(root: Path) -> tuple:
    parts = [str(root), _generation]
    p = root / INDEX_NAME
    try:
        st = p.stat()
        parts += [st.st_mtime_ns, st.st_size]
    except OSError:
        parts.append(None)
    try:
        for src in sorted(x for x in root.iterdir() if x.is_dir()):
            for f in (src / "problems.json",):
                try:
                    parts.append(f.stat().st_mtime_ns)
                except OSError:
                    parts.append(0)
            for cdir in sorted(x for x in src.iterdir() if x.is_dir()):
                sd = cdir / "statements"
                try:
                    parts.append(sd.stat().st_mtime_ns)
                except OSError:
                    parts.append(0)
    except OSError:
        pass
    return tuple(parts)


class Engine:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.docs: dict[str, dict] = {}
        self.order: list[str] = []
        self.idx = S.SearchIndex(FIELDS)
        self._fields: dict[str, dict[str, str]] = {}
        self._ft_cache: dict[str, dict[str, set]] = {}
        self.concepts: dict[str, dict[str, dict]] = {}       # pid -> cid -> {strength, source, evidence}
        self.stmt_vec: dict[str, dict[str, float]] = {}
        self.build_time = 0.0
        self.error = None

    # ------------------------------------------------------------------ budowa
    def build(self) -> "Engine":
        t0 = time.perf_counter()
        data = load_index(self.root)
        self.error = data.get("_error")
        problems_cache: dict[str, dict] = {}
        ad_missing: dict[str, str] = {}
        with _feat_lock:
            _disk_load()
        rounds_by: dict[tuple[str, str], dict[str, tuple[str | None, str]]] = {}
        for rec in data["programs"]:
            if not isinstance(rec, dict):
                continue
            pid = program_id(rec)
            if pid in self.docs:
                continue
            src = str(rec.get("source") or "")
            cid = str(rec.get("contest_id") or "")
            if src not in problems_cache:
                problems_cache[src] = load_problems(self.root, src) if src else {}
            key = (src, cid)
            if key not in rounds_by:
                m = {}
                for p in (problems_cache[src].get(cid) or {}).get("problems", []) or []:
                    if isinstance(p, dict) and p.get("short"):
                        m[str(p["short"]).lower()] = (p.get("round"), p.get("name") or "")
                rounds_by[key] = m
            short = str(rec.get("problem_short") or "")
            pinfo = rounds_by[key].get(short.lower())
            rnd = rec.get("round") or (P.clean_round(pinfo[0]) if pinfo and pinfo[0] else None)
            path = self.root / str(rec.get("path") or "")
            code = ""
            if rec.get("path") and inside(self.root, path) and path.is_file():
                try:
                    code = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    code = ""
            ref = rec.get("statement_ref") if isinstance(rec.get("statement_ref"), dict) else None
            if ref and ref.get("source") and ref.get("contest_id") and ref.get("short"):
                st_paths = find_statement(self.root, str(ref["source"]), str(ref["contest_id"]), str(ref["short"]))
            elif src and cid:
                st_paths = find_statement(self.root, src, cid, short)
            else:
                st_paths = {"txt": None, "html": None, "pdf": None}
            st_text, st_kind = read_statement(st_paths)
            st_fold = fold(st_text)
            feats = code_features(code) if code else code_features("")
            ad = _ad_cached(feats["sha1"]) if code else []
            if ad is None and code:
                if _ad_mode() == "sync":
                    try:
                        ad = _normalize_ad(_algodetect().detect(code))
                    except Exception:  # noqa: BLE001
                        ad = []
                    with _feat_lock:
                        _disk["ad"][feats["sha1"]] = {"sig": _ad_sig(), "algos": ad}
                        _disk["dirty"] = True
                elif _ad_mode() == "background":
                    ad_missing[feats["sha1"]] = code
            algos = merge_algos(feats["algos"], ad)
            topic = topic_of(rnd)
            s_topic = statement_topic(st_fold)
            doc = dict(rec)
            doc.update(id=pid, round=rnd, topic=topic, topic_generic=topic_is_generic(topic),
                       statement_topic=s_topic, statement_kind=st_kind,
                       statement_pending=bool(st_paths["pdf"] and not st_paths["txt"] and not st_paths["html"]),
                       has_pdf=bool(st_paths["pdf"]), _code=code, _statement=st_text, _st_fold=st_fold, _feats=feats,
                       _algos=algos,
                       _st_paths=st_paths)
            if not doc.get("lines") and code:
                doc["lines"] = len(code.splitlines())
            self.docs[pid] = doc
            self.order.append(pid)
            self.concepts[pid] = self._doc_concepts(doc)
            tag_names = []
            for c, info in sorted(self.concepts[pid].items(), key=lambda kv: -kv[1]["strength"]):
                if info["strength"] >= 0.5 and c in CONCEPT_BY_ID:
                    cc = CONCEPT_BY_ID[c]
                    tag_names.append(cc["name"] + " " + " ".join(cc["aliases"][:3]))
            fields = {
                "title": f"{rec.get('problem_name') or ''} {short}",
                "topic": " ".join(x for x in (rnd or "", s_topic or "") if x),
                "tags": " ".join(tag_names),
                "stl": " ".join(feats["stl"] + feats["headers"] + [d for d in feats["defines"]]),
                "contest": f"{rec.get('contest_name') or ''} {cid}",
                "comments": feats["comments"][:4000],
                "idents": " ".join(feats["idents"]),
                "statement": st_fold[:20000],
            }
            fields = {k: fold(v) for k, v in fields.items()}
            self.idx.add(pid, fields, payload=pid)
            self._fields[pid] = fields
        self.idx.build()
        self._build_statement_vectors()
        with _feat_lock:
            _disk_save()
        self.ad_pending = len(ad_missing)
        if ad_missing:
            _start_ad_worker(ad_missing)
        self.build_time = round(time.perf_counter() - t0, 3)
        return self

    def _doc_concepts(self, doc: dict) -> dict[str, dict]:
        out: dict[str, dict] = {}

        def put(cid, strength, source, evidence):
            if cid not in CONCEPT_BY_ID and source != "code":
                return
            cur = out.get(cid)
            if cur is None or cur["strength"] < strength:
                out[cid] = {"strength": round(strength, 3), "source": source, "evidence": evidence}

        topic = doc.get("topic")
        if topic and not doc.get("topic_generic"):
            for cid in concepts_in_topic(topic):
                put(cid, 1.0, "round", doc.get("round") or topic)
        if doc.get("statement_topic"):
            for cid in concepts_in_topic(doc["statement_topic"]):
                put(cid, 0.95, "statement_topic", doc["statement_topic"])
        name = doc.get("problem_name") or ""
        if name:
            for cid in concepts_in_topic(name):
                if cid not in _NAME_SKIP:
                    put(cid, 0.75, "name", name)
        com = (doc["_feats"].get("comments") or "")[:3000]
        if com:
            for m in matcher().match(com, exact_only=True):
                if m["id"] not in _COMMENT_SKIP:
                    put(m["id"], 0.65, "comment", m["text"])
        for a in doc["_algos"]:
            cid = a["id"]
            if cid not in CONCEPT_BY_ID:
                CONCEPT_BY_ID.setdefault(cid, {"id": cid, "name": a.get("name") or cid, "aliases": [a.get("name")
                                                                                                    or cid],
                                               "re": None, "parents": {}})
            put(cid, 0.45 + 0.4 * float(a.get("confidence") or 0.5), "code", a.get("evidence") or "")
        st = norm(doc.get("_st_fold") or "")
        if st:
            for cid, rx in STATEMENT_HINTS:
                m = re.search(rx, st)
                if m:
                    put(cid, 0.4, "statement", m.group(0))
        # domknięcie po rodzicach (2 poziomy)
        for _ in range(2):
            for cid, info in list(out.items()):
                for par, w in (CONCEPT_BY_ID.get(cid, {}).get("parents") or {}).items():
                    s = info["strength"] * w
                    cur = out.get(par)
                    if cur is None or cur["strength"] < s - 1e-9:
                        out[par] = {"strength": round(s, 3), "source": info["source"], "evidence": info["evidence"],
                                    "via": cid}
        return out

    def ft(self, pid: str) -> dict[str, set]:
        """Termy dokumentu per pole (liczone leniwie — potrzebne tylko dla wyświetlanych wyników)."""
        c = self._ft_cache.get(pid)
        if c is None:
            c = {f: set(T.terms(v)) for f, v in (self._fields.get(pid) or {}).items() if v}
            self._ft_cache[pid] = c
        return c

    def _build_statement_vectors(self) -> None:
        df: Counter = Counter()
        tfs = {}
        _disk_load()
        for pid in self.order:
            d = self.docs[pid]
            txt = (d.get("_st_fold") or "")[:20000] + " " + fold(d.get("problem_name") or "")
            h = hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()
            tf = _disk["tf"].get(h)
            if not isinstance(tf, dict):
                tf = dict(Counter(t for t in T.terms(txt) if len(t) >= 3 and not t.isdigit()))
                _disk["tf"][h] = tf
                _disk["dirty"] = True
            tfs[pid] = Counter(tf)
            df.update(tf.keys())
        n = max(1, len(self.order))
        self.stmt_df = df
        self.stmt_n = n
        for pid, tf in tfs.items():
            self.stmt_vec[pid] = self._vec(tf)

    def _vec(self, tf: Counter) -> dict[str, float]:
        n = self.stmt_n
        v = {t: (1 + math.log(c)) * math.log(1 + n / (1 + self.stmt_df.get(t, 0))) for t, c in tf.items()}
        nrm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / nrm for t, x in v.items()}

    @staticmethod
    def _cos(a: dict, b: dict) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(x * b.get(t, 0.0) for t, x in a.items())

    # ------------------------------------------------------------------ filtry
    def _filter_fn(self, filters: dict | None):
        if not filters:
            return None
        f = {k: v for k, v in filters.items() if v not in (None, "", [])}
        if not f:
            return None

        def as_list(v):
            if isinstance(v, (list, tuple, set)):
                return [str(x) for x in v if str(x).strip()]
            return [x.strip() for x in str(v).split(",") if x.strip()]

        contests = [norm(x) for x in as_list(f.get("contest", []))]
        topics = [norm(x) for x in as_list(f.get("topic", []) or f.get("round", []))]
        tags = as_list(f.get("tag", []) or f.get("algorithm", []))
        tag_ids = []
        for t in tags:
            if t in CONCEPT_BY_ID:
                tag_ids.append(t)
            else:
                ms = matcher().match(t)
                tag_ids += [m["id"] for m in ms[:1]]
        dfrom = str(f.get("from") or f.get("date_from") or "")
        dto = str(f.get("to") or f.get("date_to") or "")
        lang = norm(str(f.get("lang") or ""))
        source = str(f.get("source") or "")

        def ok(pid, _payload=None):
            d = self.docs.get(pid)
            if d is None:
                return False
            if contests and norm(d.get("contest_id") or "") not in contests and norm(d.get("contest_name") or "") \
                    not in contests:
                return False
            if topics:
                cand = {norm(d.get("topic") or ""), norm(d.get("round") or ""), norm(d.get("statement_topic") or "")}
                if not (cand & set(topics)):
                    return False
            if tag_ids:
                dc = self.concepts.get(pid, {})
                if not all(dc.get(t, {}).get("strength", 0) >= 0.5 for t in tag_ids):
                    return False
            tm = str(d.get("time") or "")
            if dfrom and tm[:len(dfrom)] < dfrom:
                return False
            if dto and tm[:len(dto)] > dto:
                return False
            if lang and norm(d.get("lang") or "") != lang:
                return False
            if source and d.get("source") != source:
                return False
            return True

        return ok

    # ------------------------------------------------------------------ szukanie
    def analyze_query(self, query: str) -> dict:
        q = (query or "").strip()
        qq = re.sub(r"(?i)\bc\+\+", "cpp", q)
        code_strong = bool(re.search(r"[#_:<>\[\]();=*|^%{}]|\+\+|->|\w\.\w", qq))
        toks = re.findall(r"[A-Za-z_]\w*", qq)
        code_weak = bool(toks) and len(toks) <= 3 and all(t in _STL_SET or (t in _CODE_STOP and len(t) > 2)
                                                          for t in toks)

        def vocab_known(w):
            try:
                return self.idx.has_term(T.stem(w))
            except Exception:  # noqa: BLE001
                return False

        concepts = matcher().match(q, vocab_known)
        corrections = [[c["text"], c["alias"]] for c in concepts if c["fuzzy"]]
        # korekta pozostałych słów (spoza pojęć) słownikiem indeksu
        consumed = set()
        ws = words_of(q)
        for c in concepts:
            consumed.update(ws[c["span"][0]:c["span"][1]])
        rest = " ".join(w for w in ws if w not in consumed)
        fixed_rest = rest
        if rest and hasattr(self.idx, "correct"):
            try:
                cr = self.idx.correct(rest)
                if cr.get("changed"):
                    fixed_rest = cr["text"]
                    corrections += [list(x) for x in cr.get("fixes", [])]
            except Exception:  # noqa: BLE001
                pass
        return {"query": q, "concepts": concepts, "corrections": corrections, "code_strong": code_strong,
                "code_weak": code_weak and not code_strong, "rest": rest, "fixed_rest": fixed_rest}

    def _literal_hits(self, q: str, strong: bool, weak: bool) -> dict[str, tuple[int, str]]:
        """pid -> (nr linii, linia) dla dosłownego wystąpienia zapytania w kodzie."""
        if not (strong or weak):
            return {}
        ql = q.strip().lower()
        tight = _tight(ql)
        if len(tight) < 2:
            return {}
        word = re.fullmatch(r"[a-z_]\w*", tight) is not None
        rx = re.compile(r"(?<![\w])" + re.escape(tight) + r"(?![\w])") if word else None
        out = {}
        for pid in self.order:
            f = self.docs[pid]["_feats"]
            hay = f["code_tight"]
            if (rx.search(hay) if rx else tight in hay):
                code = self.docs[pid]["_code"]
                ln, line = 0, ""
                for i, l in enumerate(code.splitlines(), 1):
                    lt = _tight(l.lower())
                    if (rx.search(lt) if rx else tight in lt):
                        ln, line = i, l.strip()
                        break
                out[pid] = (ln, line)
        return out

    def _code_bigrams(self, pid: str) -> set:
        d = self.docs[pid]
        bg = d.get("_bigrams")
        if bg is None:
            toks = re.findall(r"[a-z_]\w*|\d+|[^\s\w]", d["_feats"]["code_lower"])
            bg = set(toks) | {toks[i] + " " + toks[i + 1] for i in range(len(toks) - 1)}
            d["_bigrams"] = bg
        return bg

    def _partial_code_hits(self, q: str) -> dict[str, tuple[float, list[str]]]:
        """Gdy nikt nie ma dokładnie tego fragmentu: część wspólnych par tokenów zapytania i kodu."""
        toks = re.findall(r"[a-z_]\w*|\d+|[^\s\w]", q.lower())
        if len(toks) < 2:
            return {}
        grams = [toks[i] + " " + toks[i + 1] for i in range(len(toks) - 1)]
        out = {}
        for pid in self.order:
            bg = self._code_bigrams(pid)
            hit = [g for g in grams if g in bg]
            if hit:
                frac = len(hit) / len(grams)
                if frac >= 0.25:
                    out[pid] = (frac, hit)
        return out

    def search(self, query: str, k: int = 10, filters: dict | None = None, with_meta: bool = False):
        flt = self._filter_fn(filters)
        q = (query or "").strip()
        if not q:
            pids = [p for p in self.order if not flt or flt(p)]
            pids.sort(key=lambda p: str(self.docs[p].get("time") or ""), reverse=True)
            res = [self._result(p, 0.0, [], [], None) for p in pids[:k]]
            return (res, {"total": len(pids), "concepts": [], "corrections": [], "facets": self.facets(pids)}) \
                if with_meta else res
        an = self.analyze_query(q)
        concepts = an["concepts"]
        # ---- BM25F: zapytanie + aliasy rozpoznanych pojęć jako dodatkowe termy
        extra: dict[str, float] = {}
        mt = matcher()
        for c in concepts:
            cc = CONCEPT_BY_ID[c["id"]]
            family = {c["id"]} | {k for k, v in CONCEPT_BY_ID.items() if c["id"] in (v.get("parents") or {})}
            for a in [cc["name"]] + cc["aliases"][:12]:
                ts = T.terms(a)
                w = (0.55 if len(ts) == 1 else 0.4) * c["weight"]
                for t in ts:
                    if len(t) < 2 or t in T.STOPWORDS or t in _GENERIC_TERMS:
                        continue
                    # słowo z wielowyrazowej frazy, które samo jest nazwą INNEGO pojęcia (np. „drzewo” w „minimalne
                    # drzewo rozpinające”), nie może ciągnąć wyników w stronę tamtego pojęcia
                    if len(ts) > 1 and mt.single_terms.get(t) and not (mt.single_terms[t] & family):
                        continue
                    extra[t] = max(extra.get(t, 0.0), w)
        bm_query = q if not an["corrections"] else q + " " + " ".join(fx[1] for fx in an["corrections"])
        try:
            bm = self.idx.search(bm_query, k=len(self.order) or 1, filters=(lambda d, p: flt(d)) if flt else None,
                                 extra_terms=sorted(extra.items()))
        except TypeError:   # starsze API bez extra_terms
            bm = self.idx.search(bm_query + " " + " ".join(extra), k=len(self.order) or 1,
                                 filters=(lambda d, p: flt(d)) if flt else None)
        bm_score = {r["id"]: r["score"] for r in bm}
        bm_matched = {r["id"]: r.get("matched", []) for r in bm}
        mx = max(bm_score.values(), default=0.0) or 1.0
        lits = self._literal_hits(q, an["code_strong"], an["code_weak"])
        partial = {}
        if an["code_strong"] and not lits:
            partial = self._partial_code_hits(q)
        cand = set(bm_score) | set(lits) | set(partial)
        cids = [c["id"] for c in concepts]
        if cids:
            for pid in self.order:
                dc = self.concepts.get(pid, {})
                if any(dc.get(c, {}).get("strength", 0) > 0 for c in cids):
                    cand.add(pid)
        if flt:
            cand = {p for p in cand if flt(p)}
        if an["code_strong"]:
            cw, lw = 0.3, 1.0
        elif an["code_weak"]:
            cw, lw = 0.55, 0.45
        else:
            cw, lw = 0.62, 0.3
        scored = []
        for pid in cand:
            b = bm_score.get(pid, 0.0) / mx
            dc = self.concepts.get(pid, {})
            cm = 0.0
            if concepts:
                cm = sum(dc.get(c["id"], {}).get("strength", 0.0) * c["weight"] for c in concepts) / len(concepts)
            lit = 1.0 if pid in lits else (0.8 * partial[pid][0] if pid in partial else 0.0)
            if an["code_strong"] and (lits or partial):
                b *= 0.5          # przy zapytaniu „kodowym” słowa z treści zadania mniej ważne
            if concepts:
                rest_pen = 1.0 if not an["rest"] else 0.85
                s = cw * cm * rest_pen + (1 - cw) * b + lw * lit
            else:
                s = b + lw * lit
            if s > 0:
                scored.append((s, b, cm, pid))
        scored.sort(key=lambda x: (-x[0], -x[1], str(self.docs[x[3]].get("time") or "")))
        top = scored[0][0] if scored else 0
        scored = [x for x in scored if x[0] >= 0.12 * top]
        total_pids = [x[3] for x in scored]
        res = []
        for s, b, cm, pid in scored[:k]:
            lit = lits.get(pid)
            if lit is None and pid in partial:
                lit = (0, "częściowo: " + ", ".join(partial[pid][1][:4]))
            res.append(self._result(pid, s, concepts, bm_matched.get(pid, []), lit, b))
        if with_meta:
            meta = {"total": len(total_pids), "concepts": [{"id": c["id"], "name": c["name"], "via": c["text"],
                                                            "fuzzy": c["fuzzy"]} for c in concepts],
                    "corrections": an["corrections"], "code_query": an["code_strong"] or an["code_weak"],
                    "literal_hits": len(lits), "partial_hits": len(partial),
                    "facets": self.facets(total_pids)}
            return res, meta
        return res

    # ------------------------------------------------------------------ wyniki
    def _why(self, pid: str, concepts: list[dict], matched: list[str], lit) -> list[str]:
        d = self.docs[pid]
        dc = self.concepts.get(pid, {})
        why: list[str] = []
        for c in concepts:
            info = dc.get(c["id"])
            if not info:
                continue
            src = info["source"]
            ev = info.get("evidence") or ""
            label = SOURCE_LABEL.get(src, src)
            via = info.get("via")
            extra = f" → {CONCEPT_BY_ID[c['id']]['name']}" if via and via != c["id"] else ""
            if src == "round":
                txt = f"temat rundy: {d.get('topic') or ev}{extra}"
            elif src == "code":
                txt = f"w kodzie: {ev}" if ev else f"w kodzie: {CONCEPT_BY_ID.get(via or c['id'], {}).get('name')}"
                txt += f" ({CONCEPT_BY_ID.get(via or c['id'], {}).get('name', '')}){extra}" if ev else extra
            else:
                txt = f"{label}: {ev}{extra}"
            if txt not in why:
                why.append(txt)
        if lit:
            ln, line = lit
            why.append(f"w kodzie (linia {ln}): {line[:90]}" if ln else f"w kodzie {line[:90]}" if line else "w kodzie")
        ft = self.ft(pid)
        for f in ("title", "topic", "stl", "comments", "idents", "statement", "contest"):
            hit = [t for t in matched if t in ft.get(f, set())]
            if not hit:
                continue
            if f == "topic" and any(w.startswith("temat") for w in why):
                continue
            if f == "stl":
                names = [n for n in d["_feats"]["stl"] + d["_feats"]["headers"] if any(
                    T.stem(x) in hit for x in T.tokenize(n))][:3]
                txt = "w kodzie: " + ", ".join(names) if names else None
            elif f == "title":
                txt = f"nazwa zadania: {d.get('problem_name')}"
            elif f == "topic":
                txt = f"temat rundy: {d.get('topic') or d.get('round')}"
            elif f == "statement":
                txt = "w treści: " + ", ".join(self._surface(d.get("_statement") or "", hit)[:3])
            elif f == "idents":
                txt = "identyfikatory w kodzie: " + ", ".join(self._surface_idents(d, hit)[:3])
            elif f == "comments":
                txt = "komentarz w kodzie: " + ", ".join(self._surface(d["_feats"]["comments"], hit)[:3])
            else:
                txt = f"konkurs: {d.get('contest_name')}"
            if txt and txt not in why and not txt.endswith(": "):
                why.append(txt)
        return why[:5]

    @staticmethod
    def _surface(text_: str, stems: list[str]) -> list[str]:
        out = []
        for m in re.finditer(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż0-9_]+", text_):
            w = m.group(0)
            ts = T.terms(w)
            if ts and ts[0] in stems and w.lower() not in [o.lower() for o in out]:
                out.append(w)
                if len(out) >= 4:
                    break
        return out

    @staticmethod
    def _surface_idents(d: dict, stems: list[str]) -> list[str]:
        return [w for w in d["_feats"]["idents"] if set(T.terms(w)) & set(stems)][:4]

    def _snippet(self, pid: str, matched: list[str], lit, concepts) -> tuple[str, str, int | None]:
        d = self.docs[pid]
        if lit and lit[0]:
            return lit[1][:200], "kod", lit[0]
        if lit and str(lit[1]).startswith("częściowo"):
            grams = str(lit[1]).split(": ", 1)[-1].split(", ")
            for i, line in enumerate((d.get("_code") or "").splitlines(), 1):
                ll = " ".join(re.findall(r"[a-z_]\w*|\d+|[^\s\w]", line.lower()))
                if any(g in ll for g in grams):
                    return line.strip()[:200], "kod", i
        ft = self.ft(pid)
        st = d.get("_statement") or ""
        st_hits = [t for t in matched if t in ft.get("statement", set())]
        if st_hits and st:
            return S.highlight(_statement_body(st), st_hits, width=220), "treść", None
        code_hits = [t for t in matched if t in ft.get("idents", set()) or t in ft.get("comments", set())
                     or t in ft.get("stl", set())]
        code = d.get("_code") or ""
        if code_hits and code:
            for i, line in enumerate(code.splitlines(), 1):
                lt = set(T.terms(line))
                if lt & set(code_hits):
                    return line.strip()[:200], "kod", i
        # dowód z detektora (numer linii)
        for c in concepts or []:
            for a in d["_algos"]:
                if a["id"] == c["id"] or CONCEPT_BY_ID.get(a["id"], {}).get("parents", {}).get(c["id"]):
                    if a.get("lines"):
                        ln = a["lines"][0]
                        lines = code.splitlines()
                        if 0 < ln <= len(lines):
                            return lines[ln - 1].strip()[:200], "kod", ln
        if st:
            return S.highlight(_statement_body(st), matched, width=200), "treść", None
        return "", "", None

    def _result(self, pid: str, s: float, concepts, matched, lit, bm=None) -> dict:
        d = self.docs[pid]
        snippet, kind, line = self._snippet(pid, matched, lit, concepts)
        tags = self.tags_of(pid, first=[c["id"] for c in concepts or []])
        return {
            "id": pid, "problem_name": d.get("problem_name") or "", "problem_short": d.get("problem_short") or "",
            "contest_id": d.get("contest_id") or "", "contest_name": d.get("contest_name") or "",
            "round": d.get("round"), "topic": d.get("topic"), "path": d.get("path") or "",
            "abs_path": str((self.root / (d.get("path") or "")).resolve()) if d.get("path") else "",
            "score": d.get("score"), "status": d.get("status"), "time": d.get("time") or "", "lang": d.get("lang"),
            "lines": d.get("lines"), "url": d.get("url") or "", "source": d.get("source") or "",
            "submission_id": d.get("submission_id"),
            "tags": [t["name"] for t in tags], "tag_ids": [t["id"] for t in tags], "tags_detail": tags,
            "snippet": snippet, "snippet_kind": kind, "snippet_line": line,
            "why": self._why(pid, concepts, matched, lit) if (concepts or matched or lit) else [],
            "relevance": round(s, 4), "matched": matched[:12],
            "statement_kind": d.get("statement_kind"), "statement_pending": d.get("statement_pending"),
        }

    def tags_of(self, pid: str, min_strength: float = 0.5, first: list[str] | None = None) -> list[dict]:
        dc = self.concepts.get(pid, {})
        out = []
        pref = set(first or ())

        def key(kv):
            cid, info = kv
            return (cid not in pref, -round(info["strength"], 1), info.get("via") is not None, cid in _GENERAL,
                    _ORDER.get(cid, 999))

        for cid, info in sorted(dc.items(), key=key):
            if info["strength"] < min_strength or info["source"] == "statement":
                continue
            c = CONCEPT_BY_ID.get(cid, {"name": cid})
            out.append({"id": cid, "name": c["name"], "strength": info["strength"],
                        "source": info["source"], "source_label": SOURCE_LABEL.get(info["source"], info["source"]),
                        "evidence": info.get("evidence") or "", "via": info.get("via")})
        return out[:10]

    def facets(self, pids: list[str]) -> dict:
        contests: Counter = Counter()
        cnames = {}
        topics: Counter = Counter()
        tags: Counter = Counter()
        for pid in pids:
            d = self.docs[pid]
            contests[d.get("contest_id") or ""] += 1
            cnames[d.get("contest_id") or ""] = d.get("contest_name") or d.get("contest_id")
            if d.get("topic") and not d.get("topic_generic"):
                topics[d["topic"]] += 1
            for t in self.tags_of(pid, 0.6):
                tags[t["id"]] += 1
        return {"contests": [{"id": c, "name": cnames[c], "count": n} for c, n in contests.most_common()],
                "topics": [{"id": t, "name": t, "count": n} for t, n in topics.most_common(40)],
                "tags": [{"id": t, "name": CONCEPT_BY_ID.get(t, {"name": t})["name"], "count": n}
                         for t, n in tags.most_common(40)]}

    # ------------------------------------------------------------------ szczegóły, podobne, statystyki
    def get(self, pid: str) -> dict | None:
        d = self.docs.get(pid)
        if d is None:
            for p, dd in self.docs.items():
                if str(dd.get("submission_id")) == str(pid) or dd.get("path") == pid:
                    d = dd
                    pid = p
                    break
        if d is None:
            return None
        res = self._result(pid, 0.0, [], [], None)
        f = d["_feats"]
        res.update(code=d.get("_code") or "", statement=d.get("_statement") or "",
                   statement_topic=d.get("statement_topic"), has_pdf=d.get("has_pdf"),
                   headers=f["headers"], stl=f["stl"], defines=f["defines"],
                   algorithms=[{k: v for k, v in a.items()} for a in d["_algos"]],
                   tags_all=self.tags_of(pid, 0.3), other_submissions=[
                       p for p in self.order if p != pid and self.docs[p].get("contest_id") == d.get("contest_id")
                       and str(self.docs[p].get("problem_short") or "").lower() == str(d.get("problem_short")
                                                                                       or "").lower()],
                   contest_url=_contest_url(d), problem_url=_problem_url(d))
        return res

    def similar(self, pid: str, k: int = 8) -> list[dict]:
        if pid not in self.docs:
            g = self.get(pid)
            if not g:
                return []
            pid = g["id"]
        d = self.docs[pid]
        sh = d["_feats"]["shingles"]
        dc = {c: i["strength"] for c, i in self.concepts.get(pid, {}).items() if i["strength"] >= 0.5
              and i["source"] != "statement"}
        sv = self.stmt_vec.get(pid, {})
        out = []
        for q in self.order:
            if q == pid:
                continue
            e = self.docs[q]
            same_problem = (e.get("contest_id") == d.get("contest_id") and str(e.get("problem_short") or "").lower()
                            == str(d.get("problem_short") or "").lower())
            sh2 = e["_feats"]["shingles"]
            code_j = len(sh & sh2) / len(sh | sh2) if sh and sh2 else 0.0
            ec = {c: i["strength"] for c, i in self.concepts.get(q, {}).items() if i["strength"] >= 0.5
                  and i["source"] != "statement"}
            keys = set(dc) | set(ec)
            tag_j = (sum(min(dc.get(c, 0), ec.get(c, 0)) for c in keys) / sum(max(dc.get(c, 0), ec.get(c, 0))
                                                                              for c in keys)) if keys else 0.0
            st_c = self._cos(sv, self.stmt_vec.get(q, {})) if sv else 0.0
            sim = 0.4 * code_j + 0.35 * tag_j + 0.25 * st_c
            if same_problem:
                sim = max(sim, 0.9 + 0.1 * code_j)
            elif st_c > 0.8:
                sim = max(sim, 0.75 + 0.2 * st_c * 0.5)
            if sim < 0.12:
                continue
            reasons = []
            if same_problem:
                reasons.append("to samo zadanie (inne zgłoszenie)")
            elif st_c > 0.8:
                reasons.append(f"prawie ta sama treść ({round(100 * st_c)}%) — to samo zadanie w innym konkursie?")
            common = [CONCEPT_BY_ID.get(c, {"name": c})["name"] for c in sorted(set(dc) & set(ec), key=lambda c: -min(
                dc[c], ec[c]))][:3]
            if common:
                reasons.append("wspólne tematy: " + ", ".join(common))
            if code_j >= 0.15:
                reasons.append(f"podobny kod ({round(100 * code_j)}%)")
            if 0.25 <= st_c <= 0.8:
                reasons.append(f"podobna treść ({round(100 * st_c)}%)")
            r = self._result(q, sim, [], [], None)
            r.update(similarity=round(sim, 3), code_similarity=round(code_j, 3), tag_similarity=round(tag_j, 3),
                     statement_similarity=round(st_c, 3), reasons=reasons, same_problem=same_problem)
            out.append(r)
        out.sort(key=lambda r: -r["similarity"])
        return out[:k]

    def similar_tasks(self, statement_text: str, k: int = 5) -> list[dict]:
        txt = fold(statement_text or "")
        tf = Counter(t for t in T.terms(txt[:30000]) if len(t) >= 3 and not t.isdigit())
        if not tf:
            return []
        vec = self._vec(tf)
        top_terms = [t for t, _ in sorted(vec.items(), key=lambda kv: -kv[1])[:30]]
        try:
            bm = self.idx.search(" ".join(top_terms), k=len(self.order) or 1)
        except Exception:  # noqa: BLE001
            bm = []
        bms = {r["id"]: r["score"] for r in bm}
        mx = max(bms.values(), default=0) or 1.0
        nt = norm(txt)
        hint_c = {cid for cid, rx in STATEMENT_HINTS if re.search(rx, nt)}
        topic = statement_topic(txt)
        topic_c = set(concepts_in_topic(topic)) if topic else set()
        out = []
        for pid in self.order:
            cos = self._cos(vec, self.stmt_vec.get(pid, {}))
            b = bms.get(pid, 0.0) / mx
            dc = self.concepts.get(pid, {})
            cbonus = 0.0
            for c in topic_c:
                cbonus = max(cbonus, dc.get(c, {}).get("strength", 0) * 0.3)
            for c in hint_c:
                cbonus = max(cbonus, dc.get(c, {}).get("strength", 0) * 0.1)
            sim = 0.6 * cos + 0.3 * b + cbonus
            if sim < 0.08:
                continue
            r = self._result(pid, sim, [], [], None)
            reasons = []
            if cos >= 0.2:
                reasons.append(f"podobna treść ({round(100 * cos)}%)")
            common = [t for t in top_terms[:15] if t in self.ft(pid).get("statement", set())][:5]
            if common:
                reasons.append("wspólne słowa: " + ", ".join(self._surface(self.docs[pid].get("_statement") or "",
                                                                          common)[:5]))
            r.update(similarity=round(sim, 3), statement_similarity=round(cos, 3), reasons=reasons)
            out.append(r)
        out.sort(key=lambda r: -r["similarity"])
        return out[:k]

    def stats(self) -> dict:
        docs = [self.docs[p] for p in self.order]
        by_contest: Counter = Counter()
        cnames = {}
        problems_by_topic: dict[str, set] = defaultdict(set)
        problems_by_algo: dict[str, set] = defaultdict(set)
        timeline: Counter = Counter()
        weekday: Counter = Counter()
        hours: Counter = Counter()
        stl: Counter = Counter()
        problems = set()
        lines = 0
        with_st = pending = 0
        for d in docs:
            cid = d.get("contest_id") or ""
            by_contest[cid] += 1
            cnames[cid] = d.get("contest_name") or cid
            pkey = (d.get("source"), cid, str(d.get("problem_short") or "").lower())
            problems.add(pkey)
            if d.get("topic") and not d.get("topic_generic"):
                problems_by_topic[d["topic"]].add(pkey)
            for t in self.tags_of(d["id"], 0.6):
                problems_by_algo[t["id"]].add(pkey)
            tm = str(d.get("time") or "")
            if re.match(r"\d{4}-\d{2}", tm):
                timeline[tm[:7]] += 1
                try:
                    dt = datetime.strptime(tm[:19], "%Y-%m-%d %H:%M:%S")
                    weekday[dt.weekday()] += 1
                    hours[dt.hour] += 1
                except ValueError:
                    pass
            lines += int(d.get("lines") or 0)
            if d.get("statement_kind") in ("txt", "html"):
                with_st += 1
            if d.get("statement_pending"):
                pending += 1
            for s in d["_feats"]["stl"]:
                stl[s] += 1
        # scal tematy różniące się wielkością liter
        merged: dict[str, set] = {}
        disp: dict[str, str] = {}
        for t, s in problems_by_topic.items():
            key = norm(t)
            merged.setdefault(key, set()).update(s)
            if key not in disp or len(problems_by_topic[t]) > len(problems_by_topic.get(disp[key], ())):
                disp[key] = t
        months = sorted(timeline)
        if months:
            y, m = map(int, months[0].split("-"))
            y2, m2 = map(int, months[-1].split("-"))
            full = []
            while (y, m) <= (y2, m2):
                full.append(f"{y:04d}-{m:02d}")
                m += 1
                if m > 12:
                    y, m = y + 1, 1
            months = full
        cum = 0
        tl = []
        for mo in months:
            cum += timeline.get(mo, 0)
            tl.append({"month": mo, "count": timeline.get(mo, 0), "cumulative": cum})
        wd = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]
        times = sorted(str(d.get("time")) for d in docs if d.get("time"))
        return {
            "totals": {"programs": len(docs), "problems": len(problems), "contests": len(by_contest), "lines": lines,
                       "with_statement": with_st, "pending_statements": pending,
                       "topics": len(merged), "algorithms": len(problems_by_algo)},
            "first": times[0] if times else None, "last": times[-1] if times else None,
            "by_contest": [{"id": c, "label": cnames[c], "value": n} for c, n in by_contest.most_common()],
            "by_topic": sorted(({"label": disp[k], "value": len(v)} for k, v in merged.items()),
                               key=lambda x: (-x["value"], x["label"])),
            "by_algorithm": sorted(({"id": c, "label": CONCEPT_BY_ID.get(c, {"name": c})["name"], "value": len(v)}
                                    for c, v in problems_by_algo.items()), key=lambda x: (-x["value"], x["label"])),
            "timeline": tl,
            "by_weekday": [{"label": wd[i], "value": weekday.get(i, 0)} for i in range(7)],
            "by_hour": [{"label": f"{h:02d}", "value": hours.get(h, 0)} for h in range(24)],
            "stl_top": [{"label": s, "value": n} for s, n in stl.most_common(20)],
            "build_time": self.build_time,
        }

    def list_programs(self, filters=None, sort: str = "time") -> list[dict]:
        flt = self._filter_fn(filters)
        pids = [p for p in self.order if not flt or flt(p)]
        if sort == "name":
            pids.sort(key=lambda p: norm(self.docs[p].get("problem_name") or ""))
        elif sort == "contest":
            pids.sort(key=lambda p: (norm(self.docs[p].get("contest_name") or ""), str(self.docs[p].get("time") or "")))
        else:
            pids.sort(key=lambda p: str(self.docs[p].get("time") or ""), reverse=True)
        return [self._light(p) for p in pids]

    def _light(self, pid: str) -> dict:
        d = self.docs[pid]
        tags = self.tags_of(pid, 0.6)
        return {"id": pid, "problem_name": d.get("problem_name") or "", "problem_short": d.get("problem_short") or "",
                "contest_id": d.get("contest_id"), "contest_name": d.get("contest_name"), "round": d.get("round"),
                "topic": d.get("topic"), "time": d.get("time"), "score": d.get("score"), "lines": d.get("lines"),
                "lang": d.get("lang"), "url": d.get("url"), "path": d.get("path"),
                "tags": [t["name"] for t in tags], "tag_ids": [t["id"] for t in tags],
                "statement_kind": d.get("statement_kind"), "statement_pending": d.get("statement_pending")}

    def suggest(self, prefix: str, k: int = 8) -> list[dict]:
        p = norm(prefix)
        if not p:
            return []
        out, seen = [], set()

        def add(text_, kind):
            key = norm(text_)
            if key in seen:
                return
            seen.add(key)
            out.append({"text": text_, "kind": kind})

        for c in CONCEPT_BY_ID.values():
            if norm(c["name"]).startswith(p) or any(norm(a).startswith(p) for a in c["aliases"]):
                add(c["name"], "algorytm")
        for pid in self.order:
            d = self.docs[pid]
            if norm(d.get("problem_name") or "").startswith(p) or norm(d.get("problem_short") or "") == p:
                add(d.get("problem_name") or "", "zadanie")
            if d.get("topic") and norm(d["topic"]).startswith(p):
                add(d["topic"], "temat")
            if len(out) >= 3 * k:
                break
        if len(p) >= 3:
            for m in matcher().match(prefix):
                add(m["name"], "algorytm")
        return out[:k]


def _statement_body(st: str) -> str:
    """Treść bez nagłówka PDF (tytuł, „Kod zadania”, „Limit pamięci”)."""
    lines = st.splitlines()
    for i, l in enumerate(lines[:10]):
        if "Limit pami" in l:
            return "\n".join(lines[i + 1:])
    return st


def _contest_url(d: dict) -> str:
    u = d.get("url") or ""
    m = re.match(r"^(https?://[^/]+/c/[^/]+/)", u)
    return m.group(1) if m else ""


def _problem_url(d: dict) -> str:
    cu = _contest_url(d)
    return f"{cu}p/{str(d.get('problem_short') or '').lower()}/" if cu and d.get("problem_short") else ""


def engine(root=None) -> Engine:
    """Aktualny silnik (przebudowywany, gdy zmienią się dane na dysku)."""
    global _engine
    root = Path(root or programs_dir())
    with _engine_lock:
        sig = _signature(root)
        if _engine is None or getattr(_engine, "_sig", None) != sig:
            e = Engine(root).build()
            e._sig = sig
            _engine = e
        return _engine


# ============================================================================================ Python API
def search(query: str, k: int = 10, filters: dict | None = None) -> list[dict]:
    """Wyszukiwanie programów. filters: {contest, topic|round, tag|algorithm, from, to, lang} (wartości lub listy)."""
    return engine().search(query, k=k, filters=filters)


def similar_tasks(statement_text: str, k: int = 5) -> list[dict]:
    """Moje rozwiązane zadania podobne do podanej treści (TF-IDF + BM25 + tematy)."""
    return engine().similar_tasks(statement_text, k=k)


def get(program_id_: str) -> dict | None:
    """Program z kodem, treścią i metadanymi (id = "<źródło>:<id zgłoszenia>", też samo id zgłoszenia lub ścieżka)."""
    return engine().get(program_id_)


def similar(program_id_: str, k: int = 8) -> list[dict]:
    return engine().similar(program_id_, k=k)


def stats() -> dict:
    return engine().stats()


def list_programs(filters: dict | None = None, sort: str = "time") -> list[dict]:
    return engine().list_programs(filters, sort)


# ============================================================================================ HTTP
def _filters_from(q: dict) -> dict:
    return {k: q.get(k) for k in ("contest", "topic", "round", "tag", "algorithm", "from", "to", "lang", "source")
            if q.get(k)}


def _int(v, default, lo=1, hi=500):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def handle_search(q: dict) -> dict:
    e = engine()
    t0 = time.perf_counter()
    res, meta = e.search(q.get("q") or "", k=_int(q.get("k"), 30), filters=_filters_from(q), with_meta=True)
    return {"results": res, "total": meta["total"], "concepts": meta.get("concepts", []),
            "corrections": meta.get("corrections", []), "code_query": meta.get("code_query", False),
            "literal_hits": meta.get("literal_hits"), "partial_hits": meta.get("partial_hits"),
            "facets": meta.get("facets"), "took_ms": round(1000 * (time.perf_counter() - t0), 1),
            "count": len(e.order)}


def handle_list(q: dict) -> dict:
    e = engine()
    progs = e.list_programs(_filters_from(q), q.get("sort") or "time")
    return {"programs": progs, "total": len(progs), "count": len(e.order),
            "facets": e.facets([p["id"] for p in progs]), "root": str(e.root), "error": e.error}


def handle_get(q: dict) -> dict:
    pid = q.get("id") or ""
    p = engine().get(pid)
    if not p:
        raise ApiError("Nie znaleziono programu.", 404)
    return {"program": p}


def handle_stats(_q: dict) -> dict:
    s = engine().stats()
    t = s["totals"]
    # płaskie liczniki dla kafelków na stronie głównej
    return {"stats": s, "total": t["programs"], "count": t["programs"], "problems": t["problems"],
            "contests_count": t["contests"], "pending_statements": t["pending_statements"]}


def handle_similar(q: dict) -> dict:
    pid = q.get("id") or ""
    if not engine().get(pid):
        raise ApiError("Nie znaleziono programu.", 404)
    return {"results": engine().similar(pid, k=_int(q.get("k"), 8, hi=50))}


def handle_similar_tasks(b: dict) -> dict:
    txt = b.get("statement") or b.get("text") or ""
    if not txt.strip():
        raise ApiError("Podaj treść zadania.")
    return {"results": engine().similar_tasks(txt, k=_int(b.get("k"), 5, hi=50))}


def handle_suggest(q: dict) -> dict:
    return {"suggestions": engine().suggest(q.get("q") or "", k=_int(q.get("k"), 8, hi=30))}


def handle_concepts(_q: dict) -> dict:
    return {"concepts": [{"id": c["id"], "name": c["name"], "aliases": c["aliases"][:8]} for c in
                         CONCEPT_BY_ID.values()]}


def handle_statement_pdf(q: dict) -> dict:
    """PDF treści jako base64 (UI: ekstrakcja tekstu pdf.js albo podgląd). id=<program> lub key=<źródło/konkurs/skrót>."""
    root = programs_dir()
    paths = None
    name = ""
    if q.get("key"):
        parts = str(q["key"]).split("/")
        if len(parts) != 3 or not all(_SAFE_PART.match(x) for x in parts):
            raise ApiError("Niepoprawny klucz treści.")
        paths = find_statement(root, *parts)
        name = parts[2]
    else:
        e = engine()
        d = e.docs.get(q.get("id") or "")
        if d is None:
            raise ApiError("Nie znaleziono programu.", 404)
        paths = d["_st_paths"]
        name = str(d.get("problem_short") or "")
    pdf = paths.get("pdf") if paths else None
    if not pdf or not inside(root, pdf):
        raise ApiError("Brak PDF-a z treścią.", 404)
    data = pdf.read_bytes()
    return {"name": f"{name}.pdf", "size": len(data), "b64": base64.b64encode(data).decode("ascii")}


ROUTES = {
    "GET /api/programs/search": handle_search,
    "GET /api/programs/list": handle_list,
    "GET /api/programs/get": handle_get,
    "GET /api/programs/stats": handle_stats,
    "GET /api/programs/similar": handle_similar,
    "POST /api/programs/similar_tasks": handle_similar_tasks,
    "GET /api/programs/suggest": handle_suggest,
    "GET /api/programs/concepts": handle_concepts,
    "GET /api/programs/statement/pdf": handle_statement_pdf,
}


def _warm() -> None:
    try:
        e = engine()
        if hasattr(e.idx, "correct"):
            e.idx.correct("rozgrzewka")
        matcher()
    except Exception:  # noqa: BLE001 - rozgrzewka nie może przeszkadzać
        pass


if os.environ.get("OIFORGE_NO_WARMUP") != "1" and "unittest" not in sys.modules:
    threading.Thread(target=_warm, daemon=True, name="programs-warmup").start()
