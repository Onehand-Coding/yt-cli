# Unified Saved catalog + channel-first news

Status: implemented 2026-11-04.

## Intent

The daily use is a ritual: in the morning, open yt-cli, pick a channel, pick the
show that is live right now. The channels in daily use are kapamilya-online-live,
kapuso-online-live, kapatid-live-stream, and News5Everywhere, with Ted Failon and
DJ Chacha as the flagship morning show. The user knows live stream links rotate
daily, so the saved thing must never be a link. It must be a channel that
resolves to whatever is live at the moment it is opened.

Two failures block that today:

1. A live stream saved as a preset is a `watch?v=ID` that belongs to one
   broadcast. The next day it is an ended stream (error) or yesterday's replay.
2. A saved news source silently drifts. Reproduced 2026-11-04 with the live
   config: the saved "TV Patrol" source returned two PAW Patrol livestreams.

Both come from the same gap: the tool has a pinned-URL primitive (preset,
station) and no honest channel primitive. News sources approximate one with
keywords and a global search, which is why they drift.

## Goals

- Picking a saved channel shows that channel's current lives, upcoming streams,
  and recent replays, sorted and honestly labeled. One Enter plays the top live.
- A saved channel survives the stream ending. Nothing that rotates daily is ever
  stored as a URL.
- The same list of saved things is reachable from one screen, grouped by kind:
  Channels (live), Stations (internet radio), Presets (YouTube music/radio).
- The save action always saves the durable thing. On a channel live it saves the
  channel, not the video.
- Saved channels can be verified and repaired in one pass.

## Non-goals

- No on-disk schema change and no migration. `config.json` keeps
  `radio.presets`, `news.sources`, `stations`; the unified catalog is a read
  adapter over them.
- No network fetch when the Saved screen renders. No live-count column in v1.
- No new dependencies. Standard library only.
- No change to playback engines, the resolve cache, IPC, or station supervision.
- Not a rewrite of the news player, the station supervisor, or the search mode.

## Anti-goals

- A saved item that silently resolves to a different show than the one labeled.
- A "live" badge on something that ended hours ago.
- Four parallel catalogs for one human idea.
- Repeating yt-cli2: one flat `library.json` that replaces config.json. That was
  built, tested, and deleted for churn.

## Current state, with evidence

Channel scanning is dead code. `find_streams()` (yt_cli.py:3250) reads
`richItemRenderer → videoRenderer`, `gridVideoRenderer`, and
`shelfRenderer` children. YouTube channel pages now emit `lockupViewModel`:

```text
channel/UCGEbMwiX774cseKvJqF9R2g/streams  30 items, all lockupViewModel
@abscbnnews/streams                      325 videoId fields, 0 videoRenderer
```

So every channel-bound source contributes zero streams, and the only results
come from the global "`<name>` live" search. That search is then filtered by raw
substring (`parse_video_renderer`, yt_cli.py:3184): `"patrol live"` is a
substring of `"PAW Patrol Livestream"`. Non-live results under 24h are also
accepted, so a quiet morning yields a replay with no warning.

Data errors in the live config, verified with `yt-dlp --dump-single-json`:

- `tv-patrol` and `frontline` both carry `UCmSC7h...ES-g`, which resolves to
  "24 Oras". Frontline's label says News5; its binding says 24 Oras.
- `News5Everywhere`'s `fallback_url` (`@News5Everywhere`) is HTTP 404. Its
  `channel_id` `UCGEbMwiX774cseKvJqF9R2g` works and is titled "TV5 News
  Philippines".
- 4 of 11 sources have no `channel_id`: kapamilya-online-live,
  kapuso-online-live, kapatid-live-stream, dzrh.

The new page shape is usable. `lockupViewModel` gives `contentId` (video id),
`metadata.lockupMetadataViewModel.title.content` (title), and metadata rows:

```text
lBOelH9aAxA   1 waiting · Scheduled for 11/4/26, 7:00 AM   The World Tonight
NTxOVOAOJYw   176 watching                                  Spot Report | DZMM
raHM2Lnsd5s   63K · Streamed 35 min ago                     TV Patrol Livestream
```

Cross-checked against `yt-dlp --print live_status`: `is_live`, `post_live`,
`was_live` respectively. The metadata row is an accurate live flag.

### Verified day-one bindings

Resolved 2026-11-04 by fetching each channel's `/streams` page:

| Entry label | Bind to | Channel id | What the list carries |
| --- | --- | --- | --- |
| kapamilya-online-live | ABS-CBN Entertainment | `UCstEtN0pgOmCf02EdXsGChw` | one all-day "Kapamilya Online Live" stream (122K watching when checked) |
| kapuso-online-live | GMA Network | `UCKL5hAuzgFQsyrsQKgU0Qng` | one all-day "Kapuso Stream" |
| kapatid-live-stream | TV5 Philippines | `UCiFx0kZFjR29u0uJxTSq5MQ` | one all-day "TV5 Kapatid Livestream" |
| News5Everywhere | TV5 News Philippines | `UCGEbMwiX774cseKvJqF9R2g` | per-show news lives and briefings |
| optional: 105.9 True FM | 105.9 True FM | `UCjUrQJqEIfO1e52faNzt04g` | per-show: Ted Failon DJ Chacha (morning), Good Morning Bayan, Cristy Ferminute, Heart-2-Heart |

Two things this test taught us:

1. **Where the flagship show appears.** At 8:40pm today, the News5 channel's
   `/streams` list did not include Ted Failon and DJ Chacha, while 105.9 True FM
   listed it daily as "Ted Failon DJ Chacha sa True FM Livestream | DATE" (72K,
   streamed 11h ago). The user reports watching the show on News5Everywhere, so
   the picker must not hardcode either answer: it lists whatever the bound
   channel actually serves at that moment, and shows the show if it is there.
   True FM is added as an optional fifth channel, not a replacement.
2. **Some channels are one all-day simulcast, others are per-show.**
   Kapamilya Online Live, Kapuso Stream, and TV5 Kapatid Livestream each emit a
   single stream per day, so their list has one row and the shows are inside it.
   True FM and ABS-CBN News emit one row per show. The picker cannot promise
   per-show rows on a simulcast channel, and the design must not pretend it can.

### Tooling checked

- `~/.local/bin/yt-extract` resolves channel identity (id, name, handle, RSS
  feed) by scraping or the Data API. It does not list live shows, so its shape
  is reusable for verify and repair, not for the live picker.
- `YT_DATA_API_KEY` is set in the environment but rejected as invalid
  ("API key not valid", checked 2026-11-04). The tool must not depend on the
  Data API. If a valid key is configured later, an API path
  (`channels.list` → uploads playlist → `videos.list` with
  `liveBroadcastContent`) can replace the HTML parse; out of scope now.
- `news5.com.ph` times out from this host, so the YouTube channel remains the
  only observable source.

## Approaches considered

**A. Repair only.** Replace the channel parser, stop merging global search into
a channel list, add badges and sorting. Fixes the pain in about a day and leaves
the four save paths, so a live can still be saved as a rotting preset.

**B. Repair + channel model.** A adds: a source is a channel, keywords stop
filtering channel listings, `k` on a live saves the channel, channel ids are
verified and repaired. Removes the save trap, no disk change.

**C. B + one Saved home.** All saved things in one screen grouped by kind, with
modes as filters. Chosen.

Why C: the user's complaint is structural ("something off"), not only the
broken parser. Four catalogs for one idea is the source of that feeling. C keeps
B's engine fix and pays down the split. The rejected yt-cli2 did this as a data
rewrite; C does it as a view and dispatch layer over the existing config, which
is the difference that makes it survivable.

Size: a project, roughly 3 days, phased so the morning fix ships first.

## The design

### 1. One normalized item

```text
SavedItem = {
  kind: "channel" | "station" | "preset",
  id: str,              # source id, station slug, preset slug
  name: str,
  enabled: bool,
  group: str,           # channel: "", station: country, preset: category
  url: str,             # station/preset only
  channel_id: str,      # channel only
  channel_name: str,    # channel only: resolved YouTube title, for verify
  loop: bool, no_video: bool,   # preset
  audio_only: bool,     # station
  raw: dict,            # the original config record, untouched
}
```

The field list is a normalized view, not a replacement. `raw` carries the
original record so `play_saved` can hand it to `build_mpv_cmd`
(preset `radio` flag), `_station_play` (`stationuuid`, click reporting), and
`station_playing_loop` (`tags`, `codec`, `bitrate`, `countrycode`). Adapters
must not drop fields that pause paths read.

`saved_items(cfg)` is the only reader. Three private adapters map
`news.sources`, `stations`, `radio.presets` onto it. Disabled entries are
included with `enabled: False` so the manage screens can show them; play
pickers filter them out. A channel entry with no `channel_id` is included as a
non-playable "needs repair" row, never as a keyword search.

`play_saved(cfg, ui, item)` is the only dispatcher:

- `preset` → existing preset play path (`build_mpv_cmd`, `radio_playing_loop`)
- `station` → `_station_play` + `station_playing_loop`
- `channel` → fetch `channel_lives`, pick, play through the existing news
  player, then `channel_playing_loop` (today's `run_news_mode` inner loop,
  extracted)

The mode functions keep their playback loops. C unifies entry and dispatch, not
the engines.

### 2. Channel resolution

New functions, next to the old ones:

- `parse_lockup_videos(data)` walks any JSON blob for `lockupViewModel` and
  returns items with `id, title, url, status, viewers, schedule_text,
  streamed_text, hours_ago`.
- `classify_lockup_meta(rows)` joins each row's metadata parts and classifies:
  `(\d[\d.,]*[KMB]?)\s+watching` → live; `waiting` and `Scheduled for` →
  upcoming; `Streamed ... ago` → past; otherwise unknown.
- `channel_lives(channel_id, timeout)` fetches
  `https://www.youtube.com/channel/<id>/streams`, parses lockups, sorts live
  (viewers desc), upcoming (schedule text), past (hours_ago asc), and returns
  them. It does not search YouTube.
- `search_live(query, timeout)` keeps the global live search for the explicit
  "search YouTube" path. It keeps `videoRenderer` parsing for search pages and
  falls back to lockups. Keyword filtering uses word boundaries, and live-only
  is the default, with non-live results appended under a "recent" label only
  when no live exists.

`find_streams()` and `parse_video_renderer()` stay for search pages. They are no
longer used for channel sources. The substring keyword filter is removed from
the channel path entirely.

Channel URL is always derived from `channel_id`. `<channel_id>/streams`. Stored
handles and `fallback_url` are never fetched for playback. `fallback_url` stays
in the data for humans.

A source is a channel, not a show. "TV Patrol" is a title in ABS-CBN News'
live list, not a thing you save. The entry's `name` is the user's label for the
channel and is never rewritten by automation. `channel_name` holds the resolved
YouTube title, so a label like "News5Everywhere" can be bound to a channel
titled "TV5 News Philippines" without being flagged.

### 3. The live picker

Reuses `pick_stream`, with rows and sorting as above:

```text
News5Everywhere · 3 live, 1 upcoming
  ● LIVE   176 watching    Spot Report | DZMM Teleradyo
  ● LIVE   1.2K watching   Ted Failon and DJ Chacha
  ⏰        7:00 AM         The World Tonight
  ↻         34m ago        TV Patrol Livestream | Nov 4
  ── no live? show replays · past 24h with this header ──
```

When there is no live and no upcoming, the header says so plainly
("no live right now · replays below") and the channel's YouTube page is offered
as a row. Never a silent substitute.

Upcoming rows do not play. Enter on one shows a non-playing info panel with the
start time and the channel URL. Replay rows carry no live badge and are limited
to `news.recent_hours` (default 24); the header says "replays · past 24h"
rather than "today" so the label stays true. Older items are not listed in v1.

`play_news_stream` gets the item's `status` so the playing header shows `● LIVE`
only when the data said live.

### 4. The Saved screen

Main menu becomes:

```text
Saved
Search
History
Exit
```

plus `▶ Now Playing` when a player is alive. `Saved` is one `pick_query` list:

```text
  ● News5Everywhere            [channel]
  ● ABS-CBN News               [channel]
  ♪ Love Radio Manila          [station]
  ♪ Star FM Manila             [station]
  ♪ Linkin Park                [preset]
  ⚙  manage saved
  🔍 search…
```

Rows are grouped by kind and carry a dim kind tag. Typing filters the rows.
Enter with no selected row opens a search whose scope follows the active filter;
when the filter is All it opens a small chooser (YouTube / YouTube live /
Radio stations). Selecting `manage saved` opens a kind chooser that routes to the
existing `manage_presets_screen`, `manage_stations_screen`, and
`manage_sources_screen`.

Filters are passed in, not navigated to: `--radio` opens Saved filtered to
presets, `--stations` to stations, `--news` to channels. Direct-play forms
(`--radio lofi`, `--stations <slug>`, `--news <channel>`) keep working, with
one honesty rule: `--news <channel>` plays the top live if there is one, and
otherwise prints the live/upcoming/replay table and exits without playing.
`--replay` opts in to the newest replay. The CLI has no chrome to warn you, so
it must not silently hand you yesterday's broadcast.

This replaces `run_radio_mode`'s picker, `run_stations_mode`'s picker, and
`pick_source`. The three functions become thin wrappers that call the Saved
entry with a filter, so the existing CLI and symlink behavior is preserved.

### 5. Save semantics

`save_current` stops asking "preset or news channel" as a guess. Options are
derived from what is playing:

- Channel live → `save this channel` (default), `save this video as preset`
  (secondary, labeled "fixed link, will not follow tomorrow's live").
- Station → `save this station`. Already-saved shows nothing.
- Preset or search result → `save as preset`, plus `save its channel` when the
  uploader resolves.
- URL entry → same options, channel resolve best-effort.

`prompt_save_channel` stops asking for keywords. A channel source is
`{id, name, channel_id, enabled}`. The keywords field stays in the config for
backward compatibility and is ignored for channel sources.

### 6. Verify and repair

`verify_channels(cfg)` fetches each entry's `/streams` page. The page is the
evidence, because `resolve_url_meta` returns `None` for a 404, a timeout, and a
missing `yt-dlp` alike.

Statuses:

- `ok`: page fetched, channel title equals `channel_name` when present, else
  `name` (case-insensitive).
- `label-mismatch`: page fetched, title differs from both. Report only. Never
  auto-rename the entry and never auto-rebind the id, because either rewrite
  could point a label like "TV Patrol" at 24 Oras. The report shows both names
  and offers "keep binding" / "rebind to the channel this name resolves to" /
  "disable".
- `missing-id`: no `channel_id` stored. At play time this row is dimmed and
  non-playable ("needs repair"), never a keyword search. Repair resolves the
  entry name against YouTube channels and picks a candidate only on an exact
  case-insensitive title match; anything else lists candidates for the user to
  pick in the TUI. `--apply` handles exact matches only and prints every mapping
  it wrote.
- `dead`: HTTP 404 confirmed on a retry. Disabled, not deleted, and reversible.
  Timeouts and network errors are `unknown`: reported, never applied.

Duplicate bindings (two entries on the same channel) are allowed and flagged in
the report; both rows show the same live list. Merging is a manual choice.

Surfaces:

- `yt-cli --news --verify` prints the report; `--apply` writes the safe subset:
  exact-match ids for `missing-id`, disable for confirmed `dead`. Nothing else.
- `--news --verify --json` for scripting.
- Manage saved → Channels shows per-entry status and offers repair and rebind.

This is the only migration-shaped action. It may rewrite `channel_id`, fill an
empty `name`, and set `enabled`. It never deletes and never renames a non-empty
label.

### 7. History and detach

History entries gain `kind` and `channel_id` for channel plays. Replay stays
literal: `r` replays that recording. No hidden re-resolution, because history is
history. `attach_detached` and `relaunch_detached` are unchanged; a detached
channel stream reattaches as a generic player, which is correct.

### 8. Phasing

Phase 1, engine, shippable alone: lockup parser, channel-only resolution, live
picker with badges and sort, word-boundary search filter, verify/repair. This
also includes the day-one data repair from the verified bindings table: bind the
three simulcast labels and News5Everywhere to their channel ids, add 105.9 True
FM as an optional fifth channel, and repair any entry whose label and channel
disagree. The user confirmed kapamilya-online-live, kapuso-online-live,
kapatid-live-stream, and News5Everywhere are the ones in daily use. This alone
fixes tomorrow morning.

Phase 2, catalog: SavedItem adapters, dispatcher, Saved screen, main menu, save
semantics, filters, CLI wrappers, history kind.

Phase 3, cleanup: retire the three entry pickers, unify manage behind Saved,
update E2E flows and README/CONTEXT.

Each phase leaves the tool working. If C stalls after Phase 1, the morning is
already fixed.

## Calls made from the builder check

- `SavedItem.raw` keeps the original record so no play path loses fields.
- Verify never renames a label and never rebinds an id automatically. It reports
  `label-mismatch` with options.
- `missing-id` repairs only on an exact channel-title match under `--apply`;
  ambiguous names go to the TUI.
- `dead` requires a confirmed 404 on a retry; timeouts are `unknown` and are
  never applied. Disabling is reversible.
- Upcoming rows are informational and never play. Replays honor
  `news.recent_hours` and the header says so.
- `--news <channel>` refuses to play a replay unless `--replay` is passed.

## What is left to the builder

- Exact row formatting and kind tag text.
- Whether the Saved list sorts channels before stations before presets, or by
  most-recently-played. Proposal: kind order, alphabetical within kind.
- How the search chooser is presented in curses mode (same rows, no fzf).
- Test fixture shape: a small captured `lockupViewModel` JSON snippet, checked
  in under `tests/fixtures/`.
- Whether `--list` outputs change. Proposal: leave them, add `--saved list`.

## Risks

- **Key-letter collisions.** Four action tables collapse into fewer. `k` must
  stay save, `s` search, `o` other, `p` player. Add a unit test that asserts
  every action table has unique first letters, which is the recurring bug from
  CONTEXT.md.
- **Curses parity.** No `pick_query` header prompt and diff rendering issues.
  The Saved screen must work with the existing UI abstraction; no new UI methods.
- **YouTube markup changes again.** The parser is best-effort and must degrade to
  an honest "could not read channel page" message with the channel URL, not to a
  global search that plays something else.
- **E2E churn.** flow_a, b, c, d, f all assert menu labels. Budget for updating
  them, and keep network-free unit tests for the parser so the suite is not
  fully dependent on live pages.
- **Channel ids rot too.** Mitigated by verify/repair and by resolving at save
  time.

## Verification

- Unit: lockup parsing from fixture, metadata classification, sorting, adapter
  shape, dispatcher selection, action-table key uniqueness, word-boundary
  filter (PAW Patrol case), channel URL derivation.
- Manual: pick News5 at 6am, expect Ted Failon and DJ Chacha at the top with a
  live badge. Pick ABS-CBN after TV Patrol ends, expect replays plus an honest
  "no live" header.
- E2E: updated flows for Saved entry, channel live list, station playback, save
  semantics.
- Regression: `uv run --extra dev pytest` green, `ruff` clean, and the five E2E
  flows pass or have a documented reason.

## Implementation notes (2026-11-04)

- `--news --add` was added; `--apply` cannot create entries.
- `--bind` and `--apply` resolve a plain name with an exact channel-title search.
- The four daily channels were bound. True FM was not added by default.
- CI lives in `.github/workflows/ci.yml`; pyright is a dev dependency.
