import sys

def solve(n, a):
    best = cur = a[0]
    for x in a[1:]:
        cur = max(x, cur + x)
        best = max(best, cur)
    return best

def main():
    t = int(sys.stdin.readline())
    out = []
    for _ in range(t):
        n = int(sys.stdin.readline())
        a = list(map(int, sys.stdin.readline().split()))
        out.append(str(solve(n, a)))
    sys.stdout.write("\n".join(out) + "\n")

if __name__ == "__main__":
    main()
