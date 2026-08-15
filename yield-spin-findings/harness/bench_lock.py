"""Free-threaded lock-contention benchmark.

Exercises the PyMutex spin-wait (_Py_yield) by having N threads hammer a single
shared threading.Lock around a tiny critical section. Reports throughput
(Mops/s) across thread counts, including oversubscribed regimes.
"""
import sys, time, threading, statistics

def run(nthreads, per):
    lock = threading.Lock()
    counter = [0]
    start = threading.Barrier(nthreads + 1)
    done = [0.0]
    def worker():
        l = lock; c = counter
        start.wait()
        for _ in range(per):
            l.acquire()
            c[0] += 1
            l.release()
    ts = [threading.Thread(target=worker) for _ in range(nthreads)]
    for t in ts: t.start()
    start.wait()
    t0 = time.perf_counter()
    for t in ts: t.join()
    dt = time.perf_counter() - t0
    assert counter[0] == per * nthreads
    return dt, per * nthreads

def main():
    total = 4_000_000          # total lock acquisitions per config
    reps = 7
    thread_counts = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64]
    gil = None
    try: gil = sys._is_gil_enabled()
    except AttributeError: pass
    print(f"# python={sys.version.split()[0]} gil_enabled={gil}")
    print(f"# total_ops={total} reps={reps} (reporting best of reps)")
    print(f"{'threads':>8} {'best_s':>9} {'Mops/s':>9} {'med_s':>9}")
    for n in thread_counts:
        per = max(1, total // n)
        samples = []
        for _ in range(reps):
            dt, ops = run(n, per)
            samples.append(dt)
        best = min(samples)
        med = statistics.median(samples)
        ops = per * n
        print(f"{n:>8} {best:>9.4f} {ops/best/1e6:>9.3f} {med:>9.4f}")

if __name__ == "__main__":
    main()
