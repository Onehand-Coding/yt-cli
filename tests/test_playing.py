"""Unit tests for the shared playing screen and exit prompt (no network, no player)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yt_cli  # noqa: E402  (import after sys.path setup)


class FakeUI:
    def __init__(self, codes):
        self.codes = list(codes)
        self.headers = []

    def playing_action(self, header_lines, actions):
        self.headers.append(header_lines)
        return self.codes.pop(0)

    def action(self, header_lines, actions):
        return self.codes.pop(0)


def test_playing_back_does_not_stop():
    calls = []
    result = yt_cli.playing_menu(
        FakeUI(["b"]),
        lambda: [],
        lambda: [("back", "b"), ("stop", "q")],
        lambda: False,
        lambda action: None,
        lambda: calls.append("stop"),
        lambda: calls.append("goodbye"),
    )
    assert result == "back"
    assert calls == []


def test_playing_stop_calls_stop():
    calls = []
    result = yt_cli.playing_menu(
        FakeUI(["q"]),
        lambda: [],
        lambda: [("back", "b"), ("stop", "q")],
        lambda: False,
        lambda action: None,
        lambda: calls.append("stop"),
        lambda: calls.append("goodbye"),
    )
    assert result == "stop"
    assert calls == ["stop"]


def test_playing_pause_toggles(monkeypatch):
    paused = []
    monkeypatch.setattr(yt_cli, "player_paused", lambda: False)
    monkeypatch.setattr(yt_cli, "toggle_pause", lambda: paused.append(True) or True)
    result = yt_cli.playing_menu(
        FakeUI(["u", "b"]),
        lambda: [],
        lambda: [("back", "b"), ("pause", "u"), ("stop", "q")],
        lambda: True,
        lambda action: None,
        lambda: None,
        lambda: None,
    )
    assert result == "back"
    assert paused == [True]


def test_playing_delegates_context_actions():
    seen = []
    result = yt_cli.playing_menu(
        FakeUI(["f"]),
        lambda: [],
        lambda: [("back", "b"), ("refresh", "f"), ("stop", "q")],
        lambda: False,
        lambda action: seen.append(action) or "refresh",
        lambda: None,
        lambda: None,
    )
    assert result == "refresh"
    assert seen == ["f"]


def test_playing_detach_leaves_player_alone():
    calls = []
    with pytest.raises(SystemExit):
        yt_cli.playing_menu(
            FakeUI(["d"]),
            lambda: [],
            lambda: [("back", "b"), ("detach", "d"), ("stop", "q")],
            lambda: False,
            lambda action: None,
            lambda: calls.append("stop"),
            lambda: calls.append("goodbye"),
        )
    assert calls == []


def test_playing_exit_goodbyes():
    calls = []
    with pytest.raises(SystemExit):
        yt_cli.playing_menu(
            FakeUI(["x"]),
            lambda: [],
            lambda: [("back", "b"), ("exit", "x"), ("stop", "q")],
            lambda: False,
            lambda action: None,
            lambda: calls.append("stop"),
            lambda: calls.append("goodbye"),
        )
    assert calls == ["goodbye"]


def test_exit_prompt_without_player_goodbyes(monkeypatch):
    calls = []
    monkeypatch.setattr(yt_cli, "player_active", lambda: False)
    assert yt_cli.exit_prompt(FakeUI([]), lambda: calls.append("goodbye")) is True
    assert calls == ["goodbye"]


def test_exit_prompt_detach_keeps_player(monkeypatch):
    calls = []
    monkeypatch.setattr(yt_cli, "player_active", lambda: True)
    assert yt_cli.exit_prompt(FakeUI(["d"]), lambda: calls.append("goodbye")) is True
    assert calls == []


def test_exit_prompt_stop_goodbyes(monkeypatch):
    calls = []
    monkeypatch.setattr(yt_cli, "player_active", lambda: True)
    assert yt_cli.exit_prompt(FakeUI(["s"]), lambda: calls.append("goodbye")) is True
    assert calls == ["goodbye"]


def test_exit_prompt_cancel_stays(monkeypatch):
    calls = []
    monkeypatch.setattr(yt_cli, "player_active", lambda: True)
    assert yt_cli.exit_prompt(FakeUI(["q"]), lambda: calls.append("goodbye")) is False
    assert calls == []


def test_source_id_for_channel():
    cfg = {"news": {"sources": [{"id": "news5", "name": "News5", "channel_id": "UCx"}]}}
    assert yt_cli.source_id_for_channel(cfg, "UCx") == "news5"
    assert yt_cli.source_id_for_channel(cfg, "UCz") is None


def test_mode_loop_reports_home(monkeypatch):
    def fake_mode(*args, **kwargs):
        raise yt_cli.GoTo("home")

    monkeypatch.setattr(yt_cli, "_run_mode", fake_mode)
    assert yt_cli._run_mode_loop(None, None, "Saved", None, None, None) is True


def test_mode_loop_reports_normal_return(monkeypatch):
    monkeypatch.setattr(yt_cli, "_run_mode", lambda *args, **kwargs: None)
    assert yt_cli._run_mode_loop(None, None, "Saved", None, None, None) is False


def test_now_playing_saved_channel_hides_save(monkeypatch):
    class UI:
        def __init__(self):
            self.codes = ["b"]
            self.actions = []

        def playing_action(self, header_lines, actions):
            self.actions.append(actions)
            return self.codes.pop(0)

    ui = UI()
    monkeypatch.setattr(yt_cli, "player_active", lambda: False)
    monkeypatch.setattr(
        yt_cli,
        "NOW_PLAYING",
        [
            {
                "title": "News5 live",
                "kind": "channel",
                "source": "News5",
                "status": "live",
                "pausable": False,
                "channel_id": "UCx",
                "source_id": "news5",
                "save_item": {"kind": "channel", "channel_id": "UCx"},
            }
        ],
    )
    yt_cli.now_playing_screen({"news": {"sources": []}}, ui)
    labels = [label for label, _ in ui.actions[0]]
    assert "←" in labels
    assert "channel" in labels
    assert "save" not in labels
    assert "stop" in labels
    assert "detach" in labels
    assert "exit" in labels


def test_playing_tables_start_at_back_and_end_at_exit():
    tables = [
        yt_cli.PLAY_ACTIONS_SEARCH,
        yt_cli.PLAY_ACTIONS_RADIO,
        yt_cli.PLAY_ACTIONS_NEWS,
        yt_cli.PLAY_ACTIONS_STATION,
    ]
    for table in tables:
        labels = [label for label, _ in table]
        assert labels[0] == "←"
        assert labels[-3:] == ["stop", "detach", "exit"]

    search = [label for label, _ in yt_cli.PLAY_ACTIONS_SEARCH]
    assert search[1:5] == ["go to…", "history", "new search", "replay"]


def test_pick_stream_refresh_row_maps_to_retry():
    class UI:
        def __init__(self, idx):
            self.idx = idx

        def pick(self, items, prompt, header, height):
            return self.idx

    streams = [{"title": "One", "status": "live", "viewers_text": ""}]
    assert yt_cli.pick_stream(streams, "News5", UI(0)) == "retry"
    assert yt_cli.pick_stream(streams, "News5", UI(1)) == streams[0]


def test_now_playing_unsaved_channel_offers_save(monkeypatch):
    class UI:
        def __init__(self):
            self.codes = ["b"]
            self.actions = []

        def playing_action(self, header_lines, actions):
            self.actions.append(actions)
            return self.codes.pop(0)

    ui = UI()
    monkeypatch.setattr(yt_cli, "player_active", lambda: False)
    monkeypatch.setattr(
        yt_cli,
        "NOW_PLAYING",
        [
            {
                "title": "News5 live",
                "kind": "channel",
                "source": "News5",
                "status": "live",
                "pausable": False,
                "channel_id": "UCx",
                "source_id": "",
                "save_item": {"kind": "channel", "channel_id": "UCx"},
            }
        ],
    )
    yt_cli.now_playing_screen({"news": {"sources": []}}, ui)
    labels = [label for label, _ in ui.actions[0]]
    assert "save" in labels
