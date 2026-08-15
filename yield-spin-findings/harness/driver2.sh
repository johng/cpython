#!/usr/bin/env bash
set -euo pipefail
say(){ echo; echo "==================== $* ===================="; }
test -x /root/base/python  || { echo "MISSING /root/base/python";  exit 1; }
test -x /root/treat/python || { echo "MISSING /root/treat/python"; exit 1; }

# best-effort: pin CPU governor to performance (may not exist in a KVM guest)
for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > "$f" 2>/dev/null || true; done

say "CONFIRM BUILDS"
/root/base/python  -c 'import sys;print("base  ",sys.version.split()[0],"gil=",sys._is_gil_enabled())'
/root/treat/python -c 'import sys;print("treat ",sys.version.split()[0],"gil=",sys._is_gil_enabled())'

PIN="taskset -c 0-7"
say "RUN base (A)"; $PIN /root/base/python  -X gil=0 /root/bench_lock2.py | tee /root/baseA.csv
say "RUN treat";    $PIN /root/treat/python -X gil=0 /root/bench_lock2.py | tee /root/treat.csv
say "RUN base (B)"; $PIN /root/base/python  -X gil=0 /root/bench_lock2.py | tee /root/baseB.csv

say "COMPARISON (A/A noise floor vs A/B effect; vcsw/op = futex parking)"
/root/base/python /root/merge.py /root/baseA.csv /root/baseB.csv /root/treat.csv
say "DONE"
