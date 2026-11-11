n = int(input())
scores = {}
for _ in range(n):
    name, pts = input().split()
    scores[name] = scores.get(name, 0) + int(pts)
for name in sorted(scores):
    print(name, scores[name])
best = max(scores, key=scores.get)
print(best, scores[best])
print(sum(scores.values()), len(scores))
if 'ala' in scores:
    del scores['ala']
print(sorted(scores.keys()))
