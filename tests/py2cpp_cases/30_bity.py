n = int(input())
a = list(map(int, input().split()))
target = int(input())
count = 0
for mask in range(1 << n):
    s = 0
    for i in range(n):
        if mask >> i & 1:
            s += a[i]
    if s == target:
        count += 1
print(count)
x = 0b101101
print(x & 5, x | 2, x ^ 63, x << 3, x >> 2, ~x)
print(bin(x).count('1'), bin(x), hex(255))
