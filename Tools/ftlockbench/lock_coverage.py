"""Which decode entry points and which writers the bytearray lock covers on
main: races each decode call against ba[i] = x or a memoryview write and
reports the first ASCII-flagged str that contains a non-ASCII character."""
import codecs, threading
def trial(label, decode_fn, write_kind, n=2_000_000):
    ba = bytearray(b"a" * 256)
    mv = memoryview(ba)
    stop = threading.Event()
    def writer():
        w = mv if write_kind == "memoryview" else ba
        while not stop.is_set():
            w[128] = 0xE9; w[128] = 0x61
    t = threading.Thread(target=writer); t.start()
    found = None
    for i in range(n):
        s = decode_fn(ba)
        if s.isascii() and s[128] != "a":
            found = i; break
    stop.set(); t.join(); mv.release()
    print(f"{label:<52} {'invalid after %s decodes' % f'{found:,}' if found is not None else 'none in %s' % f'{n:,}'}")
trial("ba.decode('latin-1')        vs ba[i] = x", lambda b: b.decode("latin-1"), "setitem")
trial("ba.decode('latin-1')        vs memoryview(ba)[i] = x", lambda b: b.decode("latin-1"), "memoryview")
trial("str(ba, 'latin-1')          vs ba[i] = x", lambda b: str(b, "latin-1"), "setitem")
trial("codecs.latin_1_decode(ba)   vs ba[i] = x", lambda b: codecs.latin_1_decode(b)[0], "setitem")
