# Design: Consolidate yt-cli / tune / news-stream into one script

Date: 2026-08-20
Status: Approved (all 5 design sections)

## Problem

Three scripts (`~/.local/bin/yt-cli`, `tune`, `news-stream`) share ~80% of their
machinery — fzf pickers, mpv launch + IPC status watcher, action menu while
playing, colors, config loading — but each has its own copy. Bugs are fixed in
one copy and forgotten in others (the mpv IPC watcher bug was fixed twice).
Separate entry points are also confusing: three commands with different key
layouts for the same underlying job.

## Goal

One robust script, one command (`yt-cli`), with a unified main menu and
harmonized behavior across modes, plus a new universal search capability.

## Decisions (from brainstorming)

- Motivation: both maintenance/duplication AND unified entry point.
- Surface: single unified menu (not subcommands, not name-only dispatch).
- Scope: merge + harmonize features + new capability.
- New capability: universal search — `s` from any mode's action menu.
- Approach: A — single script with mode dispatch; `tune`/`news-stream` become
  symlink aliases into their mode.
- Unified command name: `yt-cli`.
- Fallback when fzf is missing: keep yt-cli's curses UI as the universal
  fallback for all modes (user explicitly chose to keep curses over the
  plain-text fallback).

## Architecture

Single file `/home/kenneth/.local/bin/yt-cli` (~2000 lines), organized:

```
main()
 ├─ dispatch: Path(sys.argv[0]).name
 │    "yt-cli"     → main menu
 │    "tune"       → Radio mode (interactive or CLI args: --list, preset…)
 │    "news-stream"→ News mode (with its CLI args)
 │    anything else→ radio preset lookup (music/<preset> behavior) → play
 │                   directly; unknown name → main menu
 ├─ main menu (fzf): Search / Radio / News / History / Exit
 ├─ shared core section:
 │    colors (C, c()), config load/save + migration,
 │    fzf_pick + find_fzf, curses fallback UI,
 │    mpv launch + IPC status watcher (_ipc_watch),
 │    kill_player/stop_player, history load/save,
 │    unified pick_playing_action + PLAY_ACTIONS base,
 │    format/parse helpers (format_fzf_item, parse_fzf_index, …)
 ├─ MODE: SEARCH  — YouTube search → results → play → action menu
 ├─ MODE: RADIO   — curated channels/playlists → play → action menu
 └─ MODE: NEWS    — live news streams → play → action menu
```

Modes are clearly delineated sections with `# ── MODE: X ──` headers.

## Unified action menu

Every mode gets the same core keys:

| key | meaning | search | radio | news |
|---|---|---|---|---|
| o | browse alternatives (player keeps playing) | other results | other presets (new behavior for tune) | other streams |
| s | search (NEW universal) | new search | search | search |
| h | history (NEW to radio/news) | history | history | history |
| r | replay current | replay | replay | replay |
| q | stop player → back to list | stop | stop → presets | stop → streams |
| x | exit (kills player) | exit | exit | exit |

Mode-specific extras appended to the same menu:
- Radio: `v` toggle video, `l` toggle loop, `m` manage presets
- News: `p` change player, `f` refresh streams (was `r`; re-keyed)

Merges:
- tune's old `s` (switch preset) and news's old `s` (switch source) both stop
  the player and jump to a list — same as unified `q`, so they are dropped.
  News's `s`→main-menu becomes `q` + Esc.
- tune gains "browse with player still running" via `o` (matches search/news).
- Esc on action menu = `q` in all modes (unchanged).

## Shared core

- Colors: 3 copies → 1 (`C`, `c()`).
- Config: 3 loaders/files → 1 file `~/.config/yt-cli/config.json` with mode
  sections; auto-migrate tune + news-stream configs on first run (old files
  left untouched, never deleted).
- fzf: 3 copies → 1 (`fzf_pick`, `find_fzf`).
- mpv launch + IPC watcher: 2 copies today (search, radio); news has none →
  1 copy used by all modes. News gets the status watcher ("Buffering"/"▶
  playing" for live streams). The watcher bug fixed twice lives in one place.
- History: search-only today → shared; every mode logs plays to
  `~/.config/yt-cli/history.json`; `h` works from every mode.
- Action menu: 3 variants → 1 base + per-mode extras.
- Fallback: yt-cli's curses UI becomes the universal fallback for all modes
  (replaces tune/news plain-text fallbacks). Mode-specific screens (e.g.
  manage presets) get curses renderings.

## Universal search (`s` from any mode)

- `s` opens the YouTube search flow: query → results → pick → play
  (replaces current stream) → action menu with unified keys.
- While browsing search results, the current player keeps playing (same as
  `o`; no interruption until a result is picked).
- Esc at any point → back to the originating mode's list (Radio → preset
  list, News → stream list). One-shot search; no search loop.
- Main-menu Search stays the dedicated mode: search → play → `o`/`s` loop
  within search, exactly as today.
- Searched items are logged to shared history.

## Aliases & migration

- `tune` and `news-stream` become symlinks → `yt-cli`. Dispatch reads
  argv[0] basename per the table above.
- Existing `~/.local/bin/music/<preset>` symlinks keep resolving through the
  chain (they point at the real file, now yt-cli); preset-name dispatch
  preserved (tune's current behavior).
- Config migration on first run; history.json stays in place.
- Old config files are left in place (not deleted).

## Verification plan

- `python3 -m py_compile`.
- Re-run watcher harnesses in /tmp/opencode (ytplay_test.py — 2-arg
  `_ipc_watch` signature; tuneplay_test.py — 3-arg) against merged code;
  update harnesses for merged names as needed.
- tmux end-to-end per mode: main menu → mode → play → action menu (all 6
  core keys + mode extras) → Esc/back paths.
- Universal search: `s` from Radio and News → search → play → `q` → returns
  to origin mode.
- Aliases: `tune`, `news-stream`, and a `music/<preset>` symlink invocation.
- Curses fallback smoke test with fzf hidden (PATH manipulation).
- Regression: lofi live stream in Radio, Metallica-style non-live video in
  Search, CNA-style news stream in News → "▶ playing" status via watcher.

## Non-goals

- No play queue, no mix mode.
- No deletion of old config files.
- No change to mpv.conf or other external config.