total = 0
lines = 0
while True:
    try:
        line = input()
    except EOFError:
        break
    if not line.strip():
        continue
    lines += 1
    total += sum(map(int, line.split()))
print(lines, total)
