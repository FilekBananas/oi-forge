names = input().split()
scores = list(map(int, input().split()))
for i, name in enumerate(names):
    print(i, name)
for i, name in enumerate(names, 1):
    print(f"{i}. {name}")
for name, sc in zip(names, scores):
    print(name, sc)
best = max(zip(scores, names))
print(best[1], best[0])
pairs = list(zip(names, scores))
print(len(pairs))
total = sum(s * (i + 1) for i, s in enumerate(scores))
print(total)
