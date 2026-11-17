"""Every git hook in `.pre-commit-config.yaml` is local: prek downloads no hook repository.

A hook repository is code fetched from the network and run on every commit, outside the lock
and the release cooldown. Local hooks run the tools the lock or the machine already pins.
pyyaml is not installed, so the file is read line by line, and only the shape it uses is
accepted; `uv run prek validate-config` checks it against prek's own schema.

New for astrolabe.
"""

import re

import pytest
from test_paper_only import REPO_ROOT

CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
REPO_LINE = re.compile(r"^\s*-\s*repo:\s*(.*?)\s*$")
HOOK_ID = re.compile(r"^\s*-\s*id:\s*(.*?)\s*$")
FIELD = re.compile(r"^\s+([a-z_]+):\s*(.*?)\s*$")
EXPECTED = {
    "ruff-check": "uv run ruff check",
    "ruff-format": "uv run ruff format --check",
    "gitleaks": "gitleaks git --pre-commit --staged --redact --no-banner --config .gitleaks.toml",
    "zizmor": "uv run zizmor --offline .github/",
}


def hooks(text: str) -> tuple[list[str], dict[str, dict[str, str]]]:
    """The repositories named, and each hook's fields, from the file's own simple shape."""
    repos: list[str] = []
    found: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if match := REPO_LINE.match(line):
            repos.append(match[1])
            current = None
        elif match := HOOK_ID.match(line):
            assert match[1] not in found, f"hook {match[1]} is given twice"
            current = found[match[1]] = {}
        elif (match := FIELD.match(line)) and current is not None:
            current[match[1]] = match[2]
    return repos, found


def problems(text: str) -> list[str]:
    repos, found = hooks(text)
    result = [f"repo {repo!r} is not local" for repo in repos if repo != "local"]
    if not repos:
        result.append("no repository is named")
    result += [
        f"{key} is given: only a downloaded hook has a {key}"
        for key in ("rev",)
        if re.search(rf"^\s*{key}:", text, re.M)
    ]
    result += [
        f"hook {name}: language {fields.get('language')!r}, not system"
        for name, fields in found.items()
        if fields.get("language") != "system"
    ]
    return result


def test_every_hook_is_local():
    assert problems(CONFIG.read_text(encoding="utf-8")) == []


def test_the_hooks_are_ruff_format_gitleaks_and_zizmor():
    _, found = hooks(CONFIG.read_text(encoding="utf-8"))
    assert {name: fields["entry"] for name, fields in found.items()} == EXPECTED
    assert found["zizmor"]["files"] == r"^\.github/"
    assert found["gitleaks"]["pass_filenames"] == "false"
    assert found["zizmor"]["pass_filenames"] == "false"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("- repo: local", "- repo: https://github.com/astral-sh/ruff-pre-commit\n    rev: v0.17.4"),
        ("- repo: local", "- repo: https://github.com/gitleaks/gitleaks"),
        ("language: system", "language: python"),
    ],
)
def test_a_downloaded_hook_fails(old, new):
    text = CONFIG.read_text(encoding="utf-8")
    assert old in text
    assert problems(text.replace(old, new, 1))


def test_an_added_remote_repository_fails():
    text = CONFIG.read_text(encoding="utf-8")
    added = (
        text
        + "  - repo: https://github.com/example/hooks\n    rev: v1\n    hooks:\n      - id: x\n"
    )
    assert problems(added)
