MOD = 998244353
a, b = map(int, input().split())
print(pow(a, b, MOD))
r = 1
base = a % MOD
e = b
while e > 0:
    if e & 1:
        r = r * base % MOD
    base = base * base % MOD
    e >>= 1
print(r)
print(2 ** 40, 10 ** 18, (1 << 62) - 1)
print(a ** 2 + b ** 2)
