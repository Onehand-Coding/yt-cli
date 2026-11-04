"""Unit tests for the unified Saved catalog (no network)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yt_cli  # noqa: E402  (import after sys.path setup)


@pytest.fixture
def cfg_files(tmp_path, monkeypatch):
    monkeypatch.setattr(yt_cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(yt_cli, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(yt_cli, "HISTORY_PATH", tmp_path / "history.json")
    return tmp_path


def test_saved_items_maps_all_three_kinds(cfg_files):
    cfg = {
        "radio": {
            "presets": [
                {"slug": "jazz", "name": "Jazz", "url": "u1", "category": "Chill"}
            ]
        },
        "stations": [{"slug": "star", "name": "Star FM", "url": "u2", "countrycode": "PH"}],
        "news": {"sources": [{"id": "news5", "name": "News5Everywhere", "channel_id": "UCx"}]},
    }
    items = yt_cli.saved_items(cfg)
    kinds = {item["kind"] for item in items}
    assert kinds == {"preset", "station", "channel"}
    channel = next(item for item in items if item["kind"] == "channel")
    assert channel["channel_id"] == "UCx"
    assert channel["raw"]["id"] == "news5"


def test_saved_items_includes_disabled_but_marks_them(cfg_files):
    cfg = {
        "radio": {"presets": [{"slug": "x", "name": "X", "url": "u", "enabled": False}]},
        "stations": [],
        "news": {"sources": []},
    }
    assert yt_cli.saved_items(cfg)[0]["enabled"] is False


def test_format_saved_row_marks_disabled_and_kind():
    row = yt_cli.format_saved_row(
        {"kind": "channel", "name": "News5", "enabled": False}, None
    )
    assert "○" in row
    assert "[channel]" in row
    live = yt_cli.format_saved_row({"kind": "channel", "name": "News5", "enabled": True}, None)
    assert "●" in live and "○" not in live


def test_saved_mode_mapping():
    assert yt_cli.SAVED_MODE == {"preset": "radio", "station": "stations", "channel": "news"}


def test_pick_saved_maps_selected_row_to_item():
    class UI:
        def pick_query(self, items, prompt, header, height):
            return ("", 1)

    items = [
        {
            "kind": "channel",
            "name": "News5",
            "channel_id": "UCx",
            "enabled": True,
            "group": "",
            "raw": {},
        }
    ]
    picked = yt_cli.pick_saved(items, UI())
    assert isinstance(picked, dict)
    assert picked["name"] == "News5"


def test_pick_saved_typing_opens_search():
    class UI:
        def pick_query(self, items, prompt, header, height):
            return ("jazz", None)

    assert yt_cli.pick_saved([], UI()) == ("search", "jazz")


def test_pick_saved_search_row():
    class UI:
        def pick_query(self, items, prompt, header, height):
            return ("", 0)

    assert yt_cli.pick_saved([], UI()) == ("search", "")
