# `bytearray.decode()` and its critical section: analysis

Date: 2026-09-27. Baseline: `main` @ `6f64920479e`. Free-threaded build
(`--disable-gil`, `-O3`, no PGO/LTO), Apple M5 Max. The change analysed is the
previous commit on this branch. Scripts are in `Tools/ftlockbench/`; raw results
in `Tools/ftlockbench/results/decode/`.

## Summary

- Dropping `@critical_section` from `bytearray.decode()` makes small decodes about
  1.7x faster and lets decodes of a shared bytearray scale instead of serialising.
- **The lock is not redundant.** It is not needed for memory safety, but it stops
  same-size writes (`ba[i] = x`, equal-length slice assignment, `reverse()`) from
  racing the decode. Without it, `ba.decode('latin-1')` can return a `str` flagged
  ASCII that contains a non-ASCII character.
- That invalid-`str` bug **already exists on `main`** through `str(ba, 'latin-1')`,
  the `codecs` functions, and any write through a `memoryview`. Its root cause is
  in the decoders: they scan the source buffer and then copy it.
- Recommended order: fix the decoders to decide the string kind from the bytes
  they actually store, then drop the `decode` lock. The change on this branch is
  not safe to merge on its own.

## 1. The change

`Objects/bytearrayobject.c`: remove `@critical_section` from the `bytearray.decode`
clinic block, which was added by gh-129107's blanket bytearray thread-safety
change, and regenerate `Objects/clinic/bytearrayobject.c.h`. The implementation
only calls `PyUnicode_FromEncodedObject(self, encoding, errors)`.

On `main` each call takes the lock once, then `bytearray_getbuffer` and
`bytearray_releasebuffer` each re-enter it as a nested critical section. A nested
re-entry costs about 13 ns on this machine, against about 2 ns for a plain
uncontended lock pair: `PyMutex_LockFast`'s compare-and-swap fails before
`_PyCriticalSection_BeginSlow` notices the top-most section already holds the
mutex. `nested_cost.py`, best of 7, 64-byte payload:

| | `ba.decode('latin-1')` | `str(ba, 'latin-1')` |
|---|---|---|
| main | 64.4 ns | 36.6 ns |
| change | 38.4 ns | 38.7 ns |

With the change, `ba.decode()` costs the same as `str(ba, ...)`, which does the
same export and release without the outer lock.

## 2. Performance

`bench_bytearray_decode.py`: fixed-duration trials; each thread either decodes its
own bytearray ("owned") or all threads decode one ("shared"); payloads of 16 B to
64 KiB; controls `str(ba, 'utf-8')` and `bytes.decode('utf-8')`. Four interleaved
rounds per build. Machine load drifted during the runs, so baseline-vs-baseline
moved by up to 30 percent even at one thread. The drift-free measure is
`ba.decode('utf-8')` relative to `str(ba, 'utf-8')` in the same process:

| Payload | Mode | main, 1 thread | change, 1 thread | main, 6 threads | change, 6 threads |
|---|---|---|---|---|---|
| 16 B | owned | 0.57 | 0.95 | 0.68 | 0.89 |
| 256 B | owned | 0.61 | 0.95 | 0.72 | 0.84 |
| 4 KiB | owned | 0.92 | 0.94 | 0.97 | 0.94 |
| 64 KiB | owned | 1.00 | 0.98 | 0.99 | 1.02 |
| 64 KiB | shared | 1.01 | 1.02 | 0.15 | 0.93 |

Absolute throughput, best of the four rounds, millions of decodes per second:

| Case | main | change |
|---|---|---|
| 16 B, owned, 1 thread | 15.8 | 27.6 |
| 256 B, owned, 1 thread | 13.4 | 23.0 |
| 64 KiB, owned, 1 thread | 0.37 | 0.38 |
| 16 B, shared, 6 threads | 9.1 | 15.6 |
| 64 KiB, shared, 6 threads | 0.25 | 1.96 |

Two separate effects:

1. **Small decodes, about 1.7x.** From removing the two nested re-entries. Large
   single-thread decodes are unchanged.
2. **Shared decodes scale.** `main` holds the lock for the entire decode, so one
   shared bytearray gets slower as threads are added (0.37M to 0.25M per second
   from 1 to 6 threads for 64 KiB). With the change it scales like `str()`.

## 3. What the lock protects

A buffer export pins the storage: `bytearray_getbuffer` reads the pointer and
length and increments `ob_exports` under the lock, and every resizing path checks
`ob_exports` under the same lock (`_canresize`) and raises `BufferError`. So the
decoder's memory cannot move or be freed. The outer lock adds nothing there.

But `_canresize` is only checked when the size changes. Same-size writes proceed
while the buffer is exported, and they take the bytearray's lock: `ba[i] = x`,
equal-length slice assignment (`bytearray_setslice_linear` with `growth == 0`),
and `reverse()`. With the lock held across the decode, those cannot run in the
middle of it. So the lock makes `ba.decode()` atomic with respect to other
bytearray method calls.

That matters because the decoders read their input twice.
`_PyUnicode_FromUCS1` (latin-1) calls `ucs1lib_find_max_char` on the source to
choose an ASCII or latin-1 string, then `memcpy`s the source. The UTF-8 decoder's
ASCII fast path calls `find_first_nonascii` on the source, then `memcpy`s it into
an ASCII string. A write between the two reads produces a `str` flagged ASCII
that contains U+00E9:

- `s.isascii()` is `True` and `s[128]` is `'é'`.
- `s.encode('utf-8')` copies the bytes out as-is, so it returns invalid UTF-8,
  and decoding that again fails.
- It still compares equal to, and hashes the same as, the valid string.

`race_bytearray_decode.py`: four decoding threads, one writer flipping a byte
between `a` and `0xE9`, 2 seconds each:

| Build | Decode | Writer | Invalid strings |
|---|---|---|---|
| main | `ba.decode('latin-1')` | `ba[i] = x` | 0 |
| main | `str(ba, 'latin-1')` | `ba[i] = x` | 21,360 |
| main | `ba.decode('latin-1')` | `memoryview(ba)[i] = x` | 267,797 |
| change | `ba.decode('latin-1')` | `ba[i] = x` | 43,048 |

`lock_coverage.py` on main, time to the first invalid string:

| Decode | Writer | Result |
|---|---|---|
| `ba.decode('latin-1')` | `ba[i] = x` | none in 2,000,000 decodes |
| `ba.decode('latin-1')` | `memoryview(ba)[i] = x` | first decode |
| `str(ba, 'latin-1')` | `ba[i] = x` | after 2 decodes |
| `codecs.latin_1_decode(ba)` | `ba[i] = x` | after 232 decodes |

So the lock is a partial mitigation. It covers one decode entry point against one
family of writers. Writes through a `memoryview`, `readinto()`,
`struct.pack_into()` or ctypes `from_buffer()` never take the bytearray's lock.

### Minimal reproducer

`repro_decode_race.py` fails on today's `main` by default; `--method` switches to
`ba.decode()`, which the lock protects on `main` and which fails with the change:

```python
# Free-threaded build only. One thread rewrites a byte of a bytearray in
# place while the main thread decodes it as latin-1. The decoder scans the
# buffer for its largest byte, then copies it; a write in between yields a
# str flagged ASCII that contains U+00E9.
import sys, threading

ba = bytearray(b"a" * 256)
if "--method" in sys.argv:
    decode = lambda: ba.decode("latin-1")
else:
    decode = lambda: str(ba, "latin-1")

stop = threading.Event()

def writer():
    while not stop.is_set():
        ba[128] = 0xE9  # 'é', same size: no resize, so a buffer export allows it
        ba[128] = 0x61  # 'a'

t = threading.Thread(target=writer)
t.start()
try:
    for i in range(5_000_000):
        s = decode()
        if s.isascii() and s[128] != "a":
            print(f"invalid str after {i:,} decodes:")
            print(f"  s.isascii()          {s.isascii()}")
            print(f"  s[128]               {s[128]!r}")
            print(f"  s.encode()[128:129]  {s.encode()[128:129]}  (not valid UTF-8)")
            break
    else:
        print("no invalid str in 5,000,000 decodes")
finally:
    stop.set()
    t.join()
```

| Build | Mode | Result |
|---|---|---|
| main | `str(ba, 'latin-1')` | invalid after 1,751 to 44,323 decodes |
| main | `ba.decode('latin-1')` | none in 5,000,000 decodes |
| change | `ba.decode('latin-1')` | invalid after 81 to 2,927 decodes |

## 4. Fix options

| Fix | Protects `ba.decode()` from `ba[i] = x` | Fixes `str(ba)`, `codecs`, `memoryview` writers | Locks | Speed |
|---|---|---|---|---|
| **Read-once decoders, then drop the decode lock** | yes | yes | one fewer | ~1.7x small, shared decodes scale |
| Copy the buffer to private bytes before decoding | yes | only paths through the copy | one fewer | extra allocation and copy per decode |
| Hold the bytearray lock in every decode entry point | yes | no, `memoryview` writers never lock | more | slower |
| Keep the lock, decode directly from the locked buffer | yes | no | same | ~1.7x small, shared stays serialised |
| Skip the CAS on nested critical sections (parked) | yes | no | same | ~1.7x small, shared stays serialised |

Only the first meets all three goals. The last two are expectations, not
measurements.

### The read-once decoder fix

Rule: a decoder must decide what kind of string to build from the same bytes it
stores, never by inspecting the caller's buffer and reading it again.

- `_PyUnicode_FromUCS1` (latin-1), `ascii_decode`'s unaligned path, and the UTF-8
  ASCII fast path in `unicode_decode_utf8` are the scan-then-copy sites found so
  far. The other decoders need the same audit.
- The aligned loop in `ascii_decode` is the model: it loads each word once, checks
  it, and stores that same value. The most robust form is to copy first and then
  inspect only the private copy, because C does not stop a compiler re-reading
  non-atomic memory.

Decoding always copies into the new `str`, so in the normal case this reorders the
work rather than adding a copy. The cost is in wrong guesses. An ASCII string and
a latin-1 string use different object layouts, so a string cannot be fixed up in
place once the data turns out not to fit:

| Variant | ASCII input | Non-ASCII input | Cost |
|---|---|---|---|
| Copy first, guess ASCII | same work as today | extra allocation and full copy, 2x peak memory | wasted work on accented latin-1 and most non-English UTF-8 |
| Copy and check in chunks | same work as today | at most one chunk copied twice | more code in a heavily tuned file |
| Scan the source as a hint, then check the copy | one extra scan | one extra scan of the copied part | a third pass on every decode |

For UTF-8 with an early non-ASCII character, today's code copies only the short
ASCII prefix; plain copy-first would copy everything and discard most of it.

### Other downsides of the read-once approach

- It taxes every decode, including immutable `bytes` that can never race and the
  GIL build. Restricting it to possibly-mutable sources would need a flag or a
  second entry point, because decoders only see a pointer and a length.
- It is still a data race under the C memory model. The result is always a valid
  string, but ThreadSanitizer would still report the reads; atomic loads would
  cost the vectorised `memcpy`.
- The race is probabilistic, so regression tests are slow, possibly flaky, or need
  a test-only hook.
- `ba.decode()` goes from atomic with respect to bytearray methods to "valid but
  possibly a mix of old and new bytes". Maintainers may consider the gh-129107
  atomicity intentional.
- The benefit only reaches free-threaded programs that mutate a buffer while
  decoding it.

## 5. Recommendation and next steps

1. Report the invalid-`str` bug on `main` with `repro_decode_race.py`, as its own
   issue.
2. Prototype the read-once fix for latin-1, ASCII and UTF-8, and measure plain
   `bytes.decode()` on both builds. The deciding question is whether it is
   neutral.
3. If it is neutral, drop the `decode` lock (the previous commit) as a follow-up.
   If not, keep the lock, decode directly from the locked buffer to recover the
   small-decode gain, and accept that shared decodes stay serialised.

## 6. Verification of the change

- `test_bytes test_codecs test_str test_capi test_free_threading test_io`: pass.
- `test_bytes.FreeThreadingTest.test_free_threading_bytearray` runs (not skipped).
  A deliberately impossible assertion in its `decode` helper produced "Uncaught
  thread exception" warnings, which regrtest records as an altered environment,
  so the new assertion does fail CI when violated.
- Not run: ThreadSanitizer, x86, PGO/LTO builds.
