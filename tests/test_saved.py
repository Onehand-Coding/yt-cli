"""Unit tests for the unified Saved catalog (no network)."""

import json
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
        "radio": {"presets": [{"slug": "jazz", "name": "Jazz", "url": "u1", "category": "Chill"}]},
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
    row = yt_cli.format_saved_row({"kind": "channel", "name": "News5", "enabled": False}, None)
    assert "○" in row
    assert "[channel]" in row
    live = yt_cli.format_saved_row({"kind": "channel", "name": "News5", "enabled": True}, None)
    assert "●" in live and "○" not in live
    radio = yt_cli.format_saved_row({"kind": "station", "name": "Star FM", "enabled": True}, None)
    assert "[radio]" in radio and "[station]" not in radio


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


def test_save_options_channel_context_prefers_channel():
    opts = yt_cli.save_options({"kind": "channel", "channel_id": "UCx"})
    assert opts[0][1] == "channel"


def test_save_options_search_context_offers_preset_first():
    opts = yt_cli.save_options({"kind": "video", "channel_id": "UCx"})
    assert opts[0][1] == "preset"


def test_action_tables_have_unique_codes():
    tables = [
        yt_cli.PLAY_ACTIONS_SEARCH,
        yt_cli.PLAY_ACTIONS_RADIO,
        yt_cli.PLAY_ACTIONS_NEWS,
        yt_cli.PLAY_ACTIONS_STATION,
    ]
    for table in tables:
        codes = [code for _, code in table]
        assert len(codes) == len(set(codes)), [label for label, _ in table]


def test_news_add_source_stores_channel_name(cfg_files):
    cfg = {"news": {"sources": []}, "radio": {"presets": []}, "stations": []}
    yt_cli.news_add_source(
        cfg,
        "News5",
        ["news5"],
        channel_id="UCx",
        channel_name="TV5 News Philippines",
    )
    assert cfg["news"]["sources"][0]["channel_name"] == "TV5 News Philippines"


def test_add_to_history_keeps_kind(cfg_files):
    yt_cli.add_to_history(
        {"title": "Ted Failon", "url": "u", "kind": "channel", "channel_id": "UCx"},
        10,
    )
    entry = json.loads(yt_cli.HISTORY_PATH.read_text())[0]
    assert entry["kind"] == "channel"
    assert entry["channel_id"] == "UCx"


def test_saved_rows_align_kind_tag_across_kinds_and_lengths():
    rows = [
        yt_cli.format_saved_row({"kind": "channel", "name": "News5", "enabled": True}),
        yt_cli.format_saved_row({"kind": "preset", "name": "Avenged Sevenfold", "enabled": True}),
        yt_cli.format_saved_row({"kind": "station", "name": "ニュース", "enabled": True}),
    ]

    def tag_column(line):
        plain = yt_cli.strip_ansi(line)
        return yt_cli.display_width(plain[: plain.index("[")])

    assert len({tag_column(row) for row in rows}) == 1


def test_number_gutter_does_not_shift_double_digit_rows():
    row = yt_cli.format_saved_row({"kind": "preset", "name": "Jazz", "enabled": True})

    def tag_column(line):
        plain = yt_cli.strip_ansi(line)
        return yt_cli.display_width(plain[: plain.index("[")])

    one = yt_cli._num_line(1, row, 2)
    ten = yt_cli._num_line(10, row, 2)
    assert tag_column(one) == tag_column(ten)


def test_parse_index_roundtrips_padded_number_gutter():
    for number, width in ((1, 1), (1, 2), (9, 2), (10, 2), (100, 3)):
        line = yt_cli._num_line(number, "Jazz", width)
        assert yt_cli._parse_index(line) == number - 1


def test_stream_pausable_rules():
    assert yt_cli.stream_pausable({"status": "live"}) is False
    assert yt_cli.stream_pausable({"status": "past"}) is True
    assert yt_cli.stream_pausable({"kind": "station"}) is False
    assert yt_cli.stream_pausable({"title": "video"}) is True


def test_with_pause_inserts_after_replay(monkeypatch):
    monkeypatch.setattr(yt_cli, "player_paused", lambda: False)
    actions = [("replay", "r"), ("save", "k"), ("stop", "q")]
    assert yt_cli.with_pause(actions) == [
        ("replay", "r"),
        ("pause", "u"),
        ("save", "k"),
        ("stop", "q"),
    ]


def test_with_pause_uses_play_when_paused(monkeypatch):
    monkeypatch.setattr(yt_cli, "player_paused", lambda: True)
    assert ("play", "u") in yt_cli.with_pause([("stop", "q")])


def test_with_pause_hides_without_a_player(monkeypatch):
    monkeypatch.setattr(yt_cli, "player_paused", lambda: None)
    assert yt_cli.with_pause([("stop", "q")]) == [("stop", "q")]


def test_with_pause_respects_not_pausable(monkeypatch):
    monkeypatch.setattr(yt_cli, "player_paused", lambda: False)
    assert yt_cli.with_pause([("stop", "q")], pausable=False) == [("stop", "q")]


def test_toggle_pause_cycles_mpv_pause(monkeypatch):
    calls = []

    def fake_ipc(cmd, timeout=2):
        calls.append(cmd)
        return False

    monkeypatch.setattr(yt_cli, "ipc_rpc", fake_ipc)
    assert yt_cli.toggle_pause() is True
    assert calls == [["cycle", "pause"]]


def test_playing_tables_expose_stop_detach_exit():
    tables = [
        yt_cli.PLAY_ACTIONS_SEARCH,
        yt_cli.PLAY_ACTIONS_RADIO,
        yt_cli.PLAY_ACTIONS_NEWS,
        yt_cli.PLAY_ACTIONS_STATION,
    ]
    for table in tables:
        labels = [label for label, _ in table]
        assert "stop" in labels
        assert "detach" in labels
        assert "exit" in labels
        assert "stop player" not in labels
        assert "detach & exit" not in labels


def test_playing_tables_expose_save_label():
    tables = [
        yt_cli.PLAY_ACTIONS_SEARCH,
        yt_cli.PLAY_ACTIONS_RADIO,
        yt_cli.PLAY_ACTIONS_NEWS,
        yt_cli.PLAY_ACTIONS_STATION,
    ]
    for table in tables:
        labels = [label for label, _ in table]
        assert "save" in labels
        assert "keep (save)" not in labels


def test_saving_a_preset_keeps_channels_saved_earlier(cfg_files, monkeypatch):
    disk = {
        "radio": {"presets": []},
        "news": {
            "sources": [
                {
                    "id": "news5",
                    "name": "News5",
                    "channel_id": "UCx",
                    "channel_name": "TV5 News Philippines",
                    "enabled": True,
                }
            ]
        },
        "stations": [],
    }
    yt_cli.save_config(disk)
    stale = {"radio": {"presets": []}, "news": {"sources": []}, "stations": []}

    class UI:
        def __init__(self):
            self.calls = 0

        def action(self, header, actions):
            self.calls += 1
            return "yes" if self.calls == 1 else "no"

        def info(self, lines, wait=True):
            return None

        def prompt(self, label):
            return ""

    monkeypatch.setattr(yt_cli.time, "sleep", lambda seconds: None)
    yt_cli.prompt_save_search_result(
        stale, {"title": "E2E Preset", "url": "https://example.invalid/v"}, UI()
    )

    saved = yt_cli.load_config()
    assert any(source["id"] == "news5" for source in saved["news"]["sources"])
    assert any(preset["slug"] == "e2e-preset" for preset in saved["radio"]["presets"])


def test_playing_tables_have_back_and_no_other():
    tables = [
        yt_cli.PLAY_ACTIONS_SEARCH,
        yt_cli.PLAY_ACTIONS_RADIO,
        yt_cli.PLAY_ACTIONS_NEWS,
        yt_cli.PLAY_ACTIONS_STATION,
    ]
    for table in tables:
        labels = [label for label, _ in table]
        assert "go to" in labels
        assert "←" not in labels
        assert not any(label.startswith("other ") for label in labels)
