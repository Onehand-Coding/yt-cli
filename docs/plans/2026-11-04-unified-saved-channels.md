# Unified Saved Catalog + Channel-First News Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a saved news channel resolve to that channel's current live shows, and put all saved things (channels, stations, presets) behind one Saved screen.

**Architecture:** Keep the single-file `yt_cli.py` and the existing playback engines. Replace the dead `videoRenderer` channel parser with a `lockupViewModel` parser, split channel resolution from global search, and add a normalized saved-item adapter plus one dispatcher. Modes remain as filters over the saved catalog; `--presets`, `--radio`, `--channels`, and the `music/` symlinks keep working.

**Tech Stack:** Python 3.10+ stdlib only, pytest, fzf/curses UI abstraction, mpv/yt-dlp.

**Spec:** `docs/specs/2026-11-04-unified-saved-channels-design.md`

## Global Constraints

- No new runtime dependencies. Standard library only.
- No on-disk schema change. `config.json` keeps `radio.presets`, `stations`, `news.sources`; everything reads through adapters.
- No network at Saved-screen render time.
- No em-dashes or en-dashes in first-party prose.
- Action-menu labels must start with a unique letter within their table.
- Every network function must be testable with `fetch_url` monkeypatched.
- Tests must not hit the network.
- `uv run --extra dev pytest` stays green; `uv run --extra dev ruff check .` stays clean.

## Review Focus

- A channel page that returns HTML but no lockups: must show "could not read channel page", never fall back to a global search.
- A live that ended between fetch and play: the playing header must not claim LIVE after the list said `past`.
- An upcoming item selected by Enter: must never launch a player.
- The PAW Patrol class of input: a query phrase must not match across word boundaries.
- A duplicate or wrongly bound channel entry: verify must report, never silently rewrite the label or binding.

---

### Task 1: Lockup parsing, classification, and sort

**Files:**
- Modify: `yt_cli.py` (add helpers after `parse_relative_time`, around line 3184)
- Create: `tests/fixtures/lockups.json`
- Test: `tests/test_channels.py` (new)

**Interfaces:**
- Produces:
  - `classify_lockup_meta(rows: list[dict]) -> dict` with keys `status` (`"live" | "upcoming" | "past" | "unknown"`), `viewers`, `viewers_text`, `schedule_text`, `streamed_text`, `hours_ago`.
  - `parse_lockup_video(lv: dict) -> dict | None` with keys `id`, `title`, `url`, `status`, `is_live`, `viewers`, `viewers_text`, `schedule_text`, `streamed_text`, `hours_ago`, `source`.
  - `parse_lockup_videos(data: dict) -> list[dict]`.
  - `sort_stream_items(items: list[dict]) -> list[dict]`.
  - `parse_ago_text(text: str) -> float` returning hours, `9999` when unknown.

- [ ] **Step 1: Create the fixture**

`tests/fixtures/lockups.json` holds a minimal but real-shaped blob: a `richGridRenderer` inside `contents.twoColumnBrowseResultsRenderer.tabs[0].tabRenderer.content`, with four `richItemRenderer` entries whose `content` is a `lockupViewModel`:

```json
{
  "contents": {"twoColumnBrowseResultsRenderer": {"tabs": [
    {"tabRenderer": {"content": {"richGridRenderer": {"contents": [
      {"richItemRenderer": {"content": {"lockupViewModel": {
        "contentId": "live0000001",
        "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
        "metadata": {"lockupMetadataViewModel": {
          "title": {"content": "Ted Failon DJ Chacha sa True FM Livestream | November 4, 2026"},
          "metadata": {"contentMetadataViewModel": {"metadataRows": [
            {"metadataParts": [{"text": {"content": "1.2K watching"}}]}
          ]}}
        }}
      }}}},
      {"richItemRenderer": {"content": {"lockupViewModel": {
        "contentId": "upcoming001",
        "metadata": {"lockupMetadataViewModel": {
          "title": {"content": "The World Tonight | Full Episode"},
          "metadata": {"contentMetadataViewModel": {"metadataRows": [
            {"metadataParts": [{"text": {"content": "1 waiting"}}]},
            {"metadataParts": [{"text": {"content": "Scheduled for 11/4/26, 7:00 AM"}}]}
          ]}}
        }}
      }}}},
      {"richItemRenderer": {"content": {"lockupViewModel": {
        "contentId": "past0000001",
        "metadata": {"lockupMetadataViewModel": {
          "title": {"content": "TV Patrol Livestream | November 4, 2026"},
          "metadata": {"contentMetadataViewModel": {"metadataRows": [
            {"metadataParts": [
              {"text": {"content": "63K"}},
              {"text": {"content": "Streamed 35 min ago"}}
            ]}
          ]}}
        }}
      }}}},
      {"richItemRenderer": {"content": {"lockupViewModel": {
        "contentId": "unknown0001",
        "metadata": {"lockupMetadataViewModel": {
          "title": {"content": "No metadata item"},
          "metadata": {"contentMetadataViewModel": {"metadataRows": []}}
        }}
      }}}}
    ]}}}}
  ]}}
}
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_channels.py`:

```python
def _fixture():
    return json.loads((Path(__file__).parent / "fixtures" / "lockups.json").read_text())


def test_parse_lockups_classifies_live_upcoming_past_unknown():
    items = {i["id"]: i for i in yt_cli.parse_lockup_videos(_fixture())}
    assert items["live0000001"]["status"] == "live"
    assert items["live0000001"]["is_live"] is True
    assert items["live0000001"]["viewers"] == 1200
    assert items["upcoming001"]["status"] == "upcoming"
    assert items["upcoming001"]["schedule_text"] == "Scheduled for 11/4/26, 7:00 AM"
    assert items["past0000001"]["status"] == "past"
    assert items["past0000001"]["hours_ago"] < 1
    assert items["unknown0001"]["status"] == "unknown"
    assert items["live0000001"]["url"].endswith("watch?v=live0000001")


def test_sort_stream_items_live_then_upcoming_then_past():
    items = yt_cli.parse_lockup_videos(_fixture())
    order = [i["status"] for i in yt_cli.sort_stream_items(items)]
    assert order == ["live", "upcoming", "past", "unknown"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Streamed 35 min ago", 35 / 60),
        ("Streamed 2h ago", 2),
        ("Streamed 3d ago", 72),
        ("Streamed 2mo ago", 1440),
        ("", 9999),
    ],
)
def test_parse_ago_text(text, expected):
    assert yt_cli.parse_ago_text(text) == pytest.approx(expected)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_channels.py -q`
Expected: FAIL with `AttributeError: module 'yt_cli' has no attribute 'parse_lockup_videos'`.

- [ ] **Step 4: Implement the helpers**

Implementation notes:
- `parse_ago_text` regex: `(\d+)\s*(mo|y|w|d|h|hr|m|min|s)\b` mapped to hours (`mo`=720, `y`=8760, `w`=168, `d`=24, `h`/`hr`=1, `m`/`min`=1/60, `s`=1/3600). Longest units first so `mo` wins over `m`.
- `classify_lockup_meta`: join each row's `metadataParts[].text.content` with spaces, then apply in order: live regex `^([\d.,]+[KMB]?)\s+watching` on the joined row; upcoming if `waiting` or `Scheduled for` present; past if `Streamed` or `Premiered` present. Parse `1.2K` to `1200`, `63K` to `63000`.
- `parse_lockup_videos`: recursive walk collecting every `lockupViewModel` value. Skip entries without `contentId`. Title at `metadata.lockupMetadataViewModel.title.content`. Source left `""`.
- `sort_stream_items`: rank live 0, upcoming 1, past 2, unknown 3; within live viewers desc; within past `hours_ago` asc; stable otherwise.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/test_channels.py -q`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add yt_cli.py tests/test_channels.py tests/fixtures/lockups.json
git commit -m "Parse channel live listings from lockupViewModel"
```

---

### Task 2: Channel fetch with honest errors

**Files:**
- Modify: `yt_cli.py` (near `search_youtube`)
- Test: `tests/test_channels.py`

**Interfaces:**
- Produces:
  - `class ChannelError(Exception)`.
  - `channel_streams_url(channel_id: str) -> str`.
  - `channel_lives(channel_id: str, timeout: int = 15) -> list[dict]`, raising `ChannelError` on no id, HTTP error, unreadable data, or zero lockups.

- [ ] **Step 1: Write the failing tests**

```python
def test_channel_lives_returns_sorted(monkeypatch):
    monkeypatch.setattr(yt_cli, "fetch_url", lambda url, timeout=15: "<html>")
    monkeypatch.setattr(yt_cli, "extract_yt_initial_data", lambda html: _fixture())
    items = yt_cli.channel_lives("UCGEbMwiX774cseKvJqF9R2g")
    assert items[0]["status"] == "live"
    assert items[-1]["status"] == "unknown"


def test_channel_lives_404_is_a_channel_error(monkeypatch):
    monkeypatch.setattr(yt_cli, "fetch_url", lambda url, timeout=15: "HTTP Error 404: Not Found")
    with pytest.raises(yt_cli.ChannelError, match="404"):
        yt_cli.channel_lives("UCdead")


def test_channel_lives_without_id_is_a_channel_error():
    with pytest.raises(yt_cli.ChannelError, match="no channel id"):
        yt_cli.channel_lives("")


def test_channel_lives_empty_page_is_a_channel_error(monkeypatch):
    monkeypatch.setattr(yt_cli, "fetch_url", lambda url, timeout=15: "<html>")
    monkeypatch.setattr(yt_cli, "extract_yt_initial_data", lambda html: {})
    with pytest.raises(yt_cli.ChannelError, match="could not read"):
        yt_cli.channel_lives("UCx")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_channels.py -q`
Expected: FAIL with `AttributeError: module 'yt_cli' has no attribute 'ChannelError'`.

- [ ] **Step 3: Implement**

- URL is always `https://www.youtube.com/channel/<id>/streams`.
- On non-HTML `fetch_url` results: message `"channel not found (404)"` when it contains `404`, else `"could not fetch channel page"`.
- On missing initial data or zero parsed lockups: `"could not read channel page"`.
- Never call `search_youtube` or `search_live` from this function.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/test_channels.py -q`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add yt_cli.py tests/test_channels.py
git commit -m "Add channel_lives with honest failure modes"
```

---

### Task 3: Global live search with phrase matching

**Files:**
- Modify: `yt_cli.py` (replace `search_youtube`, adjust `parse_video_renderer`)
- Test: `tests/test_channels.py`

**Interfaces:**
- Produces:
  - `phrase_in_title(query: str, title: str) -> bool` comparing whitespace-normalized lowercase phrases.
  - `parse_search_video(video: dict) -> dict | None` using the existing videoRenderer fields, no keyword filter, `source` from `ownerText`.
  - `search_live(query: str, timeout: int = 15, include_recent: bool = True, recent_hours: int = 24) -> list[dict]`, live-first, phrase-filtered, appending rare non-live results only when no live exists.
- Consumes: `parse_ago_text` from Task 1, `sort_stream_items` from Task 1.

- [ ] **Step 1: Write the failing tests**

```python
def test_phrase_filter_rejects_paw_patrol():
    assert yt_cli.phrase_in_title("tv patrol", "TV Patrol Livestream") is True
    assert yt_cli.phrase_in_title("tv patrol", "PAW Patrol Livestream") is False
    assert yt_cli.phrase_in_title("ted   failon", "Ted Failon DJ Chacha") is True


def test_search_live_prefers_live_and_skips_recent_when_live_exists(monkeypatch):
    html = "<html>"
    data = {
        "contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {"sectionListRenderer": {"contents": [
            {"itemSectionRenderer": {"contents": [
                {"videoRenderer": {
                    "videoId": "livelive001",
                    "title": {"runs": [{"text": "TV Patrol Livestream | November 4"}]},
                    "badges": [{"metadataBadgeRenderer": {"style": "BADGE_STYLE_TYPE_LIVE_NOW"}}],
                    "ownerText": {"runs": [{"text": "ABS-CBN News"}]},
                }},
                {"videoRenderer": {
                    "videoId": "recent00001",
                    "title": {"runs": [{"text": "TV Patrol Express | November 4"}]},
                    "publishedTimeText": {"simpleText": "2 hours ago"},
                    "ownerText": {"runs": [{"text": "ABS-CBN News"}]},
                }},
                {"videoRenderer": {
                    "videoId": "pawpaw00001",
                    "title": {"runs": [{"text": "PAW Patrol Livestream"}]},
                    "badges": [{"metadataBadgeRenderer": {"style": "BADGE_STYLE_TYPE_LIVE_NOW"}}],
                    "ownerText": {"runs": [{"text": "PAW Patrol"}]},
                }},
            ]}}
        ]}}}
    }
    monkeypatch.setattr(yt_cli, "fetch_url", lambda url, timeout=15: html)
    monkeypatch.setattr(yt_cli, "extract_yt_initial_data", lambda h: data)
    items = yt_cli.search_live("TV Patrol", include_recent=True)
    assert [i["id"] for i in items] == ["livelive001"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_channels.py -q`
Expected: FAIL with `AttributeError: module 'yt_cli' has no attribute 'phrase_in_title'`.

- [ ] **Step 3: Implement**

- Live detection reuses the existing badge and thumbnail-overlay checks.
- Recency for non-live results uses `parse_ago_text` on `publishedTimeText`.
- `search_live` fetches `https://www.youtube.com/results?search_query=<query> live&sp=EgJAAQ%253D%253D`.
- If no live items, append phrase-matching non-live items within `recent_hours`, status `"past"`.
- Delete `find_streams`. Keep `search_youtube(query, keywords, channel_id, timeout, recent_hours)` as a thin wrapper returning `search_live(query, timeout, True, recent_hours)` until Tasks 4 and 5 rewire the call sites, then delete it in Task 5.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/test_channels.py -q`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add yt_cli.py tests/test_channels.py
git commit -m "Search live videos with whole-phrase matching"
```

---

### Task 4: Channel-first news TUI

**Files:**
- Modify: `yt_cli.py`: `pick_stream`, `run_news_mode`, `play_news`, `add_to_history` call
- Test: `tests/test_channels.py`

**Interfaces:**
- Produces:
  - `format_stream_row(item: dict) -> str` for picker rows.
  - `pick_live(items, channel_name, ui) -> dict | "retry" | None`.
  - `run_news_mode(..., initial_source: str | None = None)`.
- Consumes: `channel_lives`, `ChannelError`, `search_live`, `sort_stream_items`.

- [ ] **Step 1: Write the failing row tests**

```python
def test_stream_row_labels_live_upcoming_past():
    live = {"title": "Ted Failon", "status": "live", "viewers_text": "1.2K watching"}
    upcoming = {"title": "World Tonight", "status": "upcoming", "schedule_text": "7:00 AM"}
    past = {"title": "TV Patrol", "status": "past", "streamed_text": "34 min ago"}
    assert "LIVE" in yt_cli.format_stream_row(live)
    assert "1.2K watching" in yt_cli.format_stream_row(live)
    assert "7:00 AM" in yt_cli.format_stream_row(upcoming)
    assert "34 min ago" in yt_cli.format_stream_row(past)
    assert "LIVE" not in yt_cli.format_stream_row(past)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_channels.py::test_stream_row_labels_live_upcoming_past -q`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Write the failing status tests**

```python
def test_stream_badge_never_claims_live_for_past():
    assert "LIVE" in yt_cli.render_stream_badge({"status": "live", "viewers_text": "1.2K watching"})
    assert "LIVE" not in yt_cli.render_stream_badge({"status": "past", "streamed_text": "34 min ago"})


def test_upcoming_items_are_not_playable():
    assert yt_cli.stream_is_playable({"status": "upcoming"}) is False
    assert yt_cli.stream_is_playable({"status": "live"}) is True
    assert yt_cli.stream_is_playable({"status": "past"}) is True
```

Run: `uv run --extra dev pytest tests/test_channels.py -q`
Expected: FAIL with `AttributeError`.

- [ ] **Step 4: Implement the row formatter, badge, and picker**

- Live row: `● LIVE  {viewers_text:<16}  {title}`.
- Upcoming row: `⏰       {schedule_text:<16}  {title}`.
- Past row: `↻        {streamed_text:<16}  {title}`.
- `pick_live` header: `"{channel_name} · {n} live, {m} upcoming"`; when zero live, header ends with `"no live right now · replays · past {recent_hours}h"`.
- `render_stream_badge(item)` keys off `item["status"]`: live gets `● LIVE`, upcoming `⏰`, past `↻`, unknown a dim dot. It must not infer live from the old booleans.
- `stream_is_playable(item)` is `item.get("status") != "upcoming"`.
- Selecting an upcoming row returns `{"info": "upcoming", **item}`; the caller shows `ui.info([title, schedule_text, channel URL])` and does not play.

- [ ] **Step 5: Rewire `run_news_mode`**

- Channel source branch calls `channel_lives(source_info["channel_id"], settings["timeout"])` and nothing else.
- `ChannelError` shows `ui.action([str(e)], [("retry","r"),("back","b")])`; never a search fallback.
- Search branch calls `search_live(query, settings["timeout"], True, settings["recent_hours"])`.
- Playing loop `k` saves the channel: include `channel_id`, `channel_name`, `kind: "channel"`.
- History entry includes `kind: "channel"` and `channel_id` for channel plays.
- `initial_source` skips the source picker and runs that source's flow.
- Replays are filtered to `hours_ago <= settings["recent_hours"]`.

- [ ] **Step 5: Run the unit suite**

Run: `uv run --extra dev pytest -q`
Expected: PASS (54 tests).

- [ ] **Step 6: Commit**

```bash
git add yt_cli.py tests/test_channels.py
git commit -m "Resolve news sources as channel live lists"
```

---

### Task 5: CLI news is honest about replays

**Files:**
- Modify: `yt_cli.py`: `_news_cli_main`, `print_news_help`
- Test: `tests/test_channels.py`

**Interfaces:**
- Produces: `choose_cli_stream(items: list[dict], allow_replay: bool) -> dict | None`.
- Consumes: `channel_lives`.

- [ ] **Step 1: Write the failing tests**

```python
def test_choose_cli_stream_returns_live_only_by_default():
    items = [
        {"title": "Old", "status": "past"},
        {"title": "Now", "status": "live"},
    ]
    assert yt_cli.choose_cli_stream(items, allow_replay=False)["title"] == "Now"


def test_choose_cli_stream_refuses_when_no_live():
    items = [{"title": "Old", "status": "past"}]
    assert yt_cli.choose_cli_stream(items, allow_replay=False) is None
    assert yt_cli.choose_cli_stream(items, allow_replay=True)["title"] == "Old"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_channels.py -q`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

- `_news_cli_main` parses `--replay`, `--verify`, `--apply`, `--json` before the source query.
- With `--verify`: print each entry's status and names, then exit 0. With `--apply`, call `apply_channel_fixes` from Task 6.
- No live and no `--replay`: print the live/upcoming/replay table and exit 0 without launching a player.
- Channel fetch errors print the message and the derived channel URL, exit 1.
- Update `print_news_help` to describe channel behavior, `--replay`, and `--verify`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest -q`
Expected: PASS (56 tests).

- [ ] **Step 5: Commit**

```bash
git add yt_cli.py tests/test_channels.py
git commit -m "Make news CLI refuse silent replays"
```

---

### Task 6: Verify, repair, and bind channels

**Files:**
- Modify: `yt_cli.py`: `manage_sources_screen`, new verify helpers
- Test: `tests/test_channels.py`

**Interfaces:**
- Produces:
  - `channel_status(entry: dict, timeout: int = 15) -> dict` with `id`, `name`, `channel_id`, `status` (`ok|label-mismatch|missing-id|dead|unknown`), `channel_title`, `error`.
  - `verify_channels(cfg, timeout: int = 15) -> list[dict]`.
  - `apply_channel_fixes(cfg, report: list[dict]) -> dict` with `bound`, `disabled`, `skipped` counts.
  - `resolve_channel_id(ref: str, timeout: int = 15) -> dict | None` accepting `UC...`, `@handle`, or a channel URL, returning `{id, title}`.
  - `bind_channel(cfg, source_id: str, ref: str) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_channel_status_mismatch_is_reported_not_applied(monkeypatch, cfg_files):
    cfg = {"news": {"sources": [
        {"id": "news5", "name": "News5Everywhere", "channel_id": "UCx", "enabled": True}
    ]}}
    monkeypatch.setattr(yt_cli, "fetch_url", lambda url, timeout=15: "<html><title>TV5 News Philippines - YouTube</title>")
    report = yt_cli.verify_channels(cfg)
    assert report[0]["status"] == "label-mismatch"
    assert report[0]["channel_title"] == "TV5 News Philippines"
    yt_cli.apply_channel_fixes(cfg, report)
    assert cfg["news"]["sources"][0]["channel_id"] == "UCx"


def test_channel_status_missing_id_is_not_playable(monkeypatch):
    monkeypatch.setattr(yt_cli, "fetch_url", lambda url, timeout=15: "<html>")
    entry = {"id": "x", "name": "X", "channel_id": ""}
    assert yt_cli.channel_status(entry)["status"] == "missing-id"


def test_verify_flags_duplicate_bindings(monkeypatch):
    cfg = {"news": {"sources": [
        {"id": "a", "name": "A", "channel_id": "UCx"},
        {"id": "b", "name": "B", "channel_id": "UCx"},
    ]}}
    monkeypatch.setattr(yt_cli, "fetch_url", lambda url, timeout=15: "<html><title>A - YouTube</title>")
    report = yt_cli.verify_channels(cfg)
    assert all(r["duplicate"] is True for r in report)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_channels.py -q`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

- Fetch `/streams`; extract page title and strip `" - YouTube"`.
- `missing-id` short-circuits before fetching.
- HTTP 404 on a retry is `dead`; any other non-page result is `unknown`.
- `apply_channel_fixes` may only: fill a missing `channel_id` when `resolve_channel_id(name)` returns an exact case-insensitive title match, set `channel_name`, and disable confirmed `dead`. It never renames `name`, never rebinds a non-empty `channel_id`.
- `manage_sources_screen` shows `[ok|mismatch|missing|dead|unknown]`, adds a "verify channels" row that runs `verify_channels` and prints a report, and a "rebind" action that prompts for a handle or URL and calls `bind_channel`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest -q`
Expected: PASS (59 tests).

- [ ] **Step 5: Commit**

```bash
git add yt_cli.py tests/test_channels.py
git commit -m "Verify and repair channel bindings"
```

---

### Task 7: Saved item adapters

**Files:**
- Modify: `yt_cli.py` (new section after the stations section)
- Test: `tests/test_saved.py` (new)

**Interfaces:**
- Produces:
  - `saved_items(cfg) -> list[dict]` with `kind`, `id`, `name`, `enabled`, `group`, `url`, `channel_id`, `channel_name`, `loop`, `no_video`, `raw`.
  - `format_saved_row(item: dict, now_playing: str | None = None) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
def test_saved_items_maps_all_three_kinds(cfg_files):
    cfg = {
        "radio": {"presets": [{"slug": "jazz", "name": "Jazz", "url": "u1", "category": "Chill"}]},
        "stations": [{"slug": "star", "name": "Star FM", "url": "u2", "countrycode": "PH"}],
        "news": {"sources": [{"id": "news5", "name": "News5Everywhere", "channel_id": "UCx"}]},
    }
    items = yt_cli.saved_items(cfg)
    kinds = {i["kind"] for i in items}
    assert kinds == {"preset", "station", "channel"}
    channel = next(i for i in items if i["kind"] == "channel")
    assert channel["channel_id"] == "UCx"
    assert channel["raw"]["id"] == "news5"


def test_saved_items_includes_disabled_but_marks_them(cfg_files):
    cfg = {"radio": {"presets": [{"slug": "x", "name": "X", "url": "u", "enabled": False}]},
           "stations": [], "news": {"sources": []}}
    assert yt_cli.saved_items(cfg)[0]["enabled"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_saved.py -q`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement adapters**

- Preset: `id=slug`, `name`, `url`, `group=category`, `loop`, `no_video`, `raw=preset`.
- Station: `id=slug`, `name`, `url`, `group=countrycode`, `raw=station`.
- Channel: `id=id`, `name`, `channel_id`, `channel_name` (default `""`), `group=""`, `raw=source`.
- Sort: kind order channel, station, preset; alphabetical within kind.
- `format_saved_row`: `"{●|○|♪|▷}  {name:<32}  [{kind}]"`, dim tag, playing marker first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/test_saved.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add yt_cli.py tests/test_saved.py
git commit -m "Add normalized saved-item adapters"
```

---

### Task 8: Extract play-and-loop entry points

**Files:**
- Modify: `yt_cli.py`: `run_radio_mode`, `run_stations_mode`, `run_news_mode`
- Test: `tests/test_saved.py`

**Interfaces:**
- Produces:
  - `run_radio_mode(..., initial_slug: str | None = None)`.
  - `run_stations_mode(..., initial_slug: str | None = None)`.
  - `run_news_mode(..., initial_source: str | None = None)`.
- Behavior: when an initial id is supplied, skip the picker, play it, and run its playing loop. `on_esc="home"` returns to the caller after the loop instead of exiting.

- [ ] **Step 1: Write the failing test**

```python
def test_infer_saved_target_maps_kind_to_mode():
    assert yt_cli.SAVED_MODE["preset"] == "radio"
    assert yt_cli.SAVED_MODE["station"] == "stations"
    assert yt_cli.SAVED_MODE["channel"] == "news"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_saved.py -q`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

- Add `SAVED_MODE = {"preset": "radio", "station": "stations", "channel": "news"}`.
- In each mode, resolve the initial id before the `while True` picker; play it; run the existing playing loop; if `on_esc == "home"`, return afterwards.
- No playback changes.

- [ ] **Step 4: Run the suite**

Run: `uv run --extra dev pytest -q`
Expected: PASS (62 tests).

- [ ] **Step 5: Commit**

```bash
git add yt_cli.py tests/test_saved.py
git commit -m "Allow direct entry into radio, station, and channel playback"
```

---

### Task 9: The Saved screen and main menu

**Files:**
- Modify: `yt_cli.py`: new `run_saved_mode`, `pick_saved`, `manage_saved_screen`; `run_fzf`, `_curses_main`, `_run_mode`, `go_to_picker`, `_radio_cli_main`, `run_stations_cli`, `_news_cli_main`
- Test: `tests/test_saved.py`
- E2E: update flows later in Task 12

**Interfaces:**
- Produces:
  - `pick_saved(items, ui) -> dict | ("manage", None) | ("search", str) | ("url", str) | None`.
  - `run_saved_mode(cfg, ui, kind=None, stop=stop_shared, goodbye=None, on_esc="quit")`.
- Main menu: `Saved / Search / History / Exit` plus `▶ Now Playing`.
- `--presets` → `run_saved_mode(kind="preset")`; `--radio` → `kind="station"`; `--channels` → `kind="channel"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_pick_saved_maps_selected_row_to_item():
    class UI:
        def pick_query(self, items, prompt, header, height):
            return ("", 0)
    items = [{"kind": "channel", "name": "News5", "channel_id": "UCx", "enabled": True,
              "group": "", "raw": {}}]
    assert yt_cli.pick_saved(items, UI())["name"] == "News5"


def test_pick_saved_typing_opens_search():
    class UI:
        def pick_query(self, items, prompt, header, height):
            return ("jazz", None)
    assert yt_cli.pick_saved([], UI()) == ("search", "jazz")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_saved.py -q`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

- `pick_saved` rows include a leading `🔍  search…` row and a trailing `⚙  manage saved` row; indices map accordingly. `looks_like_url(query)` returns `("url", query)`.
- A channel row with an empty `channel_id` is rendered with `⚠ needs repair` and selecting it shows a repair hint, not playback.
- `run_saved_mode` loops: pick, then dispatch by kind via the initial-id entry points. `on_esc="home"` returns to the caller.
- `manage_saved_screen` offers Presets / Stations / Channels which route to the existing manage screens.
- `run_fzf` and `_curses_main` main menus become `["Saved","Search","History"]`; `Exit` and `▶ Now Playing` unchanged.
- `_run_mode` maps `Saved`, `Radio`, `Stations`, `News` onto `run_saved_mode` filters.
- `go_to_picker` entries become `Saved / Radio / Stations / News / Search / Home`, mapped to the same filters.

- [ ] **Step 4: Run the suite**

Run: `uv run --extra dev pytest -q`
Expected: PASS (64 tests).

- [ ] **Step 5: Commit**

```bash
git add yt_cli.py tests/test_saved.py
git commit -m "Add unified Saved screen and dispatcher"
```

---

### Task 10: Context-aware save menu

**Files:**
- Modify: `yt_cli.py`: `save_current`, `prompt_save_channel`, `news_add_source`
- Test: `tests/test_saved.py`

**Interfaces:**
- Produces: `save_options(item: dict) -> list[tuple[str, str]]`.
- Behavior:
  - Channel context → `[("save this channel","channel"), ("save video as preset","preset")]`.
  - Station context → `[("save this station","station")]`.
  - Preset/search context with a resolvable uploader → `[("save as preset","preset"), ("save its channel","channel")]`.
  - Channel saves stop asking for keywords; store `channel_name`.

- [ ] **Step 1: Write the failing tests**

```python
def test_save_options_channel_context_prefers_channel():
    opts = yt_cli.save_options({"kind": "channel", "channel_id": "UCx"})
    assert opts[0] == ("save this channel", "channel")


def test_save_options_search_context_offers_preset_first():
    opts = yt_cli.save_options({"kind": "video", "channel_id": "UCx"})
    assert opts[0] == ("save as preset", "preset")


def test_action_tables_have_unique_first_letters():
    tables = [yt_cli.PLAY_ACTIONS_SEARCH, yt_cli.PLAY_ACTIONS_RADIO,
              yt_cli.PLAY_ACTIONS_NEWS, yt_cli.PLAY_ACTIONS_STATION]
    for table in tables:
        keys = [code for _, code in table]
        assert len(keys) == len(set(keys)), [label for label, _ in table]


def test_news_add_source_stores_channel_name(cfg_files):
    cfg = {"news": {"sources": []}, "radio": {"presets": []}, "stations": []}
    yt_cli.news_add_source(cfg, "News5", ["news5"], channel_id="UCx", channel_name="TV5 News Philippines")
    assert cfg["news"]["sources"][0]["channel_name"] == "TV5 News Philippines"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_saved.py -q`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

- `save_options` reads `kind` from the item; `video` is the default for search/preset contexts.
- `save_current` uses `save_options` instead of its current fixed choice. The channel option passes `channel_id` and `channel_name`.
- `prompt_save_channel` no longer prompts for keywords when `channel_id` is present; `news_add_source` accepts and stores `channel_name`.
- The "save video as preset" label must keep `p` unused elsewhere in that action table.

- [ ] **Step 4: Run the suite**

Run: `uv run --extra dev pytest -q`
Expected: PASS (68 tests).

- [ ] **Step 5: Commit**

```bash
git add yt_cli.py tests/test_saved.py
git commit -m "Derive save options from playback context"
```

---

### Task 11: History carries kind

**Files:**
- Modify: `yt_cli.py`: `add_to_history` call sites in channel playback; history row formatter
- Test: `tests/test_saved.py`

**Interfaces:**
- Behavior: channel plays store `kind: "channel"` and `channel_id`; history rows append a dim `[channel]` tag; replay stays literal.

- [ ] **Step 1: Write the failing test**

```python
def test_add_to_history_keeps_kind(cfg_files):
    yt_cli.add_to_history(
        {"title": "Ted Failon", "url": "u", "kind": "channel", "channel_id": "UCx"},
        10,
    )
    assert json.loads(yt_cli.HISTORY_PATH.read_text())[0]["kind"] == "channel"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_saved.py -q`
Expected: FAIL (kind stripped or key missing).

- [ ] **Step 3: Implement**

- `add_to_history` already spreads the item; ensure channel call sites pass `kind` and `channel_id`.
- Extend `format_fzf_item` to append ` [channel]` when `item.get("kind") == "channel"`.

- [ ] **Step 4: Run the suite**

Run: `uv run --extra dev pytest -q`
Expected: PASS (69 tests).

- [ ] **Step 5: Commit**

```bash
git add yt_cli.py tests/test_saved.py
git commit -m "Tag channel history entries"
```

---

### Task 12: Day-one bindings, E2E labels, docs

**Files:**
- Modify: `tests/e2e/flow_a.py`, `flow_b.py`, `flow_c.py`, `flow_d.py`, `flow_e.py`, `flow_f.py`
- Modify: `README.md`, `CONTEXT.md`
- Data: the live config at `~/.config/yt-cli/config.json` (manual, via `yt-cli --channels --verify --apply` plus rebinds)

- [ ] **Step 1: Apply the verified bindings**

Run each, confirming the printed mapping:

```bash
yt-cli --channels --bind news5everywhere UCGEbMwiX774cseKvJqF9R2g
yt-cli --channels --bind kapamilya-online-live UCstEtN0pgOmCf02EdXsGChw
yt-cli --channels --bind kapuso-online-live UCKL5hAuzgFQsyrsQKgU0Qng
yt-cli --channels --bind kapatid-live-stream UCiFx0kZFjR29u0uJxTSq5MQ
```

True FM is optional and added only if the user asks:

```bash
yt-cli --channels --add "105.9 True FM" UCjUrQJqEIfO1e52faNzt04g
```

- [ ] **Step 2: Verify honest behavior**

Run: `yt-cli --channels news5everywhere` while nothing is live.
Expected: prints the current live/upcoming/replay table, launches nothing, exit 0.

Run: `yt-cli --channels news5everywhere --replay`.
Expected: plays the newest replay.

- [ ] **Step 3: Update E2E flows**

- Main menu: replace `Radio/Stations/News` expectations with `Saved`.
- Flow B enters `Saved` then filters/selects a preset.
- Flow C enters `Saved` then a channel, and accepts either a live list or the honest no-live header.
- Flow D curses main menu labels.
- Flow F stations entry.
- Keep `flow_e.py` save-key semantics, now with channel-first saving.

- [ ] **Step 4: Update docs**

- README: Saved screen, channel model, `--replay`, `--verify`.
- CONTEXT.md: replace the news-source architecture paragraph, add the lockup parser gotcha, the phrase-match rule, and the "simulcast vs per-show channel" distinction.

- [ ] **Step 5: Run the full verification**

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check .
python3 tests/e2e/flow_f.py      # network-free
```

Expected: pytest green, ruff clean, flow_f passes. Flows a to e need network and mpv; run them if the environment allows, otherwise record why not.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e README.md CONTEXT.md
git commit -m "Update flows and docs for the Saved catalog"
```
