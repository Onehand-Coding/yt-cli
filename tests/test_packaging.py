"""Packaging metadata tests: version sync, wheel contents, entry points, deps."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yt_cli  # noqa: E402  (import after sys.path setup)

tomllib = pytest.importorskip("tomllib", reason="tomllib is Python 3.11+")
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
YT_CLI = ROOT / "yt-cli"


def test_version_matches_pyproject():
    assert yt_cli.__version__ == PYPROJECT["project"]["version"]


def test_wheel_ships_the_module():
    wheel = PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["only-include"] == ["yt_cli.py"]
    assert "bypass-selection" not in wheel


def test_console_script_points_at_callable():
    assert PYPROJECT["project"]["scripts"]["yt-cli"] == "yt_cli:cli"
    assert callable(yt_cli.cli)


def test_version_flag_prints_version():
    r = subprocess.run([str(YT_CLI), "--version"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"yt-cli {yt_cli.__version__}"


def test_version_flag_skips_config_migration(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["yt-cli", "--version"])

    def boom():
        raise AssertionError("--version must not touch config")

    monkeypatch.setattr(yt_cli, "migrate_configs", boom)
    with pytest.raises(SystemExit) as exc:
        yt_cli.main()
    assert exc.value.code == 0
    assert yt_cli.__version__ in capsys.readouterr().out


def test_missing_dependency_reports_install_hints(monkeypatch, capsys):
    monkeypatch.setattr(yt_cli.shutil, "which", lambda _tool: None)
    with pytest.raises(SystemExit) as exc:
        yt_cli.check_dependencies()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "not found on PATH: mpv, yt-dlp" in err
    assert "apt install mpv yt-dlp" in err
    assert "brew install mpv yt-dlp" in err
    assert "fzf is optional" in err
