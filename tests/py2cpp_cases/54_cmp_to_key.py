from functools import cmp_to_key

def cmp(a, b):
    if a + b < b + a:
        return 1
    if a + b > b + a:
        return -1
    return 0

n = int(input())
nums = input().split()
nums.sort(key=cmp_to_key(cmp))
res = "".join(nums)
print(res.lstrip("0") or "0")
