"""(u) Doradca algorytmów: z treści zadania → dopuszczalna złożoność, ranking algorytmów z uzasadnieniem,
podobne zadania ucznia.

Trzy źródła wiedzy (bez żadnego LLM):
1. sygnały słownikowe z `data/algo_signals.json` (wyrażenia regularne → wagi algorytmów + uzasadnienie),
2. klasyfikator uczony NA DANYCH UCZNIA: treści zadań z `user_data/programs` z etykietami z nazw rund
   (Naive Bayes + regresja logistyczna softmax na haszowanych unigramach/bigramach stemów, TF-IDF),
   trenowany przy pierwszym użyciu i cache'owany w `user_data/cache/suggest_model.json`,
3. ograniczenia (n ≤ …) → tabela złożoności i priorytety (n ≤ 20 → maski, n ≥ 10^5 → nie O(n²)).
Podobne zadania: kosinus TF-IDF nad treściami zadań ucznia.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import threading
import time
import zlib
from collections import Counter, defaultdict

from .. import config
from ..core import kb
from ..core import statement as ST
from ..core import text as T
from ..core.search import SearchIndex
from ..server_api import ApiError

SIGNALS_PATH = config.DATA_DIR / "algo_signals.json"
MODEL_PATH = config.CACHE_DIR / "suggest_model.json"
EVAL_PATH = config.CACHE_DIR / "suggest_eval.json"
PROGRAMS_DIR = config.PROGRAMS_DIR
INDEX_PATH = PROGRAMS_DIR / "index.json"
HASH_BITS = 20
MODEL_VERSION = 5
BLEND_SIGNALS = 0.45      # waga sygnałów słownikowych w mieszance (dobrana walidacją krzyżową)

_lock = threading.RLock()
_sig_cache: dict = {"mtime": None, "data": None}
_corpus_cache: dict = {"sig": None, "items": None}
_model_cache: dict = {"sig": None, "model": None}
_tfidf_cache: dict = {"sig": None, "index": None}
_kb_cache: dict = {"sig": None, "idx": None}

# etykieta klasyfikatora (temat rundy) → algorytmy, którym dodaje pewności
LABEL_TO_ALGOS = {
    "graph": {"graph": 1.0, "bfs": 0.9, "dfs": 0.9},
    "tree": {"tree": 1.0, "dfs": 0.7, "lca": 0.4},
    "dp": {"dp": 1.0, "greedy": 0.5},
    "dsu": {"dsu": 1.0, "mst": 0.3},
    "dijkstra": {"dijkstra": 1.0, "bfs": 0.3},
    "topo_sort": {"topo_sort": 1.0, "dp": 0.4, "dfs": 0.3},
    "segment_tree": {"segment_tree": 1.0, "fenwick": 0.6, "lazy_segment_tree": 0.4},
    "prefix_sums": {"prefix_sums": 1.0, "two_pointers": 0.2},
    "binary_search": {"binary_search": 1.0},
    "two_pointers": {"two_pointers": 1.0, "sliding_window": 0.5},
    "sorting": {"sorting": 1.0, "greedy": 0.3},
    "stl": {"stl": 1.0, "sorting": 0.2},
    "math": {"math": 1.0, "gcd": 0.5, "sieve": 0.5, "fast_pow": 0.4},
    "simulation": {"simulation": 1.0},
    "string_processing": {"string_processing": 1.0},
    "geometry": {"geometry": 1.0},
}
# algorytm → etykieta (do oceny sygnałów i mieszanki na poziomie tematów)
ALGO_TO_LABEL = {
    "bfs": "graph", "dfs": "graph", "graph": "graph", "grid": "graph", "bipartite": "graph", "scc": "graph",
    "bridges": "graph", "flow": "graph", "matching": "graph", "bellman_ford": "dijkstra", "floyd": "dijkstra",
    "dijkstra": "dijkstra", "mst": "dsu", "dsu": "dsu", "topo_sort": "topo_sort", "tree": "tree", "lca": "tree",
    "segment_tree": "segment_tree", "lazy_segment_tree": "segment_tree", "fenwick": "segment_tree",
    "sparse_table": "segment_tree", "sqrt_decomp": "segment_tree", "mo": "segment_tree",
    "prefix_sums": "prefix_sums", "binary_search": "binary_search", "two_pointers": "two_pointers",
    "sliding_window": "two_pointers", "sorting": "sorting", "greedy": "dp", "dp": "dp", "bitmask_dp": "dp",
    "backtracking": "dp", "recursion": "math", "sieve": "math", "gcd": "math", "fast_pow": "math",
    "mod_inverse": "math", "combinatorics": "math", "math": "math", "matrix_pow": "math", "kmp": "string_processing",
    "z_function": "string_processing", "hashing": "string_processing", "trie": "string_processing",
    "manacher": "string_processing", "string_processing": "string_processing", "stl": "stl",
    "monotonic_stack": "stl", "simulation": "simulation", "simulation_queue": "stl", "geometry": "geometry",
    "convex_hull": "geometry", "coordinate_compression": "sorting", "meet_in_middle": "dp", "bitset": "dp",
}
LABEL_NAMES = {
    "graph": "Grafy (BFS/DFS)", "tree": "Drzewa", "dp": "Programowanie dynamiczne / zachłanne", "dsu": "Find & Union",
    "dijkstra": "Dijkstra / najkrótsze ścieżki", "topo_sort": "Sortowanie topologiczne / DAG",
    "segment_tree": "Drzewa przedziałowe", "prefix_sums": "Sumy prefiksowe", "binary_search": "Wyszukiwanie binarne",
    "two_pointers": "Gąsienica (dwa wskaźniki)", "sorting": "Sortowanie", "stl": "Struktury STL (set/map/kopiec)",
    "math": "Matematyka / teoria liczb", "simulation": "Implementacja (pętle, warunki, tablice)",
    "string_processing": "Napisy", "geometry": "Geometria",
}


# ============================================================================================ sygnały
def load_signals() -> dict:
    try:
        mt = SIGNALS_PATH.stat().st_mtime_ns
    except OSError:
        return {"algorithms": {}, "signals": [], "round_rules": [], "round_skip_patterns": []}
    with _lock:
        if _sig_cache["mtime"] == mt and _sig_cache["data"] is not None:
            return _sig_cache["data"]
        data = json.loads(SIGNALS_PATH.read_text(encoding="utf-8"))
        for s in data.get("signals", []):
            s["_any"] = [re.compile(p) for p in s.get("any", [])]
            s["_all"] = [[re.compile(p) for p in grp] for grp in s.get("all", [])]
            s["_none"] = [re.compile(p) for p in s.get("none", [])]
        _sig_cache.update(mtime=mt, data=data)
        return data


def norm(text: str) -> str:
    """Tekst do dopasowań sygnałów: małe litery, bez polskich znaków, pojedyncze spacje."""
    return re.sub(r"\s+", " ", T.strip_accents(text or "").lower())


def _snippet(text: str, m: re.Match, width: int = 70) -> str:
    a = max(0, m.start() - width // 2)
    b = min(len(text), m.end() + width // 2)
    return ("…" if a > 0 else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def signal_scores(text: str, weight: float = 1.0, origin: str = "treść") -> tuple[dict, dict]:
    """{algo: wynik}, {algo: [uzasadnienia]} na podstawie słownika sygnałów."""
    data = load_signals()
    t = norm(text)
    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, list] = defaultdict(list)
    for s in data.get("signals", []):
        if any(rx.search(t) for rx in s["_none"]):
            continue
        match = None
        if s["_any"]:
            for rx in s["_any"]:
                match = rx.search(t)
                if match:
                    break
            if not match:
                continue
        if s["_all"]:
            ms = []
            for grp in s["_all"]:
                mm = next((m for m in (rx.search(t) for rx in grp) if m), None)
                if not mm:
                    break
                ms.append(mm)
            else:
                match = match or ms[0]
            if len(ms) != len(s["_all"]):
                continue
        if match is None:
            continue
        for algo, w in s.get("algos", {}).items():
            scores[algo] += w * weight
            if w > 0:
                reasons[algo].append({"signal": s["id"], "reason": s.get("reason", ""), "weight": round(w * weight, 2),
                                      "match": _snippet(t, match), "origin": origin})
    return dict(scores), dict(reasons)


# ============================================================================================ korpus ucznia
def _programs_signature() -> str:
    try:
        st = INDEX_PATH.stat()
        n = sum(1 for _ in PROGRAMS_DIR.glob("*/*/statements/*"))
        return f"{st.st_mtime_ns}-{st.st_size}-{n}"
    except OSError:
        return ""


def _round_label(round_name: str | None) -> str | None:
    """Temat rundy (np. „Dzień 5 - Dijkstra”, „Drzewa przedziałowe”) → etykieta algorytmu do uczenia klasyfikatora."""
    if not round_name:
        return None
    data = load_signals()
    low = T.strip_accents(round_name).lower()
    if any(p in low for p in data.get("round_skip_patterns", [])):
        return None
    for pat, lab in data.get("round_rules", []):
        if re.search(pat, low):
            return lab
    return None


def statement_file(contest_id: str, short: str):
    """Treść zadania z dowolnego źródła (każdy serwer SIO2/Szkopuł ma swój folder w user_data/programs)."""
    for ext in ("txt", "html"):
        for p in sorted(PROGRAMS_DIR.glob(f"*/{contest_id}/statements/{short}.{ext}")):
            return p
    return None


def doc_text(p: dict) -> str:
    sec = p.get("sections", {})
    return " ".join([p.get("title") or ""] + [sec.get(k, "") for k in ("tresc", "wejscie", "wyjscie", "uwagi")])


def user_corpus() -> list[dict]:
    """Zadania ucznia z treściami: [{key, title, contest_id, short, round, label, parsed, text, programs}]."""
    sig = _programs_signature()
    with _lock:
        if _corpus_cache["sig"] == sig and _corpus_cache["items"] is not None:
            return _corpus_cache["items"]
    items: list[dict] = []
    try:
        progs = json.loads(INDEX_PATH.read_text(encoding="utf-8")).get("programs", [])
    except (OSError, json.JSONDecodeError):
        progs = []
    by_task: dict[tuple, list] = defaultdict(list)
    for p in progs:
        by_task[(p.get("contest_id"), p.get("problem_short"))].append(p)
    for (cid, short), plist in sorted(by_task.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        f = statement_file(cid, short)
        if not f:
            continue
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
            parsed = ST.parse(raw)
        except Exception:  # noqa: BLE001
            continue
        if not (parsed.get("sections") or {}).get("tresc") and not parsed.get("samples"):
            continue
        rnd = plist[0].get("round")
        best = max(plist, key=lambda x: (x.get("score") or 0, x.get("time") or ""))
        items.append({
            "key": f"{cid}/{short}", "title": parsed.get("title") or plist[0].get("problem_name") or short,
            "problem_name": plist[0].get("problem_name"), "contest_id": cid, "contest_name": plist[0].get("contest_name"),
            "short": short, "round": rnd, "label": _round_label(rnd), "parsed": parsed, "text": doc_text(parsed),
            "header_note": (parsed.get("meta") or {}).get("header_note"), "statement_path": str(f),
            "programs": [{"path": x.get("path"), "score": x.get("score"), "url": x.get("url"), "lines": x.get("lines"),
                          "submission_id": x.get("submission_id"), "time": x.get("time"),
                          "program_id": f"{x.get('source') or 'local'}:{x.get('submission_id') or x.get('path')}"}
                         for x in plist],
            "best_program": best.get("path"),
        })
    with _lock:
        _corpus_cache.update(sig=sig, items=items)
    return items


def read_program(path: str, limit: int = 60000) -> str:
    try:
        p = (PROGRAMS_DIR / path).resolve()
        p.relative_to(PROGRAMS_DIR.resolve())
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except (OSError, ValueError):
        return ""


# ============================================================================================ cechy
def _h(tok: str) -> int:
    return zlib.crc32(tok.encode("utf-8")) & ((1 << HASH_BITS) - 1)


_NUM_RE = re.compile(r"^\d+$")


def featurize(text: str) -> Counter:
    """Haszowane unigramy i bigramy stemów (bez liczb i stopwords)."""
    toks = [t for t in T.terms(text) if len(t) >= 2 and not _NUM_RE.match(t)]
    c = Counter()
    for t in toks:
        c[_h("u:" + t)] += 1
    for a, b in zip(toks, toks[1:]):
        c[_h("b:" + a + "_" + b)] += 1
    return c


def _tfidf(c: Counter, idf: dict) -> dict:
    v = {f: (1 + math.log(n)) * idf.get(f, 0.0) for f, n in c.items() if f in idf}
    norm_ = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {f: x / norm_ for f, x in v.items()}


def _idf(docs: list[Counter]) -> dict:
    df = Counter()
    for d in docs:
        df.update(d.keys())
    n = len(docs)
    return {f: math.log((n + 1) / (k + 1)) + 1.0 for f, k in df.items() if k >= 1}


class NaiveBayes:
    def __init__(self, alpha: float = 0.25):
        self.alpha = alpha
        self.classes: list[str] = []
        self.prior: dict = {}
        self.like: dict = {}       # cls -> {f: logp}
        self.default: dict = {}

    def fit(self, X: list[Counter], y: list[str]) -> "NaiveBayes":
        self.classes = sorted(set(y))
        cnt = Counter(y)
        vocab = set()
        for x in X:
            vocab.update(x)
        V = len(vocab) + 1
        tot: dict = defaultdict(float)
        fc: dict = defaultdict(lambda: defaultdict(float))
        for x, c in zip(X, y):
            for f, n in x.items():
                w = 1.0 + math.log(n)       # tłumienie częstości
                fc[c][f] += w
                tot[c] += w
        n = len(y)
        for c in self.classes:
            self.prior[c] = math.log((cnt[c] + 1) / (n + len(self.classes)))
            den = tot[c] + self.alpha * V
            self.like[c] = {f: math.log((v + self.alpha) / den) for f, v in fc[c].items()}
            self.default[c] = math.log(self.alpha / den)
        return self

    def predict_proba(self, x: Counter) -> dict:
        logs = {}
        for c in self.classes:
            lk, d = self.like[c], self.default[c]
            s = self.prior[c]
            for f, n in x.items():
                s += (1.0 + math.log(n)) * lk.get(f, d)
            logs[c] = s
        return _softmax(logs, temp=max(1.0, sum(x.values()) ** 0.5 / 3))

    def to_json(self):
        return {"alpha": self.alpha, "classes": self.classes, "prior": self.prior,
                "like": {c: {str(f): round(v, 5) for f, v in d.items()} for c, d in self.like.items()},
                "default": self.default}

    @classmethod
    def from_json(cls, d):
        m = cls(d["alpha"])
        m.classes, m.prior, m.default = d["classes"], d["prior"], d["default"]
        m.like = {c: {int(f): v for f, v in dd.items()} for c, dd in d["like"].items()}
        return m


class SoftmaxLR:
    """Wieloklasowa regresja logistyczna na wektorach TF-IDF (AdaGrad, regularyzacja L2)."""

    def __init__(self, epochs: int = 40, lr: float = 0.6, l2: float = 2e-4, seed: int = 7):
        self.epochs, self.lr, self.l2, self.seed = epochs, lr, l2, seed
        self.classes: list[str] = []
        self.W: dict = {}          # f -> [w_c]
        self.b: list[float] = []
        self.idf: dict = {}

    def fit(self, X: list[Counter], y: list[str]) -> "SoftmaxLR":
        self.classes = sorted(set(y))
        K = len(self.classes)
        ci = {c: i for i, c in enumerate(self.classes)}
        self.idf = _idf(X)
        V = [_tfidf(x, self.idf) for x in X]
        W: dict = defaultdict(lambda: [0.0] * K)
        G: dict = defaultdict(lambda: [1e-6] * K)
        b = [0.0] * K
        gb = [1e-6] * K
        idx = list(range(len(V)))
        rnd = random.Random(self.seed)
        for _ in range(self.epochs):
            rnd.shuffle(idx)
            for i in idx:
                v = V[i]
                z = list(b)
                for f, x in v.items():
                    w = W[f]
                    for k in range(K):
                        z[k] += w[k] * x
                m = max(z)
                e = [math.exp(t - m) for t in z]
                s = sum(e)
                p = [t / s for t in e]
                p[ci[y[i]]] -= 1.0
                for k in range(K):
                    gb[k] += p[k] * p[k]
                    b[k] -= self.lr * p[k] / math.sqrt(gb[k])
                for f, x in v.items():
                    w, g = W[f], G[f]
                    for k in range(K):
                        gr = p[k] * x + self.l2 * w[k]
                        g[k] += gr * gr
                        w[k] -= self.lr * gr / math.sqrt(g[k])
        self.W = {f: w for f, w in W.items()}
        self.b = b
        return self

    def predict_proba(self, x: Counter) -> dict:
        v = _tfidf(x, self.idf)
        K = len(self.classes)
        z = list(self.b)
        for f, val in v.items():
            w = self.W.get(f)
            if w:
                for k in range(K):
                    z[k] += w[k] * val
        return _softmax({c: z[k] for k, c in enumerate(self.classes)})

    def to_json(self):
        return {"classes": self.classes, "b": self.b, "idf": {str(f): round(v, 4) for f, v in self.idf.items()},
                "W": {str(f): [round(t, 5) for t in w] for f, w in self.W.items() if any(abs(t) > 1e-4 for t in w)}}

    @classmethod
    def from_json(cls, d):
        m = cls()
        m.classes, m.b = d["classes"], d["b"]
        m.idf = {int(f): v for f, v in d["idf"].items()}
        m.W = {int(f): w for f, w in d["W"].items()}
        return m


class Centroid:
    """Klasyfikator Rocchio: średni wektor TF-IDF każdej klasy, podobieństwo kosinusowe."""

    def __init__(self, temp: float = 0.05):
        self.temp = temp
        self.idf: dict = {}
        self.cent: dict = {}

    def fit(self, X: list[Counter], y: list[str]) -> "Centroid":
        self.idf = _idf(X)
        acc: dict = defaultdict(lambda: defaultdict(float))
        for x, c in zip(X, y):
            for f, v in _tfidf(x, self.idf).items():
                acc[c][f] += v
        self.cent = {}
        for c, vec in acc.items():
            nn = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            self.cent[c] = {f: v / nn for f, v in vec.items()}
        return self

    @property
    def classes(self):
        return sorted(self.cent)

    def predict_proba(self, x: Counter) -> dict:
        v = _tfidf(x, self.idf)
        sc = {c: sum(val * cv.get(f, 0.0) for f, val in v.items()) for c, cv in self.cent.items()}
        return _softmax(sc, temp=self.temp)

    def to_json(self):
        return {"temp": self.temp, "idf": {str(f): round(v, 4) for f, v in self.idf.items()},
                "cent": {c: {str(f): round(v, 5) for f, v in d.items() if abs(v) > 1e-4} for c, d in self.cent.items()}}

    @classmethod
    def from_json(cls, d):
        m = cls(d.get("temp", 0.05))
        m.idf = {int(f): v for f, v in d["idf"].items()}
        m.cent = {c: {int(f): v for f, v in dd.items()} for c, dd in d["cent"].items()}
        return m


def _softmax(logs: dict, temp: float = 1.0) -> dict:
    if not logs:
        return {}
    m = max(logs.values())
    e = {c: math.exp((v - m) / temp) for c, v in logs.items()}
    s = sum(e.values()) or 1.0
    return {c: v / s for c, v in e.items()}


def _ensemble(pn: dict, pl: dict, wn: float = 0.3) -> dict:
    keys = set(pn) | set(pl)
    return {c: wn * pn.get(c, 0.0) + (1 - wn) * pl.get(c, 0.0) for c in keys}


def training_set(corpus: list[dict] | None = None) -> tuple[list[Counter], list[str], list[dict]]:
    corpus = corpus if corpus is not None else user_corpus()
    X, y, docs = [], [], []
    for it in corpus:
        if not it.get("label"):
            continue
        X.append(featurize(it["text"]))
        y.append(it["label"])
        docs.append(it)
    return X, y, docs


def get_model(force: bool = False) -> dict | None:
    """Wytrenowany model (NB + LR) — z cache albo trenowany teraz."""
    sig = _programs_signature() + f"-v{MODEL_VERSION}"
    with _lock:
        if not force and _model_cache["sig"] == sig and _model_cache["model"] is not None:
            return _model_cache["model"]
        if not force and MODEL_PATH.exists():
            try:
                d = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
                if d.get("sig") == sig:
                    model = {"nb": NaiveBayes.from_json(d["nb"]), "cent": Centroid.from_json(d["cent"]),
                             "classes": d["classes"], "n_train": d["n_train"], "trained_at": d.get("trained_at"),
                             "counts": d.get("counts", {})}
                    _model_cache.update(sig=sig, model=model)
                    return model
            except (OSError, ValueError, KeyError):
                pass
        X, y, _ = training_set()
        if len(set(y)) < 2:
            _model_cache.update(sig=sig, model=None)
            return None
        t0 = time.time()
        nb = NaiveBayes().fit(X, y)
        cent = Centroid().fit(X, y)
        model = {"nb": nb, "cent": cent, "classes": sorted(set(y)), "n_train": len(y),
                 "trained_at": time.strftime("%Y-%m-%d %H:%M"), "counts": dict(Counter(y)),
                 "train_time": round(time.time() - t0, 2)}
        try:
            MODEL_PATH.write_text(json.dumps({"sig": sig, "nb": nb.to_json(), "cent": cent.to_json(),
                                              "classes": model["classes"], "n_train": len(y),
                                              "trained_at": model["trained_at"], "counts": model["counts"]}),
                                  encoding="utf-8")
        except OSError:
            pass
        _model_cache.update(sig=sig, model=model)
        return model


def classify(text: str, model: dict | None = None) -> dict:
    model = model or get_model()
    if not model:
        return {}
    x = featurize(text)
    if not x:
        return {}
    return _ensemble(model["nb"].predict_proba(x), model["cent"].predict_proba(x))


# ============================================================================================ podobne zadania
class TfidfIndex:
    def __init__(self, docs: list[dict]):
        self.docs = docs
        tf = []
        for d in docs:
            c = Counter(t for t in T.terms(d["text"]) if len(t) >= 2 and not _NUM_RE.match(t))
            tf.append(c)
        n = len(tf)
        df = Counter()
        for c in tf:
            df.update(c.keys())
        self.idf = {t: math.log((n + 1) / (k + 0.5)) for t, k in df.items()}
        self.vecs = [self._vec(c) for c in tf]

    def _vec(self, c: Counter) -> dict:
        v = {t: (1 + math.log(k)) * self.idf.get(t, math.log(len(self.docs) + 1)) for t, k in c.items()}
        nn = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / nn for t, x in v.items()}

    def query(self, text: str, k: int = 6, exclude: set | None = None) -> list[tuple[float, dict]]:
        c = Counter(t for t in T.terms(text) if len(t) >= 2 and not _NUM_RE.match(t))
        q = self._vec(c)
        out = []
        for d, v in zip(self.docs, self.vecs):
            if exclude and d["key"] in exclude:
                continue
            s = sum(x * v.get(t, 0.0) for t, x in q.items())
            out.append((s, d))
        out.sort(key=lambda sd: -sd[0])
        return out[:k]


def tfidf_index() -> TfidfIndex | None:
    sig = _programs_signature()
    with _lock:
        if _tfidf_cache["sig"] == sig and _tfidf_cache["index"] is not None:
            return _tfidf_cache["index"]
    docs = user_corpus()
    if not docs:
        return None
    idx = TfidfIndex(docs)
    with _lock:
        _tfidf_cache.update(sig=sig, index=idx)
    return idx


def similar_tasks(text: str, k: int = 5, with_code: bool = True, exclude: set | None = None) -> list[dict]:
    idx = tfidf_index()
    if not idx:
        return []
    out = []
    for score, d in idx.query(text, k=k, exclude=exclude):
        if score < 0.08:
            continue
        prog = max(d["programs"], key=lambda p: p.get("score") or 0) if d["programs"] else {}
        item = {"key": d["key"], "title": d["title"], "short": d["short"], "contest_id": d["contest_id"],
                "contest_name": d["contest_name"], "round": d["round"], "label": d.get("label"),
                "label_name": LABEL_NAMES.get(d.get("label") or "", None), "similarity": round(score, 3),
                "same_task": score >= 0.9, "score": prog.get("score"), "path": prog.get("path"),
                "program_id": prog.get("program_id"),
                "url": prog.get("url"), "lines": prog.get("lines")}
        msg = f"To zadanie przypomina Twoje «{d['title']} ({d['short']})»"
        if prog.get("score") is not None:
            msg += f" — rozwiązane na {prog['score']}"
        if score >= 0.9:
            msg = f"To jest prawdopodobnie Twoje zadanie «{d['title']} ({d['short']})» — masz już rozwiązanie na " \
                  f"{prog.get('score', '?')}"
        item["message"] = msg
        if with_code and prog.get("path"):
            item["code"] = read_program(prog["path"])
        out.append(item)
    return out


# ============================================================================================ baza wiedzy
def kb_links(algo_id: str, limit: int = 3) -> list[dict]:
    algos = load_signals().get("algorithms", {})
    info = algos.get(algo_id, {})
    out = []
    seen = set()
    for kid in info.get("kb", []):
        a = kb.get(kid)
        if a and kid not in seen:
            out.append({"id": kid, "title": a.get("title"), "category": a.get("category")})
            seen.add(kid)
        if len(out) >= limit:
            return out
    q = info.get("query") or info.get("name") or algo_id
    idx = _kb_index()
    if idx is not None and len(out) < limit:
        for r in idx.search(q, k=4):
            if r["score"] < 1.5 or r["id"] in seen:
                continue
            out.append({"id": r["id"], "title": r["payload"]["title"], "category": r["payload"].get("category")})
            seen.add(r["id"])
            if len(out) >= limit:
                break
    return out


def _kb_index():
    sig = kb.signature()
    with _lock:
        if _kb_cache["sig"] == sig and _kb_cache["idx"] is not None:
            return _kb_cache["idx"]
        arts = kb.articles()
        if not arts:
            return None
        idx = SearchIndex({"title": 4.0, "aliases": 3.0, "tags": 2.0, "summary": 1.0})
        for a in arts:
            idx.add(a["id"], {"title": a.get("title", ""), "aliases": " ".join(a.get("aliases") or []),
                              "tags": " ".join(a.get("tags") or []), "summary": a.get("summary", "")},
                    payload={"title": a.get("title"), "category": a.get("category")})
        idx.build()
        _kb_cache.update(sig=sig, idx=idx)
        return idx


# ============================================================================================ złożoność i priorytety
_VALUE_DIM_RE = re.compile(r"kwot\w*|pojemno\w*|ud[zź]wig\w*|bud[zż]et\w*|wag[aię]\b|kapita\w*|sum[aęy]\b|"
                           r"wydać|wyda[cć]|zap[lł]aci\w*", re.I)


def _digits_cx(D: int) -> tuple[str, dict]:
    """Złożoność względem liczby cyfr d (liczba podana jako napis): wiersz tabeli dla d, z „n” zamienionym na „d”."""
    row = dict(ST.complexity_for(D))
    c = row.get("complexity") or ""
    c = {"O(n)": "O(d) / O(d log d)" if D <= 10**7 else "O(d)", "O(n log n)": "O(d log d)"}.get(c, c.replace("n", "d"))
    return c, row


def _bignum_info(fmt: list[dict], cons: dict, var, nmax) -> dict | None:
    """„1 ≤ n ≤ 10^1000000” — rozmiarem danych jest liczba CYFR n (napis), nie wartość n."""
    sb = ST.size_bounds(cons)
    big = []
    for it in fmt:
        if it.get("kind") not in ("scalars", "multitest"):
            continue
        for v in it.get("vars") or []:
            c = cons.get(v) or sb.get(ST.base_var(v)) or {}
            if c.get("bignum") and c.get("max_digits"):
                big.append((ST.base_var(v), c))
    if not big:
        return None
    b, c = max(big, key=lambda x: x[1]["max_digits"])
    D = int(c["max_digits"])
    if nmax is not None and var != b and nmax > D:
        return None                    # są większe rozmiary (np. długość ciągu) — zostaw zwykłą analizę
    cx, row = _digits_cx(D)
    return {"var": f"d (cyfry {b})", "max": D, "max_fmt": ST.fmt_value(D), "complexity": cx,
            "hint": (f"{b} ≤ {ST.fmt_big(c)} ma do {ST.fmt_value(D)} cyfr — wczytaj {b} jako napis (string); rozmiarem "
                     f"danych jest liczba cyfr d, więc celuj w {cx}"
                     + (" (jedno lub kilka przejść po cyfrach)" if cx.startswith("O(d)") else "")
                     + ". Arytmetyka na long long nie wystarczy."),
            "index": row.get("index"), "bignum": b}


def complexity_info(parsed: dict) -> dict:
    """Dopuszczalna złożoność wg ROZMIARU danych (liczności: n, m, q, |s|, wiersze planszy), nie wartości.
    Kilka rozmiarów → osobna ocena dla każdego i podpowiedź dla sumy; plansza n × m → liczba pól n·m.
    Liczba podana jako napis (n ≤ 10^K, K > 18) → rozmiarem jest liczba jej cyfr."""
    fmt, cons = parsed.get("input_format") or [], parsed.get("constraints") or {}
    var, nmax, vals = ST.main_size(fmt, cons, product=True)
    big = _bignum_info(fmt, cons, var, nmax)
    if big is not None:
        var, nmax = big["var"], big["max"]
        vals = [(var, nmax)] + [(v, m) for v, m in vals if v != big["bignum"]]
    row = dict(ST.complexity_for(nmax))
    if big is not None:
        row.update(complexity=big["complexity"], hint=big["hint"], index=big["index"])
    grid = bool(var and ("·" in var or "²" in var))
    if grid and row.get("complexity") in ("O(n log n)", "O(n)"):
        row["complexity"] = f"O({var})"
        row["hint"] = "BFS/DFS po wszystkich polach planszy — liniowo względem liczby pól"
    sizes = [{"var": v, "max": m, "max_fmt": ST.fmt_value(m),
              "complexity": (_digits_cx(m)[0] if big is not None and v == var else ST.complexity_for(m).get("complexity"))}
             for v, m in vals]
    note = None
    if grid:
        note = (f"Plansza {' × '.join(v for v, _ in vals[:2]) if len(vals) >= 2 else var}: razem do "
                f"{ST.fmt_value(nmax)} pól — algorytm musi być (prawie) liniowy względem liczby pól.")
    elif big is not None:
        note = (f"{big['bignum']} to liczba do {ST.fmt_value(nmax)} cyfr: nie zmieści się w żadnym typie liczbowym. "
                f"Traktuj ją jak napis i licz złożoność względem liczby cyfr d.")
    elif len(vals) >= 2:
        names = " + ".join(v for v, _ in vals[:3])
        each = "; ".join(f"{v} ≤ {ST.fmt_value(m)} → {ST.complexity_for(m).get('complexity')}" for v, m in vals[:3])
        note = f"Kilka rozmiarów ({each}) — liczy się suma: celuj w O(({names}) log n) lub lepiej."
    # wymiar „po wartości”: kwota/pojemność ≤ 10^7, którą da się indeksować tablicę (DP po kwocie)
    sb = ST.size_bounds(cons)
    first = next((it for it in fmt if it.get("kind") == "scalars"), None)
    sec = parsed.get("sections") or {}
    if first and nmax and not grid and _VALUE_DIM_RE.search(" ".join(sec.get(k, "") for k in ("tresc", "wejscie"))):
        for v in first.get("vars") or []:
            b = ST.base_var(v)
            mx = (sb.get(b) or {}).get("max")
            if b != var and b not in [x for x, _ in vals] and isinstance(mx, int) and 1000 <= mx <= 10**7:
                prod = mx * nmax
                note = ((note + " ") if note else "") + (
                    f"{b} ≤ {ST.fmt_value(mx)} to wartość, ale można nią indeksować tablicę: DP po {b} to "
                    f"O({var}·{b}) ≈ {ST.fmt_value(prod)} operacji" + (" — zmieści się." if prod <= 3 * 10**8 else
                                                                       " — to za dużo, szukaj innego pomysłu."))
                sizes.append({"var": b, "max": mx, "max_fmt": ST.fmt_value(mx), "value_dim": True,
                              "complexity": f"O({var}·{b})"})
                break
    tl = parsed.get("time_limit")
    if tl and tl < 0.5:
        note = (note + " " if note else "") + f"Limit czasu jest mały ({tl} s) — potrzebujesz szybkiego wejścia i stałej."
    table = []
    for i, r in enumerate(ST.COMPLEXITY_TABLE):
        table.append({"label": r["label"], "complexity": r["complexity"], "hint": r["hint"], "ops": r["ops"],
                      "selected": row.get("index") == i})
    subtasks = []
    for st in parsed.get("subtasks") or []:
        sb = ST.size_bounds(st.get("constraints") or {})
        bsub = [(k, v) for k, v in sb.items() if v.get("bignum") and v.get("max_digits")]
        if bsub:
            k, v = bsub[0]
            D = int(v["max_digits"])
            cxs, r = _digits_cx(D)
            subtasks.append({"id": st.get("id"), "points": st.get("points"), "conditions": st.get("conditions"),
                             "var": f"d (cyfry {k})", "max": D, "complexity": cxs,
                             "hint": f"{k} ma do {ST.fmt_value(D)} cyfr — " + (r.get("hint") or "")})
            continue
        cand = [(k, v.get("max")) for k, v in sb.items() if isinstance(v.get("max"), (int, float))
                and (k == var or k in [x for x, _ in vals])]
        if not cand:
            cand = [(k, v.get("max")) for k, v in sb.items() if isinstance(v.get("max"), (int, float))]
        if cand:
            k, m = max(cand, key=lambda kv: kv[1])
            r = ST.complexity_for(m)
            subtasks.append({"id": st.get("id"), "points": st.get("points"), "conditions": st.get("conditions"),
                             "var": k, "max": m, "complexity": r.get("complexity"), "hint": r.get("hint")})
        else:
            subtasks.append({"id": st.get("id"), "points": st.get("points"), "conditions": st.get("conditions"),
                             "complexity": row.get("complexity"), "hint": "pełne ograniczenia"})
    out = {"var": var, "max": nmax, "max_fmt": ST.fmt_value(nmax) if nmax is not None else None,
           "complexity": row.get("complexity"), "hint": row.get("hint"), "index": row.get("index"),
           "sizes": sizes, "note": note, "table": table, "subtasks": subtasks}
    if big is not None:
        out["bignum"] = big["bignum"]
    return out


def _priors(parsed: dict, cx: dict) -> tuple[dict, dict]:
    pri: dict[str, float] = defaultdict(float)
    why: dict[str, list] = defaultdict(list)
    n = cx.get("max")
    fmt = parsed.get("input_format") or []

    def add(a, w, msg):
        pri[a] += w
        if w > 0:
            why[a].append({"signal": "constraints", "reason": msg, "weight": round(w, 2), "origin": "ograniczenia"})

    if n is not None:
        if n <= 10:
            add("backtracking", 1.4, f"{cx['var']} ≤ {cx['max_fmt']} — można sprawdzić wszystkie permutacje O(n!)")
            add("bitmask_dp", 0.6, f"{cx['var']} ≤ {cx['max_fmt']} — maski bitowe też się zmieszczą")
        elif n <= 22:
            add("bitmask_dp", 1.6, f"{cx['var']} ≤ {cx['max_fmt']} — klasyczny rozmiar dla 2^n (maski bitowe)")
            add("backtracking", 0.8, f"{cx['var']} ≤ {cx['max_fmt']} — przegląd wszystkich podzbiorów")
        elif n <= 45:
            add("meet_in_middle", 1.0, f"{cx['var']} ≤ {cx['max_fmt']} — 2^(n/2) się zmieści (meet in the middle)")
        elif n <= 500:
            add("floyd", 0.4, "n ≤ 500 — O(n³) się zmieści")
            add("dp", 0.4, "n ≤ 500 — DP O(n²) / O(n³) się zmieści")
        if n >= 10**5:
            for a in ("floyd", "backtracking", "bitmask_dp", "meet_in_middle"):
                pri[a] -= 2.0
        if n >= 10**9:
            for a in ("bfs", "dfs", "graph", "segment_tree", "prefix_sums", "sorting", "dp", "simulation"):
                pri[a] -= 0.8
            add("math", 1.2, f"{cx['var']} ≤ {cx['max_fmt']} — nie da się przejść pętlą, potrzebny wzór / logarytm")
            add("binary_search", 0.6, "duże n — wyszukiwanie binarne po wyniku ma tylko ~60 kroków")
            add("fast_pow", 0.3, "duże wykładniki → szybkie potęgowanie")
    if cx.get("bignum"):
        add("string_processing", 1.0, f"{cx['bignum']} ma do {cx.get('max_fmt')} cyfr — przetwarzasz ją jak napis, "
                                      "cyfra po cyfrze")
        add("math", 0.6, "operacje na bardzo dużej liczbie: przeniesienia, reszty, własności cyfr")
        for a in ("floyd", "backtracking", "bitmask_dp", "meet_in_middle", "sieve", "binary_search"):
            pri[a] -= 1.0
    kinds = [it.get("kind") for it in fmt]
    rows = [it for it in fmt if it.get("kind") == "rows"]
    if "grid" in kinds:
        add("grid", 1.2, "wejście zawiera planszę (wiersze znaków)")
        add("bfs", 0.4, "plansza → BFS po polach")
    if "queries" in kinds:
        add("segment_tree", 0.4, "wejście ma zapytania")
        add("stl", 0.3, "wejście ma zapytania różnych typów")
    if any(len(r.get("vars") or []) in (2, 3) and str(r.get("repeat", "")).replace(" ", "") in ("m", "n-1")
           for r in rows):
        rep = next(str(r.get("repeat")) for r in rows if len(r.get("vars") or []) in (2, 3))
        if rep.replace(" ", "") == "n-1":
            add("tree", 1.0, "n − 1 wierszy po dwie liczby — to wygląda na krawędzie drzewa")
        else:
            add("graph", 0.6, "m wierszy po dwie/trzy liczby — wygląda na listę krawędzi grafu")
        if any(len(r.get("vars") or []) == 3 for r in rows):
            add("dijkstra", 0.5, "krawędź opisana trzema liczbami (a, b, waga)")
    if any(it.get("kind") in ("string", "lines") for it in fmt):
        add("string_processing", 0.6, "wejście zawiera napis")
    if any(it.get("kind") == "multitest" for it in fmt):
        pass
    return dict(pri), dict(why)


# ============================================================================================ analiza
def analyze(statement: str, *, parsed: dict | None = None, exclude_keys: set | None = None,
            with_code: bool = True) -> dict:
    p = parsed or ST.parse(statement)
    text = doc_text(p)
    sig, reasons = signal_scores(text)
    note = (p.get("meta") or {}).get("header_note")
    if note and len(note) < 60:
        s2, r2 = signal_scores(note, weight=1.5, origin="nagłówek")
        for a, v in s2.items():
            sig[a] = sig.get(a, 0.0) + v
        for a, lst in r2.items():
            for r in lst:
                r["reason"] = f"nagłówek treści: «{note}» — " + r["reason"]
            reasons.setdefault(a, []).extend(lst)
    cx = complexity_info(p)
    pri, why = _priors(p, cx)
    clf = classify(text)
    model = get_model()
    algos = load_signals().get("algorithms", {})
    combined: dict[str, float] = defaultdict(float)
    s_max = max([v for v in sig.values() if v > 0] or [1.0])
    for a in set(sig) | set(pri) | {a for lab in clf for a in LABEL_TO_ALGOS.get(lab, {})}:
        s = sig.get(a, 0.0)
        part_sig = max(0.0, s) / (s_max + 1.5)
        part_clf = sum(p_ * LABEL_TO_ALGOS.get(lab, {}).get(a, 0.0) for lab, p_ in clf.items())
        combined[a] = 0.9 * (BLEND_SIGNALS * part_sig + (1 - BLEND_SIGNALS) * part_clf) + \
            0.12 * math.tanh(pri.get(a, 0.0)) + \
            (0.08 * math.tanh(s / 3) if s < 0 else 0.0)
    # ogólne kategorie („Grafy — podstawy”) ustępują konkretnym algorytmom z tej samej rodziny
    for generic, specific in (("graph", ("bfs", "dfs", "dijkstra", "topo_sort", "dsu")), ("tree", ("lca",)),
                              ("simulation", ("simulation_queue",))):
        if generic in combined and any(combined.get(x, 0) >= 0.6 * combined[generic] for x in specific):
            combined[generic] *= 0.75
    ranking = []
    for a, v in sorted(combined.items(), key=lambda kv: -kv[1]):
        if v <= 0.04 or a not in algos:
            continue
        info = algos[a]
        rs = sorted(reasons.get(a, []) + why.get(a, []), key=lambda r: -r["weight"])
        seen, uniq = set(), []
        for r in rs:
            if r["reason"] in seen:
                continue
            seen.add(r["reason"])
            uniq.append(r)
        lab = ALGO_TO_LABEL.get(a)
        if lab and clf.get(lab, 0) >= 0.15:
            uniq.append({"signal": "classifier", "origin": "klasyfikator",
                         "reason": f"klasyfikator uczony na Twoich {model['n_train'] if model else 0} zadaniach: "
                                   f"temat «{LABEL_NAMES.get(lab, lab)}» ({round(100 * clf[lab])}%)",
                         "weight": round(clf[lab], 2)})
        ranking.append({"id": a, "name": info.get("name", a), "complexity": info.get("complexity"),
                        "when": info.get("when"), "score": round(v, 4), "confidence": round(min(0.97, v * 1.25), 3),
                        "reasons": uniq[:5], "kb": kb_links(a)})
        if len(ranking) >= 8:
            break
    if ranking:
        top = ranking[0]["score"]
        for r in ranking:
            r["relative"] = round(r["score"] / top, 3) if top else 0
    topics = [{"label": lab, "name": LABEL_NAMES.get(lab, lab), "p": round(pv, 3)}
              for lab, pv in sorted(clf.items(), key=lambda kv: -kv[1])[:5]]
    similar = similar_tasks(text, k=5, with_code=with_code, exclude=exclude_keys)
    return {
        "title": p.get("title"),
        "complexity": cx,
        "algorithms": ranking,
        "topics": topics,
        "similar": similar,
        "classifier": {"n_train": model["n_train"] if model else 0, "classes": model["classes"] if model else [],
                       "trained_at": model.get("trained_at") if model else None,
                       "available": bool(model)},
        "parsed": {"title": p.get("title"), "constraints": p.get("constraints"), "input_format": p.get("input_format"),
                   "time_limit": p.get("time_limit"), "memory_limit": p.get("memory_limit"),
                   "warnings": p.get("warnings")},
    }


# ============================================================================================ ewaluacja (walidacja krzyżowa)
def _groups(docs: list[dict]) -> list[int]:
    """Grupy prawie identycznych treści (to samo zadanie w kilku konkursach) — nie mogą trafić do train i test."""
    n = len(docs)
    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    idx = TfidfIndex(docs)
    for i in range(n):
        for j in range(i + 1, n):
            s = sum(x * idx.vecs[j].get(t, 0.0) for t, x in idx.vecs[i].items())
            if s >= 0.6 or T.normalize(docs[i]["title"]) == T.normalize(docs[j]["title"]):
                par[find(i)] = find(j)
    return [find(i) for i in range(n)]


def evaluate(folds: int = 5, seed: int = 13, use_cache: bool = True, with_lr: bool = False) -> dict:
    """Walidacja krzyżowa (grupowana po zadaniu) top-1/top-3 dla: NB, centroidu TF-IDF, NB+centroid, sygnałów
    i mieszanki (opcjonalnie także wolniejszej regresji logistycznej)."""
    sig = _programs_signature() + f"-v{MODEL_VERSION}-f{folds}-lr{int(with_lr)}"
    if use_cache and EVAL_PATH.exists():
        try:
            d = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
            if d.get("sig") == sig:
                return d["result"]
        except (OSError, ValueError):
            pass
    X, y, docs = training_set()
    if len(docs) < 10:
        return {"error": "Za mało zadań z etykietami do walidacji."}
    grp = _groups(docs)
    uniq = sorted(set(grp))
    rnd = random.Random(seed)
    rnd.shuffle(uniq)
    fold_of = {g: i % folds for i, g in enumerate(uniq)}
    hits = defaultdict(lambda: [0, 0])
    per_class = defaultdict(lambda: [0, 0])
    conf = defaultdict(Counter)
    t0 = time.time()
    for f in range(folds):
        tr = [i for i in range(len(docs)) if fold_of[grp[i]] != f]
        te = [i for i in range(len(docs)) if fold_of[grp[i]] == f]
        if not te or len({y[i] for i in tr}) < 2:
            continue
        nb = NaiveBayes().fit([X[i] for i in tr], [y[i] for i in tr])
        ce = Centroid().fit([X[i] for i in tr], [y[i] for i in tr])
        lr = SoftmaxLR(epochs=12).fit([X[i] for i in tr], [y[i] for i in tr]) if with_lr else None
        for i in te:
            pn, pc = nb.predict_proba(X[i]), ce.predict_proba(X[i])
            pl = lr.predict_proba(X[i]) if lr else None
            pe = _ensemble(pn, pc)
            # sygnały → tematy
            s, _ = signal_scores(docs[i]["text"])
            sl: dict = defaultdict(float)
            for a, v in s.items():
                lab = ALGO_TO_LABEL.get(a)
                if lab and v > 0:
                    sl[lab] = max(sl[lab], v)
            smax = max(sl.values() or [1.0])
            blend = {lab: BLEND_SIGNALS * (sl.get(lab, 0.0) / (smax + 1.5)) + (1 - BLEND_SIGNALS) * pe.get(lab, 0.0)
                     for lab in set(sl) | set(pe)}
            named = [("nb", pn), ("centroid", pc), ("nb+centroid", pe), ("signals", dict(sl)), ("blend", blend)]
            if pl is not None:
                named.append(("lr", pl))
            for name, pr in named:
                if not pr:
                    continue
                order = [c for c, _ in sorted(pr.items(), key=lambda kv: -kv[1])]
                hits[name][0] += int(order[:1] == [y[i]])
                hits[name][1] += int(y[i] in order[:3])
            order = [c for c, _ in sorted(blend.items(), key=lambda kv: -kv[1])]
            per_class[y[i]][0] += int(order[:1] == [y[i]])
            per_class[y[i]][1] += 1
            conf[y[i]][order[0] if order else "-"] += 1
    n = len(docs)
    res = {"n_docs": n, "n_groups": len(uniq), "folds": folds, "classes": dict(Counter(y)),
           "time_s": round(time.time() - t0, 1),
           "models": {k: {"top1": round(v[0] / n, 3), "top3": round(v[1] / n, 3)} for k, v in hits.items()},
           "per_class_top1": {c: {"ok": v[0], "n": v[1], "acc": round(v[0] / v[1], 2)} for c, v in per_class.items()},
           "confusion": {c: dict(v) for c, v in conf.items()}}
    try:
        EVAL_PATH.write_text(json.dumps({"sig": sig, "result": res}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return res


# ============================================================================================ HTTP
def handle_suggest(body: dict) -> dict:
    st = (body.get("statement") or body.get("text") or "").strip()
    if not st:
        raise ApiError("Wklej treść zadania (albo upuść PDF).")
    if len(st) > 400_000:
        raise ApiError("Treść jest za długa (maks. 400 tys. znaków).")
    return analyze(st, with_code=bool(body.get("with_code", True)))


def handle_eval(q: dict) -> dict:
    return {"eval": evaluate(folds=int(q.get("folds") or 5), use_cache=q.get("fresh") not in ("1", "true"))}


def handle_algorithms(_q: dict) -> dict:
    algos = load_signals().get("algorithms", {})
    return {"algorithms": [{"id": k, **{kk: vv for kk, vv in v.items() if not kk.startswith("_")}}
                           for k, v in algos.items()],
            "complexity_table": ST.COMPLEXITY_TABLE}


def handle_retrain(_body: dict) -> dict:
    m = get_model(force=True)
    return {"trained": bool(m), "n_train": m["n_train"] if m else 0, "classes": m["classes"] if m else []}


ROUTES = {
    "POST /api/suggest": handle_suggest,
    "GET /api/suggest/eval": handle_eval,
    "GET /api/suggest/algorithms": handle_algorithms,
    "POST /api/suggest/retrain": handle_retrain,
}
