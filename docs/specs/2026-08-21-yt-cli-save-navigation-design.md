# yt-cli: in-app save + smoother navigation — design

Date: 2026-08-21
Status: approved (user directive: implement directly)

## Problem

1. Saving is inconsistent: radio mode saves search results as presets (only
   AFTER stopping); news mode saves keyword-only sources (never captures
   channel_id/fallback_url); main-menu Search mode has no save at all; pasted
   URLs play but can't be saved.
2. Mode switching requires q (stop) → home → other mode.

## Decisions (user-approved)

- Keep the three modes (Search/Radio/News); NO unified home screen.
- Save via a new `w` key in every playing action menu — never stops playback;
  the old post-stop "save?" prompts are removed.
- News saves auto-capture the full channel (one yt-dlp dump for channel_id +
  channel name; fallback_url = channel /streams URL) so saved sources equal
  migrated ones in quality. Fetch failure → keyword-only fallback offer.
- Pasted URLs detected in query boxes → picker: play · save as preset ·
  save as news channel · cancel.
- New `g` ("go to…") key in every playing action menu → picker
  Radio/News/Search/Home; player keeps playing across jumps; target mode
  behaves as-if launched from home (its q → home). No return-stack plumbing.

## Architecture

- `looks_like_url(q)` — http(s)://, www., youtu.be prefixes.
- `resolve_url_meta(url)` — yt-dlp --dump-single-json (30s timeout,
  youtube-dl fallback) → {title, uploader, channel_id, is_live} | None.
- `class GoTo(Exception)` with `.dest` in {"radio","news","search","home"} —
  raised by g-handlers deep in mode loops, caught ONLY in the two entry
  points (`run_fzf`, `_curses_main`) which share a `_run_mode()` dispatcher;
  loop: normal return or GoTo(home) → main menu; GoTo(<mode>) → enter mode.
- `save_current(cfg, ui, item, default_kind)` — kind picker when ambiguous
  (context default first, Esc cancels); preset path reuses
  `prompt_save_search_result`; news path uses new `prompt_save_channel`
  (skips fetch when channel_id already known from source_info).
- `url_entry_flow(cfg, ui, url)` — resolve meta (failure tolerated: save by
  URL still possible) → action picker; returns play-item or None.
- Action tables gain ("save", "w") + ("go to…", "g").
  Guards: radio `w` only when `is_search_result` (else "already a preset"
  info); news `w` only for ad-hoc search results (existing sources are
  already saved). Radio URL entries set `is_search_result=True`.
- URL detection points: run_search_mode query stage (full flow);
  pick_preset broadened to looks_like_url (auto-play kept, now savable);
  pick_source gains ("url", q) branch handled in run_news_mode.
- Post-stop prompt blocks removed (radio ~1443, news ~2158); the two
  prompt_save_* functions stay (reused by save_current).

## Config impact

None — writes go through existing `news_add_source` (already accepts
channel_id/fallback_url), `radio_add_preset`, `save_config`. No schema
changes. music/* symlinks and CLI flags untouched.

## Error handling

- resolve_url_meta failure → warn; URL-entry still offers saves
  (keyword-only source / plain preset).
- prompt_save_channel fetch failure → offer keyword-only save or cancel.
- Slug/id collisions → existing numeric-suffix logic.
- Symlink failures → existing ⚠ message.

## Testing

- New e2e `flow_e.py` (pty harness): w-save preset from search (config.json
  verified), w-save news channel (channel_id + fallback_url verified),
  URL paste → save as preset, g jump radio→news with player alive.
- Regression: flow_a–d must still PASS.
- config.json backed up before runs, restored after (md5-verified).
- Harness gotchas apply: action labels selected by typed text (not numbers),
  wait_for for initial renders, below-fold items verified via JSON.
