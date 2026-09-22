# yt-cli

Unified YouTube **search / radio / news** player — a single-file, stdlib-only Python CLI.

Formerly three scripts (`yt-cli`, `tune`, `news-stream`), merged into one. Dispatch is by `argv[0]` / flags:

| Invocation | Mode |
|---|---|
| `yt-cli` | Main menu (Search / Radio / News / History) |
| `yt-cli --radio` | Radio mode |
| `yt-cli --news` | News mode |
| `~/.local/bin/music/<preset>` | Radio preset (relative symlink → `../yt-cli`) |

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
