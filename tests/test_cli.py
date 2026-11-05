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


def test_help_exits_zero_without_extra_tools():
    # No skipif on purpose: --help must work before check_dependencies runs,
    # and the CI test job has no mpv or yt-dlp installed.
    r = run("--help")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "usage" in out
    assert "--channels" in out


def test_short_help_alias():
    r = run("-h")
    assert r.returncode == 0
    assert "usage" in (r.stdout + r.stderr).lower()


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
def test_radio_help_exits_zero():
    r = run("--radio", "--help")
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
def test_channels_list_exits_zero():
    r = run("--channels", "--list")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "channels" in out


def test_removed_news_flag_explains_replacement():
    r = run("--news", "--help")
    assert r.returncode == 2
    out = (r.stdout + r.stderr).lower()
    assert "removed" in out and "--channels" in out


def test_removed_stations_flag_explains_replacement():
    r = run("--stations", "--help")
    assert r.returncode == 2
    out = (r.stdout + r.stderr).lower()
    assert "removed" in out and "--radio" in out


@pytest.mark.skipif(
    not shutil.which("mpv") or not shutil.which("yt-dlp"),
    reason="mpv and yt-dlp required for check_dependencies",
)
def test_preset_radio_seed_is_rejected():
    r = run("--presets", "--radio")
    assert r.returncode == 2
    out = (r.stdout + r.stderr).lower()
    assert "--mix" in out
