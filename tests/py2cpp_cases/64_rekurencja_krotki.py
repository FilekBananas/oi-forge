import sys
sys.setrecursionlimit(1 << 20)

def dfs(u, p, depth):
    best = (depth, u)
    for v in g[u]:
        if v != p:
            best = max(best, dfs(v, u, depth + 1))
    return best

n = int(input())
g = [[] for _ in range(n + 1)]
for _ in range(n - 1):
    a, b = map(int, input().split())
    g[a].append(b)
    g[b].append(a)
d1, far = dfs(1, 0, 0)
d2, other = dfs(far, 0, 0)
print(d2)
