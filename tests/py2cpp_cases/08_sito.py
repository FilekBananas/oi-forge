n = int(input())
is_prime = [True] * (n + 1)
is_prime[0] = False
if n >= 1:
    is_prime[1] = False
p = 2
while p * p <= n:
    if is_prime[p]:
        for k in range(p * p, n + 1, p):
            is_prime[k] = False
    p += 1
primes = [i for i in range(n + 1) if is_prime[i]]
print(len(primes))
print(*primes[:10])
print(sum(primes) % 1000000007)
