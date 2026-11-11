lines = open(0).read().split('\n')
n = int(lines[0])
total = 0
for i in range(1, n + 1):
    a, b = map(int, lines[i].split())
    total += a * b
print(total)
print(len([ln for ln in lines if ln.strip()]))
