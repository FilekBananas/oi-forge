from collections import Counter

n = int(input())
words = input().split()
cnt = {}
for w in words:
    cnt[w] = cnt.get(w, 0) + 1
items = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
for w, c in items:
    print(w, c)
c2 = Counter(words)
print(len(c2), c2['ala'])
