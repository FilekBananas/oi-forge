from collections import defaultdict

n, m = map(int, input().split())
g = defaultdict(list)
for _ in range(m):
    a, b = input().split()
    g[a].append(b)
    g[b].append(a)
seen = set()
comps = 0
for v in sorted(g):
    if v in seen:
        continue
    comps += 1
    stack = [v]
    seen.add(v)
    while stack:
        u = stack.pop()
        for w in g[u]:
            if w not in seen:
                seen.add(w)
                stack.append(w)
print(comps, len(seen))
deg = defaultdict(int)
for v in g:
    deg[len(g[v])] += 1
for k in sorted(deg):
    print(k, deg[k])
