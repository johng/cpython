"""Concurrency race hunter for the free-threaded build under ThreadSanitizer.

The known itertools.count.__repr__ race (gh-153852) is one instance of a general
pattern: a C object stores an internal counter/index that __next__ *writes* while
__repr__ / __reduce__ / __length_hint__ / copy *read* it non-atomically.  This
script hammers a *shared* object with mutator threads (next) and reader threads
(the introspection ops) and lets TSan flag the non-atomic accesses.

Usage:
    # Build must be free-threaded + TSan.  Driver mode (spawns one child per
    # target so a race in one object can't mask another):
    TSAN_OPTIONS='...' PYTHON_GIL=0 ./python.exe tsan_race_hunt.py [name ...]

    # Run a single target in-process (used internally by the driver):
    ./python.exe tsan_race_hunt.py --run-target <name>

Env knobs:
    RACE_SECONDS   per-target run time (default 1.5)
    RACE_NMUT      mutator (next) threads   (default 2)
    RACE_NREAD     reader threads           (default 4)
"""

import copy
import os
import re
import subprocess
import sys
import threading


SECONDS = float(os.environ.get("RACE_SECONDS", "1.5"))
NMUT = int(os.environ.get("RACE_NMUT", "2"))
NREAD = int(os.environ.get("RACE_NREAD", "4"))


# --- target registry -------------------------------------------------------
#
# Each factory returns a fresh, effectively-infinite shared object so the
# mutator threads never run out of work inside the time window.

def _targets():
    import itertools as I
    from collections import deque, OrderedDict

    BIG = 1 << 40  # large enough that next() never exhausts during a run

    T = {}
    def reg(name, factory):
        T[name] = factory

    # itertools iterators with an internal counter/index/state
    reg("count",        lambda: I.count())
    reg("count_step",   lambda: I.count(0, 2))
    reg("repeat",       lambda: I.repeat(42))
    reg("cycle",        lambda: I.cycle((1, 2, 3, 4)))
    reg("chain",        lambda: I.chain(range(BIG), range(BIG)))
    reg("accumulate",   lambda: I.accumulate(I.count()))
    reg("islice",       lambda: I.islice(I.count(), 0, None, 3))
    reg("starmap",      lambda: I.starmap(lambda a, b: a, zip(I.count(), I.count())))
    reg("takewhile",    lambda: I.takewhile(lambda x: True, I.count()))
    reg("dropwhile",    lambda: I.dropwhile(lambda x: False, I.count()))
    reg("filterfalse",  lambda: I.filterfalse(lambda x: False, I.count()))
    reg("compress",     lambda: I.compress(I.count(), I.cycle((1, 0))))
    reg("pairwise",     lambda: I.pairwise(I.count()))
    reg("zip_longest",  lambda: I.zip_longest(I.count(), range(BIG)))
    reg("product",      lambda: I.product(range(1000), repeat=3))
    reg("permutations", lambda: I.permutations(range(50), 3))
    reg("combinations", lambda: I.combinations(range(200), 3))
    reg("batched",      lambda: I.batched(I.count(), 3))
    reg("groupby",      lambda: I.groupby(I.count(), lambda x: x // 4))

    # builtin iterators
    reg("enumerate",    lambda: enumerate(I.count()))
    reg("reversed",     lambda: reversed([0] * 2_000_000))
    reg("zip",          lambda: zip(range(BIG), range(BIG)))
    reg("map",          lambda: map(lambda x: x, range(BIG)))
    reg("filter",       lambda: filter(lambda x: True, range(BIG)))
    reg("list_iter",    lambda: iter([0] * 2_000_000))
    reg("tuple_iter",   lambda: iter(tuple(range(2_000_000))))
    reg("range_iter",   lambda: iter(range(BIG)))
    reg("str_iter",     lambda: iter("x" * 2_000_000))
    reg("bytes_iter",   lambda: iter(b"x" * 2_000_000))
    reg("dict_keyiter", lambda: iter({i: i for i in range(2_000_000)}))

    # stateful containers (mutate + introspect concurrently)
    reg("deque",        lambda: deque(range(1000)))
    reg("ordereddict",  lambda: OrderedDict((i, i) for i in range(1000)))

    return T


# --- worker ops ------------------------------------------------------------

def _mutate(obj, stop):
    """Advance / mutate the object."""
    from collections import deque
    if isinstance(obj, deque):
        while not stop.is_set():
            obj.append(1)
            try:
                obj.popleft()
            except IndexError:
                pass
        return
    if hasattr(obj, "__setitem__") and hasattr(obj, "popitem"):  # OrderedDict
        n = 0
        while not stop.is_set():
            obj[n] = n
            try:
                obj.popitem()
            except KeyError:
                pass
            n += 1
        return
    # default: it's an iterator
    while not stop.is_set():
        try:
            next(obj)
        except StopIteration:
            break
        except Exception:
            break


def _read(obj, stop):
    """Hammer the introspection ops that read internal state non-atomically."""
    def reduce_(o):
        o.__reduce__()
    def lenhint(o):
        o.__length_hint__()
    ops = [reduce_, copy.copy, repr, lenhint, len]
    while not stop.is_set():
        for op in ops:
            if stop.is_set():
                break
            try:
                op(obj)
            except Exception:
                pass


def run_target(name):
    obj = _targets()[name]()
    stop = threading.Event()
    threads = [threading.Thread(target=_mutate, args=(obj, stop)) for _ in range(NMUT)]
    threads += [threading.Thread(target=_read, args=(obj, stop)) for _ in range(NREAD)]
    for t in threads:
        t.start()
    threading.Event().wait(SECONDS)
    stop.set()
    for t in threads:
        t.join()


# --- driver: one subprocess per target, parse TSan reports -----------------

_FRAME = re.compile(r"#\d+ (\w+) (\S+\.[ch]:\d+)")


def _first_cpython_frame(block):
    m = _FRAME.search(block)
    return f"{m.group(1)} @ {m.group(2)}" if m else "(no frame)"


def _parse_races(stderr):
    """Return a list of (access1, access2) cpython frame pairs, deduped."""
    races = []
    # Each report starts at "WARNING: ThreadSanitizer: data race".
    reports = re.split(r"WARNING: ThreadSanitizer: data race", stderr)[1:]
    for rep in reports:
        # Split the report into the individual access stacks.
        accesses = re.split(
            r"\n\s+(?:Read|Write|Atomic read|Atomic write|Previous read|"
            r"Previous write|Previous atomic read|Previous atomic write) of size",
            rep,
        )
        frames = [_first_cpython_frame(a) for a in accesses[1:3]]
        while len(frames) < 2:
            frames.append("(?)")
        races.append(tuple(frames))
    # dedupe
    seen, out = set(), []
    for r in races:
        key = frozenset(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def driver(names):
    supp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "Tools", "tsan", "suppressions_free_threading.txt")
    env = dict(os.environ)
    env["PYTHON_GIL"] = "0"
    env["TSAN_OPTIONS"] = (
        f"halt_on_error=0 exitcode=0 suppressions={supp}"
    )
    results = {}
    for name in names:
        sys.stdout.write(f"  probing {name:<14} ... ")
        sys.stdout.flush()
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--run-target", name],
            env=env, capture_output=True, text=True, timeout=SECONDS + 60,
        )
        races = _parse_races(proc.stderr)
        results[name] = races
        print(f"{len(races)} race(s)" if races else "clean")
        for a, b in races:
            print(f"        - {a}")
            print(f"          {b}")

    print("\n" + "=" * 70)
    flagged = {k: v for k, v in results.items() if v}
    if not flagged:
        print("No data races flagged across", len(names), "targets.")
    else:
        print(f"RACES in {len(flagged)}/{len(names)} targets:")
        for name, races in flagged.items():
            print(f"  {name}: {len(races)}")
    print("=" * 70)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--run-target":
        run_target(args[1])
    else:
        all_names = list(_targets())
        driver(args if args else all_names)
