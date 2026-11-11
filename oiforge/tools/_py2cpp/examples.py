"""Przykładowe programy (Python) do wyboru w widoku tłumacza."""

EXAMPLES = [
    {
        "id": "suma",
        "title": "Suma i wczytywanie liczb",
        "description": "int(input()), list(map(int, input().split())), sum/min/max",
        "code": "n = int(input())\na = list(map(int, input().split()))\nprint(sum(a))\nprint(min(a), max(a))\n",
        "input": "5\n3 -1 4 1 5\n",
    },
    {
        "id": "dijkstra",
        "title": "Dijkstra z heapq",
        "description": "heapq (kopiec minimalny) → priority_queue z greater<>",
        "code": (
            "import heapq\n\n"
            "n, m = map(int, input().split())\n"
            "adj = [[] for _ in range(n + 1)]\n"
            "for _ in range(m):\n"
            "    a, b, w = map(int, input().split())\n"
            "    adj[a].append((b, w))\n"
            "    adj[b].append((a, w))\n"
            "INF = float('inf')\n"
            "dist = [INF] * (n + 1)\n"
            "dist[1] = 0\n"
            "pq = [(0, 1)]\n"
            "while pq:\n"
            "    d, u = heapq.heappop(pq)\n"
            "    if d > dist[u]:\n"
            "        continue\n"
            "    for v, w in adj[u]:\n"
            "        if d + w < dist[v]:\n"
            "            dist[v] = d + w\n"
            "            heapq.heappush(pq, (dist[v], v))\n"
            "print(*[d if d != INF else -1 for d in dist[1:]])\n"
        ),
        "input": "4 4\n1 2 5\n2 3 1\n1 3 10\n3 4 2\n",
    },
    {
        "id": "bfs",
        "title": "BFS na siatce (deque)",
        "description": "collections.deque, krotki, listy 2D",
        "code": (
            "from collections import deque\n\n"
            "n, m = map(int, input().split())\n"
            "g = [input() for _ in range(n)]\n"
            "dist = [[-1] * m for _ in range(n)]\n"
            "dist[0][0] = 0\n"
            "q = deque([(0, 0)])\n"
            "while q:\n"
            "    r, c = q.popleft()\n"
            "    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):\n"
            "        nr, nc = r + dr, c + dc\n"
            "        if 0 <= nr < n and 0 <= nc < m and g[nr][nc] == '.' and dist[nr][nc] < 0:\n"
            "            dist[nr][nc] = dist[r][c] + 1\n"
            "            q.append((nr, nc))\n"
            "print(dist[n - 1][m - 1])\n"
        ),
        "input": "3 4\n..#.\n#...\n..#.\n",
    },
    {
        "id": "plecak",
        "title": "Problem plecakowy (DP)",
        "description": "lista [0] * (W + 1), pętla z krokiem ujemnym",
        "code": (
            "n, W = map(int, input().split())\n"
            "dp = [0] * (W + 1)\n"
            "for _ in range(n):\n"
            "    w, v = map(int, input().split())\n"
            "    for c in range(W, w - 1, -1):\n"
            "        dp[c] = max(dp[c], dp[c - w] + v)\n"
            "print(dp[W])\n"
        ),
        "input": "3 5\n2 3\n3 4\n4 5\n",
    },
    {
        "id": "lis",
        "title": "Najdłuższy rosnący podciąg (bisect)",
        "description": "bisect_left → lower_bound",
        "code": (
            "from bisect import bisect_left\n\n"
            "n = int(input())\n"
            "tails = []\n"
            "for x in map(int, input().split()):\n"
            "    i = bisect_left(tails, x)\n"
            "    if i == len(tails):\n"
            "        tails.append(x)\n"
            "    else:\n"
            "        tails[i] = x\n"
            "print(len(tails))\n"
        ),
        "input": "8\n10 9 2 5 3 7 101 18\n",
    },
    {
        "id": "sito",
        "title": "Sito Eratostenesa",
        "description": "lista bool, pętla while, comprehension",
        "code": (
            "n = int(input())\n"
            "sito = [True] * (n + 1)\n"
            "sito[0] = sito[1] = False\n"
            "i = 2\n"
            "while i * i <= n:\n"
            "    if sito[i]:\n"
            "        for j in range(i * i, n + 1, i):\n"
            "            sito[j] = False\n"
            "    i += 1\n"
            "pierwsze = [i for i in range(n + 1) if sito[i]]\n"
            "print(len(pierwsze), pierwsze[-1])\n"
        ),
        "input": "1000000\n",
    },
    {
        "id": "ujemne",
        "title": "Ujemne // i % (semantyka Pythona)",
        "description": "floordiv / pymod tylko tam, gdzie znak może być ujemny",
        "code": (
            "a, b = map(int, input().split())\n"
            "print(a // b, a % b)\n"
            "print(divmod(a, b))\n"
            "for i in range(10):\n"
            "    print(i // 3, i % 3, end=' ')\n"
            "print()\n"
        ),
        "input": "-7 2\n",
    },
    {
        "id": "napisy",
        "title": "Napisy i słowniki",
        "description": "split, lower, dict.get, sorted z key=lambda",
        "code": (
            "words = input().lower().split()\n"
            "cnt = {}\n"
            "for w in words:\n"
            "    cnt[w] = cnt.get(w, 0) + 1\n"
            "for w, c in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0])):\n"
            "    print(f\"{w:>8}: {c}\")\n"
        ),
        "input": "Ala ma kota a kot ma Ale ala\n",
    },
    {
        "id": "fstring",
        "title": "f-stringi i formatowanie",
        "description": ":.2f, :>5, :05d, %-format, str.format",
        "code": (
            "x = float(input())\n"
            "n = int(input())\n"
            "print(f\"{x:.2f} [{n:>5}] {n:05d}\")\n"
            "print(\"%.3f %d\" % (x, n))\n"
            "print(\"{} i {:.1f}\".format(n, x))\n"
            "print(x / 3)\n"
        ),
        "input": "3.14159\n42\n",
    },
    {
        "id": "dsu",
        "title": "Find & Union (klasa → struct)",
        "description": "klasa z metodami, kompresja ścieżek",
        "code": (
            "class DSU:\n"
            "    def __init__(self, n):\n"
            "        self.p = list(range(n))\n\n"
            "    def find(self, x):\n"
            "        while self.p[x] != x:\n"
            "            self.p[x] = self.p[self.p[x]]\n"
            "            x = self.p[x]\n"
            "        return x\n\n"
            "    def union(self, a, b):\n"
            "        a, b = self.find(a), self.find(b)\n"
            "        if a == b:\n"
            "            return False\n"
            "        self.p[a] = b\n"
            "        return True\n\n"
            "n, m = map(int, input().split())\n"
            "d = DSU(n + 1)\n"
            "comps = n\n"
            "for _ in range(m):\n"
            "    a, b = map(int, input().split())\n"
            "    if d.union(a, b):\n"
            "        comps -= 1\n"
            "print(comps)\n"
        ),
        "input": "5 3\n1 2\n3 4\n2 1\n",
    },
    {
        "id": "rekurencja",
        "title": "DFS rekurencyjny",
        "description": "sys.setrecursionlimit, zmienne globalne w funkcji",
        "code": (
            "import sys\n"
            "sys.setrecursionlimit(10**6)\n\n"
            "def dfs(u, p):\n"
            "    size[u] = 1\n"
            "    for v in g[u]:\n"
            "        if v != p:\n"
            "            dfs(v, u)\n"
            "            size[u] += size[v]\n\n"
            "n = int(input())\n"
            "g = [[] for _ in range(n + 1)]\n"
            "for _ in range(n - 1):\n"
            "    a, b = map(int, input().split())\n"
            "    g[a].append(b)\n"
            "    g[b].append(a)\n"
            "size = [0] * (n + 1)\n"
            "dfs(1, 0)\n"
            "print(*size[1:])\n"
        ),
        "input": "5\n1 2\n1 3\n3 4\n3 5\n",
    },
]
