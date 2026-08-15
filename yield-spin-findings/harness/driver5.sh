#!/usr/bin/env bash
set -euo pipefail
say(){ echo; echo "==================== $* ===================="; }
test -x /root/base/python || { echo "MISSING base"; exit 1; }

say "BUILD configurable spin v2 (reload knob) -> cfg2"
rm -rf /root/cfg2
cp -r /root/base /root/cfg2
python3 /root/patcher3.py /root/cfg2
cd /root/cfg2 && git --no-pager diff -- Include/internal/pycore_lock.h Python/lock.c || true
cd /root/cfg2 && ./configure --disable-gil >/root/cfg2.configure.log 2>&1
cd /root/cfg2 && make -j"$(nproc)" >/root/cfg2.build.log 2>&1
/root/cfg2/python -c 'import sys;print("cfg2",sys.version.split()[0],"gil=",sys._is_gil_enabled())'

PIN="taskset -c 0-7"
run(){ local out=$1; shift; say "RUN $out ($*)"; env "$@" $PIN /root/cfg2/python -X gil=0 /root/bench_lock2.py | tee /root/t_$out.csv; }
say "RUN base (separate binary, reference)"; $PIN /root/base/python -X gil=0 /root/bench_lock2.py | tee /root/t_base.csv
# reload-cadence sweep (pure yield)
run rN_p0  PY_RELOAD_MASK=-1 PY_PAUSE_SPINS=0
run r3_p0  PY_RELOAD_MASK=3  PY_PAUSE_SPINS=0
run r0_p0  PY_RELOAD_MASK=0  PY_PAUSE_SPINS=0
# pause hybrids on the GOOD (never-reload) baseline
run rN_p2  PY_RELOAD_MASK=-1 PY_PAUSE_SPINS=2
run rN_p4a PY_RELOAD_MASK=-1 PY_PAUSE_SPINS=4 PY_ADDITIVE=1
run rN_pp  PY_RELOAD_MASK=-1 PY_PAUSE_SPINS=40
# pause hybrid on the main-style periodic-reload baseline
run r3_p4a PY_RELOAD_MASK=3  PY_PAUSE_SPINS=4 PY_ADDITIVE=1

say "SWEEP COMPARISON (ref = rN_p0 = in-binary never-reload pure-yield == base)"
/root/base/python /root/merge5.py rN_p0 \
  base=/root/t_base.csv rN_p0=/root/t_rN_p0.csv r3_p0=/root/t_r3_p0.csv r0_p0=/root/t_r0_p0.csv \
  rN_p2=/root/t_rN_p2.csv rN_p4a=/root/t_rN_p4a.csv rN_pp=/root/t_rN_pp.csv r3_p4a=/root/t_r3_p4a.csv
say "DONE"
