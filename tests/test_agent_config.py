"""Only admitted agent configuration is committed.

Claude Code, Codex, Antigravity and DeepSeek Harness each load configuration from the folder
they start in: MCP servers, hooks, plugins and feature switches. So the only agent files git may
list are the shared skills, their Claude links, and one reviewed file: `.claude/settings.json`,
which registers the project guard (`tools/guard/pretooluse.py`). Runtime state stays out.

Ported from meridian@014f706d:tests/test_agent_config.py (lines 42-74, the committed-config
rule only); `.claude/settings.json` is admitted and `.agents/` is limited to its skills.
"""

import pytest
from test_paper_only import git_files

# Folders the four agents (or Gemini CLI, whose layout Antigravity shares) read in a project,
# and the project folders of serena, oh-my-codex, oh-my-claudecode and beads.
AGENT_FOLDERS = (
    ".agents/",
    ".claude/",
    ".codex/",
    ".gemini/",
    ".dsh/",
    ".omx/",
    ".serena/",
    ".beads/",
    ".omc/",
)
ALLOWED_FOLDERS = (".agents/skills/", ".claude/skills/")
# The one agent configuration file that may be tracked.
ALLOWED_FILES = {".claude/settings.json"}
# Files an agent reads wherever they are: Claude Code's project MCP servers.
AGENT_FILES = {".mcp.json"}


def agent_config(names: list[str]) -> list[str]:
    return [
        name
        for name in names
        if (
            name.startswith(AGENT_FOLDERS)
            and not name.startswith(ALLOWED_FOLDERS)
            and name not in ALLOWED_FILES
        )
        or name.rsplit("/", 1)[-1] in AGENT_FILES
    ]


def test_no_agent_configuration_is_committed():
    names = git_files("--cached", "--others", "--exclude-standard")
    assert names, "git reported no files; the check would pass without looking"
    assert agent_config(names) == []


@pytest.mark.parametrize(
    "name",
    [
        ".mcp.json",
        "sub/.mcp.json",
        ".claude/settings.local.json",
        ".claude/agents/reviewer.md",
        ".claude/commands/x.md",
        ".claude/hooks/x.py",
        ".claude/settings.json.bak",
        ".codex/config.toml",
        ".agents/mcp_config.json",
        ".agents/hooks.json",
        ".agents/plugins/x/plugin.json",
        ".gemini/settings.json",
        ".dsh/cordis.patch.yml",
        ".omx/setup-scope.json",
        ".serena/project.yml",
        ".beads/issues.db",
        ".omc/state/run.json",
    ],
)
def test_the_check_catches_agent_configuration(name):
    assert agent_config([name]) == [name]


@pytest.mark.parametrize(
    "name",
    [
        ".claude/settings.json",
        ".agents/skills/orca-cli/SKILL.md",
        ".agents/skills/NOTICE.md",
        ".agents/skills/lead/scripts/gate.py",
        ".claude/skills/orca-cli",
        "tools/guard/pretooluse.py",
        "a.json",
    ],
)
def test_the_check_leaves_admitted_files_alone(name):
    assert agent_config([name]) == []
