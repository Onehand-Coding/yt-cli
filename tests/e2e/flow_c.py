import sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pty_harness import Harness

YT_CLI = str(HERE.parents[1] / "yt-cli")

def flow_c():
    h = Harness([YT_CLI, "--channels"], name="news")
    ok = True
    ok &= h.wait_new("source ❯", 15)
    h.send("news5everywhere\r", 0.4)
    ok &= h.wait_new("stream ❯", 120)
    h.send("2\r", 0.4)
    ok &= h.wait_new("action ❯", 15)
    ok &= h.wait_new("playing", 90)
    h.send("\x1b", 0.4)  # back to the stream list
    ok &= h.wait_for("stream ❯", 8)
    h.send("refresh\r", 0.4)
    ok &= h.wait_new("Scanning", 10)
    ok &= h.wait_for("stream ❯", 120)
    h.send("\x1b", 0.4)
    ok &= h.wait_new("source ❯", 8)
    h.send("\x1b", 0.4)
    time.sleep(2)
    h.read(1)
    if not h.done:
        print("  [FAIL] process still running after Esc at source picker")
        ok = False
    print("FLOW C:", "PASS" if ok else "FAIL")
    h.kill()

flow_c()