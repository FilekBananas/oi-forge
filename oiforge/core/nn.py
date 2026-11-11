"""Mała sieć neuronowa (MLP) na haszowanych cechach tekstu — czysty Python, bez numpy.

Architektura (w stylu fastText z warstwą ukrytą):

    tekst ──► cechy haszowane (unigramy rdzeni, bigramy rdzeni, trigramy znaków słów; crc32 mod 2^18)
         ──► h = tanh(b1 + (1/√n) · Σ E[cecha])          (warstwa ukryta, domyślnie 64 neurony)
         ──► softmax(W2 · h + b2)                         (rozkład nad etykietami, np. artykułami KB)

Trening: SGD z liniowo malejącym krokiem, *sampled softmax* (etykieta docelowa + K losowych negatywów)
gdy etykiet jest dużo — dzięki temu koszt kroku nie rośnie z liczbą klas. Operacje wektorowe są robione
przez `map`/`zip`/`sum` (pętle w C), więc ~3–5 tys. kroków/s w CPython.

Wagi zapisujemy kompaktowo (float16 → zlib → base64) w JSON z kluczem (np. podpisem bazy wiedzy),
a przy zmianie danych można zacząć od poprzedniego modelu (`warm_start`) — douczanie trwa wtedy krótko.

Determinizm: haszowanie przez `zlib.crc32` (nie `hash()`, który jest losowany per proces) i stałe ziarno RNG.
"""
from __future__ import annotations

import base64
import json
import math
import operator
import os
import random
import struct
import time
import zlib
from itertools import repeat
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import text as T

FORMAT_VERSION = 3
_mul, _add, _sub = operator.mul, operator.add, operator.sub


def _root(t: str) -> str:
    # import leniwy: search importuje config, a nn ma być używalne samodzielnie
    from .search import root
    return root(t)


# ============================================================================================ cechy
def text_features(text: str, char_n: int = 3) -> list[str]:
    """Cechy tekstowe (przed haszowaniem): `u:` rdzenie słów, `b:` bigramy rdzeni, `c:` trigramy znaków słów.

    Rdzenie (stem + root) sprawiają, że „seta”, „secie” i „set” dają tę samą cechę; trigramy znaków dają
    odporność na literówki („binserch” dzieli większość trigramów z „binsearch”)."""
    toks = T.tokenize(text)
    words = [t for t in toks if t not in T.STOPWORDS] or toks
    feats: list[str] = []
    roots = []
    for w in words:
        r = _root(T.stem(w))
        roots.append(r)
        feats.append("u:" + r)
    for a, b in zip(roots, roots[1:]):
        feats.append("b:" + a + "_" + b)
    for w in words:
        if len(w) < 3 or len(w) > 24 or w.isdigit():
            continue
        p = "<" + w + ">"
        for i in range(len(p) - char_n + 1):
            feats.append("c:" + p[i:i + char_n])
    return feats


def hash_feature(f: str, buckets: int) -> int:
    return zlib.crc32(f.encode("utf-8")) % buckets


# ============================================================================================ pakowanie wag
def _pack(values: Sequence[float]) -> str:
    raw = struct.pack(f"<{len(values)}e", *[max(-65000.0, min(65000.0, v)) for v in values])
    return base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def _unpack(data: str, n: int) -> list[float]:
    raw = zlib.decompress(base64.b64decode(data))
    return list(struct.unpack(f"<{n}e", raw))


# ============================================================================================ model
class HashedMLP:
    """Klasyfikator tekstu: haszowane cechy → warstwa ukryta tanh → softmax nad etykietami."""

    def __init__(self, labels: Sequence[str] | None = None, hidden: int = 64, buckets: int = 1 << 18,
                 seed: int = 1234, char_n: int = 3):
        self.labels: list[str] = list(labels or [])
        self.label_index = {l: i for i, l in enumerate(self.labels)}
        self.hidden = hidden
        self.buckets = buckets
        self.char_n = char_n
        self.seed = seed
        self.rng = random.Random(seed)
        self.rows: dict[int, list[float]] = {}           # hasz cechy → wektor osadzenia (H)
        self.b1 = [0.0] * hidden
        self.W2 = [[0.0] * hidden for _ in self.labels]    # etykieta → wektor (H)
        self.b2 = [0.0] * len(self.labels)
        self.meta: dict = {}

    # ------------------------------------------------------------------ cechy
    def featurize(self, text: str) -> list[int]:
        return [hash_feature(f, self.buckets) for f in text_features(text, self.char_n)]

    def _row(self, f: int) -> list[float]:
        r = self.rows.get(f)
        if r is None:
            a = 0.5 / math.sqrt(self.hidden)
            u = self.rng.uniform
            r = [u(-a, a) for _ in range(self.hidden)]
            self.rows[f] = r
        return r

    # ------------------------------------------------------------------ przejście w przód
    def _hidden_from(self, feats: Iterable[int]) -> list[float] | None:
        vecs = [self.rows[f] for f in feats if f in self.rows]
        if not vecs:
            return None
        scale = 1.0 / math.sqrt(len(vecs))
        pre = map(_add, map(_mul, map(sum, zip(*vecs)), repeat(scale)), self.b1)
        return list(map(math.tanh, pre))

    def embed(self, text: str) -> list[float] | None:
        """Wektor warstwy ukrytej (do podobieństwa semantycznego), None gdy brak znanych cech."""
        return self._hidden_from(self.featurize(text))

    def scores(self, text: str) -> list[float] | None:
        h = self.embed(text)
        if h is None:
            return None
        return [sum(map(_mul, w, h)) + b for w, b in zip(self.W2, self.b2)]

    def predict_proba(self, text: str, temperature: float = 1.0) -> dict[str, float]:
        """{etykieta: prawdopodobieństwo}; pusty słownik, gdy tekst nie ma żadnej znanej cechy."""
        logits = self.scores(text)
        if not logits:
            return {}
        m = max(logits)
        ex = [math.exp((x - m) / temperature) for x in logits]
        z = sum(ex) or 1.0
        return {l: e / z for l, e in zip(self.labels, ex)}

    def predict(self, text: str, k: int = 5) -> list[tuple[str, float]]:
        p = self.predict_proba(text)
        return sorted(p.items(), key=lambda kv: -kv[1])[:k]

    def known_fraction(self, text: str) -> float:
        """Jaka część cech tekstu była widziana w treningu (0..1) — miara „czy sieć zna ten język”."""
        f = self.featurize(text)
        return sum(1 for x in f if x in self.rows) / len(f) if f else 0.0

    # ------------------------------------------------------------------ trening
    def _prepare(self, examples: Sequence[tuple[str, str]]) -> list[tuple[list[list[float]], int]]:
        for _, y in examples:
            if y not in self.label_index:
                self.label_index[y] = len(self.labels)
                self.labels.append(y)
                self.W2.append([0.0] * self.hidden)
                self.b2.append(0.0)
        data = []
        for x, y in examples:
            feats = self.featurize(x)
            if feats:
                # duplikaty cech w jednym przykładzie łączymy (każdy wiersz aktualizowany raz na krok)
                data.append(([self._row(f) for f in dict.fromkeys(feats)], self.label_index[y]))
        return data

    def fit(self, examples: Sequence[tuple[str, str]], epochs: float = 5.0, lr: float = 0.5,
            negatives: int = 12, max_seconds: float | None = None, feature_dropout: float = 0.0,
            should_stop: Callable[[], bool] | None = None,
            progress: Callable[[float, str], None] | None = None) -> dict:
        """Trenuje na parach (tekst, etykieta) — *wznawialnie*.

        Docelowo `epochs` epok (harmonogram kroku liczony globalnie). Postęp jest pamiętany w `meta["step"]`,
        więc kolejne wywołanie z tymi samymi danymi kontynuuje trening (np. po wczytaniu z cache).
        max_seconds: limit czasu CPU tego wątku (`time.thread_time`) na to wywołanie — model jest używalny po
        każdym kawałku. feature_dropout: w każdym kroku losowo pomija taką część cech (regularyzacja — sieć uczy
        się rozpoznawać temat także z fragmentu pytania, zamiast zapamiętywać całe frazy). Zwraca {steps, step, planned, epochs, done, cpu_seconds, seconds, loss, examples, ...}."""
        t0, c0 = time.perf_counter(), time.thread_time()
        key = (id(examples), len(examples), len(self.labels))
        if getattr(self, "_prep_key", None) != key:
            self._prep = self._prepare(examples)
            self._prep_key = (id(examples), len(examples), len(self.labels))
        data = self._prep
        n = len(data)
        C, H = len(self.labels), self.hidden
        if not n or C < 2:
            return {"steps": 0, "step": 0, "planned": 0, "epochs": 0, "done": True, "seconds": 0.0,
                    "cpu_seconds": 0.0, "loss": None, "examples": n}
        K = min(negatives, C - 1)
        full = C <= 24
        planned = max(1, int(epochs * n))
        step = int(self.meta.get("step", 0)) if self.meta.get("examples") == n else 0
        b1, W2, b2 = self.b1, self.W2, self.b2
        tanh, exp, log, sqrt = math.tanh, math.exp, math.log, math.sqrt
        rng = self.rng
        loss_acc, loss_n, done_here = 0.0, 0, 0
        last_report = t0
        epoch_no = -1
        order: list[int] = []
        while step < planned:
            e = step // n
            if e != epoch_no:
                epoch_no = e
                order = list(range(n))
                random.Random(self.seed * 1000 + e).shuffle(order)
            vecs, y = data[order[step % n]]
            if feature_dropout and len(vecs) > 2:
                rnd = rng.random
                kept = [v for v in vecs if rnd() >= feature_dropout]
                vecs = kept if kept else vecs[:1]
            lr_t = lr * max(0.03, 1.0 - step / planned)
            scale = 1.0 / sqrt(len(vecs))
            h = list(map(tanh, map(_add, map(_mul, map(sum, zip(*vecs)), repeat(scale)), b1)))
            if full:
                cand = range(C)
                yi = y
            else:
                cs = {y}
                while len(cs) <= K:
                    cs.add(rng.randrange(C))
                cs.discard(y)
                cand = [y, *cs]
                yi = 0
            logits = [sum(map(_mul, W2[c], h)) + b2[c] for c in cand]
            m = max(logits)
            ex = [exp(v - m) for v in logits]
            z = sum(ex)
            loss_acc -= log(max(ex[yi] / z, 1e-12))
            loss_n += 1
            dh = [0.0] * H
            for i, c in enumerate(cand):
                g = ex[i] / z - (1.0 if i == yi else 0.0)
                if -1e-4 < g < 1e-4:
                    continue
                w = W2[c]
                dh = list(map(_add, dh, map(_mul, w, repeat(g))))
                w[:] = map(_sub, w, map(_mul, h, repeat(lr_t * g)))
                b2[c] -= lr_t * g
            dpre = list(map(_mul, dh, [1.0 - v * v for v in h]))
            upd = list(map(_mul, dpre, repeat(lr_t * scale)))
            for v in vecs:
                v[:] = map(_sub, v, upd)
            b1[:] = map(_sub, b1, map(_mul, dpre, repeat(lr_t)))
            step += 1
            done_here += 1
            if done_here % 128 == 0:
                if should_stop and should_stop():
                    break
                if max_seconds is not None and time.thread_time() - c0 > max_seconds:
                    break
                now = time.perf_counter()
                if progress and now - last_report > 0.5:
                    last_report = now
                    progress(step / planned, f"epoka {step / n:.1f}/{epochs:g}, strata {loss_acc / max(1, loss_n):.3f}")
        self.meta.update(step=step, examples=n, planned=planned, epochs_done=round(step / n, 2))
        stats = {"steps": done_here, "step": step, "planned": planned, "epochs": round(step / n, 2),
                 "done": step >= planned, "seconds": round(time.perf_counter() - t0, 2),
                 "cpu_seconds": round(time.thread_time() - c0, 2),
                 "loss": round(loss_acc / max(1, loss_n), 4) if loss_n else None, "examples": n,
                 "features": len(self.rows), "labels": C}
        self.meta["train"] = stats
        return stats

    def evaluate(self, examples: Sequence[tuple[str, str]], k: int = 1) -> float:
        """Trafność top-k na parach (tekst, etykieta)."""
        if not examples:
            return 0.0
        hit = 0
        for x, y in examples:
            if y in [l for l, _ in self.predict(x, k)]:
                hit += 1
        return hit / len(examples)

    # ------------------------------------------------------------------ warm start
    def warm_start(self, other: "HashedMLP") -> int:
        """Przejmuje wagi zgodnego modelu (te same wymiary): wiersze cech i wektory wspólnych etykiet.
        Zwraca liczbę przejętych etykiet."""
        if other.hidden != self.hidden or other.buckets != self.buckets or other.char_n != self.char_n:
            return 0
        for f, r in other.rows.items():
            self.rows[f] = list(r)
        self.b1 = list(other.b1)
        n = 0
        for i, l in enumerate(self.labels):
            j = other.label_index.get(l)
            if j is not None:
                self.W2[i] = list(other.W2[j])
                self.b2[i] = other.b2[j]
                n += 1
        return n

    # ------------------------------------------------------------------ zapis/odczyt
    def to_dict(self) -> dict:
        feats = sorted(self.rows)
        flat = [v for f in feats for v in self.rows[f]]
        w2 = [v for row in self.W2 for v in row]
        return {"format": FORMAT_VERSION, "hidden": self.hidden, "buckets": self.buckets, "char_n": self.char_n,
                "seed": self.seed, "labels": self.labels, "features": feats, "E": _pack(flat), "W2": _pack(w2),
                "b1": [round(v, 6) for v in self.b1], "b2": [round(v, 6) for v in self.b2], "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict) -> "HashedMLP":
        if d.get("format") != FORMAT_VERSION:
            raise ValueError("Niezgodny format modelu.")
        m = cls(d["labels"], hidden=d["hidden"], buckets=d["buckets"], seed=d.get("seed", 1234),
                char_n=d.get("char_n", 3))
        H = m.hidden
        feats = d["features"]
        flat = _unpack(d["E"], len(feats) * H)
        m.rows = {f: flat[i * H:(i + 1) * H] for i, f in enumerate(feats)}
        w2 = _unpack(d["W2"], len(m.labels) * H)
        m.W2 = [w2[i * H:(i + 1) * H] for i in range(len(m.labels))]
        m.b1 = list(d["b1"])
        m.b2 = list(d["b2"])
        m.meta = d.get("meta") or {}
        return m

    def save(self, path: Path | str, key: str = "") -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        d = self.to_dict()
        d["key"] = key
        tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")      # unikalny: kilka procesów naraz
        tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path | str, key: str | None = None) -> "HashedMLP | None":
        """Wczytuje model; None, gdy pliku brak, jest uszkodzony albo klucz się nie zgadza."""
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            if key is not None and d.get("key") != key:
                return None
            return cls.from_dict(d)
        except (OSError, ValueError, KeyError, TypeError, struct.error, zlib.error):
            return None


# ============================================================================================ augmentacja
def typo_variants(text: str, rng: random.Random, n: int = 1) -> list[str]:
    """Warianty tekstu z jedną losową literówką w jednym dłuższym słowie (usunięcie/zamiana/podwojenie/
    zamiana sąsiednich liter) — uczy sieć odporności na literówki."""
    words = text.split()
    idx = [i for i, w in enumerate(words) if len(w) >= 5 and w.isalpha()]
    out = []
    for _ in range(n):
        if not idx:
            break
        i = rng.choice(idx)
        w = words[i]
        j = rng.randrange(1, len(w) - 1)
        op = rng.randrange(4)
        if op == 0:
            w2 = w[:j] + w[j + 1:]
        elif op == 1:
            w2 = w[:j] + w[j + 1] + w[j] + w[j + 2:] if j + 1 < len(w) else w[:j]
        elif op == 2:
            w2 = w[:j] + w[j] + w[j:]
        else:
            w2 = w[:j] + rng.choice("aeiouyszcnrkl") + w[j + 1:]
        out.append(" ".join(words[:i] + [w2] + words[i + 1:]))
    return out


def word_dropout(text: str, rng: random.Random, p: float = 0.3) -> str | None:
    """Wariant bez części słów (gdy zostają ≥ 2 słowa) — uczy rozpoznawać krótkie, „ucięte” pytania."""
    words = text.split()
    if len(words) < 3:
        return None
    kept = [w for w in words if rng.random() > p]
    if len(kept) < 2 or len(kept) == len(words):
        return None
    return " ".join(kept)


__all__ = ["HashedMLP", "text_features", "hash_feature", "typo_variants", "word_dropout"]
