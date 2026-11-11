from collections import Counter

s = input().strip()
c = Counter(s)
for ch, k in sorted(c.items()):
    print(ch, k)
top = c.most_common(1)[0]
print(top[0], top[1])
print(sum(c.values()), len(c), c['z'])
odd = sum(1 for v in c.values() if v % 2 == 1)
print("TAK" if odd <= 1 else "NIE")
