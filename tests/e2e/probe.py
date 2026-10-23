import sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pty_harness import Harness

YT_CLI = str(HERE.parents[1] / "yt-cli")
h = Harness([YT_CLI], name="probe")
h.wait_for("search / radio / stations / news", 15, silent=True)
h.send("s\r", 0.5)
time.sleep(6)
h.read(0.5)
out = h.buf.decode(errors="replace")
with open(HERE / "probe_dump.txt", "w") as f:
    f.write(repr(out))
    f.write("\n\n=== text-only tail ===\n")
    f.write(out[-2000:])
print("alive:", not h.done, "buflen:", len(out))
h.kill()