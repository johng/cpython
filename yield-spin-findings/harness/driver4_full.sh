#!/usr/bin/env bash
set -euo pipefail
BASE_COMMIT=e682141c495c2e52368c4341ae54eea041070356
NJ=$(nproc)
say(){ echo; echo "==================== $* ===================="; }

say "APT DEPS"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq build-essential git pkg-config >/dev/null
gcc --version | head -1

say "FETCH cpython @ $BASE_COMMIT -> base"
rm -rf /root/base
mkdir -p /root/base && cd /root/base
git init -q
git remote add origin https://github.com/python/cpython.git
git fetch -q --depth 1 origin "$BASE_COMMIT"
git checkout -q FETCH_HEAD

say "BUILD base (-j$NJ)"
cd /root/base
./configure --disable-gil >/root/base.configure.log 2>&1
grep -E '^OPT=' Makefile | head -1
make -j"$NJ" >/root/base.build.log 2>&1
/root/base/python -c 'import sys;print("base",sys.version.split()[0],"gil=",sys._is_gil_enabled())'

say "BUILD configurable spin -> cfg"
rm -rf /root/cfg
cp -r /root/base /root/cfg
python3 /root/patcher2.py /root/cfg
cd /root/cfg && git --no-pager diff -- Include/internal/pycore_lock.h Python/lock.c || true
cd /root/cfg && ./configure --disable-gil >/root/cfg.configure.log 2>&1
cd /root/cfg && make -j"$NJ" >/root/cfg.build.log 2>&1
/root/cfg/python -c 'import sys;print("cfg",sys.version.split()[0],"gil=",sys._is_gil_enabled())'

PIN="taskset -c 0-7"
run(){ local out=$1; shift; say "RUN $out ($*)"; env "$@" $PIN /root/cfg/python -X gil=0 /root/bench_lock2.py | tee /root/s_$out.csv; }
say "RUN base (reference)"; $PIN /root/base/python -X gil=0 /root/bench_lock2.py | tee /root/s_base.csv
run p0  PY_PAUSE_SPINS=0
run p2  PY_PAUSE_SPINS=2
run p8  PY_PAUSE_SPINS=8
run p4a PY_PAUSE_SPINS=4 PY_ADDITIVE=1
run p8a PY_PAUSE_SPINS=8 PY_ADDITIVE=1
run pp  PY_PAUSE_SPINS=40

say "SWEEP COMPARISON (p0 = in-binary pure-yield control; layout-noise-free)"
/root/base/python /root/merge4.py \
  base=/root/s_base.csv p0=/root/s_p0.csv p2=/root/s_p2.csv p8=/root/s_p8.csv \
  p4a=/root/s_p4a.csv p8a=/root/s_p8a.csv pp=/root/s_pp.csv
say "DONE"
