import sys
input = sys.stdin.readline

t = int(input())
for _ in range(t):
    s = input().strip()
    n = len(s)
    print(n, s == s[::-1])
