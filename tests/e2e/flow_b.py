import sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pty_harness import Harness

YT_CLI = str(HERE.parents[1] / "yt-cli")

def flow_b():
    h = Harness([YT_CLI, "--presets"], name="radio")
    ok = True
    ok &= h.wait_new("preset ❯", 15)
    h.send("1\r", 0.4)
    ok &= h.wait_new("action ❯", 15)
    ok &= h.wait_new("playing", 90)
    time.sleep(1.0)
    h.send("\x1b", 0.6)
    ok &= h.wait_for("preset ❯", 8)
    h.send("2\r", 0.4)
    ok &= h.wait_new("action ❯", 15)
    ok &= h.wait_new("playing", 90)
    h.send("se\r", 0.4)
    ok &= h.wait_new("search ❯", 8)
    h.send("acdc thunderstruck\r", 0.02)
    ok &= h.wait_new("result ❯", 90)
    h.send("1\r", 0.4)
    ok &= h.wait_new("action ❯", 8)
    ok &= h.wait_new("playing", 90)
    h.send("q\r", 0.4)
    ok &= h.wait_for("preset ❯", 8)
    h.send("\x1b", 0.4)
    h.read(1.0)
    if not h.done:
        h.send("\x1b", 0.4)
    deadline = time.time() + 5
    while time.time() < deadline and not h.done:
        h.read(0.5)
    if not h.done:
        print("  [FAIL] process still running after Esc at preset picker")
        ok = False
    print("FLOW B:", "PASS" if ok else "FAIL")
    h.kill()

flow_b()