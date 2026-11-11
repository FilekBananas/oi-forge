from dataclasses import dataclass

@dataclass
class Point:
    x: int
    y: int

def dist2(p, q):
    return (p.x - q.x) ** 2 + (p.y - q.y) ** 2

n = int(input())
pts = []
for _ in range(n):
    a, b = map(int, input().split())
    pts.append(Point(a, b))
best = None
for i in range(n):
    for j in range(i + 1, n):
        d = dist2(pts[i], pts[j])
        if best is None or d < best:
            best = d
print(best if best is not None else -1)
