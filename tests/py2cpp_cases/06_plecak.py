n, W = map(int, input().split())
items = []
for _ in range(n):
    w, v = map(int, input().split())
    items.append((w, v))
dp = [0] * (W + 1)
for w, v in items:
    for c in range(W, w - 1, -1):
        if dp[c - w] + v > dp[c]:
            dp[c] = dp[c - w] + v
print(dp[W])
best = max(dp)
print(best, dp.index(best))
