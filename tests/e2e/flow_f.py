#!/usr/bin/env python3
"""E2E flow F: Stations mode against an isolated HOME (no network, no play).

Verifies the CLI listing plus the main-menu → Stations → back → exit path
without touching the real ~/.config/yt-cli or launching mpv.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pty_harness import Harness  # noqa: E402

YT_CLI = str(HERE.parents[1] / "yt-cli")


def main() -> int:
    if not shutil.which("fzf") or not shutil.which("mpv"):
        print("FLOW F: SKIP (fzf/mpv not installed)")
        return 0

    home = Path(tempfile.mkdtemp(prefix="ytcli-flow-f-"))
    cfgdir = home / ".config" / "yt-cli"
    cfgdir.mkdir(parents=True)
    (cfgdir / "config.json").write_text(
        json.dumps(
            {
                "stations": [
                    {
                        "slug": "jazz-fm",
                        "name": "Jazz FM",
                        "url": "http://example.invalid/jazz",
                        "countrycode": "US",
                        "codec": "MP3",
                        "bitrate": 128,
                        "tags": ["jazz"],
                        "enabled": True,
                    }
                ],
            }
        )
    )
    env = {"HOME": str(home)}

    ok = True

    listing = subprocess.run(
        [YT_CLI, "--stations", "--list"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=30,
    )
    listed = listing.returncode == 0 and "jazz-fm" in listing.stdout and "Jazz FM" in listing.stdout
    print(f"  [{'PASS' if listed else 'FAIL'}] --stations --list")
    ok &= listed

    h = Harness([YT_CLI], env=env, name="flow_f")
    try:
        ok &= h.wait_for("saved / search / history", 15)
        h.send("Saved\r", 0.4)
        ok &= h.wait_for("Jazz FM", 8)
        ok &= h.wait_for("manage saved", 5)
        h.send("\x1b", 0.4)  # Esc → main menu
        ok &= h.wait_new("main ❯", 8)
        h.send("Exit\r", 0.4)
        # A live player from another session adds the detach/exit prompt.
        # Detach leaves it alone; do not stop someone else's playback.
        if h.wait_new("Player is still running", 2):
            h.send("d\r", 0.4)
        time.sleep(2)
        h.read(1)
        exited = h.done
        print(f"  [{'PASS' if exited else 'FAIL'}] exit after Stations")
        ok &= exited
    finally:
        h.kill()
        shutil.rmtree(home, ignore_errors=True)

    print("FLOW F:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
