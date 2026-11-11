import sys

data = sys.stdin.read().split()
n = int(data[0])
vals = list(map(int, data[1:n + 1]))
words = data[n + 1:]
print(n, sum(vals), len(words))
print(" ".join(w.upper() for w in words))
