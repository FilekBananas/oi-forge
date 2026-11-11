import sys

it = iter(sys.stdin.read().split())
t = int(next(it))
for _ in range(t):
    n = int(next(it))
    word = next(it)
    nums = [int(next(it)) for _ in range(n)]
    print(word, sum(nums), max(nums))
