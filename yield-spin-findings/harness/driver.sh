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

say "PRIMITIVE PROBE (pause vs sched_yield on THIS box)"
cc -O2 -pthread -o /root/x86_probe /root/x86_probe.c
/root/x86_probe
/root/x86_probe

say "FETCH cpython @ $BASE_COMMIT"
rm -rf /root/base /root/treat
mkdir -p /root/base && cd /root/base
git init -q
git remote add origin https://github.com/python/cpython.git
git fetch -q --depth 1 origin "$BASE_COMMIT"
git checkout -q FETCH_HEAD
cd /root
cp -r /root/base /root/treat
cp /root/patch/pycore_lock.h /root/treat/Include/internal/pycore_lock.h
cp /root/patch/lock.c        /root/treat/Python/lock.c

say "TREATMENT DIFF vs baseline (must be _Py_yield only)"
cd /root/treat && git --no-pager diff -- Include/internal/pycore_lock.h Python/lock.c || true

build(){
  d=$1; cd /root/$d
  ./configure --disable-gil >/root/$d.configure.log 2>&1
  grep -E '^OPT=' Makefile | head -1
  make -j"$NJ" >/root/$d.build.log 2>&1
  echo "built $d: $(./python -c 'import sys;print(sys.version.split()[0], "gil="+str(sys._is_gil_enabled()))')"
}
say "BUILD BASELINE (-j$NJ)"; build base
say "BUILD TREATMENT (-j$NJ)"; build treat

say "SANITY: _Py_yield symbol (baseline should have out-of-line symbol; treatment inlined-away on x86)"
nm /root/base/python 2>/dev/null | grep -i _Py_yield || echo "  base: no _Py_yield symbol"
nm /root/treat/python 2>/dev/null | grep -i _Py_yield || echo "  treat: no _Py_yield symbol (inlined pause)"

say "BENCH BASELINE (sched_yield)"
cd /root && /root/base/python -X gil=0 /root/bench_lock.py

say "BENCH TREATMENT (pause)"
cd /root && /root/treat/python -X gil=0 /root/bench_lock.py

say "DONE"
