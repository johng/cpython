import sys
cfgs = []
for a in sys.argv[1:]:
    label, path = a.split('=', 1)
    d = {}
    for ln in open(path):
        ln = ln.strip()
        if not ln or ln[0] == '#' or ln.startswith('threads'): continue
        f = ln.split(','); d[int(f[0])] = (float(f[2]), float(f[5]))
    cfgs.append((label, d))
threads = sorted(cfgs[0][1])
labels = [l for l, _ in cfgs]
ref = dict(cfgs).get('p0', cfgs[0][1])
hdr = "thr " + " ".join(f"{l:>8}" for l in labels)

print("=== Mops/s (higher=better) ===")
print(hdr)
for n in threads:
    print(f"{n:>3} " + " ".join(f"{d[n][0]:>8.3f}" for _, d in cfgs))

print("\n=== % vs p0 (in-binary pure-yield control) ===")
print(hdr)
for n in threads:
    print(f"{n:>3} " + " ".join(f"{(d[n][0]/ref[n][0]-1)*100:>+7.1f}%" for _, d in cfgs))

print("\n=== parks/op (voluntary ctx-switch / op) ===")
print(hdr)
for n in threads:
    print(f"{n:>3} " + " ".join(f"{d[n][1]:>8.3f}" for _, d in cfgs))
