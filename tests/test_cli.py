"""Smoke tests for the monolithic yt-cli script (subprocess — no import side effects)."""

import shutil
import subprocess
from pathlib import Path

import pytest

YT_CLI = Path(__file__).resolve().parents[1] / "yt-cli"


def run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(YT_CLI), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_script_is_executable():
    assert YT_CLI.is_file()
    assert YT_CLI.stat().st_mode & 0o111


def test_shebang_is_python3():
    first = YT_CLI.read_text().splitlines()[0]
    assert first == "#!/usr/bin/env python3"


@pytest.mark.skipif(
    not shutil.which("mpv") or not shutil.which("yt-dlp"),
    reason="mpv and yt-dlp required for check_dependencies",
)
def test_presets_help_exits_zero():
    r = run("--presets", "--help")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "presets" in out and "usage" in out


@pytest.mark.skipif(
    not shutil.which("mpv") or not shutil.which("yt-dlp"),
    reason="mpv and yt-dlp required for check_dependencies",
)
def test_channels_help_exits_zero():
    r = run("--channels", "--help")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "channels" in out and "usage" in out


@pytest.mark.skipif(
    not shutil.which("mpv") or not shutil.which("yt-dlp"),
    reason="mpv and yt-dlp required for check_dependencies",
)
def test_news_alias_help_exits_zero():
    r = run("--news", "--help")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "channels" in out and "news" in out


@pytest.mark.skipif(not shutil.which("mpv"), reason="mpv required for check_dependencies")
def test_radio_help_exits_zero():
    r = run("--radio", "--help")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "radio" in out and "radio-browser" in out


@pytest.mark.skipif(not shutil.which("mpv"), reason="mpv required for check_dependencies")
def test_stations_alias_help_exits_zero():
    r = run("--stations", "--help")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "radio" in out and "radio-browser" in out


@pytest.mark.skipif(not shutil.which("mpv"), reason="mpv required for check_dependencies")
def test_radio_list_exits_zero():
    r = run("--radio", "--list")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "radio" in out


@pytest.mark.skipif(not shutil.which("mpv"), reason="mpv required for check_dependencies")
def test_stations_alias_list_exits_zero():
    r = run("--stations", "--list")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "radio" in out


@pytest.mark.skipif(
    not shutil.which("mpv") or not shutil.which("yt-dlp"),
    reason="mpv and yt-dlp required for check_dependencies",
)
def test_channels_list_exits_zero():
    r = run("--channels", "--list")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "channels" in out
