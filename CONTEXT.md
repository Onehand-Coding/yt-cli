# CONTEXT.md — yt-cli merge

## Project

Merged the three scripts `yt-cli`, `tune`, `news-stream` into ONE module
`yt_cli.py` behind a thin executable wrapper `yt-cli` (2026-09-22 wrapper split,
ani-py style; same content as the former monolith). The wrapper does
`from yt_cli import main` and carries the KeyboardInterrupt/error handlers that
used to live only in the monolith's `__main__` guard. Symlinked at
`~/.local/bin/yt-cli`. The `tune` and `news-stream`
symlinks were REMOVED; radio/news are invoked via `yt-cli --radio` / `yt-cli --news`.
Dispatch is by `Path(sys.argv[0]).name` (argv[0] is preserved by Python when
running through the wrapper, and `sys.path[0]` resolves the music/* → ../yt-cli
→ repo symlink chain, so `import yt_cli` works): `yt-cli` → main menu, a music/<preset>
symlink → radio preset lookup + direct play, `tune`/`news-stream` names → radio/news
(kept for backward compat if re-symlinked). A unified main menu
(Search / Radio / News / History / Exit), harmonized action menu, universal
search from any mode, shared history, config migration from the old INI files,
and a universal curses fallback (used by ALL modes when `fzf` is unavailable).
Music symlinks in `~/.local/bin/music/` point to `../yt-cli` — running
`music/lofi` resolves the "lofi" preset by argv[0] name and plays it.

## Location

Lives in the dedicated repo `~/Coding/projects/yt-cli/` — logic in `yt_cli.py`,
launcher wrapper `yt-cli` at repo root (symlink target `~/.local/bin/yt-cli` for
PATH dispatch; `music/<preset>` symlinks chain through it and keep working).
"Save preset → create symlink" resolves `argv[0]` fully, so new music links point
directly at the wrapper file — still correct. E2E harness: `tests/e2e/`. Project docs: `docs/`.

## Architecture

- **UI abstraction** — `get_ui()` returns `FzfUI` or `CursesUI`; interface:
  `pick`, `pick_query`, `action` (Esc/no-match → `"q"`), `query` (None=quit,
  ""=history), `prompt`, `info`. `fzf_pick` renders UI on stderr (fzf's stdout
  is captured to a temp file for the selection).
- **Playback** — `launch_player(cmd)` (radio, 1-arg) vs `launch_news_player(url,
  player, quality)` (news) are distinct (news's player takes 3 args). Exactly
  one `launch_player(` and one `kill_player(` exist.
- **Watcher** — `_ipc_watch(name, done)` is a daemon thread that connects to
  mpv's IPC socket (`/tmp/mpvsocket`, set in mpv.conf), spins
  Resolving→Buffering→`▶ playing`; event-phase deadline 60s from connect.
- **Modes** — `run_radio_mode`/`run_news_mode`/`run_search_mode` share the
  action-menu loop. Action-menu loop is the OUTER loop, results/preset picker
  INNER; Esc from a picker with a live player returns to the action menu;
  `q` stops and returns home; `x` exits. Esc at the radio preset / news
  source list goes HOME keeping playback when launched from the main menu
  (`on_esc="home"`); direct `--radio`/`--news` launches keep old behavior
  (stop + quit). Results/history pickers honor `return_home` on Esc.
- **Config** — `~/.config/yt-cli/config.json` (migrated: history_size,
  mpv_args, news, radio, result_count). Originals at
  `~/.config/tune/config.ini` + `~/.config/news-stream/config.ini` are NEVER
  deleted (migration is idempotent, md5-verified).

## Decisions & fixes (E2E findings)

- `load_config` deep-merges without mutating `DEFAULT_CONFIG`:
  `merged[k] = {**DEFAULT_CONFIG[k], **v}` (was shallow-copy + `.update`).
- **Radio universal search** (`s` from radio action menu): `run_search_mode`
  passes SEARCH-format items (`title`/`url`) to the radio play function, which
  requires radio-format (`name`). Fixed with a `play_search` adapter in
  `radio_playing_loop` that maps the item into radio format before calling
  `play_fn`. (Bug found by E2E — `KeyError: 'name'`.)
- **Action-menu labels must have unique filter keys per key letter.** fzf
  ranks matches by position/length, so a key like `p` that appears in multiple
  labels picks the WRONG action. NEWS menu: `"player"` (p) — renamed from
  `"change player"` (which lost to `replay`). Radio/Search tables were audited
  and are unambiguous. This bit AGAIN (2026-08-21): adding a `"save"` label
  made typing `s` select it over `"search"` (shorter line wins) — new labels
  must start with an UNUSED letter in every table (`"keep (save)"` uses k).
- Header-string bug fixed at 5 sites: `"Manage presets\n─" * 44` etc. repeated
  the whole title 44 times — now `"Title\n" + "─" * 44`. The SAME implicit
  string-concat gotcha hit the pick_preset and pick_source headers: a line
  ending `...\n"` adjacent to `"─" * 48` merges to `"...\n─"` and the `* 48`
  repeats the WHOLE title+sep 48× — flooding the screen with the header.
  Fix: add explicit `+` between the string literals (yt-cli:1283, 1899).
  All header strings use explicit `+` now — scan for lines ending `\n"` for
  regressions.
- Curses `pick` ignores the prompt arg (footer shows nav hints) — the action
  prompt "action ❯" is NOT drawn in curses mode; tests must wait on item text
  instead.
- Curses `goodbye` shows a "Goodbye! 👋" message and blocks on a keypress.

## Gotchas

- fzf lives at `/usr/bin/fzf` AND `/usr/sbin/fzf` AND `/sbin/fzf` — masking it
  for curses testing requires a PATH that excludes ALL of them (use the
  sandbox at `tests/e2e/bin` with symlinked python3/mpv/yt-dlp).
- **`pkill -f` self-match**: any pattern that appears in the caller's own
  command line kills the caller's shell. Kill by exact process name
  (`pkill -x mpv` / `pkill -x fzf`) or by PID; never `pkill -f` a script path.
- This environment reaps tmux sessions/pty processes ~2min after spawn and
  cleans `/tmp` — tmux E2E is unreliable here; the pty harness in
  `tests/e2e/` (pty + TIOCSCTTY + cursor-position-report responder
  for fzf's `\x1b[6n`) is the reliable driver. Durable backups live in
  `docs/backups/` (NOT /tmp).
- mpv/yt-dlp YouTube resolution is slow (~30-44s to playback-restart);
  YTM/radio presets and direct stream URLs are fast (5-15s); live streams
  buffer variably (up to 90s).
- News source keys are `id` not `slug`; news CLI fuzzy matching
  (`key in source_query`) can substring-match and trigger a real scrape.
- Radio CLI: flags come AFTER the preset (`yt-cli --radio lofi --no-video --loop`).
- Radio/news CLI help/examples use `yt-cli --radio` / `yt-cli --news`; `tune` /
  `news-stream` are gone (symlinks removed 2026-08-20).

## E2E harness

`tests/e2e/pty_harness.py` + `flow_a.py` (search/actions),
`flow_b.py` (radio + universal search, via `yt-cli --radio`), `flow_c.py`
(news, via `yt-cli --news`), `flow_d.py` (curses fallback via fzf-free PATH).
All four PASS on the merged script. Run: `python3 flow_*.py` (kills stray
mpv/fzf first; each takes 1-5 min; requires network). Harness gotchas from
the yt-cli2 work, still valid for any fzf-flow testing:
- `FzfUI.action` labels are NOT numbered — select by typing label text
  (`yes\r`), not `1\r`; an unmatched query yields an empty selection which
  the action loop treats as `"q"`.
- An fzf opening immediately after another fzf's teardown can eat the
  cursor-pos query answer in the harness (initial-draw spin, `\x1b[G` spam);
  sleep ~1.5s before the next wait. Real terminals answer instantly —
  harness artifact only.
- fzf renders only the first screenful (~16 rows) — verify below-fold items
  via JSON/config, not the screen.
- First-item checks must use `wait_for` (whole buffer): the initial render
  precedes the prompt, and `wait_new` only sees bytes after its call.

## Save + navigation keys (2026-08-21)

Spec: `docs/specs/2026-08-21-yt-cli-save-navigation-design.md`.
All playing action menus gained `k` = keep/save (never stops playback) and
`g` = go-to-mode picker (Radio/News/Search/Home; player keeps playing; jumps
unwind via a `GoTo` exception caught only in `_run_mode_loop`, shared by
`run_fzf` AND `_curses_main`). The old post-stop "save?" prompts were
REMOVED. Saving a news stream auto-captures channel_id via `resolve_url_meta`
(one yt-dlp dump; fallback: keyword-only source) with
fallback_url=channel /streams — saved sources equal migrated ones. URLs
pasted into any query box (`looks_like_url`) open `url_entry_flow`:
play · save as preset · save as news channel · cancel (radio's preset box
auto-plays URLs as before, now savable + www./youtu.be detected). E2E:
`flow_e.py` covers all of it; flows a–d regression-PASS.

## Config vs state split (2026-08-21)

Volatile toggles (`radio.player/no_video/loop/background`, `news.player`)
live in gitignored `~/.config/yt-cli/state.json`; tracked config.json now
holds ONLY curated data (presets, sources, quality, timeouts) and changes
just when presets/sources change. `load_state()` seeds from legacy config
values once; `migrate_configs()` then strips those keys from config.json.
Toggle handlers (`v`/`l` in radio, `p` in news) write state.json — never
config.json. Rationale: every casual keypress used to dirty the dotfiles
repo. New action-menu labels must start with a letter unused in that table
(see gotcha above — `"save"` broke `s`→search; it's `"keep (save)"` on k).

## Resolve cache / instant replays (2026-08-22)

`~/.config/yt-cli/resolve-cache.json` (gitignored) maps single-video watch
URLs → direct stream URLs (`yt-dlp -g`, quality-matched). On every play
MISS, a daemon thread fills it in the background (first play unchanged);
on HIT, build_mpv_cmd/_search_play_default feed mpv the direct URLs via
positional video + `--audio-file=` — zero extraction, replays in ~1s.
Guards: playlist URLs (list=) and radio presets bypass the cache; m3u8
manifests never cached/served (they stall mpv — same reason live streams
must keep using watch URLs); entries expire from the URL's own `expire=`
param minus 5min margin. News mode: get_direct_stream_url pre-resolve now
runs ONLY for vlc/ffplay — mpv path is single-resolve (was double).
NOTE this mpv rejects `set volume/mute` over IPC ("invalid parameter");
use `add volume ±n` / `cycle mute`.

## Detach & reattach (2026-08-21)

yt-cli players use the SHARED socket `/tmp/mpvsocket` from the user's
mpv.conf — DO NOT override with a dedicated path: the user's global
mpv-control keybindings (super/ctrl keys → control scripts) talk to that
path, and a dedicated socket made them blind to yt-cli playback.
`d` = "detach & exit" in every playing menu quits the UI leaving mpv
running; main-menu Exit with a live player prompts (leave playing / stop /
Esc). On startup `attach_detached()` probes the socket and rebuilds
NOW_PLAYING from mpv properties (title, path) — the ▶ Now Playing
controller then works over IPC (toggle video, stop=quit, replay=relaunch
from stored URL). `player_active()` = Popen alive OR ATTACHED socket
reachable. stop_shared() also quits an attached player over IPC. Stale
socket files are unlinked on failed attach. CLI `--bg/--background`
already existed (detached Popen); those sessions reattach the same way.
TUI-launched players get stdin=DEVNULL: mpv otherwise shares the terminal
with fzf and STEALS keystrokes (races — 'd', '*' randomly never reached
menus; this caused hours of phantom "broken detach" debugging — a zombie
process still shows in `ps -p PID`, check the STAT field!). Foreground CLI
plays keep inheriting the tty so direct keyboard control still works there.
This mpv build REJECTS `set volume/mute` over IPC ("invalid parameter") —
use `add volume ±n` / `cycle mute`.

## Abandoned: yt-cli2 unified-library rewrite (2026-08-20)

A separate `yt-cli2` (one flat library.json, type-to-filter home, explicit
save key) was built and fully E2E-tested, then DELETED at user decision —
too much churn; flaws get fixed in `yt-cli` directly instead. Nothing of it
remains (library.json removed; config.json never touched, md5-verified).
Transferable gotchas live in the E2E harness section below.