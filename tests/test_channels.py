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
