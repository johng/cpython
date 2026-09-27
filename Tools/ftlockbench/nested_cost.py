"""Single-thread cost of a nested same-object critical section vs a plain
uncontended lock pair: ba.decode (outer lock + nested getbuffer/releasebuffer
on main) vs str(ba, enc) (plain getbuffer/releasebuffer), with bytes and
crc32 controls.  Best of 7, nanoseconds per call."""
import time, binascii
ba = bytearray(range(64)); by = bytes(ba)
def t(f, n=3_000_000):
    best = 1e9
    for _ in range(7):
        t0 = time.perf_counter_ns(); f(n); best = min(best, (time.perf_counter_ns() - t0) / n)
    return best
def dec(n):
    d = ba.decode
    for i in range(n): d('latin-1')
def strc(n):
    s = str
    for i in range(n): s(ba, 'latin-1')
def strb(n):
    s = str
    for i in range(n): s(by, 'latin-1')
def crc(n):
    c = binascii.crc32
    for i in range(n): c(ba)
def crcb(n):
    c = binascii.crc32
    for i in range(n): c(by)
print(' '.join(f"{name}={t(f):.2f}ns" for name, f in
      [('ba.decode', dec), ('str(ba)', strc), ('str(bytes)', strb), ('crc32(ba)', crc), ('crc32(bytes)', crcb)]))
