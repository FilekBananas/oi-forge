counter = 0
best = -1

def visit(x):
    global counter, best
    counter += 1
    if x > best:
        best = x

for v in map(int, input().split()):
    visit(v)
print(counter, best)
