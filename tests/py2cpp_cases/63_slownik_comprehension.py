words = input().split()
lengths = {w: len(w) for w in words}
for w in sorted(lengths):
    print(w, lengths[w])
inv = {}
for w, l in lengths.items():
    inv.setdefault(l, []).append(w)
for l in sorted(inv):
    print(l, sorted(inv[l]))
first = {}
for i, w in enumerate(words):
    if w not in first:
        first[w] = i
print(sorted(first.items(), key=lambda kv: kv[1]))
