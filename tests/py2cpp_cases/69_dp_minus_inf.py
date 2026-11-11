n, k = map(int, input().split())
a = list(map(int, input().split()))
NEG = float('-inf')
dp = [[NEG] * (k + 1) for _ in range(n + 1)]
dp[0][0] = 0
for i in range(1, n + 1):
    for j in range(k + 1):
        dp[i][j] = dp[i - 1][j]
        if j > 0 and dp[i - 1][j - 1] != NEG:
            dp[i][j] = max(dp[i][j], dp[i - 1][j - 1] + a[i - 1])
best = dp[n][k]
print(best if best != NEG else "BRAK")
