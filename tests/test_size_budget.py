"""
The size budget: the build fails if the agent instructions or the runbook grow past their line
limits, or the prose, config, workflows or skills git would track past their totals. The limits
and what counts as prose or config are in ``size-budget.toml``; raising one takes a pull request
that says why. Every file git would track has to be of a kind the limits file names, so a new
kind of file cannot slip past the totals unseen.

Like the other scans this is a tripwire, not a lock: an agent can edit the limits file too. It
counts what git would track (``git ls-files --cached --others --exclude-standard``), new files
included, as the paper-only scan does.

Run as a script, it prints the measure the limits are set from (``size-budget.toml``, header).

Ported from meridian@014f706d:tests/test_size_budget.py; the ADR, index and character-limit
machinery is dropped, and files under ``upstream/`` are never counted.
"""

import fnmatch
import json
import tomllib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from test_paper_only import REPO_ROOT
from test_public_tree import tracked_files

LIMITS_FILE = "size-budget.toml"
INSTRUCTION_FILES = {"agents.md", "claude.md"}
KINDS = ("not_counted", "prose", "config")  # the order in which a file is matched
ALWAYS_LOADED = ("AGENTS.md", "CLAUDE.md")
# Counted in files, not lines: their limit is the count plus 10 percent, rounded up to a whole.
COUNTS = ("workflows", "skills")


def load_limits(root: Path = REPO_ROOT) -> dict[str, Any]:
    return tomllib.loads((root / LIMITS_FILE).read_text(encoding="utf-8"))


def lines(name: str, root: Path = REPO_ROOT) -> int:
    return len((root / name).read_text(encoding="utf-8").splitlines())


def matches(name: str, patterns: list[str]) -> bool:
    path, base = name.lower(), PurePosixPath(name).name.lower()
    return any(
        fnmatch.fnmatchcase(path, p.lower()) or fnmatch.fnmatchcase(base, p.lower())
        for p in patterns
    )


def kind_of(name: str, kinds: dict[str, list[str]]) -> str | None:
    return next((kind for kind in KINDS if matches(name, kinds[kind])), None)


def regular_files(names: list[str], root: Path) -> list[str]:
    """Files, not links: a link in .claude/skills/ shows again what is counted at its target."""
    return [n for n in names if (root / n).is_file() and not (root / n).is_symlink()]


def measure(names: list[str], limits: dict[str, Any], root: Path = REPO_ROOT) -> dict[str, int]:
    files = regular_files(names, root)
    kinds = {n: kind_of(n, limits["kinds"]) for n in files}
    return {
        "agent_instruction_lines": sum(
            lines(n, root) for n in files if PurePosixPath(n).name.lower() in INSTRUCTION_FILES
        ),
        "prose_lines": sum(lines(n, root) for n in files if kinds[n] == "prose"),
        "config_lines": sum(lines(n, root) for n in files if kinds[n] == "config"),
        "workflows": sum(
            1 for n in files if n.startswith(".github/workflows/") and n.endswith((".yml", ".yaml"))
        ),
        "skills": sum(
            1 for n in files if PurePosixPath(n).name == "SKILL.md" and kinds[n] != "not_counted"
        ),
    }


def unclassified(names: list[str], limits: dict[str, Any], root: Path = REPO_ROOT) -> list[str]:
    return [n for n in regular_files(names, root) if kind_of(n, limits["kinds"]) is None]


def over_budget(names: list[str], limits: dict[str, Any], root: Path = REPO_ROOT) -> list[str]:
    """One line per limit that is exceeded; empty when the tree is within its budget."""
    found = []
    for name, limit in limits["lines"].items():
        if name not in names:
            found.append(f"{name}: missing; the budget names it")
        elif (n := lines(name, root)) > limit:
            found.append(f"{name}: {n} lines, limit {limit}; cut text")
    measured = measure(names, limits, root)
    for key, limit in limits["totals"].items():
        if measured[key] > limit:
            found.append(f"{key}: {measured[key]}, limit {limit}")
    for name in unclassified(names, limits, root):
        found.append(
            f"{name}: not a kind {LIMITS_FILE} names; add it to prose, config or not_counted"
        )
    return found


def proposed_limit(measured: int) -> int:
    """How a line limit is set: the measure plus 10 percent, rounded up to the next 10."""
    return -(-measured * 11 // 100) * 10  # integer ceiling of measured * 1.1 / 10, no float


def report(root: Path = REPO_ROOT) -> dict[str, Any]:
    """The measure of the tree git would track, and the limit each number would get."""
    names, limits = tracked_files(), load_limits(root)
    files = {name: lines(name, root) for name in limits["lines"] if name in names}
    totals = measure(names, limits, root)
    return {
        "lines": {name: [n, proposed_limit(n)] for name, n in files.items()},
        "totals": {
            key: [n, -(-n * 11 // 10) if key in COUNTS else proposed_limit(n)]
            for key, n in totals.items()
        },
        "unclassified": unclassified(names, limits, root),
    }


# --- the tree as it is ------------------------------------------------------------------------


def test_the_tree_is_within_its_size_budget():
    names = tracked_files()
    assert LIMITS_FILE in names, "the limits file must be tracked"
    assert over_budget(names, load_limits()) == [], (
        "over the size budget: cut or move text rather than reflow it; raising a limit takes a"
        f" pull request that says why ({LIMITS_FILE}, its header)"
    )


def test_every_file_git_would_track_is_of_a_named_kind():
    assert unclassified(tracked_files(), load_limits()) == []


def test_the_always_loaded_files_keep_their_line_limits():
    limits = load_limits()
    for name in ALWAYS_LOADED:
        assert name in limits["lines"], f"{name} loads into every context: it keeps a line limit"
    assert limits["lines"]["CLAUDE.md"] == 1, "CLAUDE.md is only the @AGENTS.md import"
    assert set(limits["totals"]) == {
        "agent_instruction_lines",
        "prose_lines",
        "config_lines",
        "workflows",
        "skills",
    }


def test_the_measure_sees_the_files_it_should():
    names = tracked_files()
    measured = measure(names, load_limits())
    assert measured["prose_lines"] >= lines("AGENTS.md") + lines("UPSTREAM.md")
    assert measured["config_lines"] >= lines("pyproject.toml") + lines(LIMITS_FILE)
    assert measured["agent_instruction_lines"] >= lines("AGENTS.md") + lines("CLAUDE.md")
    assert measured["workflows"] >= 1


def test_upstream_files_are_never_counted():
    kinds = load_limits()["kinds"]
    for name in ("upstream/x/NOTICE.md", "upstream/x/SKILL.md", "upstream/x/a.toml"):
        assert kind_of(name, kinds) == "not_counted", name


# --- the checks catch what they should ---------------------------------------------------------


def planted(tmp_path: Path, files: Mapping[str, int | str]) -> list[str]:
    """Each file is that many lines of "x", or the text given."""
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "x\n" * content if isinstance(content, int) else content
        path.write_text(text, encoding="utf-8")
    return sorted(files)


SMALL = {
    "lines": {"AGENTS.md": 10, "CLAUDE.md": 1, "docs/RUNBOOK.md": 20},
    "totals": {
        "agent_instruction_lines": 11,
        "prose_lines": 41,
        "config_lines": 10,
        "workflows": 1,
        "skills": 1,
    },
    "kinds": load_limits()["kinds"],
}
WITHIN = {
    "AGENTS.md": 10,
    "CLAUDE.md": 1,
    "docs/RUNBOOK.md": 20,
    "astrolabe/x.py": 500,  # code is not counted
    "upstream/orca/skills/x/SKILL.md": 500,  # upstream copies are not counted
    "uv.lock": 500,
    "pyproject.toml": 6,
    ".github/workflows/ci.yml": 4,
    ".agents/skills/one/SKILL.md": 2,
}


def test_a_tree_within_the_budget_passes(tmp_path):
    assert over_budget(planted(tmp_path, WITHIN), SMALL, tmp_path) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"AGENTS.md": 11}, "AGENTS.md: 11 lines, limit 10"),
        ({"CLAUDE.md": 2}, "CLAUDE.md: 2 lines, limit 1"),
        ({"docs/RUNBOOK.md": 21}, "docs/RUNBOOK.md: 21 lines, limit 20"),
        ({"notes.md": 9}, "prose_lines: 42, limit 41"),
        ({"NOTES.MD": 9}, "prose_lines: 42, limit 41"),
        ({"tools/AGENTS.md": 1}, "agent_instruction_lines: 12, limit 11"),
        ({".claude/settings.json": 1}, "config_lines: 11, limit 10"),
        ({"orca.yaml": 1}, "config_lines: 11, limit 10"),
        ({".pre-commit-config.yaml": 1}, "config_lines: 11, limit 10"),
        ({".github/workflows/more.yml": 0}, "workflows: 2, limit 1"),
        ({".agents/skills/two/SKILL.md": 1}, "skills: 2, limit 1"),
        ({"tools/x.env.sample": 1}, "tools/x.env.sample: not a kind size-budget.toml names"),
    ],
)
def test_each_limit_catches_a_tree_over_it(tmp_path, change, expected):
    names = planted(tmp_path, WITHIN | change)
    assert any(f.startswith(expected) for f in over_budget(names, SMALL, tmp_path)), expected


@pytest.mark.parametrize("gone", ["AGENTS.md", "docs/RUNBOOK.md"])
def test_a_file_the_budget_names_cannot_simply_disappear(tmp_path, gone):
    names = planted(tmp_path, {k: v for k, v in WITHIN.items() if k != gone})
    assert f"{gone}: missing; the budget names it" in over_budget(names, SMALL, tmp_path)


def test_a_link_is_not_counted_twice(tmp_path):
    names = planted(tmp_path, WITHIN)
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "one").symlink_to("../../.agents/skills/one")
    before = measure(names, SMALL, tmp_path)
    assert measure([*names, ".claude/skills/one"], SMALL, tmp_path) == before


@pytest.mark.parametrize(("measured", "limit"), [(0, 0), (1, 10), (41, 50), (100, 110), (91, 110)])
def test_a_limit_is_the_measure_plus_ten_percent_rounded_up_to_ten(measured, limit):
    assert proposed_limit(measured) == limit


if __name__ == "__main__":
    print(json.dumps(report(), indent=2))
