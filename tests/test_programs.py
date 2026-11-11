"""Testy „Moich programów”: mini-korpus syntetyczny (zawsze), prawdziwe 238 programów ucznia (skipUnless)
z raportem top-5 i trafności względem pola `round`, test HTTP na porcie 8808.

Uruchom: python3 -m unittest tests.test_programs -v
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OIFORGE_NO_WARMUP", "1")
os.environ.setdefault("OIFORGE_ALGODETECT", "off")      # deterministycznie: bez liczenia algodetect w tle

from oiforge.tools import programs as PR  # noqa: E402

REAL_INDEX = ROOT / "user_data" / "programs" / "index.json"
PORT = 8808

DIJKSTRA = """#include <bits/stdc++.h>
using namespace std;
vector<pair<int,int>> g[200005];
long long dist[200005];
int main(){
    int n, m; cin >> n >> m;
    for (int i = 0; i < m; i++) { int a, b, c; cin >> a >> b >> c; g[a].push_back({b, c}); g[b].push_back({a, c}); }
    priority_queue<pair<long long,int>, vector<pair<long long,int>>, greater<>> pq;
    for (int i = 1; i <= n; i++) dist[i] = 1e18;
    dist[1] = 0; pq.push({0, 1});
    while (!pq.empty()) {
        auto [d, v] = pq.top(); pq.pop();
        if (d > dist[v]) continue;
        for (auto [u, w] : g[v]) if (dist[v] + w < dist[u]) { dist[u] = dist[v] + w; pq.push({dist[u], u}); }
    }
    cout << dist[n] << "\\n";
}
"""
BINSEARCH = """#include <bits/stdc++.h>
using namespace std;
// szukam najmniejszego x takiego, że x*x >= n
int main(){
    long long n; cin >> n;
    long long lo = 0, hi = 2000000000;
    while (lo < hi) {
        long long mid = (lo + hi) / 2;
        if (mid * mid >= n) hi = mid; else lo = mid + 1;
    }
    cout << lo;
}
"""
DSU = """#include <bits/stdc++.h>
int rep[100005];
int Find(int x){ if (rep[x] == x) return x; return rep[x] = Find(rep[x]); }
void Union(int a, int b){ rep[Find(a)] = Find(b); }
int main(){ int n, q; std::cin >> n >> q; for (int i = 1; i <= n; i++) rep[i] = i; }
"""
SEGTREE = """#include <bits/stdc++.h>
using namespace std;
const int M = 1 << 17;
long long tree[2 * M];
void update(int v, long long x){ v += M; tree[v] = x; v /= 2; while (v >= 1) { tree[v] = tree[2 * v] + tree[2 * v + 1]; v /= 2; } }
long long query(int v, int a, int b, int l, int r){ if (r < a || b < l) return 0; if (a <= l && r <= b) return tree[v];
  int s = (l + r) / 2; return query(2 * v, a, b, l, s) + query(2 * v + 1, a, b, s + 1, r); }
int main(){ int n; cin >> n; }
"""
PREFIX = """#include <bits/stdc++.h>
using namespace std;
#define int long long
int pref[1000005];
signed main(){ int n, q; cin >> n >> q; for (int i = 1; i <= n; i++) { int x; cin >> x; pref[i] = pref[i - 1] + x; }
  while (q--) { int a, b; cin >> a >> b; cout << pref[b] - pref[a - 1] << '\\n'; } }
"""
KNAP = """#include <bits/stdc++.h>
using namespace std;
int dp[100005];
int main(){ int n, W; cin >> n >> W; for (int i = 0; i < n; i++) { int w, v; cin >> w >> v;
  for (int j = W; j >= w; j--) dp[j] = max(dp[j], dp[j - w] + v); } cout << dp[W]; }
"""
SIEVE = """#include <bits/stdc++.h>
bool sito[1000001];
int main(){ for (int i = 2; i * i <= 1000000; i++) if (!sito[i]) for (int j = i * i; j <= 1000000; j += i) sito[j] = 1; }
"""
BFS = """#include <bits/stdc++.h>
using namespace std;
vector<int> adj[100005]; int odl[100005];
int main(){ int n, m; cin >> n >> m; for (int i = 0; i < m; i++) { int a, b; cin >> a >> b; adj[a].push_back(b);
  adj[b].push_back(a); } queue<int> q; q.push(1); fill(odl, odl + n + 1, -1); odl[1] = 0;
  while (!q.empty()) { int v = q.front(); q.pop(); for (int u : adj[v]) if (odl[u] < 0) { odl[u] = odl[v] + 1; q.push(u); } } }
"""

CORPUS = [
    # (contest, short, name, round, code, statement)
    ("kolko", "pie", "Pierwiastek", "Wyszukiwanie binarne (27 October 2025, 15:15 - )", BINSEARCH,
     "Pierwiastek\n\nKółko Kod zadania: pie\n\nWyszukiwanie binarne Limit pamięci: 64 MiB\n\nDana jest liczba n. "
     "Znajdź najmniejszą liczbę p, że p*p >= n.\n\nWejście\n\nLiczba n.\n\nWyjście\n\nLiczba p."),
    ("kolko", "lot", "Lot", "Dzień 5 - Dijkstra (22 August 2026, 09:00 - 14:00)", DIJKSTRA,
     "Lot\n\nBajtazar chce dolecieć z miasta 1 do miasta n najkrótszą drogą. Połączenia mają czasy lotu.\n\n"
     "Wejście\n\nLiczby n, m oraz m krawędzi a b c.\n\nWyjście\n\nNajkrótszy czas podróży."),
    ("kolko", "tv", "TV", "Find & Union (16 February 2026, 15:00 - )", DSU,
     "TV\n\nKoledzy łączą się w grupy oglądających. Odpowiadaj, czy dwie osoby są w tej samej grupie."),
    ("kolko", "zar", "Żarówki", "Drzewa przedziałowe (20 August 2026, 09:00 - 14:00)", SEGTREE,
     "Żarówki\n\nNa przedziale lamp zmieniamy stan i pytamy o liczbę zapalonych."),
    ("kolko", "bat", "Batonik", "Sumy prefiksowe (6 October 2025, 12:00 - )", PREFIX,
     "Batonik\n\nSmaczność fragmentu batonika to suma smaczności kawałków. Odpowiedz na q zapytań."),
    ("kolko", "cuk", "Cukiernia", "Problem plecakowy (24 November 2025, 15:00 - )", KNAP,
     "Cukiernia\n\nJaś ma k złotych i chce kupić ciastka o największej łącznej wartości."),
    ("kolko", "sit", "Sito Eratostenesa", "Rekurencja i teoria liczb (1 December 2025, 15:00 - )", SIEVE,
     "Sito Eratostenesa\n\nWypisz liczby pierwsze nie większe od n."),
    ("kolko", "bfs", "Odległości wierzchołków", "Kontest (1 March 2026, 10:00 - )", BFS,
     "Odległości wierzchołków\n\nDany jest graf nieskierowany. Wyznacz najmniejszą liczbę krawędzi z 1 do każdego "
     "wierzchołka (najkrótsza ścieżka w grafie bez wag)."),
]


def build_corpus(root: Path) -> None:
    progs = []
    problems = {"kolko": {"problems": []}}
    for i, (cid, short, name, rnd, code, st) in enumerate(CORPUS):
        sid = str(5000 + i)
        rel = f"sio2.test/{cid}/{short}__{sid}.cpp"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(code, encoding="utf-8")
        sd = root / "sio2.test" / cid / "statements"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / f"{short}.txt").write_text(st, encoding="utf-8")
        problems[cid]["problems"].append({"short": short, "name": name, "round": rnd, "cells": []})
        rec = {"source": "sio2.test", "contest_id": cid, "contest_name": "Kółko testowe", "problem_name": name,
               "problem_short": short, "submission_id": sid, "time": f"2026-0{1 + i % 9}-1{i % 9} 12:00:00",
               "score": 100, "status": "Initial tests: OK", "lang": "cpp", "path": rel,
               "url": f"https://sio2.test/c/{cid}/s/{sid}/", "lines": len(code.splitlines())}
        if i != 1:                                   # jeden rekord bez rundy — ma ją uzupełnić problems.json
            rec["round"] = PR.P.clean_round(rnd)
        progs.append(rec)
    (root / "sio2.test" / "problems.json").write_text(json.dumps(problems, ensure_ascii=False), encoding="utf-8")
    (root / "index.json").write_text(json.dumps({"fetched_at": None, "programs": progs}, ensure_ascii=False),
                                     encoding="utf-8")


class TestTopicsAndDetectors(unittest.TestCase):
    def test_topic_of(self):
        cases = {
            "Dzień 5 - Dijkstra (22 August 2026, 09:00 - 14:00)": "Dijkstra",
            "Wyszukiwanie binarne na 12.02.2026": "Wyszukiwanie binarne",
            "grafy 11.09": "Grafy",
            "Matematyka :) na 13.11.2025": "Matematyka",
            "Znaki i teksty cz 1 na 20.10": "Znaki i teksty",
            "Mapa, Set - 20.04.2026": "Mapa, Set",
            "Find & Union": "Find & Union",
        }
        for raw, want in cases.items():
            self.assertEqual(PR.topic_of(raw), want, raw)
        self.assertTrue(PR.topic_is_generic(PR.topic_of("Warsztaty - dzień 1.")))
        self.assertTrue(PR.topic_is_generic(PR.topic_of("Dzień 8 - Kontest")))
        self.assertFalse(PR.topic_is_generic(PR.topic_of("Dzień KOPCA - mojej ulubionej struktury")))

    def test_statement_topic(self):
        self.assertEqual(PR.statement_topic("Alchemia\n\nKółko 1 Kod zadania: alc\n\nNWD i NWW Limit pamięci: 64 MiB\n"),
                         "NWD i NWW")
        self.assertIsNone(PR.statement_topic("ZDP\n\nDzień 5 – 22 sierpnia 2026 Limit pamięci: 128 MiB"))
        self.assertIsNone(PR.statement_topic("X\n\ncontest Limit pamięci: 256 MiB"))

    def test_concepts_in_topic(self):
        self.assertIn("binary_search", PR.concepts_in_topic("Binsearch po wyniku"))
        self.assertIn("topo_sort", PR.concepts_in_topic("Toposort i dynamiki na grafach"))
        self.assertIn("dp", PR.concepts_in_topic("Toposort i dynamiki na grafach"))
        self.assertNotIn("sorting", PR.concepts_in_topic("Sortowanie topologiczne"))
        self.assertNotIn("tree", PR.concepts_in_topic("Drzewa przedziałowe"))
        self.assertIn("segment_tree", PR.concepts_in_topic("Drzewa przedziałowe"))
        self.assertIn("dsu", PR.concepts_in_topic("Find & Union"))
        self.assertIn("heap", PR.concepts_in_topic("Dzień KOPCA - mojej ulubionej struktury"))
        self.assertNotIn("structs", PR.concepts_in_topic("Dzień KOPCA - mojej ulubionej struktury"))

    def test_query_concepts_typos(self):
        m = PR.matcher()
        for q, cid in [("binserch", "binary_search"), ("dikstra", "dijkstra"), ("dijsktra", "dijkstra"),
                       ("drzewo przedzialowe", "segment_tree"), ("drzewa przedziałowe", "segment_tree"),
                       ("find union", "dsu"), ("plecak", "knapsack"), ("sumy prefiksowe", "prefix_sums"),
                       ("toposort", "topo_sort"), ("kopiec", "heap"), ("najkrótsza ścieżka", "shortest_path"),
                       ("lower_bound", "binary_search"), ("drzewo przedziałwe", "segment_tree")]:
            got = [c["id"] for c in m.match(q)]
            self.assertIn(cid, got, f"{q!r} → {got}")
        # najdłuższe dopasowanie wygrywa: „drzewo przedziałowe” to nie „drzewo”
        self.assertEqual([c["id"] for c in m.match("drzewo przedziałowe")], ["segment_tree"])

    def test_detectors(self):
        def ids(code):
            return {d["id"] for d in PR.detect_fallback(code)}
        self.assertIn("dijkstra", ids(DIJKSTRA))
        self.assertIn("graph", ids(DIJKSTRA))
        self.assertIn("binary_search", ids(BINSEARCH))
        self.assertIn("dsu", ids(DSU))
        self.assertIn("segment_tree", ids(SEGTREE))
        self.assertNotIn("binary_search", ids(SEGTREE))
        self.assertIn("prefix_sums", ids(PREFIX))
        self.assertIn("dp", ids(KNAP))
        self.assertIn("knapsack", ids(KNAP))
        self.assertIn("sieve", ids(SIEVE))
        self.assertIn("bfs", ids(BFS))
        self.assertNotIn("dijkstra", ids(BFS))

    def test_algodetect_integration(self):
        res = PR.detect_algorithms(DSU)
        self.assertEqual(res[0]["id"], "dsu")
        try:
            import oiforge.core.algodetect  # noqa: F401
        except ImportError:
            return
        self.assertTrue(any(a.get("via") == "algodetect" for a in PR.detect_algorithms(DIJKSTRA + DSU)))

    def test_fold(self):
        self.assertEqual(PR.fold("ﬁ ≤ 10² zażółć"), "fi   102 zażółć")


class TestSearchSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="oiforge-prog-"))
        build_corpus(cls.tmp)
        PR.set_root(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        PR.set_root(None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def top(self, q, **f):
        return [r["problem_short"] for r in PR.search(q, k=5, filters=f or None)]

    def test_typos_and_synonyms(self):
        self.assertEqual(self.top("binserch")[0], "pie")
        self.assertEqual(self.top("wyszukiwanie binarne")[0], "pie")
        self.assertEqual(self.top("dikstra")[0], "lot")
        self.assertEqual(self.top("drzewo przedzialowe")[0], "zar")
        self.assertEqual(self.top("find union")[0], "tv")
        self.assertEqual(self.top("plecak")[0], "cuk")
        self.assertEqual(self.top("sumy prefiksowe")[0], "bat")
        self.assertEqual(self.top("sito")[0], "sit")
        self.assertEqual(self.top("kopiec")[0], "lot")          # priority_queue w kodzie

    def test_round_filled_from_problems_json(self):
        r = PR.search("dijkstra", k=1)[0]
        self.assertEqual(r["round"], "Dzień 5 - Dijkstra")
        self.assertTrue(any(w.startswith("temat rundy") for w in r["why"]), r["why"])

    def test_code_search(self):
        r = PR.search("priority_queue", k=3)
        self.assertEqual(r[0]["problem_short"], "lot")
        self.assertEqual(r[0]["snippet_kind"], "kod")
        self.assertIn("priority_queue", r[0]["snippet"])
        r = PR.search("#define int long long", k=3)
        self.assertEqual(r[0]["problem_short"], "bat")
        self.assertTrue(any("linia 3" in w for w in r[0]["why"]), r[0]["why"])

    def test_statement_search(self):
        r = PR.search("najkrótsza ścieżka", k=3)
        self.assertEqual({x["problem_short"] for x in r[:2]}, {"lot", "bfs"})
        r = PR.search("smaczność batonika", k=2)
        self.assertEqual(r[0]["problem_short"], "bat")
        self.assertEqual(r[0]["snippet_kind"], "treść")
        self.assertIn("**", r[0]["snippet"])

    def test_result_fields(self):
        r = PR.search("binserch", k=1)[0]
        for key in ("problem_name", "problem_short", "contest_name", "round", "path", "score", "tags", "snippet",
                    "url", "why", "id"):
            self.assertIn(key, r)
        self.assertIn("Wyszukiwanie binarne", r["tags"])
        self.assertTrue(r["why"][0].startswith("temat rundy: Wyszukiwanie binarne"), r["why"])

    def test_filters(self):
        self.assertEqual(self.top("", tag="dijkstra"), ["lot"])
        self.assertEqual(set(self.top("", topic="Sumy prefiksowe")), {"bat"})
        self.assertEqual(self.top("binserch", contest="nie-ma-takiego"), [])
        self.assertEqual(self.top("", **{"from": "2026-09", "to": "2026-12"}), [])

    def test_get_similar_stats(self):
        pid = PR.search("dikstra", k=1)[0]["id"]
        g = PR.get(pid)
        self.assertIn("priority_queue", g["code"])
        self.assertIn("najkrótszą drogą", g["statement"])
        self.assertTrue(any(a["id"] == "dijkstra" for a in g["algorithms"]))
        sim = PR.similar(pid, k=3)
        self.assertEqual(sim[0]["problem_short"], "bfs")                  # wspólne: grafy, najkrótsze ścieżki
        self.assertTrue(sim[0]["reasons"])
        st = PR.stats()
        self.assertEqual(st["totals"]["programs"], len(CORPUS))
        self.assertTrue(any(x["label"] == "Find & Union" for x in st["by_topic"]))
        self.assertTrue(any(x["id"] == "dijkstra" for x in st["by_algorithm"]))
        self.assertTrue(st["timeline"])
        t = PR.similar_tasks("Dany jest graf z wagami. Wyznacz najkrótszą drogę z miasta 1 do miasta n.", k=2)
        self.assertEqual(t[0]["problem_short"], "lot")

    def test_reload_after_change(self):
        n0 = len(PR.list_programs())
        data = json.loads((self.tmp / "index.json").read_text(encoding="utf-8"))
        extra = dict(data["programs"][0], submission_id="9999")
        data["programs"].append(extra)
        PR.save_index(data, self.tmp)
        try:
            self.assertEqual(len(PR.list_programs()), n0 + 1)
            self.assertTrue((self.tmp / "index.backup.json").exists())
        finally:
            data["programs"].pop()
            PR.save_index(data, self.tmp)


# ============================================================================================ prawdziwe dane
QUERIES = [
    ("binserch", r"binarn|binsearch"),
    ("wyszukiwanie binarne", r"binarn|binsearch"),
    ("dijkstra", r"dijkstr"),
    ("dikstra", r"dijkstr"),
    ("drzewo przedziałowe", r"przedzia"),
    ("find union", r"find|union"),
    ("plecak", r"plecak"),
    ("sumy prefiksowe", r"prefiks"),
    ("toposort", r"topolog|toposort"),
    ("kopiec", r"kopc|kopiec"),
    ("lower_bound", None),
    ("najkrótsza ścieżka", r"dijkstr|bfs|bellman|floyd|graf"),
]


@unittest.skipUnless(REAL_INDEX.exists() and len(json.loads(REAL_INDEX.read_text(encoding="utf-8")).get("programs", []))
                     >= 200, "brak prawdziwych danych ucznia (user_data/programs)")
class TestSearchRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PR.set_root(None)
        t = time.perf_counter()
        cls.engine = PR.engine()
        cls.build = time.perf_counter() - t

    def test_report_and_relevance(self):
        lines = [f"\n=== Wyszukiwarka na {len(self.engine.order)} prawdziwych programach "
                 f"(budowa indeksu {self.build:.2f} s) ==="]
        total_ok = total = 0
        for q, rx in QUERIES:
            t = time.perf_counter()
            res = PR.search(q, k=5)
            ms = 1000 * (time.perf_counter() - t)
            self.assertTrue(res, q)
            if rx:
                hits = 0
                for r in res:
                    blob = PR.norm(" ".join(filter(None, [r["round"] or "", " ".join(r["tags"]),
                                                          self.engine.docs[r["id"]].get("statement_topic") or ""])))
                    hits += bool(re.search(rx, blob))
                total_ok += hits
                total += len(res)
                rel = f"{hits}/{len(res)}"
            else:
                hits = sum("lower_bound" in self.engine.docs[r["id"]]["_code"] or "upper_bound" in
                           self.engine.docs[r["id"]]["_code"] or "binarn" in PR.norm(r["round"] or "") for r in res)
                rel = f"{hits}/{len(res)} (kod/temat)"
            lines.append(f"\n„{q}” — trafność top-5 względem round/tagów: {rel}  [{ms:.0f} ms]")
            for i, r in enumerate(res, 1):
                lines.append(f"  {i}. {r['problem_name'][:30]:30} | {str(r['round'])[:36]:36} | {(r['why'] or [''])[0][:60]}")
            if rx:
                self.assertGreaterEqual(hits, 3 if q not in ("plecak", "find union") else 2,
                                        f"{q}: za mało trafnych w top-5")
                first = res[0]
                blob = PR.norm(" ".join(filter(None, [first["round"] or "", " ".join(first["tags"])])))
                self.assertRegex(blob, rx, f"{q}: pierwszy wynik nietrafny")
        lines.append(f"\nŁącznie trafnych w top-5: {total_ok}/{total}")
        print("\n".join(lines))

    def test_lower_bound_literal_first(self):
        r = PR.search("lower_bound", k=3)
        self.assertIn("lower_bound", self.engine.docs[r[0]["id"]]["_code"])
        self.assertEqual(r[0]["snippet_kind"], "kod")

    def test_everything_has_metadata(self):
        st = PR.stats()
        self.assertEqual(st["totals"]["programs"], len(self.engine.order))
        self.assertGreater(st["totals"]["with_statement"], 200)
        missing_round = [d for d in self.engine.docs.values() if not d.get("round")]
        self.assertLessEqual(len(missing_round), 4)          # rundy uzupełniane z problems.json (bez wielkości liter)

    def test_similar_and_similar_tasks(self):
        pid = PR.search("dijkstra", k=1)[0]["id"]
        sim = PR.similar(pid, k=5)
        self.assertTrue(sim)
        self.assertTrue(any("dijkstr" in PR.norm(" ".join(s["tags"]) + (s["round"] or "")) for s in sim[:3]))
        st = self.engine.docs[pid]["_statement"]
        t = PR.similar_tasks(st, k=3)
        self.assertTrue(any(PR.norm(x["problem_name"]) == PR.norm(self.engine.docs[pid]["problem_name"]) for x in t))


# ============================================================================================ HTTP
class TestHTTP(unittest.TestCase):
    proc = None

    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, OIFORGE_NO_WARMUP="1")
        cls.proc = subprocess.Popen([sys.executable, "-m", "oiforge", "--port", str(PORT), "--no-browser",
                                     "--no-warmup"], cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                h = cls.get("/api/health")
                if h.get("ok"):
                    cls.health = h
                    return
            except OSError:
                time.sleep(0.3)
        cls.tearDownClass()
        raise RuntimeError("Serwer testowy nie wystartował")

    @classmethod
    def tearDownClass(cls):
        if cls.proc:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
            if cls.proc.stderr:
                cls.proc.stderr.close()
            cls.proc = None

    @staticmethod
    def get(path, **q):
        url = f"http://127.0.0.1:{PORT}{path}" + ("?" + urllib.parse.urlencode(q) if q else "")
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))

    @staticmethod
    def post(path, body, header=True):
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **({"X-OIForge": "1"} if header
                                                                                      else {})}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            data = json.loads(e.read().decode("utf-8"))
            e.close()
            return data

    def test_modules_loaded(self):
        self.assertIn("programs", self.health["tools"])
        self.assertIn("sio2", self.health["tools"])
        self.assertNotIn("programs", self.health.get("tool_errors", {}))
        self.assertNotIn("sio2", self.health.get("tool_errors", {}))

    def test_endpoints(self):
        lst = self.get("/api/programs/list")
        self.assertTrue(lst["ok"])
        self.assertIn("facets", lst)
        st = self.get("/api/programs/stats")
        self.assertIn("totals", st["stats"])
        self.assertTrue(self.get("/api/programs/sync/status")["ok"])
        sc = self.get("/api/programs/sync/script")
        self.assertIn("oiforge-sio2", sc["script"])
        self.assertTrue(self.get("/api/programs/statements/pending")["ok"])
        self.assertTrue(self.get("/api/programs/concepts")["concepts"])
        if lst["programs"]:
            res = self.get("/api/programs/search", q="binserch")
            self.assertTrue(res["ok"])
            self.assertIn("results", res)
            pid = lst["programs"][0]["id"]
            g = self.get("/api/programs/get", id=pid)
            self.assertIn("code", g["program"])
            self.assertTrue(self.get("/api/programs/similar", id=pid)["ok"])
            self.assertTrue(self.get("/api/programs/suggest", q="dij")["ok"])
        self.assertFalse(self.post("/api/programs/similar_tasks", {"statement": ""})["ok"])
        self.assertTrue(self.post("/api/programs/similar_tasks", {"statement": "graf najkrótsza droga"})["ok"])

    def test_post_requires_header_and_validates(self):
        r = self.post("/api/programs/statements/text", {"items": []}, header=False)
        self.assertFalse(r["ok"])
        r = self.post("/api/programs/statements/text", {"items": [{"key": "../../etc", "text": "x"}]})
        self.assertTrue(r["ok"])
        self.assertTrue(r["errors"])
        r = self.post("/api/programs/import/zip", {"name": "x.zip", "b64": "bmllIHppcA=="})
        self.assertFalse(r["ok"])
        self.assertIn("ZIP", r["error"])
        r = self.post("/api/programs/import/folder", {"path": "/nie/ma/takiego/folderu/xyz"})
        self.assertFalse(r["ok"])
        r = self.post("/api/programs/import/json", {"json": "{zly json"})
        self.assertFalse(r["ok"])

    def test_sync_job_without_credentials(self):
        j = self.post("/api/programs/sync", {"base_url": "http://127.0.0.1:9", "username": "", "password": ""})
        self.assertTrue(j["ok"], j)
        jid = j["job"]
        for _ in range(100):
            s = self.get(f"/api/jobs/{jid}")
            if s["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(s["status"], "error")
        self.assertIn("login", s["error"].lower())


if __name__ == "__main__":
    unittest.main()
