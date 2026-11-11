def is_pal(s):
    return s == s[::-1]

n = int(input())
cnt = 0
longest = ""
for _ in range(n):
    w = input().strip().lower()
    if is_pal(w):
        cnt += 1
        if len(w) > len(longest):
            longest = w
print(cnt)
print(longest if longest else "brak")
