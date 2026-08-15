import sys
def load(p):
    d = {}
    for ln in open(p):
        ln = ln.strip()
        if not ln or ln.startswith('#') or ln.startswith('threads'): continue
        f = ln.split(',')
        d[int(f[0])] = (float(f[2]), float(f[5]))  # mops, vcsw_per_op
    return d
A, B, T = load(sys.argv[1]), load(sys.argv[2]), load(sys.argv[3])
print(f"{'thr':>4} {'baseA':>8} {'baseB':>8} {'AA_noise%':>9} | {'treat':>8} {'treat_vs_base%':>14} | {'base_vcsw/op':>12} {'treat_vcsw/op':>13}")
for n in sorted(A):
    a, b, t = A[n][0], B[n][0], T[n][0]
    mean = (a + b) / 2
    aa = abs(a - b) / mean * 100
    tv = (t / mean - 1) * 100
    print(f"{n:>4} {a:>8.3f} {b:>8.3f} {aa:>8.1f}% | {t:>8.3f} {tv:>+13.1f}% | {A[n][1]:>12.3f} {T[n][1]:>13.3f}")
