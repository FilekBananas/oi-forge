MOD = 10**9 + 7
N = 2 * 10**5

fact = [1] * (N + 1)
for i in range(1, N + 1):
    fact[i] = fact[i - 1] * i % MOD
inv = [1] * (N + 1)
inv[N] = pow(fact[N], MOD - 2, MOD)
for i in range(N, 0, -1):
    inv[i - 1] = inv[i] * i % MOD

def C(n, k):
    if k < 0 or k > n:
        return 0
    return fact[n] * inv[k] % MOD * inv[n - k] % MOD

t = int(input())
for _ in range(t):
    n, k = map(int, input().split())
    print(C(n, k))
