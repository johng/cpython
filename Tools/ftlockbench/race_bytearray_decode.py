"""Race decoding a bytearray against in-place writes (no resize).

A writer thread flips one byte between 'a' (0x61) and 0xE9.  Reader threads
decode the bytearray as latin-1 and check the str invariant: a string flagged
ASCII (str.isascii(), an O(1) flag test) must not contain a character above
0x7F.  _PyUnicode_FromUCS1 scans for the max char and then copies, so a write
between the two passes yields an ASCII-flagged str holding U+00E9.

Buffer exports only pin the storage against resizing; they do not stop
same-size writes.  On main, ba.decode() holds the bytearray's lock, which
ba[i] = x also takes, so the method path is excluded; str(ba, 'latin-1') and
writes through a memoryview were never excluded.

Usage: python race_bytearray_decode.py [seconds per case]
"""
import sys, threading, time
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
N = 256

def run(label, reader_fn, writer_kind):
    ba = bytearray(b'a' * N)
    mv = memoryview(ba) if writer_kind == 'memoryview' else None
    stop = threading.Event()
    counts = {'calls': 0, 'bad_flag': 0, 'torn': 0}
    lock = threading.Lock()
    def writer():
        if mv is not None:
            while not stop.is_set():
                mv[N // 2] = 0xE9; mv[N // 2] = 0x61
        else:
            while not stop.is_set():
                ba[N // 2] = 0xE9; ba[N // 2] = 0x61
    def reader():
        calls = bad = 0
        f = reader_fn(ba)
        while not stop.is_set():
            for _ in range(200):
                s = f()
                if s.isascii() and max(s) > '\x7f':
                    bad += 1
            calls += 200
        with lock:
            counts['calls'] += calls; counts['bad_flag'] += bad
    ts = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(4)]
    for t in ts: t.start()
    time.sleep(DUR); stop.set()
    for t in ts: t.join()
    if mv is not None:
        mv.release()
    print(f"{label:<44} decodes={counts['calls']:>10,}  ASCII-flagged but non-ASCII: {counts['bad_flag']:,}", flush=True)

run("ba.decode('latin-1')  vs  ba[i] = x", lambda ba: (lambda: ba.decode('latin-1')), 'setitem')
run("str(ba, 'latin-1')    vs  ba[i] = x", lambda ba: (lambda: str(ba, 'latin-1')), 'setitem')
run("ba.decode('latin-1')  vs  memoryview write", lambda ba: (lambda: ba.decode('latin-1')), 'memoryview')
