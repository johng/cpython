"""Contended threading.Lock benchmark, v2.

Reports median throughput + p25/p75 spread, and voluntary context switches
per op (ru_nvcsw) -- a direct measure of futex parking. CSV to stdout.
"""
import sys, time, threading, statistics, resource

def run(nthreads, per):
    lock = threading.Lock(); counter = [0]
    start = threading.Barrier(nthreads + 1)
    def worker():
        l = lock; c = counter
        start.wait()
        for _ in range(per):
            l.acquire(); c[0] += 1; l.release()
    ts = [threading.Thread(target=worker) for _ in range(nthreads)]
    for t in ts: t.start()
    start.wait()
    r0 = resource.getrusage(resource.RUSAGE_SELF)
    t0 = time.perf_counter()
    for t in ts: t.join()
    dt = time.perf_counter() - t0
    r1 = resource.getrusage(resource.RUSAGE_SELF)
    assert counter[0] == per * nthreads
    vcsw = r1.ru_nvcsw - r0.ru_nvcsw
    return dt, per * nthreads, vcsw

def main():
    total = 2_000_000
    reps = 9
    thread_counts = [1, 2, 3, 4, 5, 6, 8, 10, 12, 16]
    try: gil = sys._is_gil_enabled()
    except AttributeError: gil = None
    print(f"# python={sys.version.split()[0]} gil={gil} total={total} reps={reps}")
    print("threads,median_s,mops,p25_s,p75_s,vcsw_per_op")
    for n in thread_counts:
        per = max(1, total // n)
        ds = []; vs = []
        for _ in range(reps):
            dt, ops, vcsw = run(n, per)
            ds.append(dt); vs.append(vcsw / ops)
        ds.sort()
        med = statistics.median(ds)
        p25 = ds[len(ds)//4]; p75 = ds[(3*len(ds))//4]
        ops = per * n
        print(f"{n},{med:.4f},{ops/med/1e6:.3f},{p25:.4f},{p75:.4f},{statistics.median(vs):.3f}")

if __name__ == "__main__":
    main()
