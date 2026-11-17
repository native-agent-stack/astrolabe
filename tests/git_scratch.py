"""A scratch git repository sealed from the caller's git environment (`environment`), and the
repository's own root (`REPO_ROOT`).

Ported from meridian@014f706d:tests/git_scratch.py; changed: REPO_ROOT is defined here, where
meridian's tests imported it from tests/test_paper_only.py.
"""

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def environment(inherited: Mapping[str, str]) -> dict[str, str]:
    """The environment git runs in: `inherited` without its `GIT_*`, plus the seal."""
    env = {key: value for key, value in inherited.items() if not key.startswith("GIT_")}
    return env | {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "XDG_CONFIG_HOME": os.devnull,
    }


def git(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in `directory` with the caller's git environment sealed."""
    return subprocess.run(
        ["git", *IDENTITY, *args],
        cwd=directory,
        env=environment(os.environ),
        capture_output=True,
        text=True,
    )


def init(parent: Path, name: str = "r") -> Path:
    """Create `parent/name` as a repository with one branch, `main`."""
    repo = parent / name
    result = git(parent, "init", "-q", "-b", "main", str(repo))
    assert result.returncode == 0, result.stdout + result.stderr
    return repo
