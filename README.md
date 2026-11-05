# yt-cli

A terminal player for YouTube channels, YouTube presets, and internet radio.
The application is Python standard library only. It uses `mpv`, `yt-dlp`, and
`fzf`, with a curses fallback when `fzf` is not installed.

[![CI](https://github.com/Onehand-Coding/yt-cli/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Onehand-Coding/yt-cli/actions/workflows/ci.yml)

## Usage

| Command | Opens |
| --- | --- |
| `yt-cli` | Saved / Search / History |
| `yt-cli --presets` | Saved, filtered to YouTube presets |
| `yt-cli --radio` | Saved, filtered to internet radio |
| `yt-cli --channels` | Saved, filtered to YouTube channels |
| `~/.local/bin/music/<name>` | A saved preset or radio station |

`--stations` is an alias for `--radio`; `--news` is an alias for `--channels`.

## Channels

A saved channel is a YouTube channel, not a stream link. Selecting a channel shows
its current lives, scheduled streams, and recent replays. Stream IDs change
daily; the channel does not.

```bash
yt-cli --channels                            # channel list
yt-cli --channels news5everywhere            # top live, or print the list if none
yt-cli --channels news5everywhere --replay   # newest replay
yt-cli --channels --verify                   # report channel bindings
yt-cli --channels --verify --apply           # fill exact-name ids, disable 404s
yt-cli --channels --add "105.9 True FM"      # add a channel by exact title
yt-cli --channels --bind News5Everywhere @TV5NewsPH
```

`--verify` reports a label mismatch but does not rename the source or change a
non-empty channel id. A confirmed 404 is disabled, not deleted. Timeouts are
reported as `unknown`.

## Playing

The action menu is shared by search, presets, radio, channels, and Now Playing.

- `Esc`: return to the previous list, keep playing.
- `stop`: stop playback and return to the list.
- `detach`: exit and keep playing.
- `exit`: stop and exit.
- `pause` / `play`: available for VOD, replays, search results, and presets.
- `channel`: reopen the channel from Now Playing.
- `save`: hidden when the channel, preset, or station is already saved.

Exit or Esc at the main menu closes the UI and leaves the player running. Use `stop`
from Now Playing to end playback.

Refresh streams is a row on the live list, not an action on the playing screen.

## Install

```bash
ln -s "$PWD/yt-cli" ~/.local/bin/yt-cli
```

Runtime requirements: `mpv`, `yt-dlp`, and `fzf` (optional).

Files live in `~/.config/yt-cli/`:

- `config.json`: presets, radio stations, channels
- `state.json`: player and video toggles
- `resolve-cache.json`: direct URLs for replays
- `history.json`: play history

## Layout

```text
yt-cli                 launcher; the PATH symlink points here
yt_cli.py              application code
CONTEXT.md             architecture and decisions
.github/workflows/     CI
docs/specs/            design notes
docs/plans/            implementation plans
tests/                 unit and smoke tests
tests/e2e/             pty flows (network and mpv required)
```

## Development

```bash
uv sync --extra dev
uv run --extra dev ruff check .
uv run --extra dev pyright yt_cli.py
uv run --extra dev pytest -q
```

CI runs the same checks on pushes to `main` and on pull requests.

The pty flows are not part of pytest. They require network access, `mpv`, and
`fzf`:

```bash
python3 tests/e2e/flow_a.py   # search and playback actions
python3 tests/e2e/flow_b.py   # presets
python3 tests/e2e/flow_c.py   # channels
python3 tests/e2e/flow_d.py   # curses UI
python3 tests/e2e/flow_e.py   # save and navigation
python3 tests/e2e/flow_f.py   # radio with an isolated HOME (no network)
```
