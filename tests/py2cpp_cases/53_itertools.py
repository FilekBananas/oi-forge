from itertools import permutations, combinations, accumulate, product

a = list(map(int, input().split()))
print(len(list(permutations(a))))
for c in combinations(a, 2):
    print(c[0] + c[1], end=' ')
print()
print(list(accumulate(a)))
best = 0
for p in permutations(a):
    val = sum((i + 1) * p[i] for i in range(len(p)))
    best = max(best, val)
print(best)
cnt = 0
for bits in product([0, 1], repeat=3):
    if sum(bits) == 2:
        cnt += 1
print(cnt)
