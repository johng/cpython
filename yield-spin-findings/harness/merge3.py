import sys
def load(p):
    d = {}
    for ln in open(p):
        ln = ln.strip()
        if not ln or ln.startswith('#') or ln.startswith('threads'): continue
        f = ln.split(','); d[int(f[0])] = (float(f[2]), float(f[5]))
    return d
A, B, T, H = [load(x) for x in sys.argv[1:5]]
print(f"{'thr':>4} {'base':>7} {'AA%':>5} | {'treat':>7} {'vsbase':>7} {'park':>6} | {'hybrid':>7} {'vsbase':>7} {'park':>6}")
for n in sorted(A):
    base = (A[n][0] + B[n][0]) / 2
    aa = abs(A[n][0] - B[n][0]) / base * 100
    t, h = T[n][0], H[n][0]
    print(f"{n:>4} {base:>7.3f} {aa:>4.1f}% | {t:>7.3f} {(t/base-1)*100:>+6.1f}% {T[n][1]:>6.3f} | {h:>7.3f} {(h/base-1)*100:>+6.1f}% {H[n][1]:>6.3f}")
