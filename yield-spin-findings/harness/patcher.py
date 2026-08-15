import sys, pathlib
root = pathlib.Path(sys.argv[1])

# 1. pycore_lock.h: add _Py_cpu_relax() next to the _Py_yield decl
h = root / "Include/internal/pycore_lock.h"
s = h.read_text()
old = "extern void _Py_yield(void);"
new = old + '''

// Lightweight CPU pause hint (spin-wait) for the backoff phase of the mutex
// spin: x86 PAUSE, ARM/AArch64 YIELD, else a compiler barrier.
static inline void
_Py_cpu_relax(void)
{
#if defined(__x86_64__) || defined(__i386__)
    __asm__ volatile ("pause" ::: "memory");
#elif defined(__aarch64__) || (defined(__arm__) && __ARM_ARCH >= 7)
    __asm__ volatile ("yield" ::: "memory");
#else
    __asm__ volatile ("" ::: "memory");
#endif
}'''
assert s.count(old) == 1, ("h decl", s.count(old))
h.write_text(s.replace(old, new))

# 2. lock.c
c = root / "Python/lock.c"
s = c.read_text()

old2 = """#if Py_GIL_DISABLED
static const int MAX_SPIN_COUNT = 40;
#else
static const int MAX_SPIN_COUNT = 0;
#endif"""
new2 = old2 + """

// Backoff schedule for the spin phase: spend the first PAUSE_SPINS iterations
// on a cheap CPU pause (exponentially growing up to PAUSE_BACKOFF_CAP pauses)
// before escalating to a scheduler yield.
static const int PAUSE_SPINS = 8;
static const int PAUSE_BACKOFF_CAP = 64;"""
assert s.count(old2) == 1, ("maxspin", s.count(old2))
s = s.replace(old2, new2)

old3 = "    Py_ssize_t spin_count = 0;\n"
new3 = "    Py_ssize_t spin_count = 0;\n    int backoff = 1;\n"
assert s.count(old3) == 1, ("spincount", s.count(old3))
s = s.replace(old3, new3)

old4 = """        if (!(v & _Py_HAS_PARKED) && spin_count < MAX_SPIN_COUNT) {
            // Spin for a bit.
            _Py_yield();
            spin_count++;
            continue;
        }"""
new4 = """        if (!(v & _Py_HAS_PARKED) && spin_count < MAX_SPIN_COUNT) {
            // Adaptive backoff: cheap CPU pause (exponential, to cut cache-line
            // traffic) for the first PAUSE_SPINS iterations, then escalate to a
            // scheduler yield so the lock holder can run if it needs this core.
            // Reload each turn so an early release is grabbed without parking.
            if (spin_count < PAUSE_SPINS) {
                for (int i = 0; i < backoff; i++) {
                    _Py_cpu_relax();
                }
                if (backoff < PAUSE_BACKOFF_CAP) {
                    backoff <<= 1;
                }
            }
            else {
                _Py_yield();
            }
            spin_count++;
            v = _Py_atomic_load_uint8_relaxed(&m->_bits);
            continue;
        }"""
assert s.count(old4) == 1, ("spinblock", s.count(old4))
s = s.replace(old4, new4)

c.write_text(s)
print("patched OK")
