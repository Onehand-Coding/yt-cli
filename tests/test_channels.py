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
