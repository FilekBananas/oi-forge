n, s = map(int, input().split())
a = list(map(int, input().split()))
best = n + 1
total = 0
left = 0
for right in range(n):
    total += a[right]
    while total >= s:
        best = min(best, right - left + 1)
        total -= a[left]
        left += 1
print(best if best <= n else 0)
