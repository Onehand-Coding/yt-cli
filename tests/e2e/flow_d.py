import subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pty_harness import Harness

YT_CLI = str(HERE.parents[1] / "yt-cli")
NOFZF = str(HERE / "bin")

def mpv_alive():
    return bool(subprocess.run(["pgrep", "-x", "mpv"], capture_output=True, text=True).stdout.strip())

def flow_d():
    h = Harness([YT_CLI], env={"PATH": NOFZF}, name="curses")
    ok = True
    ok &= h.wait_new("unified search", 15)
    h.send("\r", 0.4)
    ok &= h.wait_new("search YouTube", 8, silent=True)
    h.send("acdc thunderstruck\r", 0.01)
    ok &= h.wait_new("results:", 90)
    h.send("\r", 0.4)
    ok &= h.wait_new("other results", 8, silent=True)
    time.sleep(8)
    playing = mpv_alive()
    print(f"  {'[PASS]' if playing else '[FAIL]'} mpv running while action menu up")
    ok &= playing
    h.send("q", 0.4)
    ok &= h.wait_new("unified search", 8)
    time.sleep(2)
    if mpv_alive():
        print("  [FAIL] mpv still running after q")
        ok = False
    else:
        print("  [PASS] mpv stopped after q")
    for _ in range(3):
        h.send("j", 0.05)
    h.send("\r", 0.4)
    ok &= h.wait_new("history", 8)
    h.send("q", 0.4)
    ok &= h.wait_new("unified search", 8)
    for _ in range(4):
        h.send("j", 0.05)
    h.send("\r", 0.4)
    ok &= h.wait_new("Goodbye", 8)
    h.send("\r", 0.4)
    time.sleep(2)
    h.read(1)
    if not h.done:
        print("  [FAIL] process still running after Exit dismiss")
        ok = False
    print("FLOW D:", "PASS" if ok else "FAIL")
    h.kill()

flow_d()