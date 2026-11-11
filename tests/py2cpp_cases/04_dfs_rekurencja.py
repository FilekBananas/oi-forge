import sys
sys.setrecursionlimit(10**6)

def dfs(u):
    visited[u] = True
    order.append(u)
    for v in g[u]:
        if not visited[v]:
            dfs(v)

n, m = map(int, input().split())
g = [[] for _ in range(n + 1)]
for _ in range(m):
    a, b = map(int, input().split())
    g[a].append(b)
    g[b].append(a)
for lst in g:
    lst.sort()
visited = [False] * (n + 1)
order = []
comps = 0
for v in range(1, n + 1):
    if not visited[v]:
        comps += 1
        dfs(v)
print(comps)
print(*order)
