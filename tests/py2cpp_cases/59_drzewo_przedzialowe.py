n, q = map(int, input().split())
a = list(map(int, input().split()))
size = 1
while size < n:
    size *= 2
tree = [0] * (2 * size)
for i in range(n):
    tree[size + i] = a[i]
for i in range(size - 1, 0, -1):
    tree[i] = tree[2 * i] + tree[2 * i + 1]

def update(pos, val):
    pos += size
    tree[pos] = val
    pos //= 2
    while pos >= 1:
        tree[pos] = tree[2 * pos] + tree[2 * pos + 1]
        pos //= 2

def query(l, r):
    res = 0
    l += size
    r += size + 1
    while l < r:
        if l & 1:
            res += tree[l]
            l += 1
        if r & 1:
            r -= 1
            res += tree[r]
        l >>= 1
        r >>= 1
    return res

for _ in range(q):
    t, x, y = map(int, input().split())
    if t == 1:
        update(x - 1, y)
    else:
        print(query(x - 1, y - 1))
