"""Union merges of a synthetic upstream map, and the helpers that check them.

`.gitattributes` merges `upstream-map.toml` with git's union driver. These tests show, on a
scratch repository holding this repository's `.gitattributes`, what the gate then rejects and
what it lets through.

Ported from meridian@014f706d:tests/test_upstream_map_merge.py; the new files are under tools/.
"""

import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

import pytest
from git_scratch import git, init
from test_paper_only import REPO_ROOT
from test_upstream_gate import gate_problems, read_map_text
from upstream_map_fixture import AREAS, DEPENDENCIES, LOCK, PROJECT, ROWS, quote, render

FIRST, SECOND, THIRD, *_, LAST = AREAS
NEW = {"upstream": "NautilusTrader", "how": "build-on", "feature": "new", "why": "a new file"}


def with_why(key: str, why: str, areas: dict[str, Any] = AREAS) -> dict[str, Any]:
    return {**areas, key: {**areas[key], "why": why}}


def commit(repo: Path, areas: dict[str, Any], dependencies: dict[str, str], **options: Any):
    """Commit the map, with a file for each key of `areas` and none for the other `AREAS` keys."""
    (repo / "upstream-map.toml").write_bytes(render(areas, dependencies, **options).encode())
    for key in {**AREAS, **areas}:
        path = repo / key
        if key in areas:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        else:
            path.unlink()
    assert git(repo, "add", "-A").returncode == 0
    result = git(repo, "commit", "-q", "-m", "change")
    assert result.returncode == 0, result.stdout + result.stderr


def merged_problems(
    tmp_path: Path,
    base: dict[str, Any],
    left: dict[str, Any],
    right: dict[str, Any],
    dependencies: tuple[dict[str, str], dict[str, str], dict[str, str]] = (
        DEPENDENCIES,
        DEPENDENCIES,
        DEPENDENCIES,
    ),
    rows: dict[str, tuple[str, str]] = ROWS,
    **options: Any,
) -> tuple[str, list[str]]:
    """Branch `left` and `right` from `base`, merge left into right; the text and the problems."""
    repo = init(tmp_path)
    shutil.copyfile(REPO_ROOT / ".gitattributes", repo / ".gitattributes")
    commit(repo, base, dependencies[0], **options)
    for name, areas, aliases in (
        ("left", left, dependencies[1]),
        ("right", right, dependencies[2]),
    ):
        assert git(repo, "checkout", "-q", "-b", name, "main").returncode == 0
        commit(repo, areas, aliases, **options)
    result = git(repo, "merge", "-q", "--no-edit", "left")
    assert result.returncode == 0, result.stdout + result.stderr
    text = read_map_text(repo / "upstream-map.toml")
    files = sorted(
        str(path.relative_to(repo))
        for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    )
    return text, gate_problems(text, files, rows, PROJECT, LOCK)


def entry_lines(text: str, key: str) -> list[str]:
    """The lines of `text` that start with `key`, quoted, and a space."""
    return [line for line in text.split("\n") if line.startswith(f"{quote(key)} ")]


def entry(key: str, areas: dict[str, Any]) -> str:
    """The one line `render` writes for `key` in `areas`."""
    [line] = entry_lines(render(areas, DEPENDENCIES), key)
    return line


def assert_doubled(text: str, problems: list[str], versions: dict[str, list[str]]) -> None:
    """Each key of `versions` is reported once at one of its lines, which are exactly `versions`."""
    lines = text.split("\n")
    pattern = re.compile(r"upstream-map\.toml:(\d+): (\S+) is already given in \[areas\]")
    matches = [pattern.fullmatch(problem) for problem in problems]
    reported = {match[2]: lines[int(match[1]) - 1] for match in matches if match}
    assert len(reported) == len(problems), problems
    assert set(reported) == set(versions), problems
    for key, line in reported.items():
        assert line in versions[key]
        assert sorted(entry_lines(text, key)) == sorted(versions[key])


def test_two_branches_appending_different_entries_merge_cleanly(tmp_path: Path):
    left, right = {**AREAS, "tools/one.py": NEW}, {**AREAS, "tools/two.py": NEW}
    text, problems = merged_problems(tmp_path, AREAS, left, right)
    assert problems == []
    assert tomllib.loads(text)["areas"] == {**left, **right}


def test_two_branches_rewriting_one_entry_double_its_key(tmp_path: Path):
    left, right = with_why(FIRST, "left"), with_why(FIRST, "right")
    text, problems = merged_problems(tmp_path, AREAS, left, right)
    assert_doubled(text, problems, {FIRST: [entry(FIRST, left), entry(FIRST, right)]})


def test_an_edit_beside_an_append_doubles_the_key(tmp_path: Path):
    left, right = with_why(LAST, "edited"), {**AREAS, "tools/one.py": NEW}
    text, problems = merged_problems(tmp_path, AREAS, left, right)
    assert_doubled(text, problems, {LAST: [entry(LAST, AREAS), entry(LAST, left)]})


def test_two_adjacent_edits_double_both_keys(tmp_path: Path):
    left, right = with_why(FIRST, "left"), with_why(SECOND, "right")
    text, problems = merged_problems(tmp_path, AREAS, left, right)
    assert_doubled(
        text,
        problems,
        {
            FIRST: [entry(FIRST, AREAS), entry(FIRST, left)],
            SECOND: [entry(SECOND, AREAS), entry(SECOND, right)],
        },
    )


def test_an_identical_edit_inside_a_conflicting_hunk_is_kept_twice(tmp_path: Path):
    # The middle entry, changed identically on both sides, is kept twice.
    left = with_why(THIRD, "left", with_why(SECOND, "same", with_why(FIRST, "left")))
    right = with_why(THIRD, "right", with_why(SECOND, "same", with_why(FIRST, "right")))
    text, problems = merged_problems(tmp_path, AREAS, left, right)
    assert_doubled(
        text,
        problems,
        {
            FIRST: [entry(FIRST, left), entry(FIRST, right)],
            SECOND: [entry(SECOND, left), entry(SECOND, right)],
            THIRD: [entry(THIRD, left), entry(THIRD, right)],
        },
    )
    assert entry(SECOND, left) == entry(SECOND, right)


# Below, the identical change is a hunk of its own, and the left side also changes another line.


def test_the_same_edit_on_both_sides_merges_to_one_line(tmp_path: Path):
    edited = with_why(FIRST, "the same edit")
    left = {**edited, "tools/one.py": NEW}
    text, problems = merged_problems(tmp_path, AREAS, left, edited)
    assert entry_lines(text, FIRST) == [entry(FIRST, edited)]
    assert problems == []
    assert tomllib.loads(text)["areas"] == left


def test_the_same_new_entry_on_both_sides_merges_to_one_line(tmp_path: Path):
    added = {**AREAS, "tools/one.py": NEW}
    left = with_why(FIRST, "left", added)
    text, problems = merged_problems(tmp_path, AREAS, left, added)
    assert entry_lines(text, "tools/one.py") == [entry("tools/one.py", added)]
    assert problems == []
    assert tomllib.loads(text)["areas"] == left


def test_delete_against_edit_keeps_the_edit_which_names_a_deleted_file(tmp_path: Path):
    left = {key: value for key, value in AREAS.items() if key != FIRST}
    text, problems = merged_problems(tmp_path, AREAS, left, with_why(FIRST, "edited"))
    assert tomllib.loads(text)["areas"][FIRST]["why"] == "edited"
    assert problems == [f"{FIRST}: matches no code file"]


def test_an_alias_deleted_against_an_edit_survives_unreported(tmp_path: Path):
    # An exact register row wins over the alias (test_exact_package_row_takes_priority_over_alias).
    deleted = {"pyright": DEPENDENCIES["pyright"]}
    edited = {**DEPENDENCIES, "nautilus-trader": "NautilusTrader (edited)"}
    rows = {**ROWS, "nautilus-trader": ROWS["NautilusTrader"]}
    text, problems = merged_problems(
        tmp_path, AREAS, AREAS, AREAS, (DEPENDENCIES, deleted, edited), rows
    )
    assert tomllib.loads(text)["dependencies"] == edited
    assert problems == []


def test_a_multiline_explanation_merges_into_one_string_and_fails_the_line_rule(tmp_path: Path):
    base = with_why(FIRST, "first\nmiddle\nlast")
    left, right = with_why(FIRST, "first\nleft\nlast"), with_why(FIRST, "first\nright\nlast")
    text, problems = merged_problems(tmp_path, base, left, right, multiline=FIRST)
    # Both edits end inside the one string.
    assert tomllib.loads(text)["areas"] in (
        with_why(FIRST, "first\nright\nleft\nlast"),
        with_why(FIRST, "first\nleft\nright\nlast"),
    )
    assert problems == [
        f"upstream-map.toml:{number}: not a complete TOML document" for number in (3, 4, 5, 6)
    ]


def doubled_text(left: dict[str, Any], right: dict[str, Any]) -> str:
    """`left`'s map with `right`'s line for FIRST added after it."""
    lines = render(left, DEPENDENCIES).split("\n")
    lines.insert(lines.index(entry(FIRST, left)) + 1, entry(FIRST, right))
    return "\n".join(lines)


DOUBLED = f"upstream-map.toml:4: {FIRST} is already given in [areas]"


@pytest.mark.parametrize(
    ("right_why", "problems"),
    [
        ("right", [f"upstream-map.toml:2: {FIRST} is already given in [areas]"]),
        ("right", [f"upstream-map.toml:4: {SECOND} is already given in [areas]"]),
        ("right", [DOUBLED, DOUBLED]),
        ("right", [f"upstream-map.toml:4: {FIRST} is doubled"]),
        ("left", [DOUBLED]),
        ("right", []),
    ],
)
def test_assert_doubled_refuses_a_wrong_report(right_why: str, problems: list[str]):
    left, right = with_why(FIRST, "left"), with_why(FIRST, "right")
    versions = {FIRST: [entry(FIRST, left), entry(FIRST, right)]}
    assert_doubled(doubled_text(left, right), [DOUBLED], versions)
    with pytest.raises(AssertionError):
        assert_doubled(doubled_text(left, with_why(FIRST, right_why)), problems, versions)


def test_assert_doubled_counts_a_line_kept_twice():
    left = with_why(FIRST, "left")
    assert_doubled(doubled_text(left, left), [DOUBLED], {FIRST: [entry(FIRST, left)] * 2})
    with pytest.raises(AssertionError):
        assert_doubled(doubled_text(left, left), [DOUBLED], {FIRST: [entry(FIRST, left)]})
