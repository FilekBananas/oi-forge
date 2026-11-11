class DSU:
    def __init__(self, n):
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        a = self.find(a)
        b = self.find(b)
        if a == b:
            return False
        if self.size[a] < self.size[b]:
            a, b = b, a
        self.parent[b] = a
        self.size[a] += self.size[b]
        return True

n, m = map(int, input().split())
d = DSU(n + 1)
comps = n
for _ in range(m):
    a, b = map(int, input().split())
    if d.union(a, b):
        comps -= 1
print(comps)
print(max(d.size[d.find(v)] for v in range(1, n + 1)))
