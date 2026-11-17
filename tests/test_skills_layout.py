"""Skills are shared by every agent: one copy in `.agents/skills/`, a link for Claude Code.

Codex, Antigravity and DeepSeek Harness read `.agents/skills/` themselves; Claude Code reads
`.claude/skills/`, so each skill has a relative link there (the layout `orca-ide skills install
--local` writes). A copy in `.claude/skills/` would drift from the shared one.

Ported from meridian@014f706d:tests/test_skills_layout.py; the skill set is astrolabe's, the
blob id is computed here, and the front matter is parsed strictly (pyyaml is not installed).
"""

import hashlib
import os
import re

import pytest
from test_paper_only import REPO_ROOT

SHARED = REPO_ROOT / ".agents" / "skills"
CLAUDE = REPO_ROOT / ".claude" / "skills"

# Copied unmodified from Orca 1.4.261 (git blob ids, as listed in UPSTREAM.md). A new Orca copy
# changes these on purpose, together with its UPSTREAM.md row and NOTICE.md.
UPSTREAM_SKILLS = {
    "orca-cli": "9f43957d5cb24ed0bd4c13c62516534ce969e5eb",
    "orchestration": "0e0d6f230e57f707207aa177350d2fe8d177212f",
}
# Skills written here (UPSTREAM.md, "own design").
OWN_DESIGN_SKILLS = {"lead", "learning-loop"}
SKILLS = set(UPSTREAM_SKILLS) | OWN_DESIGN_SKILLS


def git_blob_id(data: bytes) -> str:
    """The id git gives a file's bytes (`git hash-object`): SHA-1 of a blob header and the data."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


# --- strict front matter --------------------------------------------------------------------

KEY = re.compile(r"[a-z][a-z0-9_-]*")
# Characters that would change a plain YAML scalar's meaning at its start.
INDICATORS = tuple("-?:,[]{}#&*!|>'\"%@`")


class FrontMatterError(ValueError):
    """Front matter that a strict YAML reading would not give as flat string keys."""


def scalar(raw: str, where: str) -> str:
    """One YAML scalar on one line: single- or double-quoted, or plain and unambiguous."""
    if raw.startswith("'"):
        if len(raw) < 2 or not raw.endswith("'") or "'" in raw[1:-1].replace("''", ""):
            raise FrontMatterError(f"{where}: unbalanced single-quoted value")
        return raw[1:-1].replace("''", "'")
    if raw.startswith('"'):
        body = raw[1:-1]
        if len(raw) < 2 or not raw.endswith('"') or re.search(r'(?<!\\)(?:\\\\)*"', body):
            raise FrontMatterError(f"{where}: unbalanced double-quoted value")
        if re.search(r"\\(?![\\\"/nt])", body):
            raise FrontMatterError(f'{where}: an escape other than \\\\, \\", \\/, \\n or \\t')
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), body)
    if not raw or raw.startswith(INDICATORS):
        raise FrontMatterError(
            f"{where}: a plain value must not be empty or start with {raw[:1]!r}"
        )
    if ": " in raw or raw.endswith(":") or " #" in raw:
        raise FrontMatterError(f"{where}: a plain value holding ': ' or ' #' must be quoted")
    return raw


def front_matter(text: str) -> dict[str, str]:
    """The front matter between the first two `---` lines, as flat string keys and values."""
    lines = text.split("\n")
    if lines[0] != "---":
        raise FrontMatterError("the file does not start with a '---' line")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise FrontMatterError("no closing '---' line") from None
    found: dict[str, str] = {}
    body = lines[1:end]
    index = 0
    while index < len(body):
        line, where = body[index], f"line {index + 2}"
        index += 1
        if not line.strip() or line.startswith("#"):
            continue
        key, colon, rest = line.partition(":")
        if not colon or not KEY.fullmatch(key):
            raise FrontMatterError(f"{where}: not a 'key: value' line")
        if rest and not rest.startswith(" "):
            raise FrontMatterError(f"{where}: no space after the colon")
        if key in found:
            raise FrontMatterError(f"{where}: {key} is given twice")
        raw = rest.strip()
        if raw in BLOCK_INDICATORS:
            block = []
            while index < len(body) and (not body[index].strip() or body[index][:1] == " "):
                block.append(body[index])
                index += 1
            found[key] = block_scalar(raw, block, where)
        else:
            found[key] = scalar(raw, where)
    return found


# YAML block scalars: folded (>) or literal (|), with strip (-), clip (none) or keep (+).
BLOCK_INDICATORS = {">", ">-", ">+", "|", "|-", "|+"}


def block_scalar(indicator: str, block: list[str], where: str) -> str:
    """A block scalar's text: its lines less their common indentation, folded or kept."""
    filled = [line for line in block if line.strip()]
    if not filled:
        raise FrontMatterError(f"{where}: an empty block value")
    indent = len(filled[0]) - len(filled[0].lstrip(" "))
    if any(len(line) - len(line.lstrip(" ")) < indent for line in filled):
        raise FrontMatterError(f"{where}: a block line is less indented than the first")
    lines = [line[indent:] for line in block]
    while lines and not lines[-1].strip():
        lines.pop()
    if indicator.startswith("|"):
        text = "\n".join(lines)
    else:
        paragraphs = " ".join(line if line.strip() else "\n" for line in lines)
        text = re.sub(r" ?\n ?", "\n", paragraphs)
    return text if indicator.endswith("-") else text + "\n"


# --- the tree ---------------------------------------------------------------------------------


def test_shared_skills_holds_the_notice_and_the_four_skills():
    entries = {p.name for p in SHARED.iterdir()} if SHARED.is_dir() else set()
    assert entries == SKILLS | {"NOTICE.md"}, f"entries in .agents/skills: {sorted(entries)}"


@pytest.mark.parametrize("name", sorted(SKILLS))
def test_each_skill_has_strict_front_matter_that_names_it(name):
    path = SHARED / name / "SKILL.md"
    assert path.is_file(), f".agents/skills/{name}/SKILL.md is missing"
    head = front_matter(path.read_text(encoding="utf-8"))
    assert head.get("name") == name
    assert head.get("description", "").strip(), f"{name}: no description"


@pytest.mark.parametrize("name", sorted(UPSTREAM_SKILLS))
def test_upstream_skill_is_unmodified(name):
    files = sorted(p.name for p in (SHARED / name).iterdir())
    assert files == ["SKILL.md"]
    assert git_blob_id((SHARED / name / "SKILL.md").read_bytes()) == UPSTREAM_SKILLS[name]


def test_the_notice_names_the_orca_release():
    notice = (SHARED / "NOTICE.md").read_text(encoding="utf-8")
    assert "1.4.261" in notice and "MIT" in notice


@pytest.mark.parametrize("name", sorted(SKILLS))
def test_each_shared_skill_is_linked_for_claude_code(name):
    link = CLAUDE / name
    assert link.is_symlink(), f".claude/skills/{name} is not a link"
    assert os.readlink(link) == f"../../.agents/skills/{name}"
    assert link.resolve() == (SHARED / name).resolve()


def test_claude_skills_holds_only_links_to_shared_skills():
    assert CLAUDE.is_dir(), ".claude/skills is missing"
    for entry in CLAUDE.iterdir():
        assert entry.is_symlink() and entry.name in SKILLS, (
            f"unexpected .claude/skills/{entry.name}"
        )


# --- the parser refuses what a strict reading would not give --------------------------------


def test_git_blob_id_is_gits():
    # `printf 'hello\n' | git hash-object --stdin`
    assert git_blob_id(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "---\nname: lead\ndescription: Runs the loop.\n---\nbody",
            {"name": "lead", "description": "Runs the loop."},
        ),
        (
            "---\nname: lead\ndescription: 'Use when: landing'\n---\n",
            {"name": "lead", "description": "Use when: landing"},
        ),
        (
            '---\nname: lead\ndescription: "Use when: \\"x\\""\n---\n',
            {"name": "lead", "description": 'Use when: "x"'},
        ),
        (
            "---\nname: lead\ndescription: it''s fine\n---\n",
            {"name": "lead", "description": "it''s fine"},
        ),
        (
            "---\nname: lead\n# a comment\n\ndescription: a-b c:d\n---\n",
            {"name": "lead", "description": "a-b c:d"},
        ),
        (
            "---\nname: x\ndescription: >-\n  Use when: one,\n  two.\n\n  Next.\n---\n",
            {"name": "x", "description": "Use when: one, two.\nNext."},
        ),
        (
            "---\nname: x\ndescription: |\n  a: 1\n  b\n---\n",
            {"name": "x", "description": "a: 1\nb\n"},
        ),
    ],
)
def test_strict_front_matter_is_read(text, expected):
    assert front_matter(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "name: lead\n---\n",
        "---\nname: lead\n",
        "---\nname: lead\ndescription: Use when: landing\n---\n",
        "---\nname: lead\ndescription: ends with a colon:\n---\n",
        "---\nname: lead\ndescription: text # a comment\n---\n",
        "---\nname: lead\ndescription: 'unbalanced\n---\n",
        '---\nname: lead\ndescription: "bad \\q escape"\n---\n',
        "---\nname: lead\ndescription: [a, b]\n---\n",
        "---\nname: lead\ndescription: >\nnot indented\n---\n",
        "---\nname: lead\ndescription: >-\n    deeper\n  shallower\n---\n",
        "---\nname: lead\ndescription: >2\n  explicit indentation\n---\n",
        "---\nname: lead\nname: other\n---\n",
        "---\nname:lead\n---\n",
        "---\n  name: lead\n---\n",
    ],
)
def test_loose_front_matter_is_refused(text):
    with pytest.raises(FrontMatterError):
        front_matter(text)
