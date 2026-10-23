import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pty_harness import Harness

YT_CLI = str(HERE.parents[1] / "yt-cli")

def flow_a():
    h = Harness([YT_CLI], name="main")
    ok = True
    ok &= h.wait_for("search / radio / stations / news", 15)
    h.send("s\r", 0.4)
    ok &= h.wait_for("search YouTube", 8)
    h.send("acdc thunderstruck\r", 0.02)
    ok &= h.wait_for("result", 90, silent=True)
    h.send("1\r", 0.4)
    ok &= h.wait_for("action", 8, silent=True)
    ok &= h.wait_for("playing", 60, silent=True)
    h.send("o\r", 0.4)
    ok &= h.wait_for("result", 8, silent=True)
    ok &= h.wait_for("AC/DC", 5, silent=True)
    h.send("\x1b", 0.4)
    ok &= h.wait_for("action", 8, silent=True)
    h.send("h\r", 0.4)
    ok &= h.wait_for("history", 8, silent=True)
    h.send("\x1b", 0.4)
    ok &= h.wait_for("action", 8, silent=True)
    h.send("q\r", 0.4)
    ok &= h.wait_for("search / radio / stations / news", 10)
    h.send("x\r", 0.4)
    time.sleep(2)
    h.read(1)
    if not h.done:
        print("  [FAIL] process still running after x")
        ok = False
    print("FLOW A:", "PASS" if ok else "FAIL")
    h.kill()

import time
flow_a()