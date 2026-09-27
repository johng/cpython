"""Benchmark for dropping the critical section around bytearray.decode().

On main, bytearray.decode() locks the bytearray, then PyUnicode_FromEncodedObject
exports it (bytearray_getbuffer) and releases it (bytearray_releasebuffer), both
of which re-enter the same lock as nested critical sections.  A nested re-entry
costs ~13 ns on Apple M5 (a failed casalb before _PyCriticalSection_BeginSlow
sees the lock is already held) against ~2 ns for a plain lock pair.  Without the
outer section, decode takes two plain pairs, exactly like str(ba, encoding).

Cases, each at several payload sizes:
  ba.decode(latin-1)   ba.decode(utf-8)   the changed path
  str(ba, utf-8)       same export/release, never had the outer lock: the
                       target the changed path should reach
  bytes.decode(utf-8)  no bytearray lock at all: lock-free ceiling

Modes: 'owned' (each thread decodes its own bytearray, created in the worker)
and 'shared' (all threads decode one bytearray).  Every trial runs for a fixed
wall time, so one convoyed trial shows up as a low minimum instead of a
multi-minute run hiding behind the median.

Usage:
  python bench_bytearray_decode.py run  [--threads 1,2,4,6] [--trials 5] [--ms 200] [--json out.json]
  python bench_bytearray_decode.py compare base.json new.json [base2.json new2.json]
"""
import argparse, json, math, statistics, sys, threading, time

SIZES = [16, 256, 4096, 65536]
PAYLOAD = bytes(range(32, 127))  # printable ASCII: valid latin-1 and utf-8

def payload(n):
    return (PAYLOAD * (n // len(PAYLOAD) + 1))[:n]

def ba_decode_latin1(obj, k):
    d = obj.decode
    for _ in range(k): d('latin-1')

def ba_decode_utf8(obj, k):
    d = obj.decode
    for _ in range(k): d('utf-8')

def str_ba_utf8(obj, k):
    s = str
    for _ in range(k): s(obj, 'utf-8')

CASES = {
    'ba.decode(latin-1)': (bytearray, ba_decode_latin1),
    'ba.decode(utf-8)': (bytearray, ba_decode_utf8),
    'str(ba, utf-8)': (bytearray, str_ba_utf8),
    'bytes.decode(utf-8)': (bytes, ba_decode_utf8),
}

def trial(make, op, size, nthreads, shared, ms):
    obj = make(payload(size)) if shared else None
    counts = [0] * nthreads
    barrier = threading.Barrier(nthreads + 1)
    deadline = [0.0]
    chunk = max(1, 20000 // (size // 64 + 1))
    def worker(i):
        o = obj if shared else make(payload(size))  # owned: created by this thread
        c = 0
        barrier.wait()
        end = deadline[0]
        while time.perf_counter() < end:
            op(o, chunk); c += chunk
        counts[i] = c
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(nthreads)]
    for t in ts: t.start()
    deadline[0] = time.perf_counter() + ms / 1000
    barrier.wait()
    t0 = time.perf_counter()
    for t in ts: t.join()
    return sum(counts) / (time.perf_counter() - t0)

def run(a):
    threads = [int(x) for x in a.threads.split(',')]
    res = {'python': sys.executable, 'gil': sys._is_gil_enabled(), 'threads': threads,
           'sizes': SIZES, 'median': {}, 'min': {}}
    print(f"{'case':<22}{'size':>7}{'mode':>8}" + ''.join(f"{'T='+str(t):>10}" for t in threads) + '  (Mops/s, median)')
    for name, (make, op) in CASES.items():
        for size in SIZES:
            for mode in ('owned', 'shared'):
                key = f'{name}|{size}|{mode}'
                med, mn = {}, {}
                for T in threads:
                    r = [trial(make, op, size, T, mode == 'shared', a.ms) for _ in range(a.trials)]
                    med[str(T)], mn[str(T)] = statistics.median(r), min(r)
                res['median'][key], res['min'][key] = med, mn
                flag = ' OUTLIER' if any(mn[k] < med[k] / 5 for k in med) else ''
                print(f"{name:<22}{size:>7}{mode:>8}" + ''.join(f"{med[str(t)]/1e6:>10.2f}" for t in threads) + flag, flush=True)
    if a.json:
        json.dump(res, open(a.json, 'w'), indent=1)

def compare(paths):
    runs = [json.load(open(p)) for p in paths]
    pairs = list(zip(runs[0::2], runs[1::2]))
    threads = [str(t) for t in runs[0]['threads']]
    aa = len(pairs) > 1
    print('new/base throughput, geometric mean over rounds' + ('; A/A = base2/base1' if aa else ''))
    print(f"{'case':<22}{'size':>7}{'mode':>8}" + ''.join(f"{'T='+t:>7}" for t in threads) + ('   A/A' + ''.join(f"{'T='+t:>7}" for t in threads) if aa else ''))
    for key in runs[0]['median']:
        name, size, mode = key.split('|')
        ab = [math.exp(statistics.fmean(math.log(n['median'][key][t] / b['median'][key][t]) for b, n in pairs)) for t in threads]
        row = f"{name:<22}{size:>7}{mode:>8}" + ''.join(f"{x:>7.2f}" for x in ab)
        if aa:
            row += '      ' + ''.join(f"{pairs[1][0]['median'][key][t] / pairs[0][0]['median'][key][t]:>7.2f}" for t in threads)
        print(row)

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'compare':
        compare(sys.argv[2:])
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument('cmd', choices=['run'])
        ap.add_argument('--threads', default='1,2,4,6')
        ap.add_argument('--trials', type=int, default=5)
        ap.add_argument('--ms', type=float, default=200)
        ap.add_argument('--json')
        run(ap.parse_args())
