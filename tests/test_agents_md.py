"""The owner's boundaries in `AGENTS.md` hold the words pinned here and no others, under the
pinned section headings, in a file every context loads and so kept short.

Each boundary is compared with runs of white space folded, so a rewrap passes and a dropped,
softened or added word fails. A boundary changes only on purpose, with this file changed in the
same commit.

Ported from meridian@014f706d:tests/test_agents_md.py; the worker-rules coupling is dropped,
the boundaries are astrolabe's, and the headings and the line limit are new.
"""

import pytest
from test_paper_only import REPO_ROOT

AGENTS = REPO_ROOT / "AGENTS.md"
BOUNDARIES = "## Boundaries (the owner's; not yours to change)"
HEADINGS = (
    BOUNDARIES,
    "## Compounding (the core practice)",
    "## The lead orchestrates",
    "## A worker delivers one task",
)
MAX_LINES = 45

# Each boundary of AGENTS.md, word for word.
OWNER = (
    "Paper only: never create, enable or test a route to a real-money account. For IBKR: the IB "
    "Gateway's paper port on the loopback address only, never a live port or a Live Trading "
    "login.",
    # The owner's own line, from the owner's standing instruction file.
    "On real-money accounts: drafts only; the owner submits.",
    "Only the engine places orders, in a run the owner starts or in a service the owner enabled. "
    "No agent places, changes or cancels an order or types the confirmation. No agent starts an "
    "IBKR lane run in any mode, and no agent connects to the Gateway's API port.",
    "Never read, print, copy or move a key or credential value.",
    "`risk/` and `*.contract.json` are the owner's; `upstream/` is verbatim. Read, never edit.",
    "Public repository: no account id, order history or tuned value (those go to "
    "`astrolabe-evidence`).",
    "Ask the owner before adding a paid service or an account.",
    "No automated account rotation or failover: on a rate limit, slow down.",
)


def section(text: str, heading: str) -> list[str]:
    """The lines under `heading` up to the next heading."""
    _, found, rest = text.partition(heading + "\n")
    assert found, f"no section {heading!r}"
    lines = rest.splitlines()
    end = next((i for i, line in enumerate(lines) if line.startswith("#")), len(lines))
    return lines[:end]


def bullets(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        if line.startswith("- "):
            items.append(line[2:])
        elif line.startswith("  ") and items:
            items[-1] += " " + line
        elif line.strip():  # a line outside any bullet is an item of its own, and mismatches
            items.append(line)
    return [" ".join(item.split()) for item in items]


def problems(agents: str) -> list[str]:
    found: list[str] = []
    headings = [line for line in agents.splitlines() if line.startswith("## ")]
    missing = [heading for heading in HEADINGS if heading not in headings]
    if missing:
        return [f"AGENTS.md lacks the sections {missing}"]
    if [h for h in headings if h in HEADINGS] != list(HEADINGS):
        found.append(f"AGENTS.md sections are out of order: {headings}")
    if (count := len(agents.splitlines())) > MAX_LINES:
        found.append(f"AGENTS.md is {count} lines, limit {MAX_LINES}; every context loads it")
    agents_bullets = bullets(section(agents, BOUNDARIES))
    if agents_bullets != list(OWNER):
        found.append(f"AGENTS.md boundaries differ from the pinned words: {agents_bullets}")
    return found


def read() -> str:
    return AGENTS.read_text(encoding="utf-8")


def test_the_boundaries_are_the_pinned_words():
    assert problems(read()) == []


def test_the_owners_drafts_only_line_follows_paper_only():
    assert bullets(section(read(), BOUNDARIES))[:2] == list(OWNER[:2])


def planted_removals() -> list[int]:
    """The line number of each non-blank line of the boundary section."""
    lines = read().splitlines()
    start = lines.index(BOUNDARIES) + 1
    return [
        start + offset + 1
        for offset, line in enumerate(section("\n".join(lines) + "\n", BOUNDARIES))
        if line.strip()
    ]


@pytest.mark.parametrize("number", planted_removals())
def test_removing_any_boundary_line_fails(number: int):
    lines = read().splitlines(keepends=True)
    del lines[number - 1]
    assert problems("".join(lines))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("drafts only; the owner submits", "drafts first; the owner reviews"),
        ("Read, never edit.", "Read, and edit with care."),
        ("never a live port", "rarely a live port"),
        ("No agent starts an\n  IBKR lane run in any mode", "No agent starts an\n  IBKR lane run"),
        ("slow down.", "slow down, or switch accounts."),
    ],
)
def test_softening_a_boundary_fails(old: str, new: str):
    agents = read()
    assert old in agents
    assert problems(agents.replace(old, new))


def test_a_rewrapped_boundary_still_passes():
    agents = read()
    old = "- Ask the owner before adding a paid service or an account.\n"
    assert old in agents
    assert (
        problems(
            agents.replace(old, "- Ask the owner before adding\n  a paid service or an account.\n")
        )
        == []
    )


@pytest.mark.parametrize("heading", HEADINGS)
def test_each_section_heading_is_required(heading: str):
    agents = read()
    assert problems(agents.replace(heading + "\n", "## Renamed\n"))


def test_a_long_agents_md_fails():
    agents = read()
    padding = "\n" * (MAX_LINES + 1 - len(agents.splitlines()))
    assert any("limit 45" in problem for problem in problems(agents + padding))
