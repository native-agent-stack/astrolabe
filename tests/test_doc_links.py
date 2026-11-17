"""Every relative link in the Markdown git would track resolves to a file the tree has.

A link in a record is evidence: if it 404s on GitHub the reader cannot check the claim it
supports. The base is the file's own folder. This test reads every tracked Markdown file, drops
fenced and inline code (a naming example is not a link), and checks each relative destination.

Ported from meridian@014f706d:tests/test_doc_links.py; only upstream/ is left out.
"""

import re
from pathlib import Path

import pytest
from test_paper_only import REPO_ROOT
from test_public_tree import tracked_files

MARKDOWN = (".md", ".markdown")
NOT_OURS = ("upstream/",)
FENCE = re.compile(r"^\s{0,3}(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")
EXTERNAL = ("#", "//", "http://", "https://", "mailto:")


def markdown_files() -> list[str]:
    """Tracked Markdown we read: never the byte-for-byte upstream copies."""
    return [
        name
        for name in tracked_files()
        if name.endswith(MARKDOWN)
        and not name.startswith(NOT_OURS)
        and (REPO_ROOT / name).is_file()
    ]


def prose_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) outside fenced blocks, with inline code spans removed."""
    lines = []
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            lines.append((number, INLINE_CODE.sub("", line)))
    return lines


def relative_destinations(text: str) -> list[tuple[int, str]]:
    """(line number, destination) for each relative link in the text."""
    found = []
    for number, line in prose_lines(text):
        for match in LINK.finditer(line):
            destination = match.group(1).strip()
            if destination and not destination.startswith(EXTERNAL):
                found.append((number, destination))
    return found


def shown(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def broken_links(names: list[str], root: Path = REPO_ROOT) -> list[str]:
    broken = []
    for name in names:
        source = root / name
        for number, destination in relative_destinations(source.read_text(encoding="utf-8")):
            target = (source.parent / destination.partition("#")[0]).resolve()
            if not target.exists():
                broken.append(
                    f"{name}:{number}: {destination} resolves to {shown(target)}, "
                    "which does not exist"
                )
    return broken


def test_every_relative_link_in_the_tracked_markdown_resolves():
    names = markdown_files()
    assert names, "no Markdown found; the check would pass without looking"
    broken = broken_links(names)
    assert broken == [], "relative links that lead nowhere:\n" + "\n".join(broken)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("see [a](a.md)", [(1, "a.md")]),
        ("![img](pic.png) and [b](sub/b.md#part)", [(1, "pic.png"), (1, "sub/b.md#part")]),
        ("[web](https://example.org) [anchor](#top) [mail](mailto:x)", []),
        ("`[code](x.md)` is a name", []),
        ("```\n[fenced](x.md)\n```\n[after](y.md)", [(4, "y.md")]),
    ],
)
def test_relative_destinations_skip_code_and_external_links(text, expected):
    assert relative_destinations(text) == expected


def test_a_planted_broken_link_is_reported(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("[ok](../b.md) [gone](missing.md)\n")
    (tmp_path / "b.md").write_text("x\n")
    found = broken_links(["docs/a.md"], tmp_path)
    assert len(found) == 1 and found[0].startswith("docs/a.md:1: missing.md resolves to")
