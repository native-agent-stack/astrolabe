"""The worker rules the lead appends to every task (`.agents/skills/lead/worker-rules.md`) name
every lane and probe entry point on the tree, so a new one cannot be left unnamed and fall
under "Everything else is allowed without asking".

A lane or probe module is one under `astrolabe/` that a shell can start (it has a
`__main__` guard, or it is a package's `__main__.py`, which `python -m` runs under the
package's name) and is not one of the offline simulations below, or one whose name says it
is a lane, a probe or the unattended service, started or not. Each must be named in the
rules' boundary section; each offline simulation in its allowed section; and every
`astrolabe.*` name the rules give must still be a module, so the list does not go stale.

Ported from meridian@014f706d:tests/test_worker_rules.py; changed: astrolabe's package, its
entry points and never-run list, and the boundaries pinned word for word against AGENTS.md.
"""

import re
import sys
from pathlib import Path

from git_scratch import REPO_ROOT
from test_agents_md import BOUNDARIES, OWNER, bullets, section

RULES = REPO_ROOT / ".agents" / "skills" / "lead" / "worker-rules.md"
SKILL = REPO_ROOT / ".agents" / "skills" / "lead" / "SKILL.md"
PACKAGE = REPO_ROOT / "astrolabe"
ALLOWED_HEADING = "## Everything else is allowed without asking"
RULES_HEADING = "## Boundaries (the owner's; the only hard rules)"
# Offline entry points the rules allow: none on the tree yet. A simulation that a shell can
# start is added here and named in the rules' allowed section in the same change.
OFFLINE: set[str] = set()
LANE_WORDS = ("lane", "probe", "unattended")
MAIN_GUARD = re.compile(r"^if __name__ == [\"']__main__[\"']:", re.MULTILINE)
MODULE_NAME = re.compile(r"\bastrolabe(?:\.[a-z_][a-z0-9_]*)+")


def module_name(path: Path, root: Path = REPO_ROOT) -> str:
    parts = path.relative_to(root).with_suffix("").parts
    if parts[-1] == "__main__":  # `python -m astrolabe.sim` would run `astrolabe/sim/__main__.py`
        parts = parts[:-1]
    return ".".join(parts)


def modules() -> dict[str, Path]:
    return {
        module_name(p): p
        for p in sorted(PACKAGE.rglob("*.py"))
        if p.name != "__init__.py" and "__pycache__" not in p.parts
    }


def lane_or_probe(name: str, path: Path) -> bool:
    leaf = name.rsplit(".", 1)[-1]
    if any(word in leaf for word in LANE_WORDS):
        return True
    if name in OFFLINE:
        return False
    return (
        path.name == "__main__.py"
        or MAIN_GUARD.search(path.read_text(encoding="utf-8")) is not None
    )


def sections() -> tuple[str, str]:
    text = RULES.read_text(encoding="utf-8")
    forbidden, heading, allowed = text.partition(ALLOWED_HEADING)
    assert heading, f"{RULES.name} has no section {ALLOWED_HEADING!r}"
    return forbidden, allowed


def named(text: str) -> set[str]:
    return set(MODULE_NAME.findall(text))


def test_every_lane_and_probe_module_is_named_in_the_boundaries():
    forbidden, _ = sections()
    found = {n for n, p in modules().items() if lane_or_probe(n, p)}
    assert found, "no lane or probe module found; the discovery is broken"
    assert sorted(found - named(forbidden)) == []


def test_the_offline_simulations_exist_and_are_named_as_allowed():
    _, allowed = sections()
    assert sorted(OFFLINE - set(modules())) == []
    assert sorted(OFFLINE - named(allowed)) == []


def test_every_module_the_rules_name_exists():
    assert sorted(named(RULES.read_text(encoding="utf-8")) - set(modules())) == []


def test_the_rules_name_the_gateway_port_orca_and_the_credential_store():
    forbidden, _ = sections()
    for needle in (
        "port 4002",
        "`orca-ide open`",
        "`orca-ide serve`",
        "`orca-ide serve service install`",
        "/etc/credstore",
        "`~/Jts`",
    ):
        assert needle in forbidden, needle


def test_the_lead_skill_appends_the_rules():
    assert "`worker-rules.md`" in SKILL.read_text(encoding="utf-8")


def test_a_new_lane_module_would_be_caught(tmp_path: Path):
    lane = tmp_path / "astrolabe" / "new_lane.py"
    lane.parent.mkdir()
    lane.write_text('"""A lane."""\n', encoding="utf-8")
    entry = tmp_path / "astrolabe" / "tool.py"
    entry.write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    assert lane_or_probe("astrolabe.new_lane", lane)
    assert lane_or_probe("astrolabe.tool", entry)


def test_an_offline_simulation_is_not_a_lane(tmp_path: Path, monkeypatch):
    sim = tmp_path / "astrolabe" / "sim.py"
    sim.parent.mkdir()
    sim.write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "OFFLINE", {"astrolabe.sim"})
    assert not lane_or_probe("astrolabe.sim", sim)


def test_a_package_main_is_an_entry_point(tmp_path: Path):
    main = tmp_path / "astrolabe" / "tool" / "__main__.py"
    main.parent.mkdir(parents=True)
    main.write_text("raise SystemExit(0)\n", encoding="utf-8")  # no guard
    assert module_name(main, tmp_path) == "astrolabe.tool"
    assert lane_or_probe("astrolabe.tool", main)


def test_the_rules_carry_the_owners_boundaries_word_for_word():
    """The boundary section of the rules is AGENTS.md's, as `tests/test_agents_md.py` pins it."""
    forbidden, _ = sections()
    assert bullets(section(forbidden, RULES_HEADING)) == list(OWNER)


def test_the_lead_skill_carries_the_owners_boundaries_word_for_word():
    skill = SKILL.read_text(encoding="utf-8")
    assert bullets(section(skill, BOUNDARIES)) == list(OWNER)


# The never-run list, held here word for word, so it cannot be softened.
NEVER_RUN_HEADING = "### Never run"
NEVER_RUN = (
    "`python -m astrolabe.ibkr_paper`, in any mode.",
    "`python -m astrolabe.boundary contract`.",
    "Any connection to the Gateway's API port (port 4002), `python -m astrolabe.ib_probe` "
    "included.",
    "`systemctl` on an astrolabe unit or timer.",
)


def test_the_never_run_list_keeps_its_words():
    forbidden, _ = sections()
    assert bullets(section(forbidden, NEVER_RUN_HEADING)) == list(NEVER_RUN)


def test_the_lead_skill_names_the_same_entry_points():
    skill = " ".join(SKILL.read_text(encoding="utf-8").split())
    for needle in (
        "`python -m astrolabe.ibkr_paper` in any mode",
        "`python -m astrolabe.boundary contract`",
        "any connection to the Gateway's API port",
        "`systemctl` on astrolabe units",
    ):
        assert needle in skill, needle


def test_the_rules_carry_the_compounding_practice_and_the_three_body_sections():
    rules = " ".join(RULES.read_text(encoding="utf-8").split())
    assert "learnings.py paths AREA..." in rules
    assert "Nothing to record" in rules and "learnings.py add" in rules
    template = (REPO_ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    headings = [line for line in template.splitlines() if line.startswith("## ")]
    assert headings == ["## Upstream references", "## Evidence", "## Risk and rollback"]
    for heading in headings:
        assert f"`{heading}`" in rules, heading


def test_the_skill_front_matter_is_strict_yaml():
    """Meridian's description held an unquoted colon; this one is a quoted scalar."""
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    fields = dict(line.split(": ", 1) for line in lines[1:end])
    assert fields["name"] == "lead"
    description = fields["description"]
    assert description.startswith('"') and description.endswith('"')
    assert '"' not in description[1:-1] and "\\" not in description
