# DEBUG ONLY (do not merge): sourced by test_jit.py in place of the
# FINISH_TO_JIT_EXECUTOR loop.  Same loop, plus passive probes showing why a
# 'finish' out of an inline frame can run the inferior to exit on aarch64:
#
#   * For an inline frame gdb cannot plant a return breakpoint, so it steps.
#   * If the step reaches a GNU ld Cortex-A53 erratum 843419 veneer
#     (e843419@...), which has no CFI and no line info, gdb treats it as a
#     called function and plants a step-resume breakpoint at the return
#     address it unwinds from x30.
#   * The veneer is entered by a plain 'b', so x30 is stale: it still holds
#     the return address of an earlier 'bl' that has already returned, and
#     that address never executes again.
#
# None of the probes stop the inferior (their stop() returns False), so gdb's
# stepping decisions are the same as in the unmodified test.
import re

import gdb

TARGET = "py::jit:executor"
MAX_FINISH_STEPS = 20
VENEER_BRANCH_RE = re.compile(
    r"^\s*(?:=>\s*)?(0x[0-9a-f]+)\s+<[^>]*>:\s+b\s+(0x[0-9a-f]+)\s+<(e843419@[^>]+)>",
    re.MULTILINE,
)


def log(msg):
    print("DBG " + msg, flush=True)


def read_x30(frame=None):
    frame = frame or gdb.selected_frame()
    return int(frame.read_register("x30")) & 0xFFFFFFFFFFFFFFFF


def insn(addr):
    return gdb.execute("x/i %#x" % addr, to_string=True).strip()


class Probe(gdb.Breakpoint):
    """Internal breakpoint that records hits (and x30) but never stops."""

    def __init__(self, addr, label):
        super().__init__("*%#x" % addr, internal=True)
        self.addr = addr
        self.label = label
        self.x30_at_hits = []

    def stop(self):
        self.x30_at_hits.append(read_x30())
        return False

    def describe(self):
        hits = len(self.x30_at_hits)
        extra = ""
        if hits:
            extra = "  x30 at first hit = %#x" % self.x30_at_hits[0]
        return "probe %-40s @%#x  hits=%d%s" % (self.label, self.addr, hits, extra)


probes = []


def report_probes(when):
    if probes:
        log("---- probe results %s" % when)
        for p in probes:
            log("  " + p.describe())


def clear_probes():
    for p in probes:
        if p.is_valid():
            p.delete()
    probes.clear()


def on_exit(event):
    log("inferior exited (exit code %s)" % getattr(event, "exit_code", "?"))
    report_probes("after the inferior exited")


gdb.events.exited.connect(on_exit)


def arm_probes(frame):
    """Describe and probe the finish out of inline FRAME."""
    outer = frame
    while outer.type() == gdb.INLINE_FRAME:
        outer = outer.older()
    caller = outer.older()
    stale = read_x30()
    real_ret = caller.pc()
    log("  inline frame; gdb will step until it leaves %s (real frame %s)"
        % (frame.name(), outer.name()))
    log("  x30 = %#x, last written by: %s" % (stale, insn(stale - 4)))
    log("  %s will really return to %#x (%s)" % (outer.name(), real_ret, caller.name()))
    dis = gdb.execute("disassemble %#x" % outer.pc(), to_string=True)
    veneers = VENEER_BRANCH_RE.findall(dis)
    if not veneers:
        log("  no erratum-843419 veneer branches in %s" % outer.name())
    for site, target, name in veneers:
        log("  veneer branch in %s: %s" % (outer.name(), insn(int(site, 16))))
        probes.append(Probe(int(target, 16), "veneer entry " + name))
    probes.append(Probe(stale, "stale x30 target"))
    probes.append(Probe(real_ret, "real return address"))


for i in range(MAX_FINISH_STEPS):
    try:
        frame = gdb.selected_frame()
    except gdb.error as exc:
        log("after %d finishes: %s" % (i, exc))
        break
    inline = frame.type() == gdb.INLINE_FRAME
    log("finish #%d from %-45s %-6s pc=%#x x30=%#x"
        % (i + 1, frame.name(), "INLINE" if inline else "real",
           frame.pc(), read_x30(frame)))
    if frame.name() == TARGET:
        log("reached " + TARGET)
        break
    if inline:
        arm_probes(frame)
        gdb.execute("set debug infrun on")
    gdb.execute("finish")
    gdb.execute("set debug infrun off")
    report_probes("after finish #%d stopped" % (i + 1))
    clear_probes()
else:
    raise RuntimeError("did not reach %s" % TARGET)
