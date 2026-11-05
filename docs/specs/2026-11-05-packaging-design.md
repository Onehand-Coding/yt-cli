# Packaged install for sharing yt-cli

Date: 2026-11-05
Status: approved (user chose the packaged-install approach)
Branch: `feat/packaging`

## Context

Installing `yt-cli` today means cloning the repo and running
`ln -s "$PWD/yt-cli" ~/.local/bin/yt-cli`. The repo is the install, which is
convenient for the author but useless for anyone else. `pyproject.toml` cannot
help: `[tool.hatch.build.targets.wheel] bypass-selection = true` ships an empty
wheel, and there is no `[project.scripts]` entry point, so `uv tool install`
would produce a command that does not exist.

## Goals

- One command for a new user: `uv tool install git+https://github.com/Onehand-Coding/yt-cli`.
- A real `yt-cli` console script, with the KeyboardInterrupt/traceback guards
  owned by the module instead of the launcher wrapper.
- Keep `argv[0]` dispatch intact so `music/<preset>` and `music/<station>`
  symlinks still resolve to the right preset.
- An upgrade path (`uv tool upgrade yt-cli`), a PyPI-ready artifact, and
  `yt-cli --version` so users can report what they run.
- First run on a fresh machine names the packages to install, not just the
  missing binaries.

## Non-goals

- A `curl | sh` installer or a GitHub Release tarball.
- Homebrew, AUR, or distro packaging.
- Bundling `mpv`, `yt-dlp`, or `fzf`.
- Changing config, state, or cache locations.

## Decisions

**Package the module, not the wrapper.** The wheel ships the top-level module
`yt_cli.py` (`only-include = ["yt_cli.py"]`) and declares
`[project.scripts] yt-cli = "yt_cli:cli"`. `cli()` holds the guards the wrapper
used to carry, and `yt_cli.py`'s `__main__` block calls it, so there is one
implementation. The repo `yt-cli` wrapper stays as a dev launcher and calls
`cli()` too.

**Git URL now, PyPI when a tag is cut.** `uv tool install git+...` needs no
account and works as soon as the wheel builds. A tag-triggered release workflow
publishes to PyPI with trusted publishing (OIDC, no stored token), which
requires a one-time trusted-publisher setup on PyPI by the owner.

**`argv[0]` dispatch is unchanged.** uv's bin entry on Unix is a symlink to the
console script inside the tool venv, so a `music/<slug>` symlink still presents
`<slug>` as `argv[0]`, which is what `main()` dispatches on. The
save-preset-symlink code keeps resolving `argv[0]` to the real console script,
which lives at a path that is stable across `uv tool upgrade`.

**The distribution is `yt-cli-player`.** PyPI refuses `yt-cli` as "too similar"
to the existing `ytcli` project, so the publishable name is `yt-cli-player`
while the command, module, repo, and config directory all stay `yt-cli`.

**Version is written twice.** `__version__` in `yt_cli.py` and `version` in
`pyproject.toml`; a test asserts they match. A single source would need a build
hook or `hatch-vcs`, which is more machinery than a one-file tool earns.

**Dependency hints are static.** `check_dependencies` prints apt/dnf/pacman/brew
lines for the missing binaries. `fzf` stays optional (curses fallback).

## Risks

- `uv tool install` refuses to overwrite an existing executable, so the README
  must tell people to remove a hand-made `~/.local/bin/yt-cli` symlink first
  (`--force` also works).
- PyPI publishing fails until the trusted publisher exists; the workflow only
  runs on tags, so CI on `main` is unaffected.

## Verification

- `uv build` produces a wheel containing `yt_cli.py` and the entry point.
- `uv run --no-project --with dist/*.whl yt-cli --version` prints the version.
- `ruff`, `pyright`, and `pytest` are clean; `uv.lock` stays consistent.
- A `music/<slug>` symlink still dispatches after installing from the wheel.
