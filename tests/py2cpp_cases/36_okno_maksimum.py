from collections import deque

n, k = map(int, input().split())
a = list(map(int, input().split()))
dq = deque()
res = []
for i in range(n):
    while dq and a[dq[-1]] <= a[i]:
        dq.pop()
    dq.append(i)
    if dq[0] <= i - k:
        dq.popleft()
    if i >= k - 1:
        res.append(a[dq[0]])
print(*res)
