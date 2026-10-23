#!/usr/bin/env python3
"""E2E flow E: w-save preset, URL-entry save, g-jump with player alive."""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pty_harness import Harness

YT_CLI = str(HERE.parents[1] / "yt-cli")

CFG = Path("/home/kenneth/.config/yt-cli/config.json")
BACKUP = Path("/tmp/opencode/config_backup_e.json")
URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

ok = True


def check(name, cond):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    ok &= bool(cond)


def cfg_presets():
    return {p["slug"] for p in json.loads(CFG.read_text())["radio"]["presets"]}


def main():
    shutil.copy(CFG, BACKUP)
    before = cfg_presets()
    h = Harness([YT_CLI], name="flow_e")
    try:
        # -- 1. main -> Search -> paste URL -> save as preset
        ok1 = h.wait_for("main ❯", 15)
        h.send("1\r", 0.4)
        ok1 &= h.wait_new("search ❯", 10)
        h.send(URL + "\r", 0.02)
        ok1 &= h.wait_new("URL:", 60)
        time.sleep(1.5)
        ok1 &= h.wait_for("save as preset", 10)
        h.send("save as preset\r", 0.4)
        ok1 &= h.wait_new("yes, save as preset", 10)
        h.send("yes\r", 0.4)
        ok1 &= h.wait_new("name", 10)
        h.send("E2E Url\r", 0.1)
        ok1 &= h.wait_new("category", 10)
        h.send("E2E\r", 0.1)
        time.sleep(1.5)
        ok1 &= h.wait_for("no, skip", 10)
        h.send("no\r", 0.4)
        check("url-entry saves preset", ok1)
        check("config has e2e-url", "e2e-url" in cfg_presets())

        # -- 2. search -> play -> w -> save as preset
        ok2 = h.wait_new("search ❯", 10)
        h.send("acdc thunderstruck\r", 0.02)
        ok2 &= h.wait_new("result ❯", 90)
        h.send("1\r", 0.4)
        ok2 &= h.wait_new("playing", 90)
        time.sleep(1.0)
        mpv_pid = subprocess.run(["pgrep", "-x", "mpv"],
                                 capture_output=True, text=True).stdout.split()
        h.send("k\r", 0.4)
        ok2 &= h.wait_new('Save "', 10)
        time.sleep(1.5)
        ok2 &= h.wait_for("music/video", 10)
        h.send("music\r", 0.4)
        ok2 &= h.wait_new("yes, save as preset", 10)
        h.send("yes\r", 0.4)
        ok2 &= h.wait_new("name", 10)
        h.send("E2E Preset\r", 0.1)
        ok2 &= h.wait_new("category", 10)
        h.send("E2E\r", 0.1)
        time.sleep(1.5)
        ok2 &= h.wait_for("no, skip", 10)
        h.send("no\r", 0.4)
        time.sleep(1.5)
        ok2 &= h.wait_for("action ❯", 10)
        check("w-save while playing", ok2)
        check("config has e2e-preset", "e2e-preset" in cfg_presets())

        # -- 3. g-jump to News with player alive
        ok3 = True
        if not mpv_pid:
            print("  [FAIL] no mpv pid captured after playback started")
            ok3 = False
        h.send("go\r", 0.4)
        ok3 &= h.wait_new("go to ❯", 10)
        h.send("3\r", 0.4)              # go-to: Radio, Stations, News
        ok3 &= h.wait_new("source ❯", 15)
        time.sleep(1.0)
        mpv_after = subprocess.run(["pgrep", "-x", "mpv"],
                                   capture_output=True, text=True).stdout.split()
        ok3 &= bool(mpv_pid) and set(mpv_pid) <= set(mpv_after)
        check("g-jump keeps player alive", ok3)

        # -- 4. exit cleanly (Esc in news = goodbye -> main; Esc = quit)
        h.send("\x1b", 0.5)
        ok4 = h.wait_new("main ❯", 15)
        h.send("\x1b", 0.5)
        ok4 &= h.wait_new("Goodbye", 10)
        check("clean exit", ok4)
    finally:
        h.kill()
        shutil.copy(BACKUP, CFG)
    after = cfg_presets()
    added = (after - before) or {"(restored)"}
    print(f"  config restored; presets added during test: {added - {'(restored)'}}")
    print("FLOW E:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
