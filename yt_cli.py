#!/usr/bin/env python3
"""yt-cli: unified YouTube search / radio / news player.

Merges the former yt-cli (search), tune (radio), and news-stream (news) into
one script. Dispatch on argv[0]: yt-cli → main menu, tune → radio mode,
news-stream → news mode, anything else → radio preset lookup.
"""

import asyncio
import configparser
import copy
import curses
import html
import json
import os
import random
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
# IPC socket MUST stay /tmp/mpvsocket (from the user's mpv.conf) — their
# global mpv-control keybindings (super/ctrl keys -> control scripts) talk
# to that path. yt-cli launches inherit it from mpv.conf; do NOT override.
IPC_SOCKET = "/tmp/mpvsocket"

# Old config paths (for migration)
TUNE_CONFIG = Path.home() / ".config" / "tune" / "config.ini"
NEWS_CONFIG = Path.home() / ".config" / "news-stream" / "config.ini"

APP_NAME = "yt-cli"
COLORS = {"header": 0, "warn": 0, "footer": 0}


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    PLAY = "\033[32m▶\033[0m"
    LIVE = f"\033[31m{BOLD}● LIVE{RESET}"
    RECENT = f"\033[33m⏱{RESET}"


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
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(path)
    except OSError as exc:
        raise OSError(f"could not save {path}: {exc}") from exc


DEFAULT_CONFIG = {
    "result_count": 20,
    "history_size": 200,
    "mpv_args": [],
    "quality": "720",
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
    "stations": [],
}

# Volatile toggles live in state.json (gitignored) so casual keypresses
# never dirty the tracked config.json. config.json stays curated-only.
STATE_PATH = CONFIG_DIR / "state.json"
DEFAULT_STATE = {
    "radio": {"player": "mpv", "no_video": False, "loop": False, "background": False},
    "news": {"player": "auto"},
}


def load_state():
    st = load_json(STATE_PATH, None)
    if st is None:
        st = {}
    cfg = load_json(CONFIG_PATH, {}) or {}
    changed = False
    for section, defaults in DEFAULT_STATE.items():
        sec = st.setdefault(section, {})
        legacy = cfg.get(section, {})
        for k, dv in defaults.items():
            if k not in sec:
                sec[k] = legacy.get(k, dv)  # seed once from config.json
                changed = True
    if changed:
        save_json(STATE_PATH, st)
    return st


def save_state(state):
    save_json(STATE_PATH, state)


def load_config():
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        return copy.deepcopy(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged["stations"] = list(DEFAULT_CONFIG["stations"])
    for k, v in cfg.items():
        if k in ("radio", "news"):
            merged[k] = {**DEFAULT_CONFIG[k], **v}
        elif k == "stations":
            merged[k] = list(v) if isinstance(v, list) else []
        else:
            merged[k] = v
    return merged


def save_config(cfg):
    # Volatile playback toggles belong in state.json, not config.json.
    clean = dict(cfg)
    for section, keys in (
        ("radio", ("player", "no_video", "loop", "background")),
        ("news", ("player",)),
    ):
        sec = clean.get(section)
        if isinstance(sec, dict):
            sec = dict(sec)
            for key in keys:
                sec.pop(key, None)
            clean[section] = sec
    save_json(CONFIG_PATH, clean)


def add_to_history(item, cap):
    history = load_json(HISTORY_PATH, [])
    history = [h for h in history if h.get("url") != item.get("url")]
    history.insert(0, {**item, "played_at": datetime.now(timezone.utc).isoformat()})
    save_json(HISTORY_PATH, history[:cap])


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
        slug = section[len("preset.") :]
        presets.append(
            {
                "slug": slug,
                "name": parser.get(section, "name", fallback=slug),
                "url": parser.get(section, "url", fallback=""),
                "category": parser.get(section, "category", fallback="Uncategorized"),
                "loop": parser.getboolean(section, "loop", fallback=False),
                "no_video": parser.getboolean(section, "no_video", fallback=False),
                "radio": parser.getboolean(section, "radio", fallback=False),
                "enabled": parser.getboolean(section, "enabled", fallback=True),
            }
        )
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
        sid = section[len("source.") :]
        sources.append(
            {
                "id": sid,
                "name": parser.get(section, "name", fallback=sid),
                "channel_id": parser.get(section, "channel_id", fallback=None),
                "keywords": [
                    k.strip().lower()
                    for k in parser.get(section, "keywords", fallback=sid).split(",")
                ],
                "fallback_url": parser.get(section, "fallback_url", fallback=""),
                "enabled": parser.getboolean(section, "enabled", fallback=True),
            }
        )
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
    if "quality" not in cfg:
        cfg["quality"] = DEFAULT_CONFIG["quality"]
        changed = True
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
    # Volatile toggles moved to state.json — seed it from legacy values,
    # then strip them so config.json only changes on preset/source edits.
    load_state()
    for section, keys in (
        ("radio", ("player", "no_video", "loop", "background")),
        ("news", ("player",)),
    ):
        sec = cfg.get(section)
        if sec and any(k in sec for k in keys):
            for k in keys:
                sec.pop(k, None)
            changed = True
    if changed:
        save_json(CONFIG_PATH, cfg)


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
        self.label = label
        self._stop = threading.Event()
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
        sys.stdout.write(f"\r  {c(color, '✓')}  {msg}" + " " * 40 + "\n")
        sys.stdout.flush()

    def fail(self, msg):
        self._stop.set()
        self._thread.join()
        sys.stdout.write(f"\r  {c(C.YELLOW, '⚠')}  {msg}" + " " * 40 + "\n")
        sys.stdout.flush()


# ---------- UI abstraction: fzf ----------


def fzf_pick(items, prompt="❯ ", header=None, height="50%", capture_query=False):
    """Low-level fzf wrapper. capture_query=True returns (query, selected, escaped)."""
    if not items and not capture_query:
        return None
    cmd = [
        "fzf",
        "--ansi",
        "--reverse",
        "--no-multi",
        "--no-info",
        "--prompt",
        prompt,
        f"--height={height}",
        "--margin=1,2",
    ]
    if header:
        cmd += ["--header", header]
    if capture_query:
        cmd += ["--print-query"]
    tmp_fd, tmp_path = tempfile.mkstemp()
    os.close(tmp_fd)
    try:
        with open("/dev/tty", "rb+", buffering=0) as tty:
            p = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=open(tmp_path, "w"), stderr=tty, text=True
            )
            assert p.stdin is not None
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
    # Numbers stay because they are quick-select shortcuts; dim keeps rows calm.
    return f"{c(C.DIM, str(i) + '.')}  {text}"


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
        query, chosen, _ = fzf_pick(
            lines, prompt=prompt, header=header, height=height, capture_query=True
        )  # type: ignore[misc]
        return query, _parse_index(chosen)

    def action(self, header_lines, actions):
        labels = [label for label, _ in actions]
        chosen = fzf_pick(
            labels,
            prompt="action ❯ ",
            header="\n".join(header_lines) + "\n" + "─" * 44,
            height="55%",
        )
        if chosen is None:
            return "q"
        for label, code in actions:
            if strip_ansi(chosen).strip() == label:
                return code
        return "q"

    def query(self, header_lines):
        q, _, escaped = fzf_pick(
            [], prompt="search ❯ ", header="\n".join(header_lines), height="25%", capture_query=True
        )  # type: ignore[misc]
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
        text = stdscr.getstr(h - 1, min(len(label), w - 1), max(1, w - len(label) - 1)).decode(
            "utf-8", errors="replace"
        )
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
    _curses_safe_addstr(
        stdscr,
        h - 1,
        0,
        footer + ("  filter: " + filter_str if filter_str else ""),
        COLORS["footer"],
    )
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
            _curses_draw_list(
                stdscr, header, items, sel, top, "up/k down/j move   enter select   q/Esc back"
            )
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
            matches = [
                i
                for i, item in enumerate(items)
                if not buf or buf.lower() in strip_ansi(item).lower()
            ]
            filtered = [items[i] for i in matches]
            if filtered:
                sel = min(sel, len(filtered) - 1)
            else:
                sel = 0
            _curses_draw_list(
                stdscr,
                header,
                filtered,
                sel,
                top,
                "type to filter   enter select   empty enter = search   Esc back",
                filter_str=buf,
            )
            _curses_safe_addstr(stdscr, h - 1, 0, prompt)
            stdscr.refresh()
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")) and not buf:
                sel = max(0, sel - 1)
            elif key in (curses.KEY_DOWN, ord("j")) and not buf:
                sel = min(max(0, len(filtered) - 1), sel + 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                if filtered and 0 <= sel < len(matches):
                    return buf, matches[sel]
                return buf, None
            elif key == 27:
                return "", None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                buf = buf[:-1]
                sel, top = 0, 0
            elif 32 <= key < 256:
                buf += chr(key)
                sel, top = 0, 0
            h, _ = stdscr.getmaxyx()
            visible = h - 3
            if sel < top:
                top = sel
            elif sel >= top + visible:
                top = sel - visible + 1

    def action(self, header_lines, actions):
        items = [
            _num_line(i + 1, f"{label}  {c(C.DIM, '[' + code + ']')}")
            for i, (label, code) in enumerate(actions)
        ]
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


# ---------- format helpers ----------


def format_duration(seconds):
    if not seconds:
        return "?:??"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_fzf_item(idx, item):
    dur = format_duration(item.get("duration"))
    uploader = item.get("uploader") or "Unknown"
    title = item.get("title") or "Untitled"
    when = ""
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


def _ipc_watch(name, done, end_state=None, follow=False, announce=True):
    """Announce playback start and, with follow=True, keep reading events so
    end_state records why mpv stopped ('eof', 'quit', ...). Station reconnects
    need that reason: a user closing mpv must not be fought by the supervisor."""
    if announce:
        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    spinner_label = ["Resolving..."]
    spinner_stop = threading.Event()
    spin_thread = None

    def spin():
        time.sleep(0.15)
        i = 0
        while not spinner_stop.is_set():
            frame = SPINNER[i % len(SPINNER)]
            sys.stdout.write(f"\r  {c(C.CYAN, frame)}  {c(C.DIM, spinner_label[0])}      ")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1

    if announce:
        spin_thread = threading.Thread(target=spin, daemon=True)
        spin_thread.start()

    time.sleep(0.3)  # give mpv time to bind socket

    deadline = time.time() + 60
    sock = None

    while time.time() < deadline:
        if done.is_set():
            spinner_stop.set()
            return
        proc = PLAYER_PROC[0]
        if proc is not None and proc.poll() is not None:
            spinner_stop.set()
            if end_state is not None:
                end_state["ended"].set()
            return
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(IPC_SOCKET)
            break
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            time.sleep(0.2)
    else:
        spinner_stop.set()
        if announce:
            sys.stdout.write(
                f"\r  {c(C.GREEN, '▶')}  {c(C.BOLD, name)}  {c(C.GREEN, 'playing')}      "
            )
            sys.stdout.flush()
        return

    sock.settimeout(1.0)
    buf = ""
    deadline_evt = time.time() + 60
    reached_deadline = False

    try:
        while not done.is_set():
            # follow mode lives as long as the player does; videos keep the
            # old 60s ceiling so a stuck watcher can never linger.
            if not follow and time.time() >= deadline_evt:
                reached_deadline = True
                break
            proc = PLAYER_PROC[0]
            if proc is not None and proc.poll() is not None:
                break
            try:
                chunk = sock.recv(4096).decode("utf-8", errors="ignore")
            except socket.timeout:
                continue
            if not chunk:
                break

            buf += chunk
            lines = buf.split("\n")
            buf = lines[-1]

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
                    if announce:
                        spinner_stop.set()
                        if spin_thread is not None:
                            spin_thread.join()
                        sys.stdout.write(
                            f"\r  {c(C.GREEN, '▶')}  {c(C.BOLD, name)}  "
                            f"{c(C.GREEN, 'playing')}      "
                        )
                        sys.stdout.flush()
                    if not follow:
                        return

                elif event == "end-file":
                    reason = evt.get("reason", "")
                    if follow and reason == "redirect":
                        continue  # mpv reloads the new URL; keep watching
                    if end_state is not None:
                        end_state["reason"] = reason
                        end_state["ended"].set()
                    if follow:
                        spinner_stop.set()
                        return
                    if reason == "error" and announce:
                        spinner_stop.set()
                        if spin_thread is not None:
                            spin_thread.join()
                        sys.stdout.write(f"\r  {c(C.RED, '✗  Failed to load')}      \n")
                        sys.stdout.flush()
                        return

        spinner_stop.set()
        if spin_thread is not None:
            spin_thread.join()
        if announce and not follow and reached_deadline:
            sys.stdout.write(
                f"\r  {c(C.GREEN, '▶')}  {c(C.BOLD, name)}  {c(C.GREEN, 'playing')}      "
            )
            sys.stdout.flush()
    except Exception:
        pass
    finally:
        spinner_stop.set()
        if end_state is not None:
            end_state["ended"].set()
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


# ---------- MODE: SEARCH ----------


async def _search_ytdlp(query, count):
    cmd = ["yt-dlp", f"ytsearch{count}:{query}", "--flat-playlist", "-j", "--no-warnings"]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    assert proc.stdout is not None and proc.stderr is not None
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


def run_search_mode(
    cfg, ui, stop, play, return_home=False, initial_items=None, initial_header=None
):
    """Full search mode. return_home=True: after 'q' return to caller instead of the list."""
    items = initial_items or []
    header = initial_header or "yt-cli · history"

    item = None
    # If called from main menu with no initial list, start with the query stage.
    if initial_items is None:
        while True:
            query = ui.query(["yt-cli · search YouTube", "Esc → quit   ·   empty Enter → history"])
            if query is None:
                return
            if query.strip():
                if looks_like_url(query.strip()):
                    it = url_entry_flow(cfg, ui, query.strip())
                    if it is not None:
                        item = it
                        play(item)
                        break
                    continue
                clear_screen()
                print(f"  {c(C.DIM, 'Searching...')}\n")
                try:
                    items = search_with_spinner(query, cfg["result_count"])
                except SearchError as e:
                    if (
                        ui.action(
                            [f"search failed: {e}", "─" * 44],
                            [("retry search", "r"), ("back", "b")],
                        )
                        != "r"
                    ):
                        return
                    continue
                if not items:
                    if (
                        ui.action(
                            [f'No results for "{query}"', "─" * 44],
                            [("retry search", "r"), ("back", "b")],
                        )
                        != "r"
                    ):
                        return
                    continue
                header = f"yt-cli · results: {query} ({len(items)})"
            else:
                items = load_json(HISTORY_PATH, [])
                header = "yt-cli · history"
                if not items:
                    ui.info(["No history yet"])
                    return
            break

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
                item = None
                continue  # back to results picker, player keeps playing
            elif action == "h":
                items = load_json(HISTORY_PATH, [])
                header = "yt-cli · history"
                if not items:
                    ui.info(["No history yet"])
                continue
            elif action == "s":
                return run_search_mode(cfg, ui, stop, play, return_home=return_home)
            elif action == "v":
                nv, live = toggle_video_setting()
                if not live:
                    play(item)  # restart honors new state via adapter
            elif action == "k":
                save_current(
                    cfg,
                    ui,
                    {
                        "title": item.get("title") or item.get("url", ""),
                        "url": item.get("url", ""),
                        "uploader": item.get("uploader"),
                    },
                    default_kind="preset",
                )
            elif action == "g":
                dest = go_to_picker(ui)
                if dest:
                    raise GoTo(dest)
            elif action == "d":
                print(f"\n  {c(C.GREEN, '▶')}  {c(C.DIM, 'detached · playing in background')}\n")
                sys.exit(0)
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
            if return_home:
                return  # Esc → back to caller (radio/news menu, main)
            if PLAYER_PROC[0] is not None:
                continue  # Esc while playing → return to action menu
            return  # Esc with no player → exit search mode
        item = items[idx]
        play(item)


PLAYER_PROC: list = [None]
IPC_DONE: list = [None]
# Global now-playing registry: every launcher records a reopen closure so
# the main menu can offer "▶ Now Playing" controls from anywhere.
NOW_PLAYING: list = [None]
# True when we reattached to a detached player via IPC (no Popen handle).
ATTACHED: list = [False]
# Bumped on every station play/stop; stale auto-reconnect supervisors compare
# their captured generation and exit without touching the new player.
STATION_GEN: list = [0]


def ipc_rpc(cmd, timeout=2):
    """Send one command to the yt-cli mpv IPC socket, return data.
    Raises on connect failure or non-success error reply."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(IPC_SOCKET)
    try:
        s.sendall(json.dumps({"command": cmd}).encode() + b"\n")
        f = s.makefile("r")
        while True:
            line = f.readline()
            if not line:
                raise RuntimeError("no reply from mpv")
            msg = json.loads(line)
            if "error" in msg:
                if msg["error"] != "success":
                    raise RuntimeError(msg["error"])
                return msg.get("data")
    finally:
        s.close()


# ---------- unified action tables ----------

PLAY_ACTIONS_SEARCH = [
    ("other results", "o"),
    ("new search", "s"),
    ("history", "h"),
    ("replay video", "r"),
    ("go to…", "g"),
    ("stop player", "q"),
    ("exit", "x"),
    ("toggle video", "v"),
    ("keep (save)", "k"),
    ("detach & exit", "d"),
]

PLAY_ACTIONS_RADIO = [
    ("other presets", "o"),
    ("search", "s"),
    ("history", "h"),
    ("replay", "r"),
    ("go to…", "g"),
    ("stop player", "q"),
    ("exit", "x"),
    ("toggle video", "v"),
    ("toggle loop", "l"),
    ("manage presets", "m"),
    ("keep (save)", "k"),
    ("detach & exit", "d"),
]

PLAY_ACTIONS_NEWS = [
    ("other streams", "o"),
    ("search", "s"),
    ("history", "h"),
    ("replay", "r"),
    ("go to…", "g"),
    ("stop player", "q"),
    ("exit", "x"),
    ("player", "p"),
    ("refresh streams", "f"),
    ("keep (save)", "k"),
    ("detach & exit", "d"),
]


def run_history_screen(cfg, ui, stop, play):
    items = load_json(HISTORY_PATH, [])
    if not items:
        ui.info(["No history yet"])
        return
    run_search_mode(
        cfg,
        ui,
        stop,
        play,
        return_home=True,
        initial_items=items,
        initial_header="yt-cli · history",
    )


# ---------- orchestration ----------


def stop_shared():
    STATION_GEN[0] += 1  # cancel any station auto-reconnect supervisor
    if IPC_DONE[0] is not None:
        IPC_DONE[0].set()
        IPC_DONE[0] = None
    kill_player(PLAYER_PROC[0])
    PLAYER_PROC[0] = None
    if ATTACHED[0]:
        try:
            ipc_rpc(["quit"])  # detached player: kill over IPC
        except (OSError, ValueError, RuntimeError):
            pass
        ATTACHED[0] = False


def _search_play_default(cfg):
    """mpv launcher for search-mode play (used by search-from-anywhere)."""

    def play_mpv(item):
        if item.get("kind") == "station":
            _station_play(
                cfg,
                {
                    "name": item.get("title") or item.get("url", ""),
                    "url": item.get("url", ""),
                    "stationuuid": item.get("stationuuid", ""),
                },
                stop_fn=stop_shared,
            )
            return
        stop_shared()
        add_to_history(item, cfg["history_size"])
        NOW_PLAYING[0] = {
            "title": item.get("title") or item.get("url", ""),
            "replay": lambda: play_mpv(item),
        }
        url = item.get("url", "")
        hit = None if ("list=" in url) else cached_direct_urls(url)
        if hit:
            video_url, audio_url = hit
            cmd = ["mpv", "--force-window=no"]
            if load_state().get("radio", {}).get("no_video", False):
                cmd += ["--vid=no", "--video=no"]
            cmd.append(video_url)
            if audio_url:
                cmd += [f"--audio-file={audio_url}"]
        else:
            cmd = [
                "mpv",
                "--force-window=no",
                f"--ytdl-format={ytdl_quality_format(cfg.get('quality', '720'))}",
                *cfg["mpv_args"],
            ]
            if load_state().get("radio", {}).get("no_video", False):
                cmd += ["--vid=no", "--video=no"]
            else:
                cmd += ["--vid=auto", "--video=auto"]
            if "list=" not in url:
                prefetch_resolve(url, cfg.get("quality", "720"))
            cmd.append(url)
        PLAYER_PROC[0] = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        done = threading.Event()
        IPC_DONE[0] = done
        threading.Thread(
            target=_ipc_watch, args=(item.get("title") or item.get("url"), done), daemon=True
        ).start()

    return play_mpv


def now_playing_screen(cfg, ui):
    """Compact controls for whatever is playing, reachable from the main
    menu regardless of which screen the user wandered to."""
    while True:
        np = NOW_PLAYING[0] or {}
        alive = player_active()
        header = [
            f"▶  {c(C.BOLD, (np.get('title') or 'Nothing registered')[:60])}",
            f"   {c(C.GREEN, 'playing') if alive else c(C.DIM, 'player stopped')}",
            "─" * 44,
        ]
        entries = ["replay", "toggle video", "go to…", "stop player", "detach & exit", "main menu"]
        if np.get("kind") == "station":
            entries.remove("toggle video")
        idx = ui.pick(entries, "now playing ❯ ", "\n".join(header) + "\nEsc → main menu", "50%")
        if idx is None:
            return
        choice = entries[idx]
        if choice == "replay":
            if np.get("replay"):
                np["replay"]()
        elif choice == "toggle video":
            nv, live = toggle_video_setting()
            if not live and np.get("replay"):
                np["replay"]()
        elif choice == "go to…":
            dest = go_to_picker(ui)
            if dest:
                raise GoTo(dest)
        elif choice == "stop player":
            stop_shared()
            return
        elif choice == "detach & exit":
            print(f"\n  {c(C.GREEN, '▶')}  {c(C.DIM, 'detached · playing in background')}\n")
            sys.exit(0)
        elif choice == "main menu":
            return


def _run_mode(cfg, ui, choice, stop, play_mpv, goodbye, on_esc="quit"):
    if choice == "Search":
        run_search_mode(cfg, ui, stop, play_mpv, return_home=True)
    elif choice == "Radio":
        run_radio_mode(cfg, ui, stop, play_mpv, goodbye, on_esc=on_esc)
    elif choice == "Stations":
        run_stations_mode(cfg, ui, stop, goodbye, on_esc=on_esc)
    elif choice == "News":
        run_news_mode(cfg, ui, stop, goodbye, on_esc=on_esc)
    elif choice == "History":
        run_history_screen(cfg, ui, stop, play_mpv)


def _run_mode_loop(cfg, ui, dest, stop, play_mpv, goodbye, on_esc="quit"):
    """Run a mode; follow g-jumps until a normal return or Home."""
    while dest is not None:
        try:
            _run_mode(cfg, ui, dest, stop, play_mpv, goodbye, on_esc=on_esc)
            break
        except GoTo as g:
            dest = GO_TO_LABELS.get(g.dest)  # "home" → None → caller


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

    if mode in ("radio", "stations", "news"):
        dest = {"radio": "Radio", "stations": "Stations", "news": "News"}[mode]
        _run_mode_loop(cfg, ui, dest, stop_shared, play_mpv, goodbye)
        return

    while True:
        clear_screen()
        entries = ["Search", "Radio", "Stations", "News", "History"]
        if player_active():
            entries.insert(0, "▶ Now Playing")
        entries.append("Exit")
        idx = ui.pick(entries, "main ❯ ", "yt-cli · search / radio / stations / news", "50%")
        if idx is None:
            goodbye()
            return
        choice = entries[idx]
        if choice == "Exit":
            if player_active():
                act = ui.action(
                    ["Player is still running.", "─" * 44],
                    [("leave playing in background", "d"), ("stop player and exit", "s")],
                )
                if act == "d":
                    print(
                        f"\n  {c(C.GREEN, '▶')}  {c(C.DIM, 'detached · playing in background')}\n"
                    )
                    return
                if act != "s":
                    continue  # Esc → cancel
            goodbye()
            return
        if choice == "▶ Now Playing":
            try:
                now_playing_screen(cfg, ui)
            except GoTo as g:
                if g.dest != "home":
                    _run_mode_loop(
                        cfg,
                        ui,
                        GO_TO_LABELS.get(g.dest),
                        stop_shared,
                        play_mpv,
                        goodbye,
                        on_esc="home",
                    )
            continue
        _run_mode_loop(cfg, ui, choice, stop_shared, play_mpv, goodbye, on_esc="home")


# ---------- MODE: RADIO ----------


def load_radio_cfg(cfg):
    r = cfg["radio"]
    st = load_state().get("radio", {})
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
        "player": st.get("player", "mpv"),
        "no_video": st.get("no_video", False),
        "loop": st.get("loop", False),
        "background": st.get("background", False),
        "quality": r.get("quality", cfg.get("quality", "720")),
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
    cfg["radio"]["presets"].append(
        {
            "slug": slug,
            "name": name,
            "url": url,
            "category": category,
            "loop": loop,
            "no_video": no_video,
            "radio": radio,
            "enabled": True,
        }
    )
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


def build_radio_url(url):
    video_id = re.search(r"(?:v=)([^&]+)", url)
    list_id = re.search(r"(?:list=)([^&]+)", url)
    if not video_id:
        return url
    vid = video_id.group(1)
    lst = list_id.group(1) if list_id else ""
    final_list = f"RD{vid}" if (lst.startswith("RDAT") or not lst) else lst
    return f"https://www.youtube.com/watch?v={vid}&list={final_list}"


# ---------- resolve cache (instant replays) ----------
# yt-dlp extraction inside mpv is THE startup cost (5-60s). For single
# videos we resolve once in the background and remember the direct stream
# URLs (~6h lifetime, YouTube-bound). Next play of the same item feeds mpv
# the direct URLs — no extraction at all. Playlists/live are excluded
# (mpv/yt-dlp must handle those themselves).

RESOLVE_CACHE_PATH = CONFIG_DIR / "resolve-cache.json"


def load_resolve_cache():
    c = load_json(RESOLVE_CACHE_PATH, {})
    if not isinstance(c, dict):
        return {}
    now = time.time()
    changed = False
    for k in list(c.keys()):
        if not isinstance(c[k], dict) or c[k].get("expires", 0) < now:
            del c[k]
            changed = True
    if changed:
        save_json(RESOLVE_CACHE_PATH, c)
    return c


def cached_direct_urls(url):
    """Fresh cached (video_url, audio_url|None) for a single video, else
    None. HLS/m3u8 manifests are never returned — they stall in mpv."""
    ent = load_resolve_cache().get(url)
    if not ent or ent.get("expires", 0) < time.time():
        return None
    v, a = ent.get("video") or "", ent.get("audio")
    if not v or ".m3u8" in v:
        return None
    if a and ".m3u8" in a:
        a = None
    return v, a


def prefetch_resolve(url, quality):
    """Resolve in the background and fill the cache. Never blocks play."""

    def work():
        fmt = ytdl_quality_format(quality)
        try:
            result = subprocess.run(
                ["yt-dlp", "-g", "-f", fmt, "--no-warnings", url],
                capture_output=True,
                text=True,
                timeout=90,
            )
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            if result.returncode != 0 or not lines:
                return
            video_url, audio_url = lines[0], (lines[1] if len(lines) > 1 else None)
            if ".m3u8" in video_url:
                return  # live/manifest — don't cache
            m = re.search(r"[?&]expire=(\d+)", video_url)
            expires = int(m.group(1)) - 300 if m else time.time() + 4 * 3600
            c = load_resolve_cache()
            c[url] = {"video": video_url, "audio": audio_url, "expires": expires}
            save_json(RESOLVE_CACHE_PATH, c)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
            pass

    threading.Thread(target=work, daemon=True).start()


def ytdl_quality_format(quality):
    return f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"


def build_mpv_cmd(url, settings, preset_overrides=None):
    p = preset_overrides or {}
    no_video = p.get("no_video", settings.get("no_video", False))
    loop = p.get("loop", settings.get("loop", False))
    radio = p.get("radio", False)
    quality = settings.get("quality", "720")
    final_url = build_radio_url(url) if radio else url
    # Explicit flags so behavior is deterministic regardless of mpv.conf:
    # video on -> window; video off -> no track, no window.
    base = ["mpv", "--force-window=no", f"--ytdl-format={ytdl_quality_format(quality)}"]
    if no_video:
        base += ["--vid=no", "--video=no"]
    else:
        base += ["--vid=auto", "--video=auto"]
    if loop:
        base += ["--loop-playlist"]

    # Instant replays: fresh cached direct stream URLs skip extraction.
    hit = None if ("list=" in final_url or radio) else cached_direct_urls(final_url)
    if hit:
        video_url, audio_url = hit
        cmd = ["mpv", "--force-window=no"]
        if no_video:
            cmd += ["--vid=no", "--video=no"]
        if loop:
            cmd += ["--loop-playlist"]
        cmd.append(video_url)
        if audio_url:
            cmd += [f"--audio-file={audio_url}"]
        return cmd

    # Miss: play normally (mpv extracts), and fill the cache in the
    # background so the next play of this item is instant. Singles only.
    if "list=" not in final_url and not radio:
        prefetch_resolve(final_url, quality)
    return base + [final_url]


def mpv_ipc_set_vid(show):
    """Toggle mpv's video output (and its window) live. Returns True on
    success. Both video= and vid= must be set: video=no suppresses output
    entirely, vid=no deselects the track. Retries before giving up — a
    transient IPC hiccup must not trigger a full player restart."""
    for attempt in range(3):
        try:
            ipc_rpc(["set", "video", "auto" if show else "no"])
            ipc_rpc(["set", "vid", "auto" if show else "no"])
            return True
        except (OSError, ValueError, RuntimeError):
            if attempt == 2:
                return False
            time.sleep(0.5)


def player_active():
    """True if a yt-cli player is running via Popen handle OR a detached
    player we can still reach over IPC."""
    proc = PLAYER_PROC[0]
    if proc is not None and proc.poll() is None:
        return True
    if ATTACHED[0]:
        try:
            ipc_rpc(["get_property", "path"])
            return True
        except (OSError, ValueError, RuntimeError):
            ATTACHED[0] = False  # stale — player is gone
    return False


def attach_detached():
    """Rebuild NOW_PLAYING from a detached player left by a previous
    session. Returns True when reattached."""
    try:
        path = ipc_rpc(["get_property", "path"])
        title = ipc_rpc(["get_property", "media-title"])
    except (OSError, ValueError, RuntimeError):
        try:
            os.unlink(IPC_SOCKET)  # stale socket file from a dead player
        except OSError:
            pass
        return False
    if not path:
        return False
    station = station_for_url(path)
    if station:
        NOW_PLAYING[0] = {
            "title": title or station.get("name") or path,
            "kind": "station",
            "replay": lambda: relaunch_detached(path),
        }
    else:
        NOW_PLAYING[0] = {
            "title": title or path,
            "replay": lambda: relaunch_detached(path),
        }
    ATTACHED[0] = True
    return True


def relaunch_detached(url):
    stop_shared()
    station = station_for_url(url)
    if station:
        PLAYER_PROC[0] = launch_player(build_station_cmd(station))
        ATTACHED[0] = False
        return
    cmd = [
        "mpv",
        "--force-window=no",
        f"--ytdl-format={ytdl_quality_format(load_config().get('quality', '720'))}",
    ]
    st = load_state().get("radio", {})
    if st.get("no_video"):
        cmd += ["--vid=no", "--video=no"]
    else:
        cmd += ["--vid=auto", "--video=auto"]
    PLAYER_PROC[0] = launch_player(cmd + [url])
    ATTACHED[0] = False


def toggle_video_setting():
    """Flip radio.no_video in state.json and apply it live over IPC.
    Returns (new_value, applied_live). Works for BOTH Popen-managed and
    reattached (detached) players — anything reachable over the socket."""
    st = load_state()
    st["radio"]["no_video"] = not st["radio"].get("no_video", False)
    save_state(st)
    show = not st["radio"]["no_video"]
    proc = PLAYER_PROC[0]
    has_target = ATTACHED[0] or (proc is not None and proc.poll() is None)
    live = has_target and mpv_ipc_set_vid(show)
    return st["radio"]["no_video"], live


def launch_player(cmd):
    # stdin=DEVNULL: without it mpv shares the terminal with our fzf UI and
    # steals keystrokes (races — 'd', '*' etc. randomly never reach menus).
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _fetch(url, timeout=12):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_initial_data(page):
    for pat in [
        r"var ytInitialData = ({.*?});</script>",
        r'window\["ytInitialData"\] = ({.*?});',
        r"ytInitialData\s*=\s*({.*?});",
    ]:
        m = re.search(pat, page, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None


def _text(obj):
    if not obj:
        return ""
    if "simpleText" in obj:
        return obj["simpleText"]
    return "".join(r.get("text", "") for r in obj.get("runs", []))


def _parse_ytm_results(data):
    results = []
    try:
        tabs = data.get("contents", {}).get("tabbedSearchResultsRenderer", {}).get("tabs", [])
        for tab in tabs:
            sections = (
                tab.get("tabRenderer", {})
                .get("content", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
            for section in sections:
                shelf = section.get("musicShelfRenderer", {})
                shelf_title = _text(shelf.get("title", {})).lower()
                for item in shelf.get("contents", []):
                    r = item.get("musicResponsiveListItemRenderer", {})
                    if not r:
                        continue
                    flex = r.get("flexColumns", [])
                    title = (
                        _text(
                            flex[0]
                            .get("musicResponsiveListItemFlexColumnRenderer", {})
                            .get("text", {})
                        )
                        if flex
                        else ""
                    )
                    if not title:
                        continue
                    ne = r.get("navigationEndpoint", {})
                    wne = ne.get("watchEndpoint") or ne.get("browseEndpoint") or {}
                    url = ""
                    if "videoId" in wne:
                        vid = wne["videoId"]
                        lid = wne.get("playlistId", "")
                        url = (
                            f"https://music.youtube.com/watch?v={vid}&list={lid}"
                            if lid
                            else f"https://music.youtube.com/watch?v={vid}"
                        )
                    elif "browseId" in wne:
                        url = f"https://music.youtube.com/browse/{wne['browseId']}"
                    if not url:
                        continue
                    subtitle = " ".join(
                        _text(
                            col.get("musicResponsiveListItemFlexColumnRenderer", {}).get("text", {})
                        )
                        for col in flex[1:]
                    ).lower()
                    type_label = "ytmusic"
                    for kw, label in [
                        ("playlist", "playlist/mix"),
                        ("mix", "playlist/mix"),
                        ("radio", "playlist/mix"),
                        ("album", "album"),
                        ("artist", "artist"),
                        ("song", "song"),
                    ]:
                        if kw in shelf_title or kw in subtitle:
                            type_label = label
                            break
                    results.append(
                        {
                            "title": html.unescape(title),
                            "url": url,
                            "type_label": type_label,
                            "source": "YouTube Music",
                        }
                    )
    except Exception:
        pass
    return results


def _parse_yt_results(data):
    results = []
    try:
        contents = (
            data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )
        for section in contents:
            for item in section.get("itemSectionRenderer", {}).get("contents", []):
                vr = item.get("videoRenderer", {})
                if vr:
                    vid = vr.get("videoId", "")
                    title = _text(vr.get("title", {}))
                    if vid and title:
                        results.append(
                            {
                                "title": html.unescape(title),
                                "url": f"https://www.youtube.com/watch?v={vid}",
                                "type_label": "video",
                                "source": "YouTube",
                            }
                        )
                pr = item.get("playlistRenderer", {})
                if pr:
                    lid = pr.get("playlistId", "")
                    title = _text(pr.get("title", {}))
                    if lid and title:
                        results.append(
                            {
                                "title": html.unescape(title),
                                "url": f"https://www.youtube.com/playlist?list={lid}",
                                "type_label": "playlist",
                                "source": "YouTube",
                            }
                        )
                rr = item.get("radioRenderer", {})
                if rr:
                    lid = rr.get("playlistId", "")
                    title = _text(rr.get("title", {}))
                    if lid and title:
                        results.append(
                            {
                                "title": html.unescape(title),
                                "url": f"https://www.youtube.com/watch?list={lid}",
                                "type_label": "radio",
                                "source": "YouTube",
                            }
                        )
    except Exception:
        pass
    return results


def _fetch_ytm(query, timeout=12):
    url = "https://music.youtube.com/youtubei/v1/search?prettyPrint=false"
    payload = json.dumps(
        {
            "context": {
                "client": {
                    "clientName": "WEB_REMIX",
                    "clientVersion": "1.20240101.01.00",
                    "hl": "en",
                }
            },
            "query": query,
        }
    ).encode()
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://music.youtube.com",
                "X-YouTube-Client-Name": "67",
                "X-YouTube-Client-Version": "1.20240101.01.00",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def search_music(query, timeout=12):
    results = []
    seen = set()
    data = _fetch_ytm(query, timeout)
    if data:
        for r in _parse_ytm_results(data):
            if r["url"] not in seen:
                seen.add(r["url"])
                results.append(r)
    yt_page = _fetch(
        "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query), timeout
    )
    if yt_page:
        data = _extract_initial_data(yt_page)
        if data:
            for r in _parse_yt_results(data):
                if r["url"] not in seen:
                    seen.add(r["url"])
                    results.append(r)
    return results


def pick_search_result(results, query, ui):
    if not results:
        action = ui.action(
            [f'No results for "{query}"', "─" * 44], [("retry search", "r"), ("back", "b")]
        )
        return "retry" if action == "r" else None
    lines = []
    for r in results:
        lines.append(
            f"{c(C.DIM, '[' + r['type_label'] + ']')}  {r['title']}  {c(C.DIM, r['source'])}"
        )
    idx = ui.pick(lines, "result ❯ ", f"Search: {c(C.BOLD, query)}\n{'─' * 48}\nEsc → back", "70%")
    if idx is None:
        return None
    return results[idx]


def pick_preset(presets, now_playing_name, ui):
    categories = {}
    for slug, info in presets.items():
        categories.setdefault(info["category"], []).append((slug, info))

    lines = []
    index_map = []
    for cat, items in categories.items():
        for slug, info in items:
            marker = f"{C.PLAY}" if info["name"] == now_playing_name else "  "
            badge = (
                "  🔍"
                if info.get("is_search_result")
                else f"  {c(C.DIM, '[' + info['category'] + ']')}"
            )
            lines.append(f"  {marker}  {info['name']}{badge}")
            index_map.append((slug, info))
    MANAGE = "manage presets"
    lines.append(f"  ⚙   {MANAGE}")
    header = (
        "yt-cli · radio: select a preset or type to search\n"
        + "─" * 48
        + "\n"
        + "Esc → quit   ·   type a query to search YouTube Music"
    )
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
        return ("url", q) if looks_like_url(q) else ("search", q)
    return None


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
            lines.append(
                f"{status}  {p['name']}  {c(C.DIM, '[' + p['category'] + ']')}  {c(C.DIM, p['slug'])}"
            )
            index_map.append(p)
        idx = ui.pick(lines, "preset ❯ ", "Manage presets\n" + "─" * 44 + "\nEsc → back", "70%")
        if idx is None:
            break
        target = index_map[idx]

        toggle_label = "disable" if target["enabled"] else "enable"
        action = ui.action(
            [
                f"{'●' if target['enabled'] else '○'}  {target['name']}  "
                f"[{target['category']}]  [{target['slug']}]",
                "─" * 44,
            ],
            [(toggle_label, toggle_label), ("delete", "delete"), ("back", "back")],
        )
        if action == "back":
            continue
        if action == "delete":
            confirmed = (
                ui.action(
                    [f'Delete "{target["name"]}"?', "─" * 44],
                    [("yes, delete", "yes"), ("no, keep", "no")],
                )
                == "yes"
            )
            if confirmed:
                radio_delete_preset(cfg, target["slug"])
                ui.info([f"✓  Deleted {target['name']}"], wait=False)
        elif action in ("enable", "disable"):
            radio_toggle_preset(cfg, target["slug"], enable=(action == "enable"))
            ui.info([f"✓  {target['name']} is now {action}d"], wait=False)

    return load_radio_cfg(load_config())[1]


def _find_bin_dir():
    d = Path.home() / ".local" / "bin" / "music"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prompt_save_search_result(cfg, result, ui):
    title = result["title"]
    action = ui.action(
        [f'Save "{title}" as a preset?', "─" * 48],
        [("yes, save as preset", "yes"), ("no, discard", "no")],
    )
    if action != "yes":
        return None

    ui.info([f'Save "{title}"', "", "Display name (Enter to use title):"], wait=False)
    name = ui.prompt("name") or title
    category = ui.prompt("category") or "Search"

    slug = radio_add_preset(cfg, name, result["url"], category=category)
    ui.info([f"✓  Saved as [preset.{slug}]"], wait=False)

    sym_path = _find_bin_dir() / slug
    create = (
        ui.action(
            [f'Create symlink "{name}"?', f"  {sym_path}  →  yt-cli {slug}", "─" * 48],
            [("yes — create symlink", "yes"), ("no, skip", "no")],
        )
        == "yes"
    )
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


class GoTo(Exception):
    def __init__(self, dest):
        super().__init__(dest)
        self.dest = dest


GO_TO_LABELS = {"radio": "Radio", "stations": "Stations", "news": "News", "search": "Search"}


def go_to_picker(ui):
    entries = ["Radio", "Stations", "News", "Search", "Home"]
    idx = ui.pick(entries, "go to ❯ ", "Go to\n" + "─" * 44 + "\nEsc → stay here", "35%")
    if idx is None:
        return None
    return {
        "Radio": "radio",
        "Stations": "stations",
        "News": "news",
        "Search": "search",
        "Home": "home",
    }[entries[idx]]


def prompt_save_channel(cfg, ui, name, url, channel_id=None, keywords=None, fallback_url=None):
    """Save a news source. Captures channel_id when resolvable (one yt-dlp
    call); falls back to keyword-only save when lookup fails."""
    if not channel_id:
        sp = Spinner("Resolving channel...")
        sp.start()
        meta = resolve_url_meta(url)
        if meta and meta.get("channel_id"):
            channel_id = meta["channel_id"]
            name = meta.get("uploader") or name
            sp.done(f"Channel: {name}")
        else:
            sp.fail("Channel lookup failed")
    if channel_id:
        action = ui.action(
            [f'Save "{name}" as a permanent source?', "─" * 44],
            [("yes, save full channel", "yes"), ("no, cancel", "no")],
        )
        if action != "yes":
            return None
        fallback_url = fallback_url or f"https://www.youtube.com/channel/{channel_id}/streams"
    else:
        action = ui.action(
            [f'Save "{name}" as keyword-only source?', "─" * 44],
            [("yes, save keyword-only", "yes"), ("no, cancel", "no")],
        )
        if action != "yes":
            return None
    ui.info([f'Save "{name}"', "", "Display name (Enter to use suggested):"], wait=False)
    final_name = ui.prompt("name") or name
    ui.info(["Keywords, comma-separated (Enter to use name):"], wait=False)
    kw_raw = ui.prompt("keywords")
    kws = (
        [k.strip().lower() for k in kw_raw.split(",")]
        if kw_raw
        else (keywords or [final_name.lower()])
    )
    slug = news_add_source(cfg, final_name, kws, channel_id=channel_id, fallback_url=fallback_url)
    kind = "full-channel" if channel_id else "keyword-only"
    ui.info([f"✓  Saved as [{kind} source.{slug}]"], wait=False)
    time.sleep(0.8)
    return slug


def save_current(cfg, ui, item, default_kind="preset"):
    """Save the currently playing item without stopping playback."""
    title = item.get("title") or item.get("url", "")
    url = item.get("url", "")
    preset_choice = ("save as music/video preset", "preset")
    news_choice = ("save as news channel", "news")
    choices = (
        [news_choice, preset_choice] if default_kind == "news" else [preset_choice, news_choice]
    )
    action = ui.action([f'Save "{title}"', "─" * 48], choices)
    if action == "preset":
        return prompt_save_search_result(cfg, {"title": title, "url": url}, ui)
    if action == "news":
        return prompt_save_channel(
            cfg,
            ui,
            name=item.get("uploader") or title,
            url=url,
            channel_id=item.get("channel_id"),
            keywords=item.get("keywords"),
            fallback_url=item.get("fallback_url"),
        )
    return None


def url_entry_flow(cfg, ui, url):
    """A URL was typed into a query box. Resolve it and offer
    play / save-as-preset / save-as-news-channel. Returns a play item
    or None (saved or cancelled)."""
    sp = Spinner("Resolving URL...")
    sp.start()
    meta = resolve_url_meta(url)
    if meta:
        sp.done(f"Found: {meta['title'][:60]}")
    else:
        sp.fail("Could not resolve: using URL as-is")
    title = (meta or {}).get("title") or url[:60]
    action = ui.action(
        [f"URL:  {c(C.BOLD, title)}", f"   {c(C.DIM, url[:70])}", "─" * 48],
        [
            ("play now", "play"),
            ("save as preset", "preset"),
            ("save as news channel", "channel"),
            ("cancel", "cancel"),
        ],
    )
    if action == "play":
        return {
            "title": title,
            "url": url,
            "uploader": (meta or {}).get("uploader") or "Unknown",
            "duration": None,
        }
    if action == "preset":
        prompt_save_search_result(cfg, {"title": title, "url": url}, ui)
    elif action == "channel":
        prompt_save_channel(
            cfg,
            ui,
            name=(meta or {}).get("uploader") or title,
            url=url,
            channel_id=(meta or {}).get("channel_id"),
        )
    return None


def run_radio_mode(cfg, ui, stop, play_mpv, goodbye, on_esc="quit"):
    settings, presets = load_radio_cfg(cfg)
    now_playing = [None]
    search_cache = {}

    def play_radio(info):
        stop()
        NOW_PLAYING[0] = {"title": info["name"], "replay": lambda: play_radio(info)}
        cmd = build_mpv_cmd(info["url"], settings, preset_overrides=info)
        PLAYER_PROC[0] = launch_player(cmd)
        now_playing[0] = info
        done = threading.Event()
        IPC_DONE[0] = done
        threading.Thread(target=_ipc_watch, args=(info["name"], done), daemon=True).start()
        add_to_history(
            {"title": info["name"], "url": info["url"], "uploader": info["category"]},
            cfg["history_size"],
        )

    while True:
        np_name = now_playing[0]["name"] if now_playing[0] else None
        merged = dict(presets)
        merged.update(search_cache)
        result = pick_preset(merged, np_name, ui)

        if result is None:
            if on_esc == "home":
                return  # back to main menu, player keeps playing
            goodbye()
            return
        if result == "invalid":
            continue

        if isinstance(result, tuple) and result[0] == "manage":
            presets = manage_presets_screen(cfg, ui)
            continue

        if isinstance(result, tuple) and result[0] == "url":
            url = result[1] or ""
            info = {
                "name": url[:60],
                "url": url,
                "loop": settings["loop"],
                "no_video": settings["no_video"],
                "radio": False,
                "is_search_result": True,
            }
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
                info = {
                    "name": picked["title"],
                    "url": picked["url"],
                    "category": "Search",
                    "loop": settings["loop"],
                    "no_video": settings["no_video"],
                    "radio": False,
                    "is_search_result": True,
                    "_search_raw": picked,
                }
                search_cache[slug] = info
                play_radio(info)
                radio_playing_loop(cfg, ui, info, settings, stop, play_radio, goodbye)
                cfg = load_config()
                settings, presets = load_radio_cfg(cfg)
                if any(p["slug"] == slug for p in cfg["radio"]["presets"]):
                    search_cache.pop(slug, None)
                break
            continue

        slug, info = result
        play_radio(info)
        radio_playing_loop(cfg, ui, info, settings, stop, play_radio, goodbye)


def radio_playing_loop(cfg, ui, info, settings, stop_fn, play_fn, goodbye_fn):
    def play_search(item):
        # History/search items are SEARCH-format (title/url); map them into
        # radio format before hitting play_fn (which requires "name").
        # Read state at call time so video-toggle fallbacks stay in sync.
        st = load_state().get("radio", {})
        play_fn(
            {
                "name": item.get("title") or item.get("url", ""),
                "url": item["url"],
                "category": "Search",
                "loop": st.get("loop", False),
                "no_video": st.get("no_video", False),
                "radio": False,
                "is_search_result": True,
            }
        )

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
        actions = (
            PLAY_ACTIONS_RADIO
            if info.get("is_search_result")
            else [a for a in PLAY_ACTIONS_RADIO if a[1] != "k"]
        )
        action = ui.action(header_lines, actions)
        if action == "o":
            return False  # back to preset list, keep playing
        elif action == "v":
            nv, live = toggle_video_setting()
            settings["no_video"] = nv
            info["no_video"] = nv
            if not live:
                play_fn(info)  # fallback: full restart
        elif action == "l":
            settings["loop"] = not settings["loop"]
            info["loop"] = settings["loop"]
            st = load_state()
            st["radio"]["loop"] = settings["loop"]
            save_state(st)
            play_fn(info)
        elif action == "m":
            manage_presets_screen(cfg, ui)
        elif action == "k":
            save_current(
                cfg, ui, {"title": info["name"], "url": info["url"]}, default_kind="preset"
            )
        elif action == "g":
            dest = go_to_picker(ui)
            if dest:
                raise GoTo(dest)
        elif action == "s":
            run_search_mode(cfg, ui, stop_fn, play_search, return_home=True)
        elif action == "h":
            run_history_screen(cfg, ui, stop_fn, play_search)
        elif action == "d":
            print(f"\n  {c(C.GREEN, '▶')}  {c(C.DIM, 'detached · playing in background')}\n")
            sys.exit(0)
        elif action == "q":
            stop_fn()
            return True
        elif action == "x":
            goodbye_fn()
            sys.exit(0)


# ---------- MODE: STATIONS (real internet radio) ----------

RB_USER_AGENT = "yt-cli/1.1"
RB_FALLBACK_HOSTS = (
    "de1.api.radio-browser.info",
    "de2.api.radio-browser.info",
    "nl1.api.radio-browser.info",
)
RB_SEARCH_LIMIT = 30
RB_TIMEOUT = 8

STATION_FIELDS = (
    "slug",
    "name",
    "url",
    "stationuuid",
    "countrycode",
    "tags",
    "codec",
    "bitrate",
    "homepage",
    "favicon",
    "enabled",
)

_rb_host_cache: list = [None]
_rb_host_discovery: list = [None]
_STATION_SEARCH_CACHE = {}


def _rb_hosts():
    """radio-browser mirrors: discovered names first, hardcoded fallbacks last."""
    if _rb_host_discovery[0] is None:
        hosts = []
        try:
            _name, _aliases, ips = socket.gethostbyname_ex("all.api.radio-browser.info")
        except OSError:
            ips = []
        for ip in ips:
            try:
                name = socket.gethostbyaddr(ip)[0]
            except OSError:
                continue
            if name and name not in hosts:
                hosts.append(name)
        random.shuffle(hosts)
        for host in RB_FALLBACK_HOSTS:
            if host not in hosts:
                hosts.append(host)
        _rb_host_discovery[0] = hosts
    hosts = list(_rb_host_discovery[0])
    cached = _rb_host_cache[0]
    if cached in hosts:
        hosts.remove(cached)
        hosts.insert(0, cached)
    return hosts


def _rb_get(path, params=None, timeout=RB_TIMEOUT):
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    last_err = None
    for host in _rb_hosts()[:3]:
        try:
            req = urllib.request.Request(
                f"https://{host}{path}{qs}",
                headers={"User-Agent": RB_USER_AGENT, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            _rb_host_cache[0] = host
            return data
        except (OSError, ValueError) as e:
            last_err = e
    raise SearchError(f"radio-browser unavailable: {last_err}")


def _normalize_station(raw):
    """Map one radio-browser record to a station dict; None when unusable."""
    if not isinstance(raw, dict):
        return None
    url = (raw.get("url_resolved") or raw.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    name = html.unescape((raw.get("name") or "").strip())
    if not name:
        return None
    tags = []
    for tag in (raw.get("tags") or "").split(","):
        tag = tag.strip().lower()
        if tag and tag not in tags:
            tags.append(tag)

    def _int(key):
        try:
            return int(raw.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "name": name,
        "url": url,
        "stationuuid": (raw.get("stationuuid") or "").strip(),
        "countrycode": (raw.get("countrycode") or "").strip().upper()[:2],
        "tags": tags,
        "codec": (raw.get("codec") or "").strip(),
        "bitrate": _int("bitrate"),
        "homepage": (raw.get("homepage") or "").strip(),
        "favicon": (raw.get("favicon") or "").strip(),
        "clickcount": _int("clickcount"),
        "votes": _int("votes"),
        "enabled": True,
    }


def _merge_station_results(*groups):
    """Merge normalized station lists, dedupe, rank by clicks then votes."""
    merged = {}
    for group in groups:
        for station in group or []:
            if not station:
                continue
            key = station.get("stationuuid") or (station["name"].lower(), station["url"])
            if key not in merged:
                merged[key] = station
    out = list(merged.values())
    out.sort(key=lambda s: (-s.get("clickcount", 0), -s.get("votes", 0), s["name"].lower()))
    return out


def _normalize_station_list(raw):
    """Normalize a radio-browser response, tolerating non-list payloads."""
    if not isinstance(raw, list):
        return []
    return [s for s in (_normalize_station(r) for r in raw) if s]


def search_stations(query):
    """Search radio-browser by name, falling back to tags when sparse."""
    q = (query or "").strip()
    if not q:
        return []
    cache_key = q.lower()
    if cache_key in _STATION_SEARCH_CACHE:
        return list(_STATION_SEARCH_CACHE[cache_key])

    params = {
        "name": q,
        "limit": RB_SEARCH_LIMIT,
        "hidebroken": "true",
        "order": "clickcount",
        "reverse": "true",
    }
    results = _normalize_station_list(_rb_get("/json/stations/search", params))
    if len(results) < 8:
        tag_params = {
            "tag": q,
            "limit": RB_SEARCH_LIMIT,
            "hidebroken": "true",
            "order": "clickcount",
            "reverse": "true",
        }
        tag_results = _normalize_station_list(_rb_get("/json/stations/search", tag_params))
        results = _merge_station_results(results, tag_results)
    else:
        results = _merge_station_results(results)
    results = results[:RB_SEARCH_LIMIT]
    _STATION_SEARCH_CACHE[cache_key] = results
    return list(results)


def report_station_click(uuid):
    """Tell radio-browser a station was played; never block the UI."""
    if not uuid:
        return

    def work():
        try:
            host = _rb_host_cache[0] or _rb_hosts()[0]
            req = urllib.request.Request(
                f"https://{host}/json/url/{urllib.parse.quote(uuid)}",
                headers={"User-Agent": RB_USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=4):
                pass
        except (OSError, ValueError, IndexError):
            pass

    threading.Thread(target=work, daemon=True).start()


# --- saved stations ---


def get_all_stations(cfg):
    raw = cfg.get("stations")
    if not isinstance(raw, list):
        return []
    return [dict(s) for s in raw if isinstance(s, dict)]


def load_stations_cfg(cfg):
    return [s for s in get_all_stations(cfg) if s.get("enabled", True)]


def station_slug(name, existing=()):
    slug = _slugify(name or "station") or "station"
    base = slug
    i = 1
    while slug in existing:
        slug = f"{base}-{i}"
        i += 1
    return slug


def _station_record(station):
    rec = {k: station[k] for k in STATION_FIELDS if k in station}
    rec.setdefault("name", rec.get("url", "station"))
    rec.setdefault("enabled", True)
    rec["tags"] = list(rec.get("tags") or [])
    return rec


def station_add(cfg, station):
    """Add or update a saved station. Returns its slug."""
    rec = _station_record(station)
    if not isinstance(cfg.get("stations"), list):
        cfg["stations"] = []
    stations = cfg["stations"]
    for existing in stations:
        same_uuid = bool(rec.get("stationuuid")) and existing.get("stationuuid") == rec.get(
            "stationuuid"
        )
        if same_uuid or existing.get("url") == rec.get("url"):
            existing.update({k: v for k, v in rec.items() if k != "slug"})
            save_config(cfg)
            return existing.get("slug", "")
    slug = station_slug(rec.get("name") or rec.get("url"), {s.get("slug") for s in stations})
    stations.append({**rec, "slug": slug})
    save_config(cfg)
    return slug


def station_delete(cfg, slug):
    cfg["stations"] = [s for s in get_all_stations(cfg) if s.get("slug") != slug]
    save_config(cfg)


def station_toggle(cfg, slug, enable):
    if not isinstance(cfg.get("stations"), list):
        return
    for station in cfg["stations"]:
        if station.get("slug") == slug:
            station["enabled"] = enable
    save_config(cfg)


def station_for_url(url):
    for station in get_all_stations(load_config()):
        if station.get("url") == url:
            return station
    return None


def resolve_station(query, stations):
    q = (query or "").lower().strip()
    for station in stations:
        if station.get("slug") == q:
            return station
    for station in stations:
        if q in station.get("slug", "") or q in station.get("name", "").lower():
            return station
    return None


def resolve_music_target(cfg, name):
    """music/<slug> dispatch: YouTube presets win over saved stations."""
    settings, presets = load_radio_cfg(cfg)
    info = resolve_preset(name, presets)
    if info:
        return ("radio", info, settings)
    station = resolve_station(name, load_stations_cfg(cfg))
    if station:
        return ("station", station, None)
    return None


# --- station playback ---


def build_station_cmd(station):
    """Audio-only mpv command for a live stream; no yt-dlp extraction.

    keep-open=no is deliberate: a radio server that closes the stream must end
    the process so the supervisor below can reconnect. The user's mpv.conf sets
    keep-open=yes, which would leave a silent player alive and the UI claiming
    it is still playing. Positions are meaningless for live streams and would
    make a relaunch seek to a dead offset, so saving/resuming is disabled.
    """
    return [
        "mpv",
        "--force-window=no",
        "--vid=no",
        "--video=no",
        "--audio-display=no",
        "--keep-open=no",
        "--save-position-on-quit=no",
        "--no-resume-playback",
        "--network-timeout=15",
        "--ytdl=no",
        f"--input-ipc-server={IPC_SOCKET}",
        station["url"],
    ]


STATION_POLL_SECS = 2
STATION_RETRY_LIMIT = 5
STATION_MAX_BACKOFF = 10


def _station_launch(station, announce=True):
    """Start mpv for a station and watch it for the whole playback.

    announce=False is used by reconnects: the menu is already on screen and a
    fresh spinner would scribble over it.
    """
    title = station.get("name") or station.get("url", "Station")
    proc = launch_player(build_station_cmd(station))
    PLAYER_PROC[0] = proc
    done = threading.Event()
    IPC_DONE[0] = done
    end_state = {"reason": None, "ended": threading.Event()}
    threading.Thread(
        target=_ipc_watch,
        args=(title, done, end_state, True, announce),
        daemon=True,
    ).start()
    return proc, end_state


def _station_supervise(station, gen, proc, end_state, sleep_fn=time.sleep):
    """Reconnect a station whose stream ended (EOF, error, or dead connection).

    Manual replay already fixed this; the supervisor automates it. A player
    that survives one poll interval counts as healthy again, so a station that
    drops every few minutes reconnects forever, while a dead URL gives up after
    STATION_RETRY_LIMIT consecutive fast failures. An end-file reason of
    'quit'/'stop' means the user closed mpv: never fight that.
    """
    failures = 0
    while proc is not None:
        sleep_fn(STATION_POLL_SECS)
        if STATION_GEN[0] != gen or PLAYER_PROC[0] is not proc:
            return  # stopped, or another station/player took over
        if proc.poll() is None:
            failures = 0
            continue
        end_state["ended"].wait(1.5)  # let the event reader record the reason
        if end_state["reason"] in ("quit", "stop"):
            return
        if failures >= STATION_RETRY_LIMIT:
            return
        failures += 1
        sleep_fn(min(failures * 2, STATION_MAX_BACKOFF))
        if STATION_GEN[0] != gen or PLAYER_PROC[0] is not proc:
            return
        proc, end_state = _station_launch(station, announce=False)


def _station_play(cfg, station, stop_fn=stop_shared):
    stop_fn()
    STATION_GEN[0] += 1
    gen = STATION_GEN[0]
    title = station.get("name") or station.get("url", "Station")
    NOW_PLAYING[0] = {
        "title": title,
        "kind": "station",
        "replay": lambda: _station_play(cfg, station, stop_fn),
    }
    proc, end_state = _station_launch(station)
    if proc is not None:
        threading.Thread(
            target=_station_supervise,
            args=(station, gen, proc, end_state),
            daemon=True,
        ).start()
    add_to_history(
        {
            "title": title,
            "url": station.get("url", ""),
            "uploader": station.get("countrycode") or "Radio",
            "kind": "station",
        },
        cfg["history_size"],
    )
    report_station_click(station.get("stationuuid"))


def _station_cli_play(station, flags=None):
    flags = flags or {}
    cmd = build_station_cmd(station)
    if flags.get("background"):
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        print(f"  {C.PLAY}  Started in background: {station.get('name', station['url'])}")
    else:
        subprocess.run(cmd)


# --- stations UI ---


def format_station_row(station, now_playing_name=None):
    full = (station.get("name") or "?").strip()
    # Compare before truncating: the marker is built from the full name.
    marker = C.PLAY if full == now_playing_name else " "
    name = full[:29] + "…" if len(full) > 30 else full
    country = (station.get("countrycode") or "--")[:2]
    tags = ", ".join(station.get("tags") or [])[:20] or "-"
    codec = station.get("codec") or "?"
    bitrate = f"{station['bitrate']}k" if station.get("bitrate") else "-"
    meta = f"{country:<2}  {tags:<20}  {codec} {bitrate}"
    return f"{marker}  {name:<30}  {c(C.DIM, meta)}"


def pick_station(stations, now_playing_name, ui):
    lines = ["  🔍  Search stations..."]
    index_map = [("search", None)]
    for station in stations:
        lines.append(format_station_row(station, now_playing_name))
        index_map.append(("station", station))
    lines.append("  ⚙   manage stations")
    index_map.append(("manage", None))
    header = (
        "yt-cli · Stations"
        + "\n"
        + "─" * 48
        + "\n"
        + "Esc → back   ·   type to filter or search radio-browser.info"
    )
    query, idx = ui.pick_query(lines, "station ❯ ", header, "70%")
    if idx is not None and 0 <= idx < len(index_map):
        kind, value = index_map[idx]
        if kind == "search":
            q = ui.query(["Stations · search radio-browser.info", "─" * 48, "Esc → back"])
            if not q or not q.strip():
                return None
            q = q.strip()
            return ("url", q) if looks_like_url(q) else ("search", q)
        return (kind, value)
    q = query.strip()
    if not q:
        return None
    if q.lower() in ("manage stations", "manage", "m"):
        return ("manage", None)
    return ("url", q) if looks_like_url(q) else ("search", q)


def pick_station_results(results, query, ui):
    if not results:
        action = ui.action(
            [f'No stations for "{query}"', "─" * 44], [("retry search", "r"), ("back", "b")]
        )
        return "retry" if action == "r" else None
    lines = [format_station_row(station) for station in results]
    header = (
        f"Stations · {c(C.BOLD, query)} ({len(results)})" + "\n" + "─" * 48 + "\n" + "Esc → back"
    )
    idx = ui.pick(lines, "result ❯ ", header, "70%")
    if idx is None:
        return None
    return results[idx]


def _station_search_pick(ui, query):
    while True:
        clear_screen()
        sp = Spinner(f"Searching radio-browser.info for '{query}'...")
        sp.start()
        try:
            results = search_stations(query)
        except SearchError as e:
            sp.fail(str(e))
            if (
                ui.action([f"Search failed: {e}", "─" * 44], [("retry search", "r"), ("back", "b")])
                != "r"
            ):
                return None
            continue
        if results:
            sp.done(f"Found {len(results)} station(s)")
        else:
            sp.fail("No stations found")
        picked = pick_station_results(results, query, ui)
        if picked == "retry":
            continue
        return picked


def prompt_save_station(cfg, ui, station):
    title = station.get("name") or station.get("url", "station")
    action = ui.action(
        [f'Save "{title}" as a station?', "─" * 48],
        [("yes, save station", "yes"), ("no, discard", "no")],
    )
    if action != "yes":
        return None
    ui.info([f'Save "{title}"', "", "Display name (Enter to use station name):"], wait=False)
    name = ui.prompt("name") or title
    slug = station_add(cfg, {**station, "name": name})
    ui.info([f"✓  Saved as [station.{slug}]"], wait=False)

    sym_path = _find_bin_dir() / slug
    create = (
        ui.action(
            [f'Create symlink "{name}"?', f"  {sym_path}  →  yt-cli {slug}", "─" * 48],
            [("yes, create symlink", "yes"), ("no, skip", "no")],
        )
        == "yes"
    )
    if create:
        tune_path = Path(sys.argv[0]).resolve()
        try:
            if sym_path.is_symlink() or sym_path.exists():
                if sym_path.resolve() == tune_path:
                    ui.info([f"✓  Already linked: {sym_path}"], wait=False)
                else:
                    ui.info([f"⚠  Link exists, not overwritten: {sym_path}"], wait=False)
            else:
                sym_path.symlink_to(tune_path)
                ui.info([f"✓  {sym_path}"], wait=False)
        except OSError as e:
            ui.info([f"⚠  Symlink failed: {e}"], wait=False)
    time.sleep(0.8)
    return slug


def manage_stations_screen(cfg, ui):
    while True:
        all_stations = get_all_stations(cfg)
        if not all_stations:
            ui.info(["No saved stations."])
            break
        lines = []
        index_map = []
        for station in all_stations:
            status = c(C.GREEN, "●") if station.get("enabled", True) else c(C.DIM, "○")
            lines.append(
                f"{status}  {station.get('name', '?')}  "
                f"{c(C.DIM, '[' + station.get('slug', '') + ']')}"
            )
            index_map.append(station)
        idx = ui.pick(lines, "station ❯ ", "Manage stations\n" + "─" * 44 + "\nEsc → back", "70%")
        if idx is None:
            break
        target = index_map[idx]
        toggle = "disable" if target.get("enabled", True) else "enable"
        action = ui.action(
            [f"{target.get('name', '?')}  [{target.get('slug', '')}]", "─" * 44],
            [(toggle, toggle), ("remove", "remove"), ("back", "back")],
        )
        if action == "back":
            continue
        if action == "remove":
            confirmed = (
                ui.action(
                    [f'Remove "{target.get("name", "?")}"?', "─" * 44],
                    [("yes, remove", "yes"), ("no, keep", "no")],
                )
                == "yes"
            )
            if confirmed:
                station_delete(cfg, target.get("slug", ""))
                ui.info([f"✓  Removed {target.get('name', '?')}"], wait=False)
        elif action in ("enable", "disable"):
            station_toggle(cfg, target.get("slug", ""), enable=(action == "enable"))
            ui.info([f"✓  {target.get('name', '?')} is now {action}d"], wait=False)
    return load_stations_cfg(load_config())


PLAY_ACTIONS_STATION = [
    ("other results", "o"),
    ("new search", "s"),
    ("history", "h"),
    ("replay", "r"),
    ("go to…", "g"),
    ("stop player", "q"),
    ("exit", "x"),
    ("keep (save)", "k"),
    ("detach & exit", "d"),
]


def station_song_title(station):
    """Current ICY song title for the station's own player, or "".

    The shared IPC socket may belong to another player; require the path to
    match the station URL so a YouTube title never shows up as radio metadata.
    """
    url = station.get("url") or ""
    try:
        if url and ipc_rpc(["get_property", "path"], timeout=1) != url:
            return ""
    except (OSError, ValueError, RuntimeError):
        return ""
    for prop in ("metadata/by-key/icy-title", "media-title"):
        try:
            title = ipc_rpc(["get_property", prop], timeout=1)
        except (OSError, ValueError, RuntimeError):
            continue
        if isinstance(title, str) and title.strip():
            return title.strip()
    return ""


def station_playing_loop(cfg, ui, station, stop_fn, play_fn, goodbye_fn):
    while True:
        name = station.get("name") or station.get("url", "")
        tags = ", ".join(station.get("tags") or [])[:40] or "-"
        codec = station.get("codec") or "?"
        bitrate = f"{station['bitrate']}k" if station.get("bitrate") else "-"
        header_lines = [
            f"▶  {c(C.BOLD, name[:60])}",
            f"   {c(C.DIM, 'Country')} : "
            f"{station.get('countrycode') or '--'}   "
            f"{c(C.DIM, 'Codec')} : {codec}   "
            f"{c(C.DIM, 'Bitrate')} : {bitrate}",
            f"   {c(C.DIM, tags)}",
        ]
        song = station_song_title(station)
        if song and song != name:
            header_lines.insert(1, f"   {c(C.DIM, '♪')}  {song[:60]}")
        if PLAYER_PROC[0] is not None and PLAYER_PROC[0].poll() is not None:
            header_lines.append(f"   {c(C.YELLOW, '⚠  Player exited.')}")
        actions = (
            PLAY_ACTIONS_STATION
            if not station.get("slug")
            else [a for a in PLAY_ACTIONS_STATION if a[1] != "k"]
        )
        action = ui.action(header_lines, actions)
        if action == "o":
            return False
        if action == "s":
            q = ui.query(["Stations · search radio-browser.info", "─" * 48, "Esc → back"])
            if q and q.strip():
                q = q.strip()
                if looks_like_url(q):
                    new_station = {"name": q[:60], "url": q}
                else:
                    new_station = _station_search_pick(ui, q)
                if new_station:
                    station = new_station
                    play_fn(station)
            continue
        if action == "h":
            run_history_screen(cfg, ui, stop_fn, _search_play_default(cfg))
        elif action == "r":
            play_fn(station)
        elif action == "g":
            dest = go_to_picker(ui)
            if dest:
                raise GoTo(dest)
        elif action == "k":
            slug = prompt_save_station(cfg, ui, station)
            if slug and not station.get("slug"):
                station = {**station, "slug": slug}
            cfg = load_config()
        elif action == "d":
            print(f"\n  {c(C.GREEN, '▶')}  {c(C.DIM, 'detached · playing in background')}\n")
            sys.exit(0)
        elif action == "q":
            stop_fn()
            return True
        elif action == "x":
            goodbye_fn()
            sys.exit(0)


def run_stations_mode(cfg, ui, stop, goodbye, on_esc="quit"):
    while True:
        stations = load_stations_cfg(cfg)
        now = NOW_PLAYING[0]
        np_name = now.get("title") if now and now.get("kind") == "station" else None
        result = pick_station(stations, np_name, ui)
        if result is None:
            if on_esc == "home":
                return  # back to main menu, player keeps playing
            goodbye()
            return

        kind, value = result
        if kind == "manage":
            manage_stations_screen(cfg, ui)
            cfg = load_config()
            continue
        if kind == "url":
            url = value if isinstance(value, str) else ""
            station = {"name": url[:60], "url": url}
        elif kind == "search":
            station = _station_search_pick(ui, value)
            if station is None:
                continue
        else:
            station = value

        def play_fn(st):
            _station_play(cfg, st, stop_fn=stop)

        play_fn(station)
        station_playing_loop(cfg, ui, station, stop, play_fn, goodbye)
        cfg = load_config()


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
        "player": load_state().get("news", {}).get("player", n.get("player", "auto")),
        "quality": n.get("quality", cfg.get("quality", "720")),
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
    cfg["news"]["sources"].append(
        {
            "id": slug,
            "name": name,
            "channel_id": channel_id or "",
            "keywords": keywords or [name.lower()],
            "fallback_url": fallback_url
            or f"https://www.youtube.com/results?search_query={urllib.parse.quote(name)}",
            "enabled": True,
        }
    )
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


# ─── HTTP ─────────────────────────────────────────────────────────────────────


def fetch_url(url, headers=None, timeout=15):
    if headers is None:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"URL Error: {e.reason}"
    except Exception as e:
        return f"Error: {str(e)}"


# ─── YOUTUBE SCRAPING ─────────────────────────────────────────────────────────


def extract_yt_initial_data(html_content):
    patterns = [
        r"var ytInitialData = ({.*?});</script>",
        r'window\["ytInitialData"\] = ({.*?});',
        r"ytInitialData\s*=\s*({.*?});",
    ]
    for pattern in patterns:
        match = re.search(pattern, html_content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    return None


def parse_relative_time(time_text):
    if not time_text:
        return 9999
    time_text = time_text.lower()
    patterns = [
        (r"(\d+)\s*hour", 1),
        (r"(\d+)\s*day", 24),
        (r"(\d+)\s*week", 168),
        (r"(\d+)\s*month", 720),
        (r"(\d+)\s*minute", 1 / 60),
        (r"(\d+)\s*second", 1 / 3600),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, time_text)
        if match:
            return float(match.group(1)) * multiplier
    if any(x in time_text for x in ["just now", "moments ago", "minute ago"]):
        return 0
    return 9999


def parse_video_renderer(video, keywords, channel_name, recent_hours=24):
    try:
        video_id = video.get("videoId", "")
        title_runs = video.get("title", {}).get("runs", [])
        title = "".join(run.get("text", "") for run in title_runs)
        if not title:
            title = video.get("title", {}).get("simpleText", "")

        title_lower = title.lower()
        if not any(kw in title_lower for kw in keywords):
            return None

        # Live badge check
        is_live = False
        for badge in video.get("badges", []):
            br = badge.get("metadataBadgeRenderer", {})
            if (
                br.get("style") == "BADGE_STYLE_TYPE_LIVE_NOW"
                or br.get("label", "").upper() == "LIVE"
            ):
                is_live = True
                break

        for overlay in video.get("thumbnailOverlays", []):
            if "thumbnailOverlayTimeStatusRenderer" in overlay:
                if overlay["thumbnailOverlayTimeStatusRenderer"].get("style") == "LIVE":
                    is_live = True
                    break

        # Recency
        published_text = ""
        hours_ago = 9999
        if "publishedTimeText" in video:
            pt = video["publishedTimeText"]
            if "runs" in pt:
                published_text = "".join(r.get("text", "") for r in pt["runs"])
            else:
                published_text = pt.get("simpleText", "")
            hours_ago = parse_relative_time(published_text)

        if not is_live and hours_ago > recent_hours:
            return None

        # View count
        view_count = ""
        if "viewCountText" in video:
            vct = video["viewCountText"]
            if "simpleText" in vct:
                view_count = vct["simpleText"]
            elif "runs" in vct:
                view_count = "".join(r.get("text", "") for r in vct["runs"])

        return {
            "id": video_id,
            "title": html.unescape(title),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "is_live": is_live,
            "view_count": view_count,
            "published_text": published_text,
            "hours_ago": hours_ago,
            "source": channel_name,
        }
    except Exception:
        return None


def find_streams(data, keywords, channel_name, recent_hours=24):
    streams = []
    try:
        contents = data.get("contents") or data.get("response", {}).get("contents")
        if not contents:
            return streams

        browse = contents.get("twoColumnBrowseResultsRenderer", {})
        tabs = browse.get("tabs", [])

        for tab in tabs:
            content = tab.get("tabRenderer", {}).get("content", {})
            grid = content.get("richGridRenderer", {})
            sections = grid.get("contents", []) or content.get("sectionListRenderer", {}).get(
                "contents", []
            )

            for section in sections:
                videos = []

                if "richItemRenderer" in section:
                    item = section["richItemRenderer"].get("content", {})
                    v = item.get("videoRenderer")
                    if v:
                        videos.append(v)

                elif "shelfRenderer" in section:
                    shelf_content = section["shelfRenderer"].get("content", {})
                    if "horizontalListRenderer" in shelf_content:
                        for item in shelf_content["horizontalListRenderer"].get("items", []):
                            v = item.get("gridVideoRenderer")
                            if v:
                                videos.append(v)
                    elif "expandedShelfContentsRenderer" in shelf_content:
                        for item in shelf_content["expandedShelfContentsRenderer"].get("items", []):
                            v = item.get("videoRenderer")
                            if v:
                                videos.append(v)

                elif "itemSectionRenderer" in section:
                    for item in section["itemSectionRenderer"].get("contents", []):
                        v = item.get("videoRenderer") or item.get("gridVideoRenderer")
                        if v:
                            videos.append(v)

                for v in videos:
                    info = parse_video_renderer(v, keywords, channel_name, recent_hours)
                    if info:
                        streams.append(info)

    except Exception:
        pass

    streams.sort(key=lambda x: (not x["is_live"], x.get("hours_ago", 9999)))
    return streams


def search_youtube(query, keywords, channel_id=None, timeout=15, recent_hours=24):
    streams = []

    # Channel streams page
    if channel_id:
        if channel_id.startswith("@") or not channel_id.startswith("UC"):
            handle = channel_id if channel_id.startswith("@") else f"@{channel_id}"
            channel_url = f"https://www.youtube.com/{handle}/streams"
        else:
            channel_url = f"https://www.youtube.com/channel/{channel_id}/streams"

        html_content = fetch_url(channel_url, timeout=timeout)
        if not html_content.startswith(("Error", "HTTP")):
            data = extract_yt_initial_data(html_content)
            if data:
                streams.extend(find_streams(data, keywords, query, recent_hours))

    # YouTube search (live filter)
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query + ' live')}&sp=EgJAAQ%253D%253D"
    html_content = fetch_url(search_url, timeout=timeout)
    if not html_content.startswith(("Error", "HTTP")):
        data = extract_yt_initial_data(html_content)
        if data:
            try:
                contents = (
                    data.get("contents", {})
                    .get("twoColumnSearchResultsRenderer", {})
                    .get("primaryContents", {})
                    .get("sectionListRenderer", {})
                    .get("contents", [])
                )
                for section in contents:
                    if "itemSectionRenderer" in section:
                        for item in section["itemSectionRenderer"].get("contents", []):
                            v = item.get("videoRenderer")
                            if v:
                                info = parse_video_renderer(
                                    v, keywords, "YouTube Search", recent_hours
                                )
                                if info:
                                    streams.append(info)
            except Exception:
                pass

    # Deduplicate by video ID, live first
    seen, unique = set(), []
    for s in streams:
        if s["id"] not in seen:
            seen.add(s["id"])
            unique.append(s)
    unique.sort(key=lambda x: (not x["is_live"], x.get("hours_ago", 9999)))
    return unique


# ─── PLAYBACK ─────────────────────────────────────────────────────────────────


def get_direct_stream_url(youtube_url, quality="720"):
    quality_map = {
        "360": "best[height<=360]/worst",
        "480": "best[height<=480]/best",
        "720": "best[height<=720]/best",
        "1080": "best[height<=1080]/best",
        "best": "best",
    }
    format_str = quality_map.get(quality, quality_map["720"])
    for tool in ["yt-dlp", "youtube-dl"]:
        try:
            result = subprocess.run(
                [tool, "-g", "-f", format_str, youtube_url],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split("\n")[0]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return None


def looks_like_url(q):
    q = q.strip().lower()
    return q.startswith(("http://", "https://", "www.", "youtu.be/"))


def resolve_url_meta(url):
    """Resolve a video/channel URL via yt-dlp. Returns
    {title, uploader, channel_id, is_live} or None on failure."""
    for tool in ["yt-dlp", "youtube-dl"]:
        try:
            result = subprocess.run(
                [tool, "--dump-single-json", "--skip-download", "--no-warnings", url],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                if not isinstance(data, dict):
                    continue
                return {
                    "title": data.get("title") or url,
                    "uploader": data.get("uploader") or data.get("channel") or "",
                    "channel_id": data.get("channel_id") or "",
                    "is_live": bool(data.get("is_live")),
                }
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, ValueError):
            continue
    return None


def detect_player():
    for p in ["mpv", "vlc", "ffplay"]:
        try:
            subprocess.run(["which", p], capture_output=True, check=True)
            return p
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return None


def list_players():
    """Return list of installed players."""
    found = []
    for p in ["mpv", "vlc", "ffplay"]:
        try:
            subprocess.run(["which", p], capture_output=True, check=True)
            found.append(p)
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return found


def launch_news_player(url, player, quality="720", direct_url=None):
    """Launch player as background process. Returns Popen object.
    mpv gets the watch URL (yt-dlp handles YouTube live properly);
    other players get the pre-resolved direct URL."""
    if player == "mpv":
        cmd = [
            "mpv",
            "--really-quiet",
            f"--ytdl-format=best[height<={quality}]/best",
            url,
        ]
    else:
        target = direct_url or url
        if player == "vlc":
            cmd = ["vlc", "--intf", "dummy", target]
        elif player == "ffplay":
            cmd = ["ffplay", "-window_title", "News Stream", "-autoexit", target]
        else:
            cmd = [player, target]

    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,  # don't steal TUI keystrokes
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # gives it its own process group
    )


# ─── NEWS MODE ─────────────────────────────────────────────────────────────────


def render_stream_badge(stream):
    if stream["is_live"]:
        return C.LIVE
    hrs = stream.get("hours_ago", 9999)
    if hrs < 1:
        label = "just now"
    elif hrs < 2:
        label = "1 hr ago"
    else:
        label = f"{int(hrs)} hrs ago"
    return f"{C.RECENT} {c(C.YELLOW, label)}"


def render_header(subtitle="", now_playing=None):
    width = get_terminal_width()
    time_str = datetime.now().strftime("%I:%M %p")
    print()
    print(f"  {c(C.BOLD + C.CYAN, APP_NAME)}  {c(C.DIM, subtitle)}  {c(C.DIM, time_str)}")
    if now_playing:
        ticker = now_playing["title"][: width - 6]
        print(f"  {C.LIVE}  {c(C.DIM, ticker)}")
    print(c(C.DIM, "─" * width))
    print()


def pick_source(sources, ui):
    source_list = list(sources.items())
    lines = [info["name"] for key, info in source_list]
    MANAGE = "manage sources"
    lines.append(f"⚙   {MANAGE}")
    header = (
        "yt-cli · news: select a source or type to search YouTube\n"
        + "─" * 48
        + "\n"
        + "Esc → quit"
    )
    query, idx = ui.pick_query(lines, "source ❯ ", header, "60%")
    if idx is not None:
        if idx < len(source_list):
            return source_list[idx]
        if idx == len(source_list):
            return ("manage", None)
    if query.strip().lower() in ("manage sources", "manage", "m"):
        return ("manage", None)
    if query.strip():
        q = query.strip()
        return ("url", q) if looks_like_url(q) else ("search", q)
    return None


def pick_stream(streams, source_name, ui):
    if not streams:
        action = ui.action(
            [f"{source_name} · no streams found", "─" * 40],
            [("retry", "r"), ("back", "b"), ("quit", "q")],
        )
        if action == "r":
            return "retry"
        return None
    lines = []
    for stream in streams:
        badge = render_stream_badge(stream)
        views = f"  {c(C.DIM, '👥 ' + stream['view_count'])}" if stream["view_count"] else ""
        lines.append(f"{badge}  {stream['title']}{views}")
    idx = ui.pick(
        lines,
        "stream ❯ ",
        f"{source_name} · {len(streams)} stream(s) found\n{'─' * 40}\nEsc → back",
        "60%",
    )
    if idx is None:
        return None
    return streams[idx]


def pick_player(current_player, ui):
    players = list_players()
    if not players:
        return None
    lines = [f"{'▶ ' if p == current_player else '  '}{p}" for p in players]
    idx = ui.pick(lines, "player ❯ ", "Select player\n" + "─" * 44, "30%")
    if idx is None:
        return None
    return players[idx]


def confirm_search(query, ui):
    action = ui.action(
        [f"Search YouTube for:  {c(C.BOLD + C.CYAN, query)}", "─" * 44],
        [("yes, search", "yes"), ("no, go back", "no")],
    )
    return action == "yes"


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
            lines.append(f"{status}  {s['name']}  {c(C.DIM, '[' + s['id'] + ']')}")
            index_map.append(s)
        idx = ui.pick(lines, "source ❯ ", "Manage sources\n" + "─" * 44 + "\nEsc → back", "60%")
        if idx is None:
            break
        target = index_map[idx]

        toggle_label = "disable" if target["enabled"] else "enable"
        action = ui.action(
            [f"{'●' if target['enabled'] else '○'}  {target['name']}  [{target['id']}]", "─" * 44],
            [(toggle_label, toggle_label), ("delete", "delete"), ("back", "back")],
        )
        if action == "back":
            continue
        if action == "delete":
            confirmed = (
                ui.action(
                    [f'Delete "{target["name"]}"?', "─" * 44],
                    [("yes, delete", "yes"), ("no, keep", "no")],
                )
                == "yes"
            )
            if confirmed:
                news_delete_source(cfg, target["id"])
                ui.info([f"✓  Deleted {target['name']}"], wait=False)
        elif action in ("enable", "disable"):
            news_toggle_source(cfg, target["id"], enable=(action == "enable"))
            ui.info([f"✓  {target['name']} is now {action}d"], wait=False)

    return load_news_cfg(load_config())[1]


def run_news_mode(cfg, ui, stop, goodbye, on_esc="quit"):
    settings, sources = load_news_cfg(cfg)
    current_stream: list = [None]
    player = [settings["player"]]

    def resolve_player():
        p = player[0]
        if p == "auto":
            p = detect_player()
        return p

    def play_news(stream):
        stop()
        NOW_PLAYING[0] = {"title": stream["title"], "replay": lambda: play_news(stream)}
        clear_screen()
        pl = resolve_player()
        direct_url = None
        if pl != "mpv":
            # mpv resolves the watch URL itself (single pass, handles live);
            # only vlc/ffplay need the pre-resolved direct stream.
            sp = Spinner("Tuning in to stream...")
            sp.start()
            direct_url = get_direct_stream_url(stream["url"], settings["quality"])
            if direct_url:
                sp.done("On air")
            else:
                sp.fail("Resolving failed: using watch URL")
        PLAYER_PROC[0] = launch_news_player(
            stream["url"], pl, settings["quality"], direct_url=direct_url
        )
        current_stream[0] = stream
        done = threading.Event()
        IPC_DONE[0] = done
        threading.Thread(target=_ipc_watch, args=(stream["title"], done), daemon=True).start()
        add_to_history(
            {"title": stream["title"], "url": stream["url"], "uploader": stream["source"]},
            cfg["history_size"],
        )

    while True:
        result = pick_source(sources, ui)

        if result is None:
            if on_esc == "home":
                return  # back to main menu, player keeps playing
            goodbye()
            return
        if result == "invalid":
            continue
        if result == ("manage", None):
            sources = manage_sources_screen(cfg, ui)
            continue

        if isinstance(result, tuple) and result[0] == "url":
            it = url_entry_flow(cfg, ui, result[1])
            if it is not None:
                stream = {
                    "title": it["title"],
                    "url": it["url"],
                    "source": it.get("uploader") or "URL",
                    "is_live": bool(it.get("is_live")),
                    "view_count": "",
                }
                pl = resolve_player()
                if not pl:
                    ui.info(["❌ No player found (mpv / vlc / ffplay)."])
                    continue
                play_news(stream)
                while True:
                    st = current_stream[0]
                    action = ui.action(
                        [
                            f"▶  {c(C.BOLD, st['title'])}",
                            f"   {c(C.DIM, 'Source')}  : {st['source']}",
                            "─" * 44,
                        ],
                        PLAY_ACTIONS_NEWS,
                    )
                    if action == "r":
                        play_news(st)
                    elif action == "k":
                        save_current(
                            cfg,
                            ui,
                            {"title": st["title"], "url": st["url"], "uploader": st.get("source")},
                            default_kind="news",
                        )
                    elif action == "g":
                        dest = go_to_picker(ui)
                        if dest:
                            raise GoTo(dest)
                    elif action == "d":
                        print(
                            f"\n  {c(C.GREEN, '▶')}  {c(C.DIM, 'detached · playing in background')}\n"
                        )
                        sys.exit(0)
                    elif action == "q":
                        stop()
                        break
                    elif action == "x":
                        goodbye()
                        sys.exit(0)
                    else:  # o/s/h/p/f → back to source list
                        break
            continue

        source_info: dict = {}
        if isinstance(result, tuple) and result[0] == "search":
            query = str(result[1] or "")
            if not confirm_search(query, ui):
                continue
            source_info = {
                "name": query,
                "channel_id": None,
                "keywords": [w.lower() for w in query.split()],
                "fallback_url": "https://www.youtube.com/results?search_query="
                + urllib.parse.quote(query),
            }
            is_search_result = True
        else:
            raw_info = result[1] if isinstance(result, tuple) else None
            if not isinstance(raw_info, dict):
                continue
            source_info = raw_info
            is_search_result = False

        while True:
            clear_screen()
            sp = Spinner("Scanning for live streams...")
            sp.start()
            cached_streams = search_youtube(
                source_info["name"],
                source_info["keywords"],
                source_info.get("channel_id"),
                settings["timeout"],
                settings["recent_hours"],
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
                            f"   {c(C.YELLOW, '⚠  Player exited: stream may have ended.')}"
                        )
                    actions = (
                        PLAY_ACTIONS_NEWS
                        if is_search_result
                        else [a for a in PLAY_ACTIONS_NEWS if a[1] != "k"]
                    )
                    action = ui.action(header_lines, actions)

                    if action == "o":
                        break  # stream list, player keeps playing
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
                            st = load_state()
                            st["news"]["player"] = new_player
                            save_state(st)
                            play_news(stream)
                    elif action == "k":
                        save_current(
                            cfg,
                            ui,
                            {
                                "title": stream["title"],
                                "url": stream["url"],
                                "uploader": stream.get("source"),
                                "channel_id": source_info.get("channel_id"),
                                "keywords": source_info.get("keywords"),
                                "fallback_url": source_info.get("fallback_url"),
                            },
                            default_kind="news",
                        )
                    elif action == "g":
                        dest = go_to_picker(ui)
                        if dest:
                            raise GoTo(dest)
                    elif action == "d":
                        print(
                            f"\n  {c(C.GREEN, '▶')}  {c(C.DIM, 'detached · playing in background')}\n"
                        )
                        sys.exit(0)
                    elif action == "q":
                        stop()
                        break
                    elif action == "x":
                        goodbye()
                        return

                cfg = load_config()
                settings, sources = load_news_cfg(cfg)

                if go_to_main_from_playing:
                    go_back_to_main = True
                    break
                if refetch_from_playing:
                    break

            if go_back_to_main:
                break


def play_stream_cli(url, player=None, quality="720", direct_url=None):
    if not player or player == "auto":
        player = detect_player()
        if not player:
            print("❌ No suitable player found (mpv, vlc, or ffplay)")
            return False
    print(f"▶️  Playing with {player}...")
    try:
        if player == "mpv":
            cmd = [
                "mpv",
                "--fs",
                "--really-quiet",
                f"--ytdl-format=best[height<={quality}]/best",
                url,
            ]
        else:
            target = direct_url or url
            if player == "vlc":
                cmd = ["vlc", target, "--fullscreen"]
            elif player == "ffplay":
                cmd = ["ffplay", "-fs", "-window_title", "News Stream", target]
            else:
                cmd = [player, target]
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
    yt-cli --news / -n            Interactive TUI mode (news)
    yt-cli --news <source> [player]  Play directly (CLI mode)
    yt-cli --news --list
    yt-cli --news --config
    yt-cli --news --help

SUPPORTED SOURCES:""")
    for key, info in sorted(sources.items()):
        print(f"    {'✓' if info['enabled'] else '✗'}  {key:<25} - {info['name']}")
    print("""
PLAYERS:
    auto  mpv  vlc  ffplay

EXAMPLES:
    yt-cli --news
    yt-cli --news tv-patrol
    yt-cli --news 24-oras vlc
""")


def edit_news_config():
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(CONFIG_PATH)])


def resolve_preset(query, presets):
    q = query.lower().strip()
    aliases = {
        "lp": "linkin-park",
        "bb": "breaking-benjamin",
        "mcr": "mcr",
        "eheads": "eraserheads",
        "bfmv": "bfmv",
        "a7x": "a7x",
    }
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
        if a in ("--no-video", "--no-window"):
            flags["no_video"] = True
        elif a == "--video":
            flags["no_video"] = False
        elif a in ("--loop", "-l"):
            flags["loop"] = True
        elif a == "--no-loop":
            flags["loop"] = False
        elif a in ("--bg", "--background"):
            flags["background"] = True
        elif a in ("--fg", "--foreground"):
            flags["background"] = False
        elif a == "--radio":
            flags["radio"] = True
        elif a.startswith("--quality="):
            flags["quality"] = a.split("=")[1]
        elif a == "--shuffle":
            flags["shuffle"] = True
        elif a == "--no-resume":
            flags["no_resume"] = True
        else:
            remaining.append(a)
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
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        print(f"  {C.PLAY}  Started in background: {info['name']}")
    else:
        subprocess.run(cmd)


def print_radio_help(presets):
    categories = {}
    for slug, info in presets.items():
        categories.setdefault(info["category"], []).append((slug, info["name"]))
    print(f"""
🎵 RADIO · Music preset player (yt-cli)

CONFIG: {CONFIG_PATH}

USAGE:
    yt-cli                        Main menu
    yt-cli --radio / -r           Radio TUI
    yt-cli --radio <preset> [options]  Direct play
    yt-cli --radio <url> [options]     Play a URL directly
    yt-cli --radio --list
    yt-cli --radio --config
    yt-cli --radio --help

OPTIONS:
    --no-video, --no-window     Force audio-only (suppress window)
    --loop, -l                  Loop playlist
    --no-loop                   Don't loop
    --bg, --background          Run in background
    --fg, --foreground          Run in foreground
    --radio                     Seed YouTube radio mix
    --quality=<n>               Cap video height (default 720, from config)
    --shuffle                   Shuffle playlist

PRESETS:""")
    for cat, items in categories.items():
        print(f"\n  {cat}:")
        for slug, name in items:
            print(f"    {slug:<28}  {name}")


# ---------- entry point ----------


def check_dependencies(need_ytdlp=True):
    tools = ("mpv", "yt-dlp") if need_ytdlp else ("mpv",)
    missing = [tool for tool in tools if not shutil.which(tool)]
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
        print_radio_help(presets)
        sys.exit(0)

    if args[0] in ("--list", "-l", "list"):
        categories = {}
        for slug, info in presets.items():
            categories.setdefault(info["category"], []).append((slug, info))
        print(f"\n{c(C.BOLD + C.CYAN, '🎵  radio')}  ·  presets\n")
        for cat, items in categories.items():
            print(f"  {c(C.DIM + C.BOLD, cat)}")
            for slug, info in items:
                print(f"    {c(C.CYAN, slug):<30}  {info['name']}")
            print()
        sys.exit(0)

    if args[0] in ("--config", "-c", "config", "--edit"):
        subprocess.run([os.environ.get("EDITOR", "nano"), str(CONFIG_PATH)])
        sys.exit(0)

    query = args[0]
    flags, _rest = parse_cli_flags(args[1:])

    if query.startswith(("http://", "https://")):
        info = {
            "name": query[:60],
            "url": query,
            "loop": settings["loop"],
            "no_video": settings["no_video"],
            "radio": flags.get("radio", False),
        }
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
        print_news_help(sources)
        sys.exit(0)

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
        suggestions = [
            k for k in sources if any(p in k for p in source_query.replace("-", " ").split())
        ]
        if suggestions:
            print("Did you mean: " + ", ".join(suggestions[:4]))
        sys.exit(1)

    print(f"\n📺 Searching for: {matched['name']}")
    streams = search_youtube(
        matched["name"],
        matched["keywords"],
        matched.get("channel_id"),
        settings["timeout"],
        settings["recent_hours"],
    )
    if not streams:
        print(f"⚠️  No streams found. Try: {matched['fallback_url']}")
        sys.exit(0)
    stream = streams[0]
    direct_url = get_direct_stream_url(stream["url"], quality)
    player = player_override or settings["player"]
    play_stream_cli(stream["url"], player, quality, direct_url=direct_url)


def print_stations_help(stations):
    print(f"""
📻 STATIONS · Internet radio (radio-browser.info)

CONFIG: {CONFIG_PATH}

USAGE:
    yt-cli --stations / -s              Stations TUI
    yt-cli --stations <slug|url>        Play a saved station or stream URL
    yt-cli --stations --list
    yt-cli --stations --config
    yt-cli --stations --help

OPTIONS:
    --bg, --background     Run in background
    --fg, --foreground     Run in foreground

SAVED STATIONS:""")
    if not stations:
        print("    (none yet: search in the TUI and press k to keep)")
    for station in stations:
        name = station.get("name", "?")
        slug = station.get("slug", "")
        print(f"    {slug:<28}  {name}")


def run_stations_cli():
    cfg = load_config()
    stations = get_all_stations(cfg)
    args = sys.argv[1:]

    if not args:
        run_fzf(cfg, mode="stations")
        return

    if args[0] in ("--help", "-h", "help"):
        print_stations_help(stations)
        sys.exit(0)

    if args[0] in ("--list", "-l", "list"):
        print(f"\n{c(C.BOLD + C.CYAN, '📻  stations')}  ·  saved\n")
        if not stations:
            print(f"  {c(C.DIM, 'no saved stations yet')}")
        for station in stations:
            status = "●" if station.get("enabled", True) else "○"
            bitrate = f"{station['bitrate']}k" if station.get("bitrate") else "-"
            meta = f"{station.get('countrycode') or '--'}  {station.get('codec') or '?'} {bitrate}"
            print(
                f"  {status}  {c(C.CYAN, station.get('slug', '')):<30}  "
                f"{station.get('name', '?')}  {c(C.DIM, meta)}"
            )
        print()
        sys.exit(0)

    if args[0] in ("--config", "-c", "config", "--edit"):
        subprocess.run([os.environ.get("EDITOR", "nano"), str(CONFIG_PATH)])
        sys.exit(0)

    query = args[0]
    flags, _rest = parse_cli_flags(args[1:])

    if looks_like_url(query):
        station = {"name": query[:60], "url": query}
    else:
        station = resolve_station(query, load_stations_cfg(cfg))
        if station is None:
            print(f"❌ Unknown station: '{query}'")
            suggestions = [
                s.get("slug", "")
                for s in stations
                if query.replace("-", "") in s.get("slug", "").replace("-", "")
            ]
            if suggestions:
                print(f"   Did you mean: {', '.join(suggestions[:4])}")
            sys.exit(1)
    _station_cli_play(station, flags)


def run_curses(cfg, mode=None):
    """Universal curses fallback entry (fzf missing)."""
    curses.wrapper(_curses_main, cfg, mode)


def _curses_main(stdscr, cfg, mode):
    ui = CursesUI(stdscr)
    play_mpv = _search_play_default(cfg)

    def goodbye():
        stop_shared()
        _curses_ui_message(stdscr, ["Goodbye! 👋"])

    if mode in ("radio", "stations", "news"):
        dest = {"radio": "Radio", "stations": "Stations", "news": "News"}[mode]
        _run_mode_loop(cfg, ui, dest, stop_shared, play_mpv, goodbye)
        return

    detached = False
    while True:
        entries = ["Search", "Radio", "Stations", "News", "History"]
        if player_active():
            entries.insert(0, "▶ Now Playing")
        entries.append("Exit")
        idx = ui.pick(entries, "main ❯ ", "yt-cli · search / radio / stations / news", "50%")
        if idx is None:
            break
        choice = entries[idx]
        if choice == "Exit":
            if player_active():
                act = ui.action(
                    ["Player is still running.", "─" * 44],
                    [("leave playing in background", "d"), ("stop player and exit", "s")],
                )
                if act == "d":
                    detached = True
                    break
                if act != "s":
                    continue
            break
        if choice == "▶ Now Playing":
            try:
                now_playing_screen(cfg, ui)
            except GoTo as g:
                if g.dest != "home":
                    _run_mode_loop(
                        cfg,
                        ui,
                        GO_TO_LABELS.get(g.dest),
                        stop_shared,
                        play_mpv,
                        goodbye,
                        on_esc="home",
                    )
            continue
        _run_mode_loop(cfg, ui, choice, stop_shared, play_mpv, goodbye, on_esc="home")
    if not detached:
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
    args = sys.argv[1:]
    stations_flag = bool(args) and args[0] in ("--stations", "-s")
    migrate_configs()
    script_name = Path(sys.argv[0]).name
    cfg = load_config()
    target = None
    # A saved-station symlink streams directly, so it must not require yt-dlp.
    if script_name not in ("yt-cli", "tune", "news-stream"):
        target = resolve_music_target(cfg, script_name)
        if target is None:
            print(f"❌ No preset or station found for '{script_name}'")
            sys.exit(1)
    check_dependencies(
        need_ytdlp=not (stations_flag or (target is not None and target[0] == "station"))
    )

    if script_name == "tune":
        _radio_cli_main()
    elif script_name == "news-stream":
        _news_cli_main()
    elif stations_flag:
        sys.argv = [sys.argv[0]] + args[1:]
        run_stations_cli()
    elif args and args[0] in ("--radio", "-r"):
        sys.argv = [sys.argv[0]] + args[1:]
        _radio_cli_main()
    elif args and args[0] in ("--news", "-n"):
        sys.argv = [sys.argv[0]] + args[1:]
        _news_cli_main()
    else:
        # yt-cli, a radio preset symlink, or a saved-station symlink
        if target is not None:
            flags, _ = parse_cli_flags(args)
            kind, info, settings = target
            if kind == "station":
                _station_cli_play(info, flags)
            else:
                cli_play(info, settings, flags)
            return
        attach_detached()  # reattach to a player left running
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
        import traceback

        traceback.print_exc()
        sys.exit(1)
