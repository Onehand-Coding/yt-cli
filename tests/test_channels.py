"""Unit tests for channel live listings and the Saved catalog (no network)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yt_cli  # noqa: E402  (import after sys.path setup)


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


def test_phrase_filter_rejects_paw_patrol():
    assert yt_cli.phrase_in_title("tv patrol", "TV Patrol Livestream") is True
    assert yt_cli.phrase_in_title("tv patrol", "PAW Patrol Livestream") is False
    assert yt_cli.phrase_in_title("ted   failon", "Ted Failon DJ Chacha") is True


def test_search_live_prefers_live_and_skips_recent_when_live_exists(monkeypatch):
    data = {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "sectionListRenderer": {
                        "contents": [
                            {
                                "itemSectionRenderer": {
                                    "contents": [
                                        {
                                            "videoRenderer": {
                                                "videoId": "livelive001",
                                                "title": {
                                                    "runs": [
                                                        {
                                                            "text": "TV Patrol Livestream | November 4"
                                                        }
                                                    ]
                                                },
                                                "badges": [
                                                    {
                                                        "metadataBadgeRenderer": {
                                                            "style": "BADGE_STYLE_TYPE_LIVE_NOW"
                                                        }
                                                    }
                                                ],
                                                "ownerText": {"runs": [{"text": "ABS-CBN News"}]},
                                            }
                                        },
                                        {
                                            "videoRenderer": {
                                                "videoId": "recent00001",
                                                "title": {
                                                    "runs": [
                                                        {"text": "TV Patrol Express | November 4"}
                                                    ]
                                                },
                                                "publishedTimeText": {"simpleText": "2 hours ago"},
                                                "ownerText": {"runs": [{"text": "ABS-CBN News"}]},
                                            }
                                        },
                                        {
                                            "videoRenderer": {
                                                "videoId": "pawpaw00001",
                                                "title": {"runs": [{"text": "PAW Patrol Livestream"}]},
                                                "badges": [
                                                    {
                                                        "metadataBadgeRenderer": {
                                                            "style": "BADGE_STYLE_TYPE_LIVE_NOW"
                                                        }
                                                    }
                                                ],
                                                "ownerText": {"runs": [{"text": "PAW Patrol"}]},
                                            }
                                        },
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        }
    }
    monkeypatch.setattr(yt_cli, "fetch_url", lambda url, timeout=15: "<html>")
    monkeypatch.setattr(yt_cli, "extract_yt_initial_data", lambda html: data)
    items = yt_cli.search_live("TV Patrol", include_recent=True)
    assert [i["id"] for i in items] == ["livelive001"]


def test_stream_row_labels_live_upcoming_past():
    live = {"title": "Ted Failon", "status": "live", "viewers_text": "1.2K watching"}
    upcoming = {"title": "World Tonight", "status": "upcoming", "schedule_text": "7:00 AM"}
    past = {"title": "TV Patrol", "status": "past", "streamed_text": "34 min ago"}
    assert "LIVE" in yt_cli.format_stream_row(live)
    assert "1.2K watching" in yt_cli.format_stream_row(live)
    assert "7:00 AM" in yt_cli.format_stream_row(upcoming)
    assert "34 min ago" in yt_cli.format_stream_row(past)
    assert "LIVE" not in yt_cli.format_stream_row(past)


def test_stream_badge_never_claims_live_for_past():
    assert "LIVE" in yt_cli.render_stream_badge(
        {"status": "live", "viewers_text": "1.2K watching"}
    )
    assert "LIVE" not in yt_cli.render_stream_badge(
        {"status": "past", "streamed_text": "34 min ago"}
    )


def test_upcoming_items_are_not_playable():
    assert yt_cli.stream_is_playable({"status": "upcoming"}) is False
    assert yt_cli.stream_is_playable({"status": "live"}) is True
    assert yt_cli.stream_is_playable({"status": "past"}) is True


def test_choose_cli_stream_returns_live_only_by_default():
    items = [
        {"title": "Old", "status": "past"},
        {"title": "Now", "status": "live"},
    ]
    chosen = yt_cli.choose_cli_stream(items, allow_replay=False)
    assert chosen is not None
    assert chosen["title"] == "Now"


def test_choose_cli_stream_refuses_when_no_live():
    items = [{"title": "Old", "status": "past"}]
    assert yt_cli.choose_cli_stream(items, allow_replay=False) is None
    replay = yt_cli.choose_cli_stream(items, allow_replay=True)
    assert replay is not None
    assert replay["title"] == "Old"


@pytest.fixture
def cfg_files(tmp_path, monkeypatch):
    monkeypatch.setattr(yt_cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(yt_cli, "CONFIG_PATH", tmp_path / "config.json")
    return tmp_path


def test_channel_status_mismatch_is_reported_not_applied(monkeypatch, cfg_files):
    cfg = {
        "news": {
            "sources": [
                {"id": "news5", "name": "News5Everywhere", "channel_id": "UCx", "enabled": True}
            ]
        }
    }
    monkeypatch.setattr(
        yt_cli,
        "fetch_url",
        lambda url, timeout=15: "<html><title>TV5 News Philippines - YouTube</title></html>",
    )
    report = yt_cli.verify_channels(cfg)
    assert report[0]["status"] == "label-mismatch"
    assert report[0]["channel_title"] == "TV5 News Philippines"
    yt_cli.apply_channel_fixes(cfg, report)
    assert cfg["news"]["sources"][0]["channel_id"] == "UCx"


def test_channel_status_missing_id_is_not_playable():
    entry = {"id": "x", "name": "X", "channel_id": ""}
    assert yt_cli.channel_status(entry)["status"] == "missing-id"


def test_verify_flags_duplicate_bindings(monkeypatch, cfg_files):
    cfg = {
        "news": {
            "sources": [
                {"id": "a", "name": "A", "channel_id": "UCx"},
                {"id": "b", "name": "B", "channel_id": "UCx"},
            ]
        }
    }
    monkeypatch.setattr(
        yt_cli,
        "fetch_url",
        lambda url, timeout=15: "<html><title>A - YouTube</title></html>",
    )
    report = yt_cli.verify_channels(cfg)
    assert all(item["duplicate"] is True for item in report)


def test_resolve_channel_id_from_handle(monkeypatch):
    html = (
        '<html><head><title>TV5 News Philippines - YouTube</title>'
        '<link rel="canonical" href="https://www.youtube.com/channel/UCGEbMwiX774cseKvJqF9R2g">'
        "</head></html>"
    )
    monkeypatch.setattr(yt_cli, "fetch_url", lambda url, timeout=15: html)
    resolved = yt_cli.resolve_channel_id("@TV5NewsPH")
    assert resolved is not None
    assert resolved["id"] == "UCGEbMwiX774cseKvJqF9R2g"
    assert resolved["title"] == "TV5 News Philippines"


def test_apply_channel_fixes_binds_exact_name_only(monkeypatch, cfg_files):
    cfg = {
        "news": {
            "sources": [
                {"id": "news5", "name": "News5 Everywhere", "channel_id": "", "enabled": True},
                {"id": "other", "name": "Some Channel", "channel_id": "", "enabled": True},
            ]
        }
    }
    report = [
        {"id": "news5", "name": "News5 Everywhere", "status": "missing-id"},
        {"id": "other", "name": "Some Channel", "status": "missing-id"},
    ]

    def fake_resolve(ref, timeout=15):
        if ref == "News5 Everywhere":
            return {"id": "UCabc", "title": "News5 Everywhere"}
        return {"id": "UCdef", "title": "Totally Different"}

    monkeypatch.setattr(yt_cli, "resolve_channel_id", fake_resolve)
    counts = yt_cli.apply_channel_fixes(cfg, report)
    assert counts["bound"] == 1
    assert counts["skipped"] == 1
    assert cfg["news"]["sources"][0]["channel_id"] == "UCabc"
    assert cfg["news"]["sources"][1]["channel_id"] == ""


def test_apply_channel_fixes_disables_dead(monkeypatch, cfg_files):
    cfg = {"news": {"sources": [{"id": "dead", "name": "Dead", "channel_id": "UCx"}]}}
    report = [{"id": "dead", "name": "Dead", "status": "dead"}]
    counts = yt_cli.apply_channel_fixes(cfg, report)
    assert counts["disabled"] == 1
    assert cfg["news"]["sources"][0]["enabled"] is False


def test_resolve_channel_id_from_channel_url(monkeypatch):
    html = '<html><head><title>TV5 News Philippines - YouTube</title></head></html>'
    monkeypatch.setattr(yt_cli, "fetch_url", lambda url, timeout=15: html)
    resolved = yt_cli.resolve_channel_id(
        "https://www.youtube.com/channel/UCGEbMwiX774cseKvJqF9R2g"
    )
    assert resolved is not None
    assert resolved["id"] == "UCGEbMwiX774cseKvJqF9R2g"
