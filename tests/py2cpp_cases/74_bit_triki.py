n = int(input())
a = list(map(int, input().split()))
for x in a:
    print(x & -x, x.bit_length(), bin(x).count("1"), x ^ (x >> 1), (x >> 3) & 1)
xor_all = 0
for x in a:
    xor_all ^= x
print(xor_all, 1 << n, (1 << n) - 1)
