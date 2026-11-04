# yt-cli

Unified YouTube **saved channels / radio / news** player — a single-file, stdlib-only Python CLI.

Formerly three scripts (`yt-cli`, `tune`, `news-stream`), merged into one. The home screen is
**Saved** (channels, stations, presets in one list), plus Search and History. Dispatch is by `argv[0]` / flags:

| Invocation | Mode |
|---|---|
| `yt-cli` | Main menu (Saved / Search / History) |
| `yt-cli --radio` | Saved filtered to YouTube presets |
| `yt-cli --stations` | Saved filtered to internet radio |
| `yt-cli --news` | Saved filtered to channels (live shows) |
| `~/.local/bin/music/<preset>` | Radio preset (relative symlink → `../yt-cli`) |

## Channels

A news source is a YouTube **channel**, never a saved stream link. Picking a channel fetches its
`/streams` tab and lists what is live now, what is upcoming, and recent replays. Stream IDs rotate
daily; the channel does not.

```bash
yt-cli --news                     # channel list, live shows first
yt-cli --news news5everywhere     # play the top live, or print the list if nothing is live
yt-cli --news news5everywhere --replay   # opt in to the newest replay
yt-cli --news --verify            # check every channel binding, report only
yt-cli --news --verify --apply    # fill exact-name ids, disable confirmed 404s
```

## Layout

```
yt-cli              # executable wrapper (imports yt_cli; PATH symlink target)
yt_cli.py           # the monolith — all app logic
CONTEXT.md          # architecture, decisions, E2E gotchas
docs/
  plans/            # implementation plans
  specs/            # design specs
  backups/          # pre-merge tune/news-stream backups
tests/
  test_cli.py       # fast subprocess smoke tests (pytest)
  e2e/              # pty E2E flows (not collected by pytest)
    pty_harness.py
    flow_a.py … flow_e.py
    bin/            # fzf-free PATH sandbox for curses tests
```

## Run

```bash
# Via PATH (symlinked from ~/.local/bin)
yt-cli
yt-cli --radio
yt-cli --news

# Direct
./yt-cli --radio lofi --no-video --loop
python3 yt_cli.py --radio lofi   # note: top-level dispatch uses argv[0] name;
                                 # a non-"yt-cli" name takes the preset branch
```

Requires: `mpv`, `yt-dlp`, `fzf` (curses fallback if fzf missing).

Config/state live in `~/.config/yt-cli/` (outside this repo).

## Test

```bash
# Smoke tests (fast, no network)
uv run --extra dev pytest

# E2E flows (network + mpv; each takes 1–5 min)
python3 tests/e2e/flow_a.py   # search/actions
python3 tests/e2e/flow_b.py   # radio
python3 tests/e2e/flow_c.py   # news
python3 tests/e2e/flow_d.py   # curses (fzf-free PATH)
python3 tests/e2e/flow_e.py   # save / navigation keys
```

See `CONTEXT.md` for architecture and harness gotchas.

## Dotfiles

The live entrypoint is a symlink:

```
~/.local/bin/yt-cli → ~/Coding/projects/yt-cli/yt-cli
```

Untracked from the bare dotfiles repo; ignored via `~/.gitignore`.
