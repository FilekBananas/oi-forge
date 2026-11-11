import sys

def main():
    data = sys.stdin.read().split()
    it = iter(data)
    t = int(next(it))
    for _ in range(t):
        n = int(next(it))
        arr = [int(next(it)) for _ in range(n)]
        print(sum(arr) - min(arr))
main()
