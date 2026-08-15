# `_Py_yield` / PyMutex spin — investigation findings

Investigation prompted by **gh-144586 / PR python/cpython#144587**
("Improve `_Py_yield` to use light-weight cpu instruction", corona10), which
replaces the `sched_yield()`/`SwitchToThread()` OS-yield inside the PyMutex
spin-wait with a per-arch CPU pause hint (x86 `PAUSE`, AArch64 `WFE`, …).

TL;DR: **the instruction-swap is a category error.** For contended free-threaded
locks it is a large regression, for one measured reason after another. The
existing WebKit-style yield-spin is hard to beat, and the *one* genuinely
surprising, actionable result is that **`main`'s spin `RELOAD_SPIN_MASK` reload
looks like a ~2× throughput regression vs not reloading at all under heavy
single-lock contention.**

All measurements are free-threaded (`--disable-gil`), `-O3`, `median-of-9`,
`taskset`-pinned. Primary box: DigitalOcean **c-8**, Intel Xeon Platinum 8280
(Cascade Lake, 8 dedicated vCPU), KVM guest. arm64 numbers: Apple M5 Max.

---

## 1. Primitive cost: the swap's premise is platform-dependent

`_Py_yield()` cost, old (OS yield) vs new (pause hint), measured per call:

| platform | old `sched_yield`/`SwitchToThread` | new pause instr | direction |
|---|--:|--:|---|
| Linux x86 (Broadwell, shared) | ~620 ns (syscall) | `pause` ~19 ns | 33× cheaper |
| Linux x86 (Cascade Lake, dedicated) | ~252 ns (syscall) | `pause` ~3.4 ns | 74× cheaper |
| macOS arm64 (M5 Max) | ~140 ns (fast-trap) | `wfe` **~1338 ns** | **~9.5× *worse*** |

The PR's premise ("`sched_yield` is an expensive syscall, replace with a cheap
instruction") is **true on Linux/x86** but **false on Darwin/arm64**: there
`sched_yield` is a cheap fast-trap and the "lightweight" `wfe` is the expensive
one. A bare `WFE` has no matching `SEV` on the unlock side (grep confirms zero
`SEV` in the tree) so it stalls until the CPU event stream ticks — ~1.3 µs on
Apple silicon — overshooting the sub-µs spin window it is meant to fill. See
`harness/{yield_probe.c,sched_probe.c,x86_probe.c}`.

## 2. Workload cost: cheaper-per-spin ≠ faster locks

Contended `threading.Lock` microbench (single global lock, tiny critical
section; `harness/bench_lock2.py`). Baseline = `sched_yield` spin, treatment =
pure `pause` spin. Mops/s (higher = better), Xeon 8280:

| threads | base (`sched_yield`) | treat (`pause`) | Δ | pause parks/op |
|--:|--:|--:|--:|--:|
| 2  | 7.87 | 5.91 | −25% | 0.017 |
| 8  | 2.21 | 0.79 | −63% | 0.62 |
| 16 | 2.93 | 0.52 | **−82%** | **1.09** |

The pure-pause swap makes contended locks **2–5× slower**, despite `pause` being
74× cheaper as an instruction. Mechanism, proven via `ru_nvcsw` (voluntary
context switches / op = futex parks): `sched_yield`'s "expensive" syscall is
load-bearing — it deschedules the waiter so the **holder** can run and release,
and the waiter usually acquires *during* the 40-spin without parking (0.04
parks/op). `pause` makes the 40 spins last ~120 ns instead of ~10 µs, so the
holder hasn't released yet and the waiter **parks on nearly every acquire**
(1.09 parks/op at 16t) — a park-storm. This is corona10's "did not work as we
expected."

## 3. Why: the WebKit design spins by *yielding* on purpose

`Include/internal/pycore_lock.h` says the lock is based on WebKit's `WTF::Lock`
(<https://webkit.org/blog/6161/locking-in-webkit/>). WebKit's slow path spins 40
times *calling `sched_yield()` between spins* — the yield **is** the algorithm.
What matters is the spin-window **duration**: `sched_yield` ≈ 250 ns/iter sizes
40 spins to ~10 µs (outlasts the critical section → acquire in-spin); `pause`
≈ 3 ns/iter sizes it to ~120 ns (too short → park). The PR optimized per-spin
cost while collapsing the window below the timescale of contention.

## 4. Can a hybrid (`pause` → `yield` → park) beat plain yield? No.

A configurable spin (`harness/patcher3.py`, env-driven, one binary, in-binary
controls ⇒ layout-noise-free) sweeping **reload cadence × pause prefix**.
Reference `rN_p0` = never-reload pure-yield (reproduces `base` within layout
noise; harness validated). Mops/s @ 16 threads and % vs `rN_p0`:

| config | what | Mops/s@16 | Δ vs rN_p0 | parks/op@16 |
|---|---|--:|--:|--:|
| `rN_p0` | never-reload, all-yield (**= original e682141 lock**) | 3.13 | ref | 0.035 |
| `r3_p0` | **main's** every-4th tid-staggered reload, all-yield | 1.61 | **−48%** | 0.222 |
| `r0_p0` | every-iteration reload, all-yield | 1.47 | −53% | 0.197 |
| `rN_p2` | never-reload + 2-pause prefix | 3.04 | −3% | 0.040 |
| `rN_p4a`| never-reload + 4-pause additive prefix | 3.08 | −1.5% | 0.036 |
| `rN_pp` | never-reload + pure pause | 0.79 | −75% | 0.671 |
| `r3_p4a`| main-reload + 4-pause additive | 1.62 | −48% | 0.226 |

Findings, most important first:

1. **Reload cadence dominates, and less reload is better.** Holding all else
   fixed and varying only how often the spin re-reads `m->_bits`: never-reload
   beats **main's periodic reload by ~2×** (−48%) and every-iteration by −53% at
   16 threads. Re-reading the white-hot lock byte during the spin keeps the line
   Shared across all cores, so every unlock write is an expensive RFO → the
   handoff stalls → 5–6× more parking. **`main`'s `RELOAD_SPIN_MASK` addition
   appears to regress heavy single-lock FT contention by ~2×.** (Caveat: one
   adversarial workload; the reload was likely added to help acquire latency or
   a different contention pattern — see the crossover below.)
2. **Pure pause is irredeemable** even on the best reload strategy (`rN_pp`
   −75%): the park-storm is intrinsic to having no scheduler cooperation.
3. **A short pause prefix, done right, is a wash** (`rN_p2`, `rN_p4a` within ±3%,
   and +0.8…+2.9% at 2–5 threads — the theoretical pause fast-path win, real but
   tiny). The earlier "hybrid regresses 17–65%" result was **confounded** by a
   per-iteration reload; controlling for it, pause is ~neutral.

### The reload crossover (why WebKit-style polling isn't crazy)

`r3_p0` (poll every 4th) vs `rN_p0` (never), by thread count:

| threads | ≤ cores? | r3 vs rN |
|--:|:--|--:|
| 2 | yes | −1.4% |
| 4 | yes | −5.5% |
| 6 | yes | **+3.7%** |
| 8 | saturated | −26% |
| 16 | 2× oversub | −48% |

Polling (re-reading to catch a release ASAP) is neutral-to-*faster* while cores
are free, and only catastrophic once the lock is contended past the core count.
So `main`'s reload is a **latency / low-contention** optimization that costs
**throughput under extreme contention**. The open question is which regime FT
workloads actually live in.

---

## Conclusions

- The PR #144587 direction (swap the yield instruction) should not land as-is;
  it regresses contended FT locks. On the platform where the instruction is
  cheapest (x86) it is still −25…−82%. On arm64 the `wfe` choice is separately
  broken (superseded upstream by #149784's `nano_delay` backoff, which drops
  `wfe`).
- Plain **never-reload all-yield** spinning (the original lock) is the throughput
  winner here; no pause variant beats it.
- The genuinely actionable lead is **§4.1: does `RELOAD_SPIN_MASK` cost FT
  throughput under heavy single-lock contention?** Worth reproducing across more
  realistic workloads (short holds, threads ≤ cores, multiple independent
  locks) before drawing an upstream conclusion.

## Reproduce

```
# on a Linux/x86 box:
scp harness/* root@BOX:/root/
ssh root@BOX 'bash /root/driver5.sh'     # builds base + configurable binary, runs the reload×pause sweep
```

See `harness/` for all scripts, `results/` for raw sweep tables, and
`code-variants/` for the two committed code experiments as patches.

_Benchmarks run on ephemeral DigitalOcean c-8 instances; analysis by Claude
(Opus 4.8) with John Griffith._
