import sys
input = sys.stdin.readline

n, q = map(int, input().split())
a = list(map(int, input().split()))
pref = [0] * (n + 1)
for i in range(n):
    pref[i + 1] = pref[i] + a[i]
out = []
for _ in range(q):
    l, r = map(int, input().split())
    out.append(pref[r] - pref[l - 1])
print('\n'.join(map(str, out)))
