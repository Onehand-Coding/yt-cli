"""Unit tests for the Stations mode (radio-browser.info) in yt_cli.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yt_cli  # noqa: E402  (import after sys.path setup)


@pytest.fixture
def cfg_files(tmp_path, monkeypatch):
    """Isolate every config/state/history path and reset module globals."""
    monkeypatch.setattr(yt_cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(yt_cli, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(yt_cli, "HISTORY_PATH", tmp_path / "history.json")
    monkeypatch.setattr(yt_cli, "STATE_PATH", tmp_path / "state.json")
    yt_cli._STATION_SEARCH_CACHE.clear()
    yt_cli._rb_host_cache[0] = None
    yt_cli._rb_host_discovery[0] = None
    yt_cli.PLAYER_PROC[0] = None
    yt_cli.IPC_DONE[0] = None
    yt_cli.NOW_PLAYING[0] = None
    yt_cli.ATTACHED[0] = False
    return tmp_path


def _raw(name, uuid="", url=None, clicks=0, votes=0, tags="", **extra):
    raw = {
        "name": name,
        "url": url or f"http://{name}",
        "stationuuid": uuid,
        "clickcount": clicks,
        "votes": votes,
        "tags": tags,
    }
    raw.update(extra)
    return raw


# ---------- normalization / merging ----------

def test_normalize_prefers_resolved_url_and_cleans_fields():
    station = yt_cli._normalize_station({
        "name": " Rock &amp; Roll ",
        "url": "http://unresolved.example/stream",
        "url_resolved": "https://resolved.example/stream",
        "stationuuid": "u1",
        "countrycode": "ph",
        "tags": " Rock, rock ,, Pop ",
        "codec": "MP3",
        "bitrate": "128",
        "clickcount": "10",
        "votes": "3",
    })
    assert station is not None
    assert station["name"] == "Rock & Roll"
    assert station["url"] == "https://resolved.example/stream"
    assert station["countrycode"] == "PH"
    assert station["tags"] == ["rock", "pop"]
    assert station["bitrate"] == 128
    assert station["clickcount"] == 10
    assert station["votes"] == 3
    assert station["enabled"] is True


@pytest.mark.parametrize("raw", [
    None,
    {},
    {"name": "no url"},
    {"name": "bad scheme", "url": "ftp://example/stream"},
    {"name": "   ", "url": "http://example/stream"},
    "not a dict",
])
def test_normalize_rejects_unusable_records(raw):
    assert yt_cli._normalize_station(raw) is None


def test_merge_dedupes_by_uuid_then_ranks_by_clicks_and_votes():
    merged = yt_cli._merge_station_results(
        [_raw("alpha", "u1", clicks=1),
         _raw("bravo", "u2", clicks=50)],
        [_raw("alpha duplicate", "u1", clicks=99),
         _raw("charlie", "u3", clicks=50, votes=5)],
    )
    assert [s["name"] for s in merged] == ["charlie", "bravo", "alpha"]


def test_merge_uses_name_and_url_when_uuid_missing():
    merged = yt_cli._merge_station_results(
        [_raw("same", url="http://same.example")],
        [_raw("same", url="http://same.example")],
    )
    assert len(merged) == 1


# ---------- search ----------

def test_search_merges_name_and_tag_results(cfg_files, monkeypatch):
    calls = []

    def fake_get(path, params=None, timeout=8):
        calls.append(dict(params or {}))
        if "name" in (params or {}):
            return [_raw("Jazz FM", "1", url="http://jazz.example",
                         clicks=5, tags="jazz", countrycode="US")]
        return [_raw("Jazz Radio", "2", url="http://jazz2.example",
                     clicks=9, tags="jazz")]

    monkeypatch.setattr(yt_cli, "_rb_get", fake_get)
    results = yt_cli.search_stations("jazz")

    assert [s["name"] for s in results] == ["Jazz Radio", "Jazz FM"]
    assert calls[0]["name"] == "jazz"
    assert calls[1]["tag"] == "jazz"


def test_search_skips_tag_fallback_when_name_results_are_plenty(cfg_files, monkeypatch):
    calls = []

    def fake_get(path, params=None, timeout=8):
        calls.append(dict(params or {}))
        return [_raw(f"Jazz {i}", f"u{i}", clicks=i) for i in range(10)]

    monkeypatch.setattr(yt_cli, "_rb_get", fake_get)
    results = yt_cli.search_stations("jazz")

    assert len(results) == 10
    assert len(calls) == 1


def test_search_caches_repeat_queries(cfg_files, monkeypatch):
    calls = []
    monkeypatch.setattr(yt_cli, "_rb_get",
                        lambda *a, **k: calls.append(1) or [])

    yt_cli.search_stations("zzz")
    first = len(calls)
    yt_cli.search_stations("zzz")

    assert first == len(calls) == 2      # name + tag query, cached afterwards


def test_empty_query_does_not_hit_network(cfg_files, monkeypatch):
    monkeypatch.setattr(yt_cli, "_rb_get",
                        lambda *a, **k: pytest.fail("network call"))
    assert yt_cli.search_stations("   ") == []


# ---------- playback command ----------

def test_build_station_cmd_is_audio_only():
    cmd = yt_cli.build_station_cmd({"name": "Jazz FM",
                                    "url": "https://stream.example/jazz"})
    assert cmd[0] == "mpv"
    assert "--vid=no" in cmd
    assert "--video=no" in cmd
    assert cmd[-1] == "https://stream.example/jazz"
    assert not any("--ytdl-format" in part for part in cmd)


def test_station_play_records_history_and_reports_click(cfg_files, monkeypatch):
    cfg = yt_cli.load_config()
    launched, history, clicks = [], [], []
    monkeypatch.setattr(yt_cli, "launch_player",
                        lambda cmd: launched.append(cmd) or None)
    monkeypatch.setattr(yt_cli, "_ipc_watch", lambda *a, **k: None)
    monkeypatch.setattr(yt_cli, "add_to_history",
                        lambda item, cap: history.append(item))
    monkeypatch.setattr(yt_cli, "report_station_click",
                        lambda uuid: clicks.append(uuid))

    station = {"name": "Jazz FM", "url": "http://jazz.example",
               "stationuuid": "u9", "countrycode": "US"}
    yt_cli._station_play(cfg, station)

    assert launched and launched[0][-1] == "http://jazz.example"
    assert history[0]["kind"] == "station"
    assert history[0]["title"] == "Jazz FM"
    assert yt_cli.NOW_PLAYING[0]["kind"] == "station"
    assert clicks == ["u9"]


# ---------- saved stations ----------

def test_station_add_dedupes_by_url_and_updates_in_place(cfg_files):
    cfg = yt_cli.load_config()
    assert yt_cli.station_add(cfg, {"name": "Radio One",
                                    "url": "http://one.example"}) == "radio-one"
    assert yt_cli.station_add(cfg, {"name": "Radio Two",
                                    "url": "http://two.example"}) == "radio-two"

    again = yt_cli.station_add(cfg, {"name": "Radio One HD",
                                     "url": "http://one.example",
                                     "bitrate": 192})

    assert again == "radio-one"
    stations = yt_cli.load_config()["stations"]
    assert len(stations) == 2
    assert stations[0]["name"] == "Radio One HD"
    assert stations[0]["bitrate"] == 192
    assert stations[0]["slug"] == "radio-one"


def test_station_toggle_hides_from_playable_list(cfg_files):
    cfg = yt_cli.load_config()
    slug = yt_cli.station_add(cfg, {"name": "Alpha", "url": "http://alpha.example"})

    yt_cli.station_toggle(cfg, slug, False)

    assert yt_cli.load_stations_cfg(cfg) == []
    assert [s["slug"] for s in yt_cli.get_all_stations(cfg)] == [slug]

    yt_cli.station_toggle(cfg, slug, True)
    assert [s["slug"] for s in yt_cli.load_stations_cfg(cfg)] == [slug]


def test_station_delete_removes_only_target(cfg_files):
    cfg = yt_cli.load_config()
    yt_cli.station_add(cfg, {"name": "Alpha", "url": "http://alpha.example"})
    beta = yt_cli.station_add(cfg, {"name": "Beta", "url": "http://beta.example"})

    yt_cli.station_delete(cfg, beta)

    assert [s["slug"] for s in yt_cli.get_all_stations(cfg)] == ["alpha"]


def test_station_slug_is_unique_and_never_empty():
    assert yt_cli.station_slug("Jazz FM", set()) == "jazz-fm"
    assert yt_cli.station_slug("Jazz FM", {"jazz-fm"}) == "jazz-fm-1"
    assert yt_cli.station_slug("", set()) == "station"


def test_resolve_station_matches_slug_then_substring():
    stations = [{"slug": "jazz-fm", "name": "Jazz FM"},
                {"slug": "lofi", "name": "Lo-Fi Beats"}]
    lofi = yt_cli.resolve_station("lofi", stations)
    jazz = yt_cli.resolve_station("jazz", stations)
    assert lofi is not None and lofi["slug"] == "lofi"
    assert jazz is not None and jazz["slug"] == "jazz-fm"
    assert yt_cli.resolve_station("nope", stations) is None


def test_resolve_music_target_prefers_youtube_preset(cfg_files):
    cfg = yt_cli.load_config()
    cfg["radio"]["presets"] = [{"slug": "shared", "name": "Shared",
                                "url": "http://preset.example",
                                "category": "Test", "enabled": True}]
    yt_cli.save_config(cfg)
    cfg = yt_cli.load_config()
    yt_cli.station_add(cfg, {"name": "Shared Station",
                             "url": "http://station.example"})

    preset = yt_cli.resolve_music_target(cfg, "shared")
    station = yt_cli.resolve_music_target(cfg, "shared-station")

    assert preset is not None and preset[0] == "radio"
    assert preset[1]["url"] == "http://preset.example"
    assert station is not None and station[0] == "station"
    assert station[1]["url"] == "http://station.example"


def test_station_for_url_finds_saved_stream(cfg_files):
    cfg = yt_cli.load_config()
    yt_cli.station_add(cfg, {"name": "Jazz FM", "url": "http://jazz.example"})

    found = yt_cli.station_for_url("http://jazz.example")
    assert found is not None and found["name"] == "Jazz FM"
    assert yt_cli.station_for_url("http://other.example") is None


def test_load_config_coerces_stations_to_list(cfg_files):
    yt_cli.CONFIG_PATH.write_text(json.dumps({"stations": "oops"}))
    assert yt_cli.load_config()["stations"] == []

    yt_cli.CONFIG_PATH.write_text(json.dumps(
        {"stations": [{"name": "Alpha", "url": "http://alpha.example"}]}))
    assert len(yt_cli.load_config()["stations"]) == 1
