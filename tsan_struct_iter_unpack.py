"""Reproducer: struct.iter_unpack is not free-thread-safe (UAF + OOB read).

Modules/_struct.c has NO critical sections and NO atomics.  The unpackiter
(struct.iter_unpack) iterator advances self->index with a plain non-atomic
read-modify-write, and on exhaustion does, with no synchronization:

    Py_CLEAR(self->so);            // drop the Struct
    PyBuffer_Release(&self->buf);  // release the input buffer

Two threads iterating ONE shared iter_unpack to exhaustion can both run that
tail -> double Py_DECREF of the Struct AND double PyBuffer_Release of the buffer
(releasing an already-released buffer) -> use-after-free / memory corruption.
Separately, the non-atomic `self->index += s_size` lets a raced index read the
buffer out of bounds.

Only an ordinary pattern is used: iterate one iter_unpack from several threads.

Expected:
  * free-threaded + TSan build:
        TSAN_OPTIONS=halt_on_error=1 PYTHON_GIL=0 ./python.exe tsan_struct_iter_unpack.py
    -> WARNING: ThreadSanitizer: data race ... in unpackiter_iternext
  * free-threaded release build (no pydebug):
    -> nondeterministic SIGSEGV / SIGABRT / corruption (or survives a run).
"""

import struct
import threading

NTHREADS = 8
ROUNDS = 5000
S = struct.Struct("i")
BUF = b"\x00\x00\x00\x00" * 500   # 500 "i" items; small so it exhausts fast


def drain(it, barrier):
    barrier.wait()
    for _ in it:
        pass


def main():
    for _ in range(ROUNDS):
        it = S.iter_unpack(BUF)
        barrier = threading.Barrier(NTHREADS)
        threads = [threading.Thread(target=drain, args=(it, barrier))
                   for _ in range(NTHREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    print("survived this run")


if __name__ == "__main__":
    main()
