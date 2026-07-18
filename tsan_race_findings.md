# Free-threaded data-race hunt — findings

Follow-up to [gh-153852](https://github.com/python/cpython/issues/153852)
(`itertools.count.__repr__` non-atomic counter read). That bug is one instance of
a general pattern, so we built a harness to find more of the same.

## Method — `tsan_race_hunt.py`

The pattern: a C object stores an internal counter/index that `__next__` **writes**
while `__repr__` / `__reduce__` / `__length_hint__` / `copy` **read** it
non-atomically. The harness hammers a **shared** object with N mutator (`next`)
threads + M reader threads, runs one target per subprocess under ThreadSanitizer,
and parses the `data race` reports.

```
# free-threaded + TSan build required
RACE_SECONDS=1.5 PYTHON_GIL=0 ./python.exe tsan_race_hunt.py         # all targets
RACE_SECONDS=1.5 PYTHON_GIL=0 ./python.exe tsan_race_hunt.py str_iter enumerate
```

**Built-in control that validates the method:** `list`/`tuple`/`range`/`dict`
iterators come back **clean** — they were already hardened with
`FT_ATOMIC_LOAD/STORE_SSIZE_RELAXED` on `it_index`. Every target flagged below is
simply *missing* that same hardening.

## All findings (32 targets, 8 flagged)

| Target | Site | Class | Tier |
|---|---|---|---|
| `str` iterator | `unicodeobject.c:14979` `unicode_ascii_iter_next` | plain `it_index++` RMW | 1 |
| `bytes` iterator | `bytesobject.c:3446` `striter_next` | plain `it_index++` RMW | 1 |
| `enumerate.__reduce__` | `enumobject.c:283` `enum_reduce` | plain read vs atomic write | 1 |
| `count` slow path | `itertoolsmodule.c:3678` `count_repr` / `count_next:3663` | `long_cnt` pointer TOCTOU | 2 |
| `islice` | `itertoolsmodule.c:1710/1715/1722` | multi-field `next`/`cnt` | 2 |
| `pairwise` | `itertoolsmodule.c:351/396` | old/new `PyObject*` swap | 2 |
| `groupby` | `itertoolsmodule.c:526/537/540…` | shared state (documented not thread-safe) | 3 |
| `ordereddict` | `odictobject.c:677/742/1964` | linked-list under concurrent writers | 3 |

---

## Impact to Python code — top 3

"Data race = undefined behavior in C" is the formal answer, but what actually
breaks in *Python* differs sharply between these. Ranked by real-world impact.

### 1. `str` iterator — `unicode_ascii_iter_next` (highest impact)

```c
if (it->it_index < PyUnicode_GET_LENGTH(seq)) {          // read it_index (check)
    Py_UCS1 chr = PyUnicode_READ(..., data, it->it_index); // read it_index again
    it->it_index++;                                        // read-modify-write
    return (PyObject*)&_Py_SINGLETON(strings).ascii[chr];
}
```

`it_index` is a plain (non-atomic) read-modify-write, and it is read **multiple
times** with no synchronization. **Impact when a `str` is iterated from more than
one thread** (`for c in shared_str` in two threads, or `next(shared_it)`):

- **Silently wrong iteration (common):** two threads read the same `it_index`,
  so a character is **returned twice and another skipped**, or the index advances
  by 1 instead of 2. Results are quietly incorrect — no exception.
- **Out-of-bounds read (rare, but a real crash path):** the bounds check and the
  actual `PyUnicode_READ` read `it_index` separately, so another thread can push
  the index past the end in between → the read goes **one-or-more bytes past the
  string buffer**. Usually that byte is the trailing `\0` (harmless), but a
  larger skip can read adjacent heap and yield a byte ≥ 128. Then
  `&...ascii[chr]` indexes past the 128-entry `ascii[]` singleton array — and
  because `ascii[]` and `latin1[]` have **different element strides**, the result
  is a **mis-strided / garbage `PyObject*`** → type confusion or segfault.

This is the worst of the three because it corrupts the **most common operation
(iteration)**, and it violates the free-threading guarantee that concurrent use
of one object must never crash the interpreter (only give unspecified-but-safe
results, as `list`/`tuple` iterators already do).

### 2. `bytes` iterator — `striter_next` (high impact)

```c
if (it->it_index < PyBytes_GET_SIZE(seq))
    return _PyLong_FromUnsignedChar((unsigned char)seq->ob_sval[it->it_index++]);
```

Same non-atomic RMW on `it_index`. **Impact when a `bytes` is iterated from
multiple threads:**

- **Silently wrong iteration:** bytes are **duplicated or skipped**, same as `str`.
- **Out-of-bounds read:** the raced index can read **past `ob_sval`** into
  adjacent heap → returns a wrong integer value, and a far-enough skip can touch
  an unmapped page → **segfault**.
- No type-confusion path here: the byte is always converted through
  `_PyLong_FromUnsignedChar` (any 0–255 value is a valid small-int), so unlike
  `str` the returned object itself is always well-formed. That makes it a notch
  less dangerous than `str`, but the silent corruption + OOB read remain.

### 3. `enumerate.__reduce__` — `enum_reduce` (lowest impact, cleanest fix)

```c
Py_BEGIN_CRITICAL_SECTION(en);                       // ← does NOT help here
...
result = Py_BuildValue("O(On)", Py_TYPE(en), en->en_sit, en->en_index); // plain read
Py_END_CRITICAL_SECTION();
```

`enum_next` writes `en_index` with `FT_ATOMIC_STORE_SSIZE_RELAXED` and **takes no
lock** (it is lock-free). So the `Py_BEGIN_CRITICAL_SECTION` in `enum_reduce`
protects against nothing — the writer never contends for that lock — and the
plain read of `en_index` races the atomic write. **Impact when a shared,
actively-advancing `enumerate` is pickled or copied** (`pickle.dumps(en)` /
`copy.copy(en)` while another thread runs `next(en)`):

- The `__reduce__` snapshot can capture a **stale/torn index**, so the
  **unpickled/copied `enumerate` resumes counting from the wrong number**
  (off-by-some). Purely a correctness glitch in the restored object.
- **No crash and no OOB:** it is a single aligned `Py_ssize_t` read used only to
  build an int, and on real 64-bit hardware an aligned word read is not actually
  torn — so in practice this is "formally UB / wrong-index on exotic platforms"
  rather than an observable failure today.

The value here is that it is the **smallest, most surgical fix** (swap the plain
read for `FT_ATOMIC_LOAD_SSIZE_RELAXED`), a direct twin of the gh-153852 count
fix, and it documents a genuinely instructive trap: **a critical section gives
zero protection when the counterpart writer is lock-free.**

---

## Recommendation

All three Tier-1 fixes are the same shape as the count fix and mirror
already-merged `list`/`tuple`/`range` iterator hardening: low risk, semantics
unchanged, each with a `test_free_threading` test that fails under TSan.

- **`str` + `bytes` iterators** — highest impact (corrupt normal iteration; crash
  paths), tidy matching pair, one PR.
- **`enumerate.__reduce__`** — lowest risk, one-liner, best "explain the subtlety"
  PR description.
