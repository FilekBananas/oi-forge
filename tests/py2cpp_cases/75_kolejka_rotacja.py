from collections import deque

n, k = map(int, input().split())
q = deque(range(1, n + 1))
out = []
while q:
    q.rotate(-(k - 1))
    out.append(q.popleft())
print(*out)
d = deque([1, 2, 3])
d.appendleft(0)
d.extend([4, 5])
print(list(d), d[0], d[-1], len(d))
