# yt-cli Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge `yt-cli`, `tune`, and `news-stream` into one robust script (`/home/kenneth/.local/bin/yt-cli`) with a unified main menu, harmonized action menu, universal search from any mode, shared history, symlink aliases for `tune`/`news-stream`, and a **universal curses fallback** used by ALL modes when fzf is missing.

**Architecture:** One Python file with clearly delineated sections: shared core (colors, config+migration, UI abstraction, spinner, IPC watcher, player lifecycle, action menu), three mode sections (Search, Radio, News) written against the UI abstraction (no per-UI branches), then main menu + dispatch. The UI layer has two implementations: `FzfUI` (when fzf is on PATH) and `CursesUI` (when it is not). Dispatch reads `Path(sys.argv[0]).name`: `yt-cli` → main menu, `tune` → radio, `news-stream` → news, anything else → radio preset lookup.

**Tech Stack:** Python 3 stdlib only (urllib, configparser→JSON migration, subprocess, threading, socket, curses). External tools at runtime: `mpv`, `yt-dlp`, `fzf` (optional).

**Design doc:** `docs/specs/2026-08-20-yt-cli-merge-design.md`

**Source originals (used for porting; backed up in Task 0):**
- `/tmp/opencode/backup/yt-cli.bak` — 696 lines
- `/tmp/opencode/backup/tune.bak` — 1434 lines
- `/tmp/opencode/backup/news-stream.bak` — 1695 lines

**Environment notes:**
- No git repo (no commits — verification is py_compile + tmux E2E).
- The unified script is built as `/home/kenneth/.local/bin/yt-cli`; the originals stay at their paths until Task 18 (aliases).
- `/tmp/opencode/` has test harnesses; `~/.config/mpv/mpv.conf` already sets `input-ipc-server=/tmp/mpvsocket`.
- Existing configs: `~/.config/yt-cli/config.json` (JSON, 3 keys), `~/.config/tune/config.ini` (33 presets), `~/.config/news-stream/config.ini` (12 sources).

**Unified API surface (must be consistent across all tasks):**
- `get_ui()` → active UI instance (`FzfUI` or `CursesUI`).
- `ui.pick(items, prompt, header, height) -> int | None` — numbered list picker; returns item index or None (Esc/q). `items` are display strings (may contain ANSI codes).
- `ui.pick_query(items, prompt, header, height) -> (str, int | None)` — list picker with a query line (fzf's type-to-filter); returns `(typed_query, selected_index_or_None)`.
- `ui.action(header_lines, actions) -> str` — `actions` is `[(label, code), ...]`; returns a code; Esc → `"q"`.
- `ui.query(header_lines) -> str | None` — search-query input; `None` = quit (Esc), `""` = empty (→ history).
- `ui.prompt(label) -> str` — single-line text input.
- `ui.info(lines, wait=True) -> None` — message screen.
- `_ipc_watch(name, done)` — 2 args, `done` is `threading.Event`.
- `add_to_history(item, cap)` — item dict: `{title, url, uploader?, duration?}`.
- `load_config()` → merged dict with keys `result_count`, `history_size`, `mpv_args`, `radio`, `news`.
- `load_radio_cfg(cfg)` → `(settings, presets)`; `load_news_cfg(cfg)` → `(settings, sources)`.

---

## File Structure

- Create: `/home/kenneth/.local/bin/yt-cli` (overwrites existing; becomes the merged script)
- Create: `/tmp/opencode/backup/yt-cli.bak`, `/tmp/opencode/backup/tune.bak`, `/tmp/opencode/backup/news-stream.bak`
- Modify (symlinks, Task 18): `/home/kenneth/.local/bin/tune`, `/home/kenneth/.local/bin/news-stream`
- Test: `/tmp/opencode/ytplay_test.py`, `/tmp/opencode/tuneplay_test.py` (updated in Task 21)

---

### Task 0: Backup originals + snapshot state

**Files:**
- Create: `/tmp/opencode/backup/` directory with the three originals

- [ ] **Step 1: Backup the three scripts**

```bash
mkdir -p /tmp/opencode/backup
cp /home/kenneth/.local/bin/yt-cli /tmp/opencode/backup/yt-cli.bak
cp /home/kenneth/.local/bin/tune /tmp/opencode/backup/tune.bak
cp /home/kenneth/.local/bin/news-stream /tmp/opencode/backup/news-stream.bak
```

Expected: `ls /tmp/opencode/backup/` shows the three `.bak` files.

- [ ] **Step 2: Snapshot configs**

```bash
cp -r /home/kenneth/.config/tune /tmp/opencode/backup/tune-config
cp -r /home/kenneth/.config/news-stream /tmp/opencode/backup/news-stream-config
cp /home/kenneth/.config/yt-cli/config.json /tmp/opencode/backup/yt-cli-config.json
cp /home/kenneth/.config/yt-cli/history.json /tmp/opencode/backup/history.json
```

Expected: backup dir contains the config snapshots.

---

### Task 1: Merged script skeleton — imports, colors, persistence

**Files:**
- Create: `/home/kenneth/.local/bin/yt-cli`

- [ ] **Step 1: Write the skeleton header, colors, and persistence**

Write the following to `/home/kenneth/.local/bin/yt-cli` (the file currently holds the old search-only script; it will be overwritten):

```python
#!/usr/bin/env python3
"""yt-cli: unified YouTube search / radio / news player.

Merges the former yt-cli (search), tune (radio), and news-stream (news) into
one script. Dispatch on argv[0]: yt-cli → main menu, tune → radio mode,
news-stream → news mode, anything else → radio preset lookup.
"""

import asyncio
import configparser
import curses
import html
import itertools
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "yt-cli"
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history.json"
IPC_SOCKET = "/tmp/mpvsocket"

# Old config paths (for migration)
TUNE_CONFIG = Path.home() / ".config" / "tune" / "config.ini"
NEWS_CONFIG = Path.home() / ".config" / "news-stream" / "config.ini"

COLORS = {"header": 0, "warn": 0, "footer": 0}


class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"
    PLAY    = f"\033[32m▶\033[0m"
    LIVE    = f"\033[31m{BOLD}● LIVE{RESET}"
    RECENT  = f"\033[33m⏱{RESET}"


def c(color, text):
    return f"{color}{text}{C.RESET}"


def strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class SearchError(Exception):
    pass


# ---------- persistence ----------

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


DEFAULT_CONFIG = {
    "result_count": 20,
    "history_size": 200,
    "mpv_args": [],
    "radio": {
        "player": "mpv",
        "no_video": False,
        "loop": False,
        "background": False,
        "presets": [],
    },
    "news": {
        "player": "auto",
        "quality": "720",
        "timeout": 15,
        "recent_hours": 24,
        "sources": [],
    },
}


def load_config():
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    for k, v in cfg.items():
        if k in ("radio", "news"):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


def save_config(cfg):
    save_json(CONFIG_PATH, cfg)


def add_to_history(item, cap):
    history = load_json(HISTORY_PATH, [])
    history = [h for h in history if h.get("url") != item.get("url")]
    history.insert(0, {**item, "played_at": datetime.now(timezone.utc).isoformat()})
    save_json(HISTORY_PATH, history[:cap])
```

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0, no output.

---

### Task 2: Migration of old configs → merged JSON

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append to end)

- [ ] **Step 1: Write the migration functions**

Append to `/home/kenneth/.local/bin/yt-cli`:

```python
# ---------- config migration ----------

def _migrate_radio_ini():
    """Import ~/.config/tune/config.ini into the merged config. Returns dict or None."""
    if not TUNE_CONFIG.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(TUNE_CONFIG)
    presets = []
    for section in parser.sections():
        if not section.startswith("preset."):
            continue
        slug = section[len("preset."):]
        presets.append({
            "slug": slug,
            "name": parser.get(section, "name", fallback=slug),
            "url": parser.get(section, "url", fallback=""),
            "category": parser.get(section, "category", fallback="Uncategorized"),
            "loop": parser.getboolean(section, "loop", fallback=False),
            "no_video": parser.getboolean(section, "no_video", fallback=False),
            "radio": parser.getboolean(section, "radio", fallback=False),
            "enabled": parser.getboolean(section, "enabled", fallback=True),
        })
    return {
        "player": parser.get("settings", "player", fallback="mpv"),
        "no_video": parser.getboolean("settings", "no_video", fallback=False),
        "loop": parser.getboolean("settings", "loop", fallback=False),
        "background": parser.getboolean("settings", "background", fallback=False),
        "presets": presets,
    }


def _migrate_news_ini():
    """Import ~/.config/news-stream/config.ini into the merged config. Returns dict or None."""
    if not NEWS_CONFIG.exists():
        return None
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(NEWS_CONFIG)
    sources = []
    for section in parser.sections():
        if not section.startswith("source."):
            continue
        sid = section[len("source."):]
        sources.append({
            "id": sid,
            "name": parser.get(section, "name", fallback=sid),
            "channel_id": parser.get(section, "channel_id", fallback=None),
            "keywords": [k.strip().lower()
                         for k in parser.get(section, "keywords", fallback=sid).split(",")],
            "fallback_url": parser.get(section, "fallback_url", fallback=""),
            "enabled": parser.getboolean(section, "enabled", fallback=True),
        })
    return {
        "player": parser.get("settings", "player", fallback="auto"),
        "quality": parser.get("settings", "quality", fallback="720"),
        "timeout": parser.getint("settings", "timeout", fallback=15),
        "recent_hours": parser.getint("settings", "recent_hours", fallback=24),
        "sources": sources,
    }


def migrate_configs():
    """Merge old tune/news configs into the merged config.json (once)."""
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        cfg = dict(DEFAULT_CONFIG)
    changed = False
    if not cfg.get("radio", {}).get("presets"):
        migrated = _migrate_radio_ini()
        if migrated:
            cfg["radio"] = migrated
            changed = True
    if not cfg.get("news", {}).get("sources"):
        migrated = _migrate_news_ini()
        if migrated:
            cfg["news"] = migrated
            changed = True
    if changed:
        save_json(CONFIG_PATH, cfg)
```

- [ ] **Step 2: Verify compile and dry-run migration against a copy**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`

Then a read-only test using the real configs but a temp target:

```bash
cd /tmp/opencode && python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('y', '/home/kenneth/.local/bin/yt-cli')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
r = m._migrate_radio_ini(); n = m._migrate_news_ini()
print('radio presets:', len(r['presets']) if r else 0)
print('news sources :', len(n['sources']) if n else 0)
print('first preset :', (r['presets'][0]['slug'] if r else None))
print('first source :', (n['sources'][0]['id'] if n else None))
"
```

Expected: `radio presets: 33`, `news sources: 12`, and the first preset/source slugs print.

---

### Task 3: Shared terminal helpers + Spinner

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Write terminal helpers and Spinner**

Append:

```python
# ---------- terminal helpers ----------

def clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def get_terminal_width():
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def find_fzf():
    return shutil.which("fzf") is not None


# ---------- spinner ----------

class Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label):
        self.label   = label
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r  {c(C.CYAN, frame)}  {c(C.DIM, self.label)}      ")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()
        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()

    def done(self, msg, color=C.GREEN):
        self._stop.set()
        self._thread.join()
        sys.stdout.write(f"\r  {c(color, '✓')}  {msg}" + ' ' * 40 + '\n')
        sys.stdout.flush()

    def fail(self, msg):
        self._stop.set()
        self._thread.join()
        sys.stdout.write(f"\r  {c(C.YELLOW, '⚠')}  {msg}" + ' ' * 40 + '\n')
        sys.stdout.flush()
```

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 4: FzfUI implementation

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Write fzf_pick + FzfUI**

Append:

```python
# ---------- UI abstraction: fzf ----------

def fzf_pick(items, prompt="❯ ", header=None, height="50%", capture_query=False):
    """Low-level fzf wrapper. capture_query=True returns (query, selected, escaped)."""
    if not items and not capture_query:
        return None
    cmd = ["fzf", "--ansi", "--reverse", "--no-multi", "--no-info",
           "--prompt", prompt, f"--height={height}", "--margin=1,2"]
    if header:
        cmd += ["--header", header]
    if capture_query:
        cmd += ["--print-query"]
    tmp_fd, tmp_path = tempfile.mkstemp()
    os.close(tmp_fd)
    try:
        with open("/dev/tty", "rb+", buffering=0) as tty:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                 stdout=open(tmp_path, "w"), stderr=tty, text=True)
            p.stdin.write("\n".join(items))
            p.stdin.close()
            p.wait()
        with open(tmp_path, "r") as f:
            stdout = f.read()
    except FileNotFoundError:
        os.unlink(tmp_path)
        return ("", None, True) if capture_query else None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    if capture_query:
        lines = stdout.splitlines()
        query = lines[0] if lines else ""
        if p.returncode == 130:
            return (query, None, True)
        selected = lines[1] if len(lines) > 1 else None
        return (query, selected, False)
    return stdout.strip() if p.returncode == 0 else None


def _num_line(i, text):
    return f"{c(C.BOLD + C.CYAN, str(i) + '.')}  {text}"


def _parse_index(chosen):
    if chosen is None:
        return None
    m = re.match(r"^\x1b\[[^m]*m(\d+)\.", chosen) or re.match(r"^(\d+)\.", chosen)
    return int(m.group(1)) - 1 if m else None


class FzfUI:
    def pick(self, items, prompt, header, height):
        lines = [_num_line(i + 1, it) for i, it in enumerate(items)]
        return _parse_index(fzf_pick(lines, prompt=prompt, header=header, height=height))

    def pick_query(self, items, prompt, header, height):
        lines = [_num_line(i + 1, it) for i, it in enumerate(items)]
        query, chosen, _ = fzf_pick(lines, prompt=prompt, header=header,
                                    height=height, capture_query=True)
        return query, _parse_index(chosen)

    def action(self, header_lines, actions):
        labels = [label for label, _ in actions]
        chosen = fzf_pick(labels, prompt="action ❯ ",
                          header="\n".join(header_lines) + "\n" + "─" * 44, height="55%")
        if chosen is None:
            return "q"
        for label, code in actions:
            if strip_ansi(chosen).strip() == label:
                return code
        return "q"

    def query(self, header_lines):
        q, _, escaped = fzf_pick([], prompt="search ❯ ",
                                 header="\n".join(header_lines), height="25%",
                                 capture_query=True)
        if escaped:
            return None
        return q or ""

    def prompt(self, label):
        try:
            return input(f"  {c(C.CYAN + C.BOLD, '❯')} {label} ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    def info(self, lines, wait=True):
        clear_screen()
        for line in lines:
            print("  " + line)
        print()
        if wait:
            try:
                input("  press enter to continue")
            except (EOFError, KeyboardInterrupt):
                pass
```

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 5: CursesUI implementation

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Write the curses UI primitives + CursesUI**

Append:

```python
# ---------- UI abstraction: curses ----------

def _curses_init_colors():
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    curses.init_pair(1, curses.COLOR_CYAN, bg)
    curses.init_pair(2, curses.COLOR_YELLOW, bg)
    curses.init_pair(3, curses.COLOR_BLUE, bg)
    COLORS["header"] = curses.color_pair(1) | curses.A_BOLD
    COLORS["warn"] = curses.color_pair(2)
    COLORS["footer"] = curses.color_pair(3)


def _curses_safe_addstr(stdscr, y, x, text, attr=0):
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    try:
        stdscr.addstr(y, x, text[: max(0, w - x - 1)], attr)
    except curses.error:
        pass


def _curses_line(stdscr, y, label="", default=""):
    """Bottom-line text input. Returns stripped string ('' on Esc)."""
    h, w = stdscr.getmaxyx()
    _curses_safe_addstr(stdscr, h - 1, 0, " " * (w - 1))
    _curses_safe_addstr(stdscr, h - 1, 0, label)
    curses.echo()
    curses.curs_set(1)
    try:
        text = stdscr.getstr(h - 1, min(len(label), w - 1),
                             max(1, w - len(label) - 1)).decode("utf-8", errors="replace")
    except (curses.error, KeyboardInterrupt):
        text = ""
    finally:
        curses.noecho()
        curses.curs_set(0)
    return (text or default).strip()


def _curses_draw_list(stdscr, title, items, selected, top, footer, filter_str=""):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    _curses_safe_addstr(stdscr, 0, 0, title, COLORS["header"])
    visible = h - 3
    if not items:
        _curses_safe_addstr(stdscr, 2, 2, "(empty)", COLORS["warn"])
    else:
        for row in range(visible):
            idx = top + row
            if idx >= len(items):
                break
            text = strip_ansi(items[idx])
            if filter_str and filter_str.lower() not in text.lower():
                continue
            attr = curses.A_REVERSE if idx == selected else 0
            _curses_safe_addstr(stdscr, row + 2, 1, text, attr)
    _curses_safe_addstr(stdscr, h - 1, 0, footer + ("  filter: " + filter_str if filter_str else ""),
                        COLORS["footer"])
    stdscr.refresh()


class CursesUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        _curses_init_colors()
        curses.curs_set(0)
        stdscr.keypad(True)

    def _navigate(self, items, selected, top):
        """Return (selected, top) after one keypress. None → user cancelled."""
        stdscr = self.stdscr
        h, _ = stdscr.getmaxyx()
        visible = h - 3
        while True:
            _curses_draw_list(stdscr, "", items, selected, top, "")
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                selected = max(0, selected - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = min(max(0, len(items) - 1), selected + 1)
            elif key in (curses.KEY_NPAGE,):
                selected = min(len(items) - 1, selected + visible)
            elif key in (curses.KEY_PPAGE,):
                selected = max(0, selected - visible)
            elif key in (curses.KEY_ENTER, 10, 13):
                return selected, top
            elif key in (27, ord("q")):
                return None, top
            if selected < top:
                top = selected
            elif selected >= top + visible:
                top = selected - visible + 1

    def pick(self, items, prompt, header, height):
        stdscr = self.stdscr
        h, _ = stdscr.getmaxyx()
        _curses_safe_addstr(stdscr, 0, 0, header, COLORS["header"])
        stdscr.refresh()
        sel, top = 0, 0
        while True:
            _curses_draw_list(stdscr, header, items, sel, top,
                              "up/k down/j move   enter select   q/Esc back")
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                sel = max(0, sel - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                sel = min(max(0, len(items) - 1), sel + 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                return sel if 0 <= sel < len(items) else None
            elif key in (27, ord("q")):
                return None
            h, _ = stdscr.getmaxyx()
            visible = h - 3
            if sel < top:
                top = sel
            elif sel >= top + visible:
                top = sel - visible + 1

    def pick_query(self, items, prompt, header, height):
        stdscr = self.stdscr
        h, _ = stdscr.getmaxyx()
        buf = ""
        sel, top = 0, 0
        while True:
            _curses_draw_list(stdscr, header, items, sel, top,
                              "type to filter   enter select   empty enter = search   q/Esc back",
                              filter_str=buf)
            _curses_safe_addstr(stdscr, h - 1, 0, prompt)
            stdscr.refresh()
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                sel = max(0, sel - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                sel = min(max(0, len(items) - 1), sel + 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                if 0 <= sel < len(items):
                    return buf, sel
                return buf, None
            elif key in (27, ord("q")):
                return "", None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                buf = buf[:-1]
            elif 32 <= key < 256:
                buf += chr(key)
            h, _ = stdscr.getmaxyx()
            visible = h - 3
            if sel < top:
                top = sel
            elif sel >= top + visible:
                top = sel - visible + 1

    def action(self, header_lines, actions):
        items = [f"{c(C.CYAN + C.BOLD, str(i + 1) + '.')}  {label}  {c(C.DIM, '[' + code + ']')}"
                 for i, (label, code) in enumerate(actions)]
        idx = self.pick(items, "action ❯ ", "\n".join(header_lines) + "\n" + "─" * 44, "55%")
        if idx is None:
            return "q"
        return actions[idx][1]

    def query(self, header_lines):
        stdscr = self.stdscr
        h, _ = stdscr.getmaxyx()
        label = "search ❯ "
        for i, line in enumerate(header_lines[: h - 3]):
            _curses_safe_addstr(stdscr, i, 0, line, COLORS["header"])
        stdscr.refresh()
        text = _curses_line(stdscr, h - 1, label)
        if not text and True:  # Esc on curses line returns '' — distinguish from empty Enter below
            pass
        return text

    def prompt(self, label):
        stdscr = self.stdscr
        h, _ = stdscr.getmaxyx()
        return _curses_line(stdscr, h - 1, label)

    def info(self, lines, wait=True):
        stdscr = self.stdscr
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        y = max(1, h // 2 - len(lines) // 2)
        for i, line in enumerate(lines):
            _curses_safe_addstr(stdscr, y + i, 2, line)
        stdscr.refresh()
        if wait:
            stdscr.getch()


def get_ui(stdscr=None):
    if find_fzf():
        return FzfUI()
    return CursesUI(stdscr)
```

Note: `query()` semantics for CursesUI: returns `''` when the user pressed Enter with empty input (→ history); the ESC key inside `_curses_line` returns `''` too. To match FzfUI (`None` = quit on Esc), the caller in Task 7 treats `None` as quit and `''` as history — so in curses mode Esc must map to `None`. **Fix in this task:** override `query` so Esc returns `None`:

```python
    def query(self, header_lines):
        stdscr = self.stdscr
        h, _ = stdscr.getmaxyx()
        label = "search ❯ "
        for i, line in enumerate(header_lines[: h - 3]):
            _curses_safe_addstr(stdscr, i, 0, line, COLORS["header"])
        stdscr.refresh()
        try:
            curses.echo()
            curses.curs_set(1)
            key = stdscr.getch()
            if key in (27, ord("q")):
                return None
            curses.ungetch(key)
            text = _curses_line(stdscr, h - 1, label)
        finally:
            curses.noecho()
            curses.curs_set(0)
        return text
```

Replace the `query` method shown above with this version.

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 6: Format helpers + player lifecycle + unified IPC watcher

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Write format helpers, kill_player, _ipc_watch, search_with_spinner**

Append:

```python
# ---------- format helpers ----------

def format_duration(seconds):
    if not seconds:
        return "?:??"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_fzf_item(idx, item):
    dur      = format_duration(item.get("duration"))
    uploader = item.get("uploader") or "Unknown"
    title    = item.get("title") or "Untitled"
    when     = ""
    if item.get("played_at"):
        when = f" · {item['played_at'][:16].replace('T', ' ')}"
    return f"{title}  {c(C.DIM, f'[{dur}] {uploader}{when}')}"


# ---------- player lifecycle ----------

def kill_player(proc):
    if proc is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def _ipc_watch(name, done):
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()

    SPINNER       = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    spinner_label = ["Resolving..."]
    spinner_stop  = threading.Event()

    def spin():
        time.sleep(0.15)
        i = 0
        while not spinner_stop.is_set():
            frame = SPINNER[i % len(SPINNER)]
            sys.stdout.write(f"\r  {c(C.CYAN, frame)}  {c(C.DIM, spinner_label[0])}      ")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1

    spin_thread = threading.Thread(target=spin, daemon=True)
    spin_thread.start()

    time.sleep(0.3)  # give mpv time to bind socket

    deadline = time.time() + 60
    sock     = None

    while time.time() < deadline:
        if done.is_set():
            spinner_stop.set()
            return
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(IPC_SOCKET)
            break
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            time.sleep(0.2)
    else:
        spinner_stop.set()
        sys.stdout.write(
            f"\r  {c(C.GREEN, '▶')}  {c(C.BOLD, name)}  {c(C.GREEN, 'playing')}      "
        )
        sys.stdout.flush()
        return

    sock.settimeout(1.0)
    buf = ""
    deadline_evt = time.time() + 60

    try:
        while not done.is_set() and time.time() < deadline_evt:
            try:
                chunk = sock.recv(4096).decode("utf-8", errors="ignore")
            except socket.timeout:
                continue
            if not chunk:
                break

            buf  += chunk
            lines = buf.split("\n")
            buf   = lines[-1]

            for line in lines[:-1]:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event = evt.get("event", "")

                if event == "file-loaded":
                    spinner_label[0] = "Buffering..."

                elif event == "playback-restart":
                    spinner_stop.set()
                    spin_thread.join()
                    sys.stdout.write(
                        f"\r  {c(C.GREEN, '▶')}  {c(C.BOLD, name)}  {c(C.GREEN, 'playing')}      "
                    )
                    sys.stdout.flush()
                    return

                elif event == "end-file":
                    reason = evt.get("reason", "")
                    if reason == "error":
                        spinner_stop.set()
                        spin_thread.join()
                        sys.stdout.write(f"\r  {c(C.RED, '✗  Failed to load')}      \n")
                        sys.stdout.flush()
                        return

        spinner_stop.set()
        spin_thread.join()
        sys.stdout.write(
            f"\r  {c(C.GREEN, '▶')}  {c(C.BOLD, name)}  {c(C.GREEN, 'playing')}      "
        )
        sys.stdout.flush()
    except Exception:
        pass
    finally:
        spinner_stop.set()
        try:
            sock.close()
        except Exception:
            pass


def search_with_spinner(query, count):
    sp = Spinner(f"Searching '{query}'...")
    sp.start()
    try:
        return asyncio.run(_search_ytdlp(query, count))
    finally:
        sp.stop()
```

Note: `_search_ytdlp` is defined in Task 7.

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 7: Search mode — yt-dlp search + run_search_mode

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Port the yt-dlp search**

Append (port `_search` from `/tmp/opencode/backup/yt-cli.bak:91-126` verbatim, renamed `_search_ytdlp`):

```python
# ---------- MODE: SEARCH ----------

async def _search_ytdlp(query, count):
    cmd = ["yt-dlp", f"ytsearch{count}:{query}", "--flat-playlist", "-j", "--no-warnings"]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout_data = await proc.stdout.read()
    await proc.wait()
    if proc.returncode != 0:
        stderr_data = (await proc.stderr.read()).decode(errors="replace").strip()
        last_line = stderr_data.splitlines()[-1] if stderr_data else "yt-dlp search failed"
        raise SearchError(last_line)

    results = []
    for line in stdout_data.decode(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = entry.get("id")
        url = entry.get("webpage_url") or (
            f"https://www.youtube.com/watch?v={vid}" if vid else entry.get("url")
        )
        if not url:
            continue
        results.append(
            {
                "title": entry.get("title") or "Untitled",
                "uploader": entry.get("uploader") or entry.get("channel") or "Unknown",
                "duration": entry.get("duration"),
                "url": url,
            }
        )
    return results
```

- [ ] **Step 2: Write run_search_mode**

Append:

```python
def run_search_mode(cfg, ui, stop, play, return_home=False,
                    initial_items=None, initial_header=None):
    """Full search mode. return_home=True: after 'q' return to caller instead of the list."""
    items = initial_items or []
    header = initial_header or "yt-cli — history"

    # If called from main menu with no initial list, start with the query stage.
    if initial_items is None:
        while True:
            query = ui.query(["yt-cli — search YouTube",
                              "Esc → quit   ·   empty Enter → history"])
            if query is None:
                return
            if query.strip():
                clear_screen()
                print(f"  {c(C.DIM, 'Searching...')}\n")
                try:
                    items = search_with_spinner(query, cfg["result_count"])
                except SearchError as e:
                    if ui.action([f"search failed: {e}", "─" * 44],
                                 [("retry search", "r"), ("back", "b")]) != "r":
                        return
                    continue
                if not items:
                    if ui.action([f'No results for "{query}"', "─" * 44],
                                 [("retry search", "r"), ("back", "b")]) != "r":
                        return
                    continue
                header = f"yt-cli — results: {query} ({len(items)})"
            else:
                items = load_json(HISTORY_PATH, [])
                header = "yt-cli — history"
                if not items:
                    ui.info(["No history yet"])
                    return
            break

    item = None
    while True:
        if item is not None:
            header_lines = [
                f"▶  {c(C.BOLD, (item.get('title') or item.get('url') or '')[:60])}",
                f"   {c(C.DIM, 'Uploader')} : {item.get('uploader') or 'Unknown'}   "
                f"{c(C.DIM, 'Length')} : {format_duration(item.get('duration'))}",
            ]
            if PLAYER_PROC[0] is not None and PLAYER_PROC[0].poll() is not None:
                header_lines.append(f"   {c(C.YELLOW, '⚠  Player exited.')}")
            action = ui.action(header_lines, PLAY_ACTIONS_SEARCH)
            if action == "r":
                play(item)
            elif action == "o":
                break          # back to results picker, player keeps playing
            elif action == "h":
                items = load_json(HISTORY_PATH, [])
                header = "yt-cli — history"
                if not items:
                    ui.info(["No history yet"])
                continue
            elif action == "s":
                return run_search_mode(cfg, ui, stop, play, return_home=return_home)
            elif action == "q":
                stop()
                item = None
                if return_home:
                    return
                break
            elif action == "x":
                stop()
                print(f"\n  {c(C.DIM, 'Goodbye! 👋')}\n")
                sys.exit(0)
            continue
        lines = [format_fzf_item(i + 1, it) for i, it in enumerate(items)]
        idx = ui.pick(lines, "result ❯ ", header, "70%")
        if idx is None or idx >= len(items):
            if PLAYER_PROC[0] is not None:
                continue       # Esc while playing → return to action menu
            return            # Esc with no player → exit search mode
        item = items[idx]
        play(item)


PLAYER_PROC = [None]
IPC_DONE = [None]
```

- [ ] **Step 3: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 8: History screen + action tables

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Write run_history_screen + action tables**

Append:

```python
# ---------- unified action tables ----------

PLAY_ACTIONS_SEARCH = [
    ("other results", "o"),
    ("new search",    "s"),
    ("history",       "h"),
    ("replay video",  "r"),
    ("stop player",   "q"),
    ("exit",          "x"),
]

PLAY_ACTIONS_RADIO = [
    ("other presets", "o"),
    ("search",        "s"),
    ("history",       "h"),
    ("replay",        "r"),
    ("stop player",   "q"),
    ("exit",          "x"),
    ("toggle video",  "v"),
    ("toggle loop",   "l"),
    ("manage presets","m"),
]

PLAY_ACTIONS_NEWS = [
    ("other streams", "o"),
    ("search",        "s"),
    ("history",       "h"),
    ("replay",        "r"),
    ("stop player",   "q"),
    ("exit",          "x"),
    ("change player", "p"),
    ("refresh streams", "f"),
]


def run_history_screen(cfg, ui, stop, play):
    items = load_json(HISTORY_PATH, [])
    if not items:
        ui.info(["No history yet"])
        return
    run_search_mode(cfg, ui, stop, play, return_home=True,
                    initial_items=items, initial_header="yt-cli — history")
```

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 9: Orchestration — run_fzf, main menu, signal handlers

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Write run_fzf + main menu**

Append:

```python
# ---------- orchestration ----------

def stop_shared():
    if IPC_DONE[0] is not None:
        IPC_DONE[0].set()
        IPC_DONE[0] = None
    kill_player(PLAYER_PROC[0])
    PLAYER_PROC[0] = None


def _search_play_default(cfg):
    """mpv launcher for search-mode play (used by search-from-anywhere)."""
    def play_mpv(item):
        stop_shared()
        add_to_history(item, cfg["history_size"])
        PLAYER_PROC[0] = subprocess.Popen(["mpv", *cfg["mpv_args"], item["url"]],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          start_new_session=True)
        done = threading.Event()
        IPC_DONE[0] = done
        threading.Thread(target=_ipc_watch,
                         args=(item.get("title") or item.get("url"), done),
                         daemon=True).start()
    return play_mpv


def run_fzf(cfg, mode=None):
    ui = get_ui()
    play_mpv = _search_play_default(cfg)

    def goodbye():
        stop_shared()
        print(f"\n  {c(C.DIM, 'Goodbye! 👋')}\n")

    def _clean_exit(sig, frame):
        stop_shared()
        sys.exit(0)

    signal.signal(signal.SIGHUP, _clean_exit)
    signal.signal(signal.SIGTERM, _clean_exit)

    if mode == "radio":
        run_radio_mode(cfg, ui, stop_shared, play_mpv, goodbye)
        return
    if mode == "news":
        run_news_mode(cfg, ui, stop_shared, goodbye)
        return

    while True:
        clear_screen()
        idx = ui.pick(["Search", "Radio", "News", "History", "Exit"],
                      "main ❯ ", "yt-cli  —  unified search / radio / news", "50%")
        if idx is None or (idx < len(("a",) * 5) and
                           ["Search", "Radio", "News", "History", "Exit"][idx] == "Exit"):
            goodbye()
            return
        choice = ["Search", "Radio", "News", "History", "Exit"][idx]
        if choice == "Search":
            run_search_mode(cfg, ui, stop_shared, play_mpv, return_home=True)
        elif choice == "Radio":
            run_radio_mode(cfg, ui, stop_shared, play_mpv, goodbye)
        elif choice == "News":
            run_news_mode(cfg, ui, stop_shared, goodbye)
        elif choice == "History":
            run_history_screen(cfg, ui, stop_shared, play_mpv)
```

Note: the `idx < len(...) * 5` guard above is awkward; replace the whole menu-selection block with the cleaner version:

```python
    while True:
        clear_screen()
        entries = ["Search", "Radio", "News", "History", "Exit"]
        idx = ui.pick(entries, "main ❯ ", "yt-cli  —  unified search / radio / news", "50%")
        if idx is None:
            goodbye()
            return
        choice = entries[idx]
        if choice == "Exit":
            goodbye()
            return
        if choice == "Search":
            run_search_mode(cfg, ui, stop_shared, play_mpv, return_home=True)
        elif choice == "Radio":
            run_radio_mode(cfg, ui, stop_shared, play_mpv, goodbye)
        elif choice == "News":
            run_news_mode(cfg, ui, stop_shared, goodbye)
        elif choice == "History":
            run_history_screen(cfg, ui, stop_shared, play_mpv)
```

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 10: Radio config (JSON-backed helpers)

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Write radio config loaders + JSON mutation helpers**

Append (adapts tune's `get_presets`/`get_settings`/`config_*` from `/tmp/opencode/backup/tune.bak:407-517` to operate on the merged JSON):

```python
# ---------- MODE: RADIO ----------

def load_radio_cfg(cfg):
    r = cfg["radio"]
    presets = {}
    for p in r["presets"]:
        if not p.get("enabled", True):
            continue
        presets[p["slug"]] = {
            "name": p.get("name", p["slug"]),
            "url": p.get("url", ""),
            "category": p.get("category", "Uncategorized"),
            "loop": p.get("loop", False),
            "no_video": p.get("no_video", False),
            "radio": p.get("radio", False),
            "enabled": True,
            "is_search_result": False,
        }
    settings = {
        "player": r.get("player", "mpv"),
        "no_video": r.get("no_video", False),
        "loop": r.get("loop", False),
        "background": r.get("background", False),
    }
    return settings, presets


def _slugify(name):
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:40]


def radio_add_preset(cfg, name, url, category="Search", loop=False, no_video=False, radio=False):
    slug = _slugify(name)
    existing = [p["slug"] for p in cfg["radio"]["presets"]]
    base = slug
    i = 1
    while slug in existing:
        slug = f"{base}-{i}"
        i += 1
    cfg["radio"]["presets"].append({
        "slug": slug, "name": name, "url": url, "category": category,
        "loop": loop, "no_video": no_video, "radio": radio, "enabled": True,
    })
    save_config(cfg)
    return slug


def radio_delete_preset(cfg, slug):
    cfg["radio"]["presets"] = [p for p in cfg["radio"]["presets"] if p["slug"] != slug]
    save_config(cfg)


def radio_toggle_preset(cfg, slug, enable):
    for p in cfg["radio"]["presets"]:
        if p["slug"] == slug:
            p["enabled"] = enable
    save_config(cfg)


def get_all_presets_from_config(cfg):
    return [dict(p) for p in cfg["radio"]["presets"]]
```

- [ ] **Step 2: Verify compile + a loader round-trip**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`

```bash
cd /tmp/opencode && python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('y', '/home/kenneth/.local/bin/yt-cli')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
cfg = dict(m.DEFAULT_CONFIG)
cfg['radio']['presets'] = [{'slug':'lofi','name':'Lo-Fi','url':'x','category':'Chill','loop':False,'no_video':False,'radio':False,'enabled':True}]
s, p = m.load_radio_cfg(cfg)
print('settings:', s)
print('presets:', sorted(p.keys()))
"
```

Expected: settings dict prints; `presets: ['lofi']`.

---

### Task 11: Radio playback builders + pickers (UI-agnostic)

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Port the radio playback builders**

Append (ported from `/tmp/opencode/backup/tune.bak:695-734` verbatim — `build_radio_url`, `build_mpv_cmd`, `launch_player`):

```python
def build_radio_url(url):
    video_id = re.search(r"(?:v=)([^&]+)", url)
    list_id  = re.search(r"(?:list=)([^&]+)", url)
    if not video_id:
        return url
    vid        = video_id.group(1)
    lst        = list_id.group(1) if list_id else ""
    final_list = f"RD{vid}" if (lst.startswith("RDAT") or not lst) else lst
    return f"https://www.youtube.com/watch?v={vid}&list={final_list}"


def build_mpv_cmd(url, settings, preset_overrides=None):
    p        = preset_overrides or {}
    no_video = p.get("no_video", settings.get("no_video", False))
    loop     = p.get("loop",     settings.get("loop",     False))
    radio    = p.get("radio",    False)
    final_url = build_radio_url(url) if radio else url
    cmd = ["mpv"]
    if no_video:
        cmd += ["--no-video", "--force-window=no", "--ytdl-format=bestaudio/best"]
    if loop:
        cmd += ["--loop-playlist"]
    cmd.append(final_url)
    return cmd


def launch_player(cmd):
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
```

- [ ] **Step 2: Port the YouTube Music search + result picker**

Append (port verbatim from `/tmp/opencode/backup/tune.bak:519-691` — `_fetch`, `_extract_initial_data`, `_text`, `_parse_ytm_results`, `_parse_yt_results`, `_fetch_ytm`, `search_music` — and TYPE_COLORS from `/tmp/opencode/backup/tune.bak:821-830`).

Then write the result picker via the UI abstraction (replaces tune's `pick_search_result` from `/tmp/opencode/backup/tune.bak:832-877`):

```python
def pick_search_result(results, query, ui):
    if not results:
        action = ui.action([f'No results for "{query}"', "─" * 44],
                           [("retry search", "r"), ("back", "b")])
        return "retry" if action == "r" else None
    lines = []
    for i, r in enumerate(results, 1):
        tcolor = TYPE_COLORS.get(r["type_label"], C.DIM)
        lines.append(f"{c(tcolor, '[' + r['type_label'] + ']')}  {r['title']}  {c(C.DIM, r['source'])}")
    idx = ui.pick(lines, "result ❯ ",
                  f"Search: {c(C.BOLD, query)}\n{'─' * 48}\nEsc → back", "70%")
    if idx is None:
        return None
    return results[idx]
```

- [ ] **Step 3: Write the preset picker via the UI abstraction**

Append (replaces tune's `pick_preset` from `/tmp/opencode/backup/tune.bak:881-949`):

```python
def pick_preset(presets, now_playing_name, ui):
    categories = {}
    for slug, info in presets.items():
        categories.setdefault(info["category"], []).append((slug, info))

    lines     = []
    index_map = []
    for cat, items in categories.items():
        for slug, info in items:
            marker = f"{C.PLAY}" if info["name"] == now_playing_name else "  "
            badge  = (f"  🔍" if info.get("is_search_result")
                      else f"  {c(C.DIM, '[' + info['category'] + ']')}")
            lines.append(f"  {marker}  {info['name']}{badge}")
            index_map.append((slug, info))
    MANAGE = "manage presets"
    lines.append(f"  ⚙   {MANAGE}")
    header = ("yt-cli  —  radio: select a preset or type to search\n"
              "─" * 48 + "\n"
              "Esc → quit   ·   type a query to search YouTube Music")
    query, idx = ui.pick_query(lines, "preset ❯ ", header, "70%")
    if idx is not None:
        if idx < len(index_map):
            return index_map[idx]
        if idx == len(index_map):
            return ("manage", None)
    if query.strip().lower() in ("manage presets", "manage", "m"):
        return ("manage", None)
    if query.strip():
        q = query.strip()
        return ("url", q) if q.startswith(("http://", "https://")) else ("search", q)
    return None
```

- [ ] **Step 4: Port manage-presets screen (UI-agnostic)**

Append (adapts tune's `manage_presets_screen` from `/tmp/opencode/backup/tune.bak:1067-1153`; drop the fzf/text branches and route through `ui.pick`/`ui.action`/`ui.info`; use the JSON helpers from Task 10):

```python
def manage_presets_screen(cfg, ui):
    while True:
        all_presets = get_all_presets_from_config(cfg)
        if not all_presets:
            ui.info(["No presets defined."])
            break

        lines = []
        index_map = []
        for p in all_presets:
            status = c(C.GREEN, "●") if p["enabled"] else c(C.DIM, "○")
            lines.append(f"{status}  {p['name']}  {c(C.DIM, '[' + p['category'] + ']')}  {c(C.DIM, p['slug'])}")
            index_map.append(p)
        idx = ui.pick(lines, "preset ❯ ", "Manage presets\n─" * 44 + "\nEsc → back", "70%")
        if idx is None:
            break
        target = index_map[idx]

        toggle_label = "disable" if target["enabled"] else "enable"
        action = ui.action([f"{'●' if target['enabled'] else '○'}  {target['name']}  "
                            f"[{target['category']}]  [{target['slug']}]", "─" * 44],
                           [(toggle_label, toggle_label), ("delete", "delete"), ("back", "back")])
        if action == "back":
            continue
        if action == "delete":
            confirmed = ui.action([f'Delete "{target["name"]}"?', "─" * 44],
                                  [("yes, delete", "yes"), ("no, keep", "no")]) == "yes"
            if confirmed:
                radio_delete_preset(cfg, target["slug"])
                ui.info([f"✓  Deleted {target['name']}"], wait=False)
        elif action in ("enable", "disable"):
            radio_toggle_preset(cfg, target["slug"], enable=(action == "enable"))
            ui.info([f"✓  {target['name']} is now {action}d"], wait=False)

    return load_radio_cfg(load_config())[1]
```

- [ ] **Step 5: Port save+symlink flow (UI-agnostic)**

Append (adapts tune's `prompt_save_search_result` from `/tmp/opencode/backup/tune.bak:1007-1063`; route through `ui.*`; use `radio_add_preset`):

```python
def _find_bin_dir():
    d = Path.home() / ".local" / "bin" / "music"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prompt_save_search_result(cfg, result, ui):
    title = result["title"]
    action = ui.action([f'Save "{title}" as a preset?', "─" * 48],
                       [("yes, save as preset", "yes"), ("no, discard", "no")])
    if action != "yes":
        return None

    ui.info([f'Save "{title}"', "", "Display name (Enter to use title):"], wait=False)
    name = ui.prompt("name") or title
    category = ui.prompt("category") or "Search"

    slug = radio_add_preset(cfg, name, result["url"], category=category)
    ui.info([f"✓  Saved as [preset.{slug}]"], wait=False)

    sym_path = _find_bin_dir() / slug
    create = ui.action([f'Create symlink "{name}"?', f"  {sym_path}  →  yt-cli {slug}", "─" * 48],
                       [("yes — create symlink", "yes"), ("no, skip", "no")]) == "yes"
    if create:
        tune_path = Path(sys.argv[0]).resolve()
        try:
            if sym_path.exists() or sym_path.is_symlink():
                sym_path.unlink()
            sym_path.symlink_to(tune_path)
            ui.info([f"✓  {sym_path}"], wait=False)
        except OSError as e:
            ui.info([f"⚠  Symlink failed: {e}"], wait=False)
    time.sleep(0.8)
    return slug
```

- [ ] **Step 6: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 12: Radio mode — run_radio_mode + playing loop

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Write run_radio_mode + radio_playing_loop**

Append (adapts tune's `run_interactive` + `_playing_loop` from `/tmp/opencode/backup/tune.bak:1155-1273`; the old `use_fzf` branches are gone — everything routes through `ui`):

```python
def run_radio_mode(cfg, ui, stop, play_mpv, goodbye):
    settings, presets = load_radio_cfg(cfg)
    now_playing = [None]
    search_cache = {}

    def play_radio(info):
        stop()
        cmd = build_mpv_cmd(info["url"], settings, preset_overrides=info)
        PLAYER_PROC[0] = launch_player(cmd)
        now_playing[0] = info
        done = threading.Event()
        IPC_DONE[0] = done
        threading.Thread(target=_ipc_watch, args=(info["name"], done),
                         daemon=True).start()
        add_to_history({"title": info["name"], "url": info["url"],
                        "uploader": info["category"]}, cfg["history_size"])

    while True:
        np_name = now_playing[0]["name"] if now_playing[0] else None
        merged = dict(presets)
        merged.update(search_cache)
        result = pick_preset(merged, np_name, ui)

        if result is None:
            goodbye()
            return
        if result == "invalid":
            continue

        if isinstance(result, tuple) and result[0] == "manage":
            presets = manage_presets_screen(cfg, ui)
            continue

        if isinstance(result, tuple) and result[0] == "url":
            info = {"name": result[1][:60], "url": result[1], "loop": settings["loop"],
                    "no_video": settings["no_video"], "radio": False, "is_search_result": False}
            radio_playing_loop(cfg, ui, info, settings, stop, play_radio, goodbye)
            continue

        if isinstance(result, tuple) and result[0] == "search":
            query = result[1]
            while True:
                clear_screen()
                print(f"  {c(C.DIM, '🔍 Searching YouTube Music + YouTube...')}\n")
                search_results = search_music(query)
                picked = pick_search_result(search_results, query, ui)
                if picked is None:
                    break
                if picked == "retry":
                    continue
                slug = _slugify(picked["title"])
                info = {"name": picked["title"], "url": picked["url"], "category": "Search",
                        "loop": settings["loop"], "no_video": settings["no_video"],
                        "radio": False, "is_search_result": True, "_search_raw": picked}
                search_cache[slug] = info
                play_radio(info)
                stopped = radio_playing_loop(cfg, ui, info, settings, stop,
                                             play_radio, goodbye)
                if stopped:
                    new_slug = prompt_save_search_result(cfg, picked, ui)
                    if new_slug:
                        cfg = load_config()
                        settings, presets = load_radio_cfg(cfg)
                        search_cache.pop(slug, None)
                break
            continue

        slug, info = result
        play_radio(info)
        radio_playing_loop(cfg, ui, info, settings, stop, play_radio, goodbye)


def radio_playing_loop(cfg, ui, info, settings, stop_fn, play_fn, goodbye_fn):
    while True:
        header_lines = [
            f"▶  {c(C.BOLD, info['name'])}",
            f"   {c(C.DIM, 'Video')} : "
            f"{c(C.DIM, 'suppressed') if settings['no_video'] else c(C.GREEN, 'on')}   "
            f"{c(C.DIM, 'Loop')} : "
            f"{c(C.GREEN, 'on') if settings['loop'] else c(C.DIM, 'off')}",
        ]
        if PLAYER_PROC[0] is not None and PLAYER_PROC[0].poll() is not None:
            header_lines.append(f"   {c(C.YELLOW, '⚠  Player exited.')}")
        action = ui.action(header_lines, PLAY_ACTIONS_RADIO)
        if action == "o":
            return False                     # back to preset list, keep playing
        elif action == "v":
            settings["no_video"] = not settings["no_video"]
            info["no_video"]     = settings["no_video"]
            cfg["radio"]["no_video"] = settings["no_video"]
            save_config(cfg)
            play_fn(info)
        elif action == "l":
            settings["loop"] = not settings["loop"]
            info["loop"]     = settings["loop"]
            cfg["radio"]["loop"] = settings["loop"]
            save_config(cfg)
            play_fn(info)
        elif action == "m":
            manage_presets_screen(cfg, ui)
        elif action == "s":
            run_search_mode(cfg, ui, stop_fn, play_fn, return_home=True)
        elif action == "h":
            run_history_screen(cfg, ui, stop_fn, play_fn)
        elif action == "q":
            stop_fn()
            return True
        elif action == "x":
            goodbye_fn()
            sys.exit(0)
```

Note: search-from-radio uses `play_fn` (the radio player) so no_video/loop settings apply to search results. `run_history_screen` uses `play_fn` too (radio plays history entries).

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 13: News config (JSON-backed helpers)

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Write news config loaders + JSON mutation helpers**

Append (adapts news's `get_sources`/settings from `/tmp/opencode/backup/news-stream.bak:210-227` and the config write helpers from `/tmp/opencode/backup/news-stream.bak:1001-1175` to operate on merged JSON):

```python
# ---------- MODE: NEWS ----------

def load_news_cfg(cfg):
    n = cfg["news"]
    sources = {}
    for s in n["sources"]:
        if not s.get("enabled", True):
            continue
        sources[s["id"]] = {
            "name": s.get("name", s["id"]),
            "channel_id": s.get("channel_id"),
            "keywords": s.get("keywords", [s["id"]]),
            "fallback_url": s.get("fallback_url", ""),
            "enabled": True,
        }
    settings = {
        "player": n.get("player", "auto"),
        "quality": n.get("quality", "720"),
        "timeout": n.get("timeout", 15),
        "recent_hours": n.get("recent_hours", 24),
    }
    return settings, sources


def news_add_source(cfg, name, keywords, channel_id=None, fallback_url=None):
    slug = _slugify(name)
    existing = [s["id"] for s in cfg["news"]["sources"]]
    base = slug
    i = 1
    while slug in existing:
        slug = f"{base}-{i}"
        i += 1
    cfg["news"]["sources"].append({
        "id": slug, "name": name,
        "channel_id": channel_id or "",
        "keywords": keywords or [name.lower()],
        "fallback_url": fallback_url or f"https://www.youtube.com/results?search_query={urllib.parse.quote(name)}",
        "enabled": True,
    })
    save_config(cfg)
    return slug


def news_delete_source(cfg, sid):
    cfg["news"]["sources"] = [s for s in cfg["news"]["sources"] if s["id"] != sid]
    save_config(cfg)


def news_toggle_source(cfg, sid, enable):
    for s in cfg["news"]["sources"]:
        if s["id"] == sid:
            s["enabled"] = enable
    save_config(cfg)


def get_all_sources_from_config(cfg):
    return [dict(s) for s in cfg["news"]["sources"]]
```

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 14: News scraping + playback port

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Port the scraping + playback code**

Append (port verbatim from `/tmp/opencode/backup/news-stream.bak`): `fetch_url` (233-249), `extract_yt_initial_data` (255-268), `parse_relative_time` (271-289), `parse_video_renderer` (292-355), `find_streams` (358-416), `search_youtube` (419-470), `get_direct_stream_url` (476-497), `detect_player` (500-507), `list_players` (510-519), `launch_player` (522-543).

Note: `kill_player` is already shared (Task 6); news's local copy is dropped.

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 15: News mode — pickers + run_news_mode (UI-agnostic)

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Port rendering + pickers via UI abstraction**

Append (adapts news's pickers from `/tmp/opencode/backup/news-stream.bak`; drop the fzf/text branches and route through `ui`):
- `render_stream_badge` (677-687) verbatim.
- `render_header` (690-701) verbatim.
- `pick_source` (707-787) — via `ui.pick_query`:

```python
def pick_source(sources, ui):
    source_list = list(sources.items())
    lines = [info['name'] for key, info in source_list]
    MANAGE = "manage sources"
    lines.append(f"⚙   {MANAGE}")
    header = ("yt-cli  —  news: select a source or type to search YouTube\n"
              "─" * 48 + "\n"
              "Esc → quit")
    query, idx = ui.pick_query(lines, "source ❯ ", header, "60%")
    if idx is not None:
        if idx < len(source_list):
            return source_list[idx]
        if idx == len(source_list):
            return ("manage", None)
    if query.strip().lower() in ("manage sources", "manage", "m"):
        return ("manage", None)
    if query.strip():
        return ("search", query.strip())
    return None
```

- `pick_stream` (790-871) — via `ui.pick` + `ui.action`:

```python
def pick_stream(streams, source_name, ui):
    if not streams:
        action = ui.action([f"{source_name}  —  no streams found", "─" * 40],
                           [("retry", "r"), ("back", "b"), ("quit", "q")])
        if action == "r":
            return "retry"
        return None
    lines = []
    for stream in streams:
        badge = render_stream_badge(stream)
        views = f"  {c(C.DIM, '👥 ' + stream['view_count'])}" if stream["view_count"] else ""
        lines.append(f"{badge}  {stream['title']}{views}")
    idx = ui.pick(lines, "stream ❯ ",
                  f"{source_name}  —  {len(streams)} stream(s) found\n{'─' * 40}\nEsc → back",
                  "60%")
    if idx is None:
        return None
    return streams[idx]
```

- `pick_player` (946-973) — via `ui.pick`:

```python
def pick_player(current_player, ui):
    players = list_players()
    if not players:
        return None
    lines = [f"{'▶ ' if p == current_player else '  '}{p}" for p in players]
    idx = ui.pick(lines, "player ❯ ", "Select player\n─" * 44, "30%")
    if idx is None:
        return None
    return players[idx]
```

- `confirm_search` (1052-1071) — via `ui.action`:

```python
def confirm_search(query, ui):
    action = ui.action([f"Search YouTube for:  {c(C.BOLD + C.CYAN, query)}", "─" * 44],
                       [("yes, search", "yes"), ("no, go back", "no")])
    return action == "yes"
```

- `prompt_save_source` (1077-1125) — via `ui.action`/`ui.prompt`/`ui.info`; use `news_add_source`:

```python
def prompt_save_source(cfg, query, ui):
    action = ui.action([f'Save "{query}" as a permanent source?', "─" * 44],
                       [("yes, save to config", "yes"), ("no, keep temporary", "no")])
    if action != "yes":
        return False
    ui.info([f'Save "{query}"', "", "Display name (Enter to use query):"], wait=False)
    name = ui.prompt("name") or query
    ui.info([f'Keywords, comma-separated (Enter to use name as keyword):'], wait=False)
    kw_raw = ui.prompt("keywords")
    keywords = [k.strip().lower() for k in kw_raw.split(",")] if kw_raw else [name.lower()]
    slug = news_add_source(cfg, name, keywords)
    ui.info([f"✓  Saved as [source.{slug}]"], wait=False)
    time.sleep(0.8)
    return True
```

- `manage_sources_screen` (1178-1310) — via `ui.pick`/`ui.action`/`ui.info`; use the JSON helpers:

```python
def manage_sources_screen(cfg, ui):
    while True:
        all_sources = get_all_sources_from_config(cfg)
        if not all_sources:
            ui.info(["No sources defined."])
            break
        lines = []
        index_map = []
        for s in all_sources:
            status = c(C.GREEN, "●") if s["enabled"] else c(C.DIM, "○")
            lines.append(f"{status}  {s['name']}  {c(C.DIM, '[' + s['slug'] + ']')}")
            index_map.append(s)
        idx = ui.pick(lines, "source ❯ ", "Manage sources\n─" * 44 + "\nEsc → back", "60%")
        if idx is None:
            break
        target = index_map[idx]

        toggle_label = "disable" if target["enabled"] else "enable"
        action = ui.action([f"{'●' if target['enabled'] else '○'}  {target['name']}  [{target['slug']}]", "─" * 44],
                           [(toggle_label, toggle_label), ("delete", "delete"), ("back", "back")])
        if action == "back":
            continue
        if action == "delete":
            confirmed = ui.action([f'Delete "{target["name"]}"?', "─" * 44],
                                  [("yes, delete", "yes"), ("no, keep", "no")]) == "yes"
            if confirmed:
                news_delete_source(cfg, target["slug"])
                ui.info([f"✓  Deleted {target['name']}"], wait=False)
        elif action in ("enable", "disable"):
            news_toggle_source(cfg, target["slug"], enable=(action == "enable"))
            ui.info([f"✓  {target['name']} is now {action}d"], wait=False)

    return load_news_cfg(load_config())[1]
```

- [ ] **Step 2: Write run_news_mode**

Append (adapts news's `run_interactive` from `/tmp/opencode/backup/news-stream.bak:1316-1516`; uses shared `PLAYER_PROC`/`IPC_DONE`, adds the IPC watcher to play, and maps the unified action codes):

```python
def run_news_mode(cfg, ui, stop, goodbye):
    settings, sources = load_news_cfg(cfg)
    current_stream = [None]
    player = [settings["player"]]

    def resolve_player():
        p = player[0]
        if p == "auto":
            p = detect_player()
        return p

    def play_news(stream):
        stop()
        clear_screen()
        sp = Spinner('Tuning in to stream...')
        sp.start()
        direct_url = get_direct_stream_url(stream['url'], settings['quality'])
        if direct_url:
            sp.done('On air')
        else:
            sp.fail('Resolving failed — using direct URL')
        url = direct_url or stream['url']
        PLAYER_PROC[0] = launch_player(url, resolve_player(), settings['quality'])
        current_stream[0] = stream
        done = threading.Event()
        IPC_DONE[0] = done
        threading.Thread(target=_ipc_watch,
                         args=(stream['title'], done), daemon=True).start()
        add_to_history({"title": stream['title'], "url": stream['url'],
                        "uploader": stream['source']}, cfg['history_size'])

    while True:
        result = pick_source(sources, ui)

        if result is None:
            goodbye()
            return
        if result == "invalid":
            continue
        if result == ("manage", None):
            sources = manage_sources_screen(cfg, ui)
            continue

        if isinstance(result, tuple) and result[0] == "search":
            query = result[1]
            if not confirm_search(query, ui):
                continue
            source_info = {"name": query, "channel_id": None,
                           "keywords": [w.lower() for w in query.split()],
                           "fallback_url": f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"}
            is_search_result = True
        else:
            source_key, source_info = result
            is_search_result = False

        while True:
            clear_screen()
            sp = Spinner('Scanning for live streams...')
            sp.start()
            cached_streams = search_youtube(
                source_info["name"], source_info["keywords"],
                source_info.get("channel_id"),
                settings["timeout"], settings["recent_hours"],
            )
            if cached_streams:
                sp.done(f"Found {len(cached_streams)} stream(s)")
            else:
                sp.fail("No streams found")
            print()

            go_back_to_main = False

            while True:
                result = pick_stream(cached_streams, source_info["name"], ui)
                if result is None:
                    go_back_to_main = True
                    break
                if result == "retry":
                    break
                if result == "invalid":
                    continue

                chosen = result
                pl = resolve_player()
                if not pl:
                    ui.info(["❌ No player found (mpv / vlc / ffplay)."])
                    go_back_to_main = True
                    break

                play_news(chosen)

                go_to_main_from_playing = False
                refetch_from_playing = False
                stopped = False

                while True:
                    stream = current_stream[0]
                    status = C.LIVE if stream["is_live"] else render_stream_badge(stream)
                    header_lines = [
                        f"▶  {c(C.BOLD, stream['title'])}",
                        f"   {c(C.DIM, 'Source')}  : {stream['source']}",
                        f"   {c(C.DIM, 'Status')}  : {status}",
                        f"   {c(C.DIM, 'Quality')} : {settings['quality']}p  "
                        f"{c(C.DIM, 'Player')} : {pl}",
                    ]
                    if PLAYER_PROC[0] is not None and PLAYER_PROC[0].poll() is not None:
                        header_lines.append(
                            f"   {c(C.YELLOW, '⚠  Player exited — stream may have ended.')}")
                    action = ui.action(header_lines, PLAY_ACTIONS_NEWS)

                    if action == "o":
                        break          # stream list, player keeps playing
                    elif action == "s":
                        run_search_mode(cfg, ui, stop, _search_play_default(cfg), return_home=True)
                    elif action == "h":
                        run_history_screen(cfg, ui, stop, _search_play_default(cfg))
                    elif action == "r":
                        play_news(stream)
                    elif action == "f":
                        stop()
                        refetch_from_playing = True
                        break
                    elif action == "p":
                        new_player = pick_player(pl, ui)
                        if new_player and new_player != pl:
                            player[0] = new_player
                            cfg["news"]["player"] = new_player
                            save_config(cfg)
                            play_news(stream)
                    elif action == "q":
                        stop()
                        stopped = True
                        break
                    elif action == "x":
                        goodbye()
                        return

                if is_search_result and (go_to_main_from_playing or stopped):
                    saved = prompt_save_source(cfg, source_info["name"], ui)
                    if saved:
                        cfg = load_config()
                        settings, sources = load_news_cfg(cfg)
                        is_search_result = False

                if go_to_main_from_playing:
                    go_back_to_main = True
                    break
                if refetch_from_playing:
                    break

            if go_back_to_main:
                break
```

- [ ] **Step 3: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 16: News CLI mode + entry dispatch

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (append)

- [ ] **Step 1: Port news CLI helpers**

Append (port from `/tmp/opencode/backup/news-stream.bak:1522-1548` and `1551-1581` — `play_stream_cli`, `print_news_help`, `edit_news_config`):

```python
def play_stream_cli(url, player=None, quality="720"):
    if not player or player == "auto":
        player = detect_player()
        if not player:
            print("❌ No suitable player found (mpv, vlc, or ffplay)")
            return False
    print(f"▶️  Playing with {player}...")
    try:
        if player == "mpv":
            cmd = ["mpv", "--fs", "--really-quiet",
                   f"--ytdl-format=best[height<={quality}]/best", url]
        elif player == "vlc":
            cmd = ["vlc", url, "--fullscreen"]
        elif player == "ffplay":
            cmd = ["ffplay", "-fs", "-window_title", "News Stream", url]
        else:
            cmd = [player, url]
        subprocess.run(cmd)
        return True
    except Exception as e:
        print(f"❌ Error playing stream: {e}")
        return False


def print_news_help(sources):
    print(f"""
📺 NEWS: Filipino News Live Stream Finder (yt-cli)

CONFIG: {CONFIG_PATH}

USAGE:
    yt-cli                         Main menu
    news-stream                    Interactive TUI mode
    news-stream <source> [player]  Play directly (CLI mode)
    news-stream --list
    news-stream --config
    news-stream --help

SUPPORTED SOURCES:""")
    for key, info in sorted(sources.items()):
        print(f"    {'✓' if info['enabled'] else '✗'}  {key:<25} - {info['name']}")
    print(f"""
PLAYERS:
    auto  mpv  vlc  ffplay

EXAMPLES:
    news-stream
    news-stream tv-patrol
    news-stream 24-oras vlc
""")


def edit_news_config():
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(CONFIG_PATH)])
```

- [ ] **Step 2: Port radio CLI helpers**

Append (port from `/tmp/opencode/backup/tune.bak:1277-1355` — `resolve_preset`, `parse_cli_flags`, `cli_play`, `print_radio_help`):

```python
def resolve_preset(query, presets):
    q = query.lower().strip()
    aliases = {"lp": "linkin-park", "bb": "breaking-benjamin", "mcr": "mcr",
               "eheads": "eraserheads", "bfmv": "bfmv", "a7x": "a7x"}
    if q in aliases and aliases[q] in presets:
        return presets[aliases[q]]
    if q in presets:
        return presets[q]
    for slug, info in presets.items():
        if q in slug or q in info["name"].lower():
            return info
    return None


def parse_cli_flags(args):
    flags, remaining = {}, []
    for a in args:
        if   a in ("--no-video", "--no-window"): flags["no_video"]   = True
        elif a == "--video":                      flags["no_video"]   = False
        elif a in ("--loop", "-l"):               flags["loop"]       = True
        elif a == "--no-loop":                    flags["loop"]       = False
        elif a in ("--bg", "--background"):       flags["background"] = True
        elif a in ("--fg", "--foreground"):       flags["background"] = False
        elif a == "--radio":                      flags["radio"]      = True
        elif a == "--shuffle":                    flags["shuffle"]    = True
        elif a == "--no-resume":                  flags["no_resume"]  = True
        else:                                     remaining.append(a)
    return flags, remaining


def cli_play(info, settings, extra_flags):
    s = dict(settings)
    s.update(extra_flags)
    info = dict(info)
    for key in ("no_video", "loop", "radio"):
        if key in extra_flags:
            info[key] = extra_flags[key]
    cmd = build_mpv_cmd(info["url"], s, preset_overrides=info)
    if extra_flags.get("background"):
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        print(f"  {C.PLAY}  Started in background: {info['name']}")
    else:
        subprocess.run(cmd)


def print_radio_help(presets):
    categories = {}
    for slug, info in presets.items():
        categories.setdefault(info["category"], []).append((slug, info["name"]))
    print(f"""
🎵 RADIO — Music preset player (yt-cli)

CONFIG: {CONFIG_PATH}

USAGE:
    yt-cli                        Main menu
    tune                          Radio TUI
    tune <preset> [options]       Direct play
    tune <url>   [options]        Play a URL directly
    tune --list                   List all presets
    tune --config                 Edit config
    tune --help

OPTIONS:
    --no-video, --no-window     Force audio-only (suppress window)
    --loop, -l                  Loop playlist
    --no-loop                   Don't loop
    --bg, --background          Run in background
    --fg, --foreground          Run in foreground
    --radio                     Seed YouTube radio mix
    --shuffle                   Shuffle playlist

PRESETS:""")
    for cat, items in categories.items():
        print(f"\n  {cat}:")
        for slug, name in items:
            print(f"    {slug:<28}  {name}")
```

- [ ] **Step 3: Write the top-level main() + dispatch**

Append (combines the three originals' entry points; dispatch on `Path(sys.argv[0]).name`):

```python
# ---------- entry point ----------

def check_dependencies():
    missing = [tool for tool in ("mpv", "yt-dlp") if not shutil.which(tool)]
    if missing:
        print(f"error: required tool(s) not found on PATH: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def _radio_cli_main():
    cfg = load_config()
    settings, presets = load_radio_cfg(cfg)
    args = sys.argv[1:]

    if not args:
        run_fzf(cfg, mode="radio")
        return

    if args[0] in ("--help", "-h", "help"):
        print_radio_help(presets); sys.exit(0)

    if args[0] in ("--list", "-l", "list"):
        categories = {}
        for slug, info in presets.items():
            categories.setdefault(info["category"], []).append((slug, info))
        print(f"\n{c(C.BOLD + C.CYAN, '🎵  radio')}  —  presets\n")
        for cat, items in categories.items():
            print(f"  {c(C.DIM + C.BOLD, cat)}")
            for slug, info in items:
                print(f"    {c(C.CYAN, slug):<30}  {info['name']}")
            print()
        sys.exit(0)

    if args[0] in ("--config", "-c", "config", "--edit"):
        subprocess.run([os.environ.get("EDITOR", "nano"), str(CONFIG_PATH)])
        sys.exit(0)

    query        = args[0]
    flags, _rest = parse_cli_flags(args[1:])

    if query.startswith(("http://", "https://")):
        info = {"name": query[:60], "url": query, "loop": settings["loop"],
                "no_video": settings["no_video"], "radio": flags.get("radio", False)}
    else:
        info = resolve_preset(query, presets)
        if not info:
            print(f"❌ Unknown preset: '{query}'")
            suggestions = [s for s in presets if query.replace("-", "") in s.replace("-", "")]
            if suggestions:
                print(f"   Did you mean: {', '.join(suggestions[:4])}")
            sys.exit(1)
    cli_play(info, settings, flags)


def _news_cli_main():
    cfg = load_config()
    settings, sources = load_news_cfg(cfg)
    args = sys.argv[1:]

    if not args:
        run_fzf(cfg, mode="news")
        return

    if args[0] in ("--help", "-h", "help"):
        print_news_help(sources); sys.exit(0)

    if args[0] in ("--list", "-l", "list"):
        print("\n📡 AVAILABLE NEWS SOURCES:")
        print("-" * 60)
        for key, info in sorted(sources.items()):
            status = "🟢" if info["enabled"] else "🔴"
            print(f"  {status} {key:<25} - {info['name']}")
        print("-" * 60)
        sys.exit(0)

    if args[0] in ("--config", "-c", "config", "--edit"):
        edit_news_config()
        sys.exit(0)

    source_query = args[0].lower()
    player_override = None
    quality = settings["quality"]
    for arg in args[1:]:
        if arg.startswith("--quality="):
            quality = arg.split("=")[1]
        elif arg in ("mpv", "vlc", "ffplay", "auto"):
            player_override = arg

    matched = None
    for key, info in sources.items():
        if source_query == key:
            matched = info
            break
        if source_query in key or key in source_query or source_query in info["name"].lower():
            matched = info

    if not matched:
        print(f"❌ Unknown source: '{source_query}'")
        suggestions = [k for k in sources
                       if any(p in k for p in source_query.replace("-", " ").split())]
        if suggestions:
            print("Did you mean: " + ", ".join(suggestions[:4]))
        sys.exit(1)

    print(f"\n📺 Searching for: {matched['name']}")
    streams = search_youtube(matched["name"], matched["keywords"],
                             matched.get("channel_id"),
                             settings["timeout"], settings["recent_hours"])
    if not streams:
        print(f"⚠️  No streams found. Try: {matched['fallback_url']}")
        sys.exit(0)
    stream = streams[0]
    direct_url = get_direct_stream_url(stream["url"], quality)
    player = player_override or settings["player"]
    play_stream_cli(direct_url or stream["url"], player, quality)


def run_curses(cfg, mode=None):
    """Universal curses fallback entry (fzf missing)."""
    curses.wrapper(_curses_main, cfg, mode)


def _curses_main(stdscr, cfg, mode):
    ui = CursesUI(stdscr)
    play_mpv = _search_play_default(cfg)

    def goodbye():
        stop_shared()
        _curses_ui_message(stdscr, ["Goodbye! 👋"])

    if mode == "radio":
        run_radio_mode(cfg, ui, stop_shared, play_mpv, goodbye)
        return
    if mode == "news":
        run_news_mode(cfg, ui, stop_shared, goodbye)
        return

    entries = ["Search", "Radio", "News", "History", "Exit"]
    while True:
        idx = ui.pick(entries, "main ❯ ", "yt-cli  —  unified search / radio / news", "50%")
        if idx is None:
            break
        choice = entries[idx]
        if choice == "Exit":
            break
        if choice == "Search":
            run_search_mode(cfg, ui, stop_shared, play_mpv, return_home=True)
        elif choice == "Radio":
            run_radio_mode(cfg, ui, stop_shared, play_mpv, goodbye)
        elif choice == "News":
            run_news_mode(cfg, ui, stop_shared, goodbye)
        elif choice == "History":
            run_history_screen(cfg, ui, stop_shared, play_mpv)
    goodbye()


def _curses_ui_message(stdscr, lines):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    y = max(1, h // 2 - len(lines) // 2)
    for i, line in enumerate(lines):
        _curses_safe_addstr(stdscr, y + i, 2, line)
    stdscr.refresh()
    stdscr.getch()


def main():
    check_dependencies()
    migrate_configs()
    script_name = Path(sys.argv[0]).name

    if script_name == "tune":
        _radio_cli_main()
    elif script_name == "news-stream":
        _news_cli_main()
    else:
        # yt-cli or a radio preset symlink (music/<preset>)
        cfg = load_config()
        if script_name != "yt-cli":
            settings, presets = load_radio_cfg(cfg)
            flags, _ = parse_cli_flags(sys.argv[1:])
            info = resolve_preset(script_name, presets)
            if not info:
                print(f"❌ No preset found for '{script_name}'")
                sys.exit(1)
            cli_play(info, settings, flags)
            return
        if find_fzf():
            try:
                run_fzf(cfg)
            except KeyboardInterrupt:
                pass
        else:
            try:
                run_curses(cfg)
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
```

- [ ] **Step 4: Verify compile + entry-point import smoke test**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`

```bash
cd /tmp/opencode && python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('y', '/home/kenneth/.local/bin/yt-cli')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for fn in ('run_search_mode','run_radio_mode','run_news_mode','run_fzf','run_curses','main','FzfUI','CursesUI','get_ui','_ipc_watch'):
    assert hasattr(m, fn), fn
print('all entry points present')
"
```

Expected: `all entry points present`.

---

### Task 17: FzfUI must match CursesUI query semantics — verify parity

**Files:**
- Modify: `/home/kenneth/.local/bin/yt-cli` (verify; fix if needed)

- [ ] **Step 1: Confirm query() parity**

`FzfUI.query` returns `None` on Esc (quit), `""` on empty Enter (→ history), else the query. `CursesUI.query` (Task 5 fix) returns `None` on Esc/q, `""` on empty Enter, else the query. Add a check: in `CursesUI.query`, after `curses.ungetch(key)` the first char is the typed char; an empty-input Enter yields `""`. Verify the logic handles the case where the user types nothing and presses Enter (returns `""` → history).

If the ungetch path proves unreliable, simplify: make `CursesUI.query` use `_curses_line` and treat a *leading* Esc/`q` specially:

```python
    def query(self, header_lines):
        stdscr = self.stdscr
        h, _ = stdscr.getmaxyx()
        label = "search ❯ "
        for i, line in enumerate(header_lines[: h - 3]):
            _curses_safe_addstr(stdscr, i, 0, line, COLORS["header"])
        stdscr.refresh()
        text = _curses_line(stdscr, h - 1, label)
        return None if text in ("q", "quit") else text
```

Choose whichever version behaves correctly in the Task 21 tmux test (Step 4: Esc in search query must quit to main menu; empty Enter must show history).

- [ ] **Step 2: Verify compile**

Run: `python3 -m py_compile /home/kenneth/.local/bin/yt-cli`
Expected: exit 0.

---

### Task 18: Create symlink aliases

**Files:**
- Modify: `/home/kenneth/.local/bin/tune` → symlink
- Modify: `/home/kenneth/.local/bin/news-stream` → symlink

- [ ] **Step 1: Replace the two scripts with symlinks**

```bash
rm /home/kenneth/.local/bin/tune
ln -s yt-cli /home/kenneth/.local/bin/tune
rm /home/kenneth/.local/bin/news-stream
ln -s yt-cli /home/kenneth/.local/bin/news-stream
chmod +x /home/kenneth/.local/bin/yt-cli
```

Expected: `ls -l /home/kenneth/.local/bin/tune /home/kenneth/.local/bin/news-stream` shows symlinks → yt-cli.

- [ ] **Step 2: Verify alias dispatch**

```bash
/home/kenneth/.local/bin/tune --list 2>&1 | head -3
/home/kenneth/.local/bin/news-stream --list 2>&1 | head -3
```

Expected: both print their lists (radio presets / news sources), confirming argv[0] dispatch works through the symlinks.

---

### Task 19: Config migration first-run + data integrity check

**Files:**
- Modify: none (run-only)

- [ ] **Step 1: Run migration and verify merged config**

```bash
python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('y', '/home/kenneth/.local/bin/yt-cli')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.migrate_configs()
cfg = m.load_config()
print('radio presets:', len(cfg['radio']['presets']))
print('news sources :', len(cfg['news']['sources']))
print('result_count :', cfg['result_count'])
"
```

Expected: `radio presets: 33`, `news sources: 12`, `result_count: 20`. The merged `~/.config/yt-cli/config.json` now contains the imported data; the old `~/.config/tune/config.ini` and `~/.config/news-stream/config.ini` remain untouched.

- [ ] **Step 2: Verify radio CLI play of a preset**

```bash
/home/kenneth/.local/bin/tune --no-video --loop lofi &
sleep 4; pgrep -af "mpv" | head -2; sleep 1; pkill -f "mpv.*youtube" 2>/dev/null
```

Expected: an mpv process appears playing the lofi URL (audio-only). (Kill by URL pattern only — never bare `pkill mpv`.)

---

### Task 20: News CLI smoke test

**Files:**
- Modify: none (run-only)

- [ ] **Step 1: Run news CLI list + a live search (no playback)**

```bash
/home/kenneth/.local/bin/news-stream --list | head -6
```

Expected: the 12 sources listed.

- [ ] **Step 2: Search only (no player) via the interactive path is covered in Task 21; here verify the CLI source resolve errors nicely**

```bash
/home/kenneth/.local/bin/news-stream nonexistent-source 2>&1 | head -2
```

Expected: `❌ Unknown source: 'nonexistent-source'` + a suggestion.

---

### Task 21: End-to-end verification (tmux, fzf + curses)

**Files:**
- Modify: `/tmp/opencode/ytplay_test.py`, `/tmp/opencode/tuneplay_test.py` (verify signatures)

- [ ] **Step 1: Update the watcher harnesses**

`/tmp/opencode/ytplay_test.py` imports yt-cli and calls `_ipc_watch(name, done)` — the 2-arg signature is preserved, so it should still work. Verify:

```bash
cd /tmp/opencode && python3 ytplay_test.py
```

Expected: prints `▶  Test Video  playing` and the thread returns within ~10s (uses a real mpv + the IPC watcher).

- [ ] **Step 2: Main menu → search → play → action menu (tmux, fzf)**

```bash
tmux kill-server 2>/dev/null; sleep 1
tmux new-session -d -s yv -x 100 -y 30 'export TERM=xterm-256color; /home/kenneth/.local/bin/yt-cli'
sleep 2; tmux capture-pane -t yv -p | grep -q "main" && echo "main menu OK"
```

Expected: `main menu OK`.

- [ ] **Step 3: Search mode end-to-end (fzf)**

```bash
tmux send-keys -t yv "s" Enter; sleep 2   # 's' filters Search in the menu; Enter selects
tmux send-keys -t yv "acdc thunderstruck" Enter; sleep 8
tmux send-keys -t yv "1" Enter; sleep 12
tmux capture-pane -t yv -p | grep "▶" | head -1
```

Expected: a `▶  AC/DC - Thunderstruck ... playing` line. Then exercise action keys: `o`+Enter → results list → Esc → back to action menu; `h`+Enter → history picker → Esc → action menu; `q`+Enter → stop; `x`+Enter → exit.

- [ ] **Step 4: Radio mode end-to-end (fzf)**

```bash
tmux send-keys -t yv "r" Enter; sleep 2   # 'r' filters Radio
tmux send-keys -t yv "lofi" Enter; sleep 12
tmux capture-pane -t yv -p | grep "▶" | head -1
```

Expected: `▶  Lo-Fi  playing` (or similar preset). Exercise `o` (preset list, keeps playing) → Esc → action menu; `q` → stop; `x` → exit.

- [ ] **Step 5: News mode end-to-end (fzf)**

```bash
tmux send-keys -t yv "n" Enter; sleep 2   # 'n' filters News
tmux send-keys -t yv "1" Enter; sleep 20
tmux capture-pane -t yv -p | grep "▶\|LIVE" | head -1
```

Expected: a live-stream playing line. Exercise `o`/`q`/`x`.

- [ ] **Step 6: Universal search from Radio and News (fzf)**

```bash
tmux send-keys -t yv "r" Enter; sleep 2
tmux send-keys -t yv "lofi" Enter; sleep 10
tmux send-keys -t yv "s"; sleep 1; tmux send-keys -t yv Enter; sleep 2
tmux send-keys -t yv "metallica nothing else matters" Enter; sleep 8
tmux send-keys -t yv "1" Enter; sleep 12
tmux capture-pane -t yv -p | grep "▶" | head -1
tmux send-keys -t yv "q"; sleep 1; tmux send-keys -t yv Enter; sleep 2
tmux capture-pane -t yv -p | grep "preset\|radio" | head -1
```

Expected: search plays a Metallica video; after `q`+Enter, back in the radio preset list.

- [ ] **Step 7: Curses fallback — main menu + search mode**

Simulate no fzf by hiding it from PATH:

```bash
tmux kill-server 2>/dev/null; sleep 1
tmux new-session -d -s yc -x 100 -y 30 'export TERM=xterm-256color; export PATH=/usr/bin:/bin; /home/kenneth/.local/bin/yt-cli'
sleep 2; tmux capture-pane -t yc -p | head -5
```

Expected: curses main menu (list with Search/Radio/News/History/Exit). Navigate: `j` to Radio, Enter → radio preset list (curses); Esc → back; navigate to News, Enter → source list; navigate to Search, Enter → query line; type `acdc thunderstruck` Enter → results list; `j` + Enter → plays via mpv (mpv takes over the tty via curses endwin — the merged `_curses_play_mpv` must `curses.def_prog_mode()`/`endwin()` before spawning and reset after. **If playback in curses mode leaves a dead screen, add the `curses.def_prog_mode(); curses.endwin()` + `curses.reset_prog_mode()` wrappers around the `Popen`/`wait` in `_curses_play_mpv`.**)

- [ ] **Step 8: Clean up**

```bash
tmux kill-server 2>/dev/null
for p in $(pgrep -x mpv); do kill $p 2>/dev/null; done
```

Expected: no mpv/fzf/yt-cli processes remain (`pgrep -a fzf; pgrep -x mpv` empty).

---

## Self-Review

**Spec coverage:**
- Single script with main menu → Tasks 1-9, 16.
- Unified action menu (all modes, o/s/h/r/q/x + mode extras) → Tasks 8, 7, 12, 15.
- Universal search (`s` from any mode) → Tasks 7, 12 (radio), 15 (news).
- Shared history (all modes log; `h` everywhere) → Tasks 1, 7, 8, 12, 15.
- IPC watcher single copy (search+radio+news) → Tasks 6, 12, 15.
- Config merge + migration → Tasks 2, 10, 13, 19.
- Aliases (tune/news-stream symlinks, preset dispatch) → Tasks 16, 18.
- **Universal curses fallback (all modes)** → Tasks 5 (CursesUI), 12, 15 (modes route through `ui`), 16 (`run_curses`), 17 (parity), 21 (tmux test).
- Verification → Task 21.

**Placeholder scan:** All porting steps reference exact source ranges in `/tmp/opencode/backup/*.bak`; no TBD/TODO. The one flagged risk (curses-mode playback leaving a dead screen) has an explicit fix in Task 21 Step 7.

**Type consistency:**
- `ui.pick`/`ui.pick_query`/`ui.action`/`ui.query`/`ui.prompt`/`ui.info` — identical signatures on both UI classes; every mode function routes through `ui`, never branching on fzf vs curses.
- `_ipc_watch(name, done)` 2-arg everywhere (Tasks 6, 9, 12, 15).
- `PLAYER_PROC`/`IPC_DONE` module-level lists shared across modes (Tasks 7, 9, 12, 15, 16).
- `load_radio_cfg`/`load_news_cfg` return `(settings, dict)` consistently.
- Config helpers named `radio_*` / `news_*` consistently.
- `_search_play_default(cfg)` used by news (Task 15); radio search uses `play_fn` (radio player).

**Design-doc conformance:** This plan fully implements the "universal curses fallback" decision — no fzf/text branches remain in any mode; the UI abstraction is the single code path with two renderers.