"""The video toggle must close mpv's window, not only disable its video track.

mpv keeps a window alive while `force-window=yes`, which is a common global
mpv.conf setting. The channel player is launched without option overrides, so
its window belongs to the config, and `video=no` alone never closes it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yt_cli  # noqa: E402


def recording_rpc(monkeypatch):
    sent = []

    def fake_rpc(cmd, timeout=2):
        sent.append(list(cmd))
        return None

    monkeypatch.setattr(yt_cli, "ipc_rpc", fake_rpc)
    return sent


def test_toggle_off_clears_force_window(monkeypatch):
    sent = recording_rpc(monkeypatch)
    assert yt_cli.mpv_ipc_set_vid(False) is True
    assert sent == [["set", "vid", "no"], ["set", "force-window", "no"]]


def test_toggle_on_turns_video_back_on(monkeypatch):
    sent = recording_rpc(monkeypatch)
    assert yt_cli.mpv_ipc_set_vid(True) is True
    assert sent == [["set", "vid", "auto"]]


def test_toggle_retries_and_reports_failure(monkeypatch):
    calls = []

    def failing_rpc(cmd, timeout=2):
        calls.append(list(cmd))
        raise OSError("socket missing")

    monkeypatch.setattr(yt_cli, "ipc_rpc", failing_rpc)
    monkeypatch.setattr(yt_cli.time, "sleep", lambda _seconds: None)
    assert yt_cli.mpv_ipc_set_vid(False) is False
    assert len(calls) == 3  # first command fails, so one call per attempt


def fake_popen(monkeypatch):
    captured: list[list[str]] = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured.append(list(cmd))

    monkeypatch.setattr(yt_cli.subprocess, "Popen", FakePopen)
    return captured


def test_channel_player_matches_the_video_players(monkeypatch):
    captured = fake_popen(monkeypatch)
    monkeypatch.setattr(yt_cli, "load_state", lambda: {"radio": {"no_video": False}})
    yt_cli.launch_news_player("https://www.youtube.com/watch?v=x", "mpv", "720")
    cmd = captured[0]
    assert "--force-window=no" in cmd
    assert "--vid=auto" in cmd and "--video=auto" in cmd


def test_channel_player_honors_audio_only(monkeypatch):
    captured = fake_popen(monkeypatch)
    monkeypatch.setattr(yt_cli, "load_state", lambda: {"radio": {"no_video": True}})
    yt_cli.launch_news_player("https://www.youtube.com/watch?v=x", "mpv", "720")
    cmd = captured[0]
    assert "--vid=no" in cmd and "--video=no" in cmd
    assert "--vid=auto" not in cmd
