# Free-threaded build only. One thread rewrites a byte of a bytearray in
# place while the main thread decodes it as latin-1. The decoder scans the
# buffer for its largest byte, then copies it; a write in between yields a
# str flagged ASCII that contains U+00E9.
#
#   python repro_decode_race.py            # str(ba, 'latin-1'): fails on main
#   python repro_decode_race.py --method   # ba.decode('latin-1'): the bytearray
#                                          # lock prevents it on main
import sys, threading

ba = bytearray(b"a" * 256)
if "--method" in sys.argv:
    decode = lambda: ba.decode("latin-1")
else:
    decode = lambda: str(ba, "latin-1")

stop = threading.Event()

def writer():
    while not stop.is_set():
        ba[128] = 0xE9  # 'é', same size: no resize, so a buffer export allows it
        ba[128] = 0x61  # 'a'

t = threading.Thread(target=writer)
t.start()
try:
    for i in range(5_000_000):
        s = decode()
        if s.isascii() and s[128] != "a":
            print(f"invalid str after {i:,} decodes:")
            print(f"  s.isascii()          {s.isascii()}")
            print(f"  s[128]               {s[128]!r}")
            print(f"  s.encode()[128:129]  {s.encode()[128:129]}  (not valid UTF-8)")
            break
    else:
        print("no invalid str in 5,000,000 decodes")
finally:
    stop.set()
    t.join()
