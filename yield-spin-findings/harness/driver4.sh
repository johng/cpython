#!/usr/bin/env bash
set -euo pipefail
say(){ echo; echo "==================== $* ===================="; }
test -x /root/base/python || { echo "MISSING base"; exit 1; }

say "BUILD CONFIGURABLE SPIN"
rm -rf /root/cfg
cp -r /root/base /root/cfg
python3 /root/patcher2.py /root/cfg
say "CFG DIFF vs baseline"
cd /root/cfg && git --no-pager diff -- Include/internal/pycore_lock.h Python/lock.c || true
cd /root/cfg && ./configure --disable-gil >/root/cfg.configure.log 2>&1
cd /root/cfg && make -j"$(nproc)" >/root/cfg.build.log 2>&1
/root/cfg/python -c 'import sys;print("cfg",sys.version.split()[0],"gil=",sys._is_gil_enabled())'

PIN="taskset -c 0-7"
run(){ # label env...
  local out=$1; shift
  say "RUN $out ($*)"
  env "$@" $PIN /root/cfg/python -X gil=0 /root/bench_lock2.py | tee /root/s_$out.csv
}
say "RUN base (separate binary, reference)"
$PIN /root/base/python -X gil=0 /root/bench_lock2.py | tee /root/s_base.csv
run p0  PY_PAUSE_SPINS=0
run p2  PY_PAUSE_SPINS=2
run p8  PY_PAUSE_SPINS=8
run p4a PY_PAUSE_SPINS=4 PY_ADDITIVE=1
run p8a PY_PAUSE_SPINS=8 PY_ADDITIVE=1
run pp  PY_PAUSE_SPINS=40

say "SWEEP COMPARISON"
/root/base/python /root/merge4.py \
  base=/root/s_base.csv p0=/root/s_p0.csv p2=/root/s_p2.csv p8=/root/s_p8.csv \
  p4a=/root/s_p4a.csv p8a=/root/s_p8a.csv pp=/root/s_pp.csv
say "DONE"
