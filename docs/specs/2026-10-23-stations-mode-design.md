# Stations mode: real internet radio in yt-cli

Date: 2026-10-23
Status: approved
Branch: `feat/stations-mode`

## Context

`yt-cli` has a Radio mode whose presets are YouTube videos, playlists, and
mixes. The user wants real internet radio stations alongside it, using the
same data source as Transistor (`radio-browser.info`), without disturbing the
existing YouTube presets or `music/<slug>` symlinks. The user also wants the
UI to stop looking noisy, but explicitly deferred removing existing emoji
until a later review.

Transistor is an Android app (MIT licensed). It is a reference for the station
model and API usage only. No Transistor code is copied.

## Goals

- Add a Stations mode for real internet radio beside Radio.
- Search stations by name and tag through `radio-browser.info`.
- Play direct audio streams with mpv, without yt-dlp extraction.
- Save, enable, disable, delete, and symlink favorite stations.
- Replay stations from History and Now Playing.
- Clean up layout, headers, and color noise without removing existing emoji.
- Keep all existing YouTube radio presets and E2E flows working.

## Non-goals

- Replacing or migrating the existing Radio mode.
- Browsing stations by country or popularity sections.
- Rendering station logos or favicons in the terminal.
- Removing emoji from existing screens in this pass.
- Adding third-party Python dependencies.
- Changing the shared mpv IPC socket path.

## Decisions

### Mode placement

Add a separate `Stations` mode. The main menu becomes:

```
Search
Radio        (YouTube mixes)
Stations     (live internet radio)
News
History
Exit
```

Radio keeps its current behavior and presets. `go to...` gains a Stations
destination. `--stations` mirrors the existing `--radio` CLI shape.

### Data storage

Saved stations are curated data and live in `config.json` under a new
top-level `stations` list. There is no migration. The key is additive and
removing it disables the feature cleanly.

Station record:

```json
{
  "slug": "dzrh",
  "name": "DZRH",
  "url": "https://example.invalid/stream",
  "stationuuid": "00000000-0000-0000-0000-000000000000",
  "countrycode": "PH",
  "tags": ["news", "talk"],
  "codec": "MP3",
  "bitrate": 64,
  "homepage": "https://example.invalid/",
  "favicon": "https://example.invalid/favicon.png",
  "enabled": true
}
```

`url` prefers the API's `url_resolved` value. When that is empty, fall back to
`url`. `stationuuid` is kept for click reporting and future refresh. Slugs are
unique within `stations`; symlink creation must not overwrite an unrelated
existing file.

### API client

Source: `radio-browser.info`. Free, no API key. The client uses only the
standard library (`socket`, `urllib.request`, `urllib.parse`, `json`).

- Server discovery: resolve `all.api.radio-browser.info`, shuffle the results,
  and try them in order. Fall back to `de1.api.radio-browser.info`,
  `de2.api.radio-browser.info`, and `nl1.api.radio-browser.info`.
- Send a descriptive User-Agent: `yt-cli/1.1`.
- Search endpoint:
  `/json/stations/search?name=<query>&limit=30&hidebroken=true&order=clickcount&reverse=true`
- If the name search yields fewer than 8 usable results, also query the `tag`
  field and merge results.
- Normalize and dedupe by `stationuuid`, discard entries without a usable
  stream URL, and rank by click count then votes.
- Cache results per query for the life of the process. Cache server choice per
  process as well.
- Timeout 8 seconds per request, with one retry on the next discovered host.
- On play, report `/json/url/<stationuuid>` in a daemon thread, as the
  radio-browser API guidelines request.

### Playback

Stations are live audio streams, so they use a dedicated command:

```
mpv --force-window=no --vid=no --video=no --audio-display=no <url>
```

No `yt-dlp`, no `--ytdl-format`, no resolve cache, no loop flag. Reuse
`launch_player` and the existing IPC watcher. History entries store
`kind: "station"` so replay from History or Now Playing routes back through
the station player. Now Playing hides the video toggle for stations.

### Stations UX

Saved stations screen:

```
yt-cli · Stations
------------------------------------------------
  1.  Search stations...
  2.  DZRH              PH  News, Talk      MP3  64k
  3.  Jazz FM           US  Jazz            AAC 128k
type to filter saved · enter play · esc back
```

- Selecting `Search stations...` or entering a query that matches no saved row
  opens station search.
- Search results use the same columns. Selecting one plays it immediately and
  opens the station action menu.
- Station action menu: other results, new search, history, replay, go to,
  stop, exit, keep (save), detach. No video or loop actions.
- A pasted HTTP(S) URL, including `.m3u` and `.pls`, can be played and saved.
- Saving offers an optional `music/<slug>` symlink, matching YouTube presets.
- Manage stations supports enable, disable, and delete.

### CLI

- `yt-cli --stations` opens the TUI.
- `yt-cli --stations --list` lists saved stations.
- `yt-cli --stations <name>` plays a saved station by fuzzy slug/name match.
- `yt-cli --stations <url>` plays a raw stream URL.
- `yt-cli --stations --config` opens the config in `$EDITOR`.
- `yt-cli --stations --help` prints usage.
- `music/<slug>` dispatch checks YouTube presets first, then saved stations.

### Dependencies

`--stations` and station symlinks require `mpv` only. Search, Radio, News, and
the main menu still require `yt-dlp`.

### UI cleanup

- Keep existing emoji in existing screens.
- Reuse the same emoji vocabulary in Stations so a later emoji review covers
  everything at once. Do not introduce new emoji types.
- Keep list numbers because they are quick-select shortcuts (`1`, `2`, ...).
  Render the number prefix in dim text instead of bold cyan.
- Replace em-dash separators in headers with `·`.
- Reduce headers to one title line plus at most one context line.
- Shorten footers to short key hints.
- Palette: cyan accent and bold titles, dim metadata, green only for playing,
  yellow for warnings, red for errors. Drop per-category rainbow colors.
- Keep fzf and curses wording identical.
- New action-menu labels must start with a letter unused in that table.

## Components

All changes are in `yt_cli.py`.

- Config: extend `DEFAULT_CONFIG`; add `load_stations_cfg`, `station_add`,
  `station_delete`, `station_toggle`, `station_slug`.
- API: `_rb_hosts`, `_rb_get`, `search_stations`, `report_station_click`,
  `_normalize_station`, `_merge_station_results`.
- Playback: `build_station_cmd`, `_station_play_default`,
  `play_station_item`.
- UI: `format_station_row`, `pick_station`, `pick_station_results`,
  `manage_stations_screen`, `prompt_save_station`.
- Mode: `run_stations_mode`, `stations_playing_loop`.
- CLI: `_stations_cli_main`, `print_stations_help`, dispatch in `main`.
- Integration: main menu, `GO_TO_LABELS`, `_run_mode`, `_run_mode_loop`,
  `_search_play_default` history routing, `now_playing_screen`.
- UI cleanup: `_num_line`, header and footer strings, `TYPE_COLORS` usage.

## Error handling

- API unreachable: readable message plus retry/back. Saved stations stay
  usable.
- Empty result set: retry/back.
- Dead stream: `failed to load`, then return to the results/action loop.
- Invalid saved URL: play failure is reported; the record stays until deleted.
- Symlink collision: if `music/<slug>` already exists and points to another
  target, skip creation and report it. Do not overwrite.
- Config writes continue to use atomic `save_json`.

## Testing

Unit tests (`tests/test_stations.py`, importing `yt_cli` directly):

- Normalize API records, prefer `url_resolved`, drop invalid entries.
- Dedupe by UUID and rank correctly.
- Build search URLs and merge name/tag results.
- Build the station mpv command.
- Add, toggle, delete, and slug saved stations.
- History routing for `kind: "station"`.
- Symlink precedence: YouTube preset wins over a station with the same name.

Smoke tests: `--stations --help` and `--stations --list`.

E2E (`tests/e2e/flow_f.py`):

- Run with an isolated `HOME` so the real config is untouched.
- Open Stations, search a stable term, play a result, save it, verify the
  sandbox `config.json`, then exit.
- Skip cleanly when the API or mpv is unavailable.

Regression: existing flows A through E, `pytest`, and `ruff check`.

## Rollout and rollback

No schema migration and no writes to existing preset data. To roll back,
remove the `stations` key from `config.json` and delete any station symlinks
created under `~/.local/bin/music/`. Existing YouTube presets are untouched.

## References

- Transistor (MIT): https://codeberg.org/y20k/transistor
- radio-browser API: https://api.radio-browser.info/
