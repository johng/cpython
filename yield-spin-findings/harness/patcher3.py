import sys, pathlib
root = pathlib.Path(sys.argv[1])

# header: add _Py_cpu_relax()
h = root / "Include/internal/pycore_lock.h"
s = h.read_text()
old = "extern void _Py_yield(void);"
new = old + '''

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
assert s.count(old) == 1
h.write_text(s.replace(old, new))

# lock.c
c = root / "Python/lock.c"
s = c.read_text()

old2 = """#if Py_GIL_DISABLED
static const int MAX_SPIN_COUNT = 40;
#else
static const int MAX_SPIN_COUNT = 0;
#endif"""
new2 = old2 + """

// Experimental runtime-configurable spin (env vars, read once at load).
//   PY_PAUSE_SPINS = leading CPU-pause iterations (0 = pure yield)
//   PY_ADDITIVE    = 1: add pause iters on top of the yield budget
//   PY_BACKOFF_CAP = max pauses per iteration (exponential up to this)
//   PY_RELOAD_MASK = reload v when ((spin_count+tid) & mask)==0; -1 = never
static int g_pause_spins = 0;
static int g_additive = 0;
static int g_backoff_cap = 64;
static int g_reload_mask = 0;
__attribute__((constructor))
static void
init_spin_cfg(void)
{
    const char *s;
    if ((s = getenv("PY_PAUSE_SPINS")) != NULL) g_pause_spins = atoi(s);
    if ((s = getenv("PY_ADDITIVE")) != NULL)    g_additive = atoi(s);
    if ((s = getenv("PY_BACKOFF_CAP")) != NULL) g_backoff_cap = atoi(s);
    if ((s = getenv("PY_RELOAD_MASK")) != NULL) g_reload_mask = atoi(s);
}"""
assert s.count(old2) == 1
s = s.replace(old2, new2)

old3 = "    Py_ssize_t spin_count = 0;\n    for (;;) {"
new3 = """    Py_ssize_t spin_count = 0;
    int backoff = 1;
    const int pause_spins = g_pause_spins;
    const int backoff_cap = g_backoff_cap;
    const int reload_mask = g_reload_mask;
    // per-thread offset (stack address) to de-phase reloads across threads
    const Py_ssize_t tid = (Py_ssize_t)((uintptr_t)&entry >> 8);
    const Py_ssize_t total_spins = MAX_SPIN_COUNT + (g_additive ? pause_spins : 0);
    for (;;) {"""
assert s.count(old3) == 1
s = s.replace(old3, new3)

old4 = """        if (!(v & _Py_HAS_PARKED) && spin_count < MAX_SPIN_COUNT) {
            // Spin for a bit.
            _Py_yield();
            spin_count++;
            continue;
        }"""
new4 = """        if (!(v & _Py_HAS_PARKED) && spin_count < total_spins) {
            if (spin_count < pause_spins) {
                for (int i = 0; i < backoff; i++) {
                    _Py_cpu_relax();
                }
                if (backoff < backoff_cap) {
                    backoff <<= 1;
                }
            }
            else {
                _Py_yield();
            }
            spin_count++;
            if (reload_mask >= 0 && ((spin_count + tid) & reload_mask) == 0) {
                v = _Py_atomic_load_uint8_relaxed(&m->_bits);
            }
            continue;
        }"""
assert s.count(old4) == 1
s = s.replace(old4, new4)

c.write_text(s)
print("patched OK")
