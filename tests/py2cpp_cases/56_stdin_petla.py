import sys

total = 0
cnt = 0
for line in sys.stdin:
    parts = line.split()
    if not parts:
        continue
    cnt += 1
    total += int(parts[-1])
print(cnt, total)
