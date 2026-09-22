import subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pty_harness import Harness

YT_CLI = str(HERE.parents[1] / "yt-cli")
h = Harness([YT_CLI, "--news"], name="news-probe")
ok = h.wait_new("source ❯", 15)
h.send("1\r", 0.4)
ok = h.wait_new("stream ❯", 120) and ok
h.send("1\r", 0.4)
ok = h.wait_new("action ❯", 15) and ok
for i in range(24):
    mpv = subprocess.run(["pgrep", "-x", "mpv"], capture_output=True, text=True).stdout.strip()
    print(f"  t={i*10}s mpv={mpv or 'NONE'}")
    h.read(9.5)
    if b"playing" in h.buf[-4000:]:
        print("  [PASS] playing within", (i+1)*10, "s")
        ok = True
        break
    if h.done:
        print("  [FAIL] process exited")
        break
print("PROBE:", "PASS" if ok else "FAIL")
h.kill()