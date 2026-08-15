#!/usr/bin/env bash
set -euo pipefail
say(){ echo; echo "==================== $* ===================="; }
test -x /root/base/python  || { echo "MISSING base";  exit 1; }
test -x /root/treat/python || { echo "MISSING treat"; exit 1; }

say "BUILD HYBRID (backoff: pause->yield->park)"
rm -rf /root/hybrid
cp -r /root/base /root/hybrid
python3 /root/patcher.py /root/hybrid
say "HYBRID DIFF vs baseline"
cd /root/hybrid && git --no-pager diff -- Include/internal/pycore_lock.h Python/lock.c || true
cd /root/hybrid && ./configure --disable-gil >/root/hybrid.configure.log 2>&1
cd /root/hybrid && make -j"$(nproc)" >/root/hybrid.build.log 2>&1
/root/hybrid/python -c 'import sys;print("hybrid",sys.version.split()[0],"gil=",sys._is_gil_enabled())'

PIN="taskset -c 0-7"
say "RUN base (A)"; $PIN /root/base/python   -X gil=0 /root/bench_lock2.py | tee /root/h_baseA.csv
say "RUN treat";    $PIN /root/treat/python  -X gil=0 /root/bench_lock2.py | tee /root/h_treat.csv
say "RUN hybrid";   $PIN /root/hybrid/python -X gil=0 /root/bench_lock2.py | tee /root/h_hybrid.csv
say "RUN base (B)"; $PIN /root/base/python   -X gil=0 /root/bench_lock2.py | tee /root/h_baseB.csv

say "4-WAY COMPARISON (park = voluntary ctx-switches/op)"
/root/base/python /root/merge3.py /root/h_baseA.csv /root/h_baseB.csv /root/h_treat.csv /root/h_hybrid.csv
say "DONE"
