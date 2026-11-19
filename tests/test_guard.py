"""The agent-side guard: what it refuses, what it lets through, and how it is registered.

The guard is a guardrail (tools/guard/pretooluse.py). These cases pin its rules; they do not
claim it stops a process that writes its own socket code.
"""

import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from astrolabe import ib_ids

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "tools" / "guard" / "pretooluse.py"
_spec = importlib.util.spec_from_file_location("pretooluse", GUARD)
assert _spec is not None and _spec.loader is not None
pretooluse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pretooluse)

PAPER = ib_ids.PAPER_PORT
LIVE = PAPER - 1
HOME = Path.home()


def run_guard(call: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(call),
        capture_output=True,
        text=True,
        check=False,
    )


def bash(command: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def file_call(tool: str, path: Path | str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(path)},
    }


def test_the_guard_knows_the_same_paper_port_as_the_lane():
    assert pretooluse.GATEWAY_PAPER_PORT == ib_ids.PAPER_PORT
    assert set(pretooluse.API_PORTS) == {PAPER, LIVE, 7497, 7497 - 1}


REFUSED = [
    f"nc 127.0.0.1 {PAPER}",
    f"nc -z localhost {LIVE}",
    f"curl http://127.0.0.1:{PAPER}/",
    f"exec 3<>/dev/tcp/127.0.0.1/{PAPER}",
    f"python3 -c \"import socket; socket.create_connection(('127.0.0.1', {PAPER}))\"",
    f"git status && nc -z localhost {PAPER}",
    f"echo $(nc localhost {PAPER})",
    f"git ls-remote git://127.0.0.1:{PAPER}/x",
    f"telnet localhost {7497}",
    "uv run python -m astrolabe.ibkr_paper restart",
    "uv run python -m astrolabe.ibkr_paper fill",
    "python3 -m astrolabe.boundary contract",
    "python3 astrolabe/ibkr_paper.py order",
    "uv run python -c \"from astrolabe import ibkr_paper; ibkr_paper.main(['fill'])\"",
    "uv run python -m astrolabe.gateway_probe --client-id 1",
    "sudo systemctl start astrolabe-gateway-probe@owner.service",
    "systemctl --user enable --now astrolabe-evidence-sync.timer",
    "sudo rm /var/lib/astrolabe-lane/hold",
    "cat ~/Jts/jts.ini",
    "cd ~/Jts && cat jts.ini",
    "tail -f ~/.local/state/astrolabe/ibkr-paper/logs/engine.log",
    "ls ~/.local/state/astrolabe/ibkr-paper/runs",
    "sudo ls /etc/credstore.encrypted",
    "cat ~/.ssh/id_ed25519",
    "cat .env",
    "grep KEY .env.local",
    "echo '{}' > risk/boundary.contract.json",
    "sed -i s/a/b/ risk/limits.json",
    "cp /tmp/x risk/boundary.contract.json",
    # A single & starts another command: the search before it does not make the rest plain.
    f"git grep -n {PAPER} & nc localhost {PAPER}",
    f"git grep -n {PAPER} 2>&1 & nc -z localhost {PAPER}",
    # A plain reader pointed at the port: a redirection to bash's socket path, a host
    # option, a pager or a preprocessor command.
    f"git grep -n {PAPER} > /dev/tcp/127.0.0.1/{PAPER}",
    f"git grep -n port > /dev/tcp/gateway-host/{PAPER}",
    f"gh api --hostname localhost:{PAPER} /",
    f'git -c core.pager="nc 127.0.0.1 {PAPER}" log',
    f'rg --pre "nc localhost {LIVE}" x',
    # The modules that connect, loaded by an interpreter one-liner.
    'uv run python -c "from astrolabe import ib_probe; ib_probe.managed_accounts()"',
    'uv run python -c "import astrolabe.ib_probe as p; print(p)"',
    'uv run python -c "from astrolabe.ibkr_node import run; run()"',
    "uv run python -m astrolabe.ib_probe",
    "uv run python -m astrolabe.ibkr_node",
    'uv run python -c "from astrolabe import boundary; boundary._contract()"',
    "uv run python -c \"from astrolabe import boundary; boundary._main(['contract'])\"",
    # upstream/ is verbatim for the shell too.
    "rm upstream/vendor/README.md",
    "sed -i s/a/b/ upstream/claude-trading-skills/skills/x/SKILL.md",
    "echo x >> upstream/NOTICE",
    "git checkout -- upstream/claude-trading-skills",
    # A protected folder named without a trailing slash is read whole by a recursive tool.
    "ls ~/Jts",
    "grep -r Trading ~/Jts",
    "grep -r KEY ~/.ssh",
    "tar c ~/.gnupg",
    "cat jts.ini",
    "cat ~/.local/state/astrolabe/ibkr-paper/*/*.json",
    "ls ~/.local/state/astrolabe/ibkr-paper",
    "grep -r account ~/.local/state/astrolabe",
    "ls ~/.local/state/astrolabe/ibkr-paper/evidence-old",
    # A protected folder named as a bare word, and every form of an output redirection.
    "rm -rf upstream",
    "rm -rf risk",
    "sudo rm -rf risk",
    "mv risk risk.bak",
    "find risk -delete",
    "echo x &> upstream/NOTICE",
    "echo x 1> upstream/NOTICE",
    "echo x | tee upstream/NOTICE",
    # A command handed to a shell, substituted, or fed as a here-document.
    "bash -c 'rm -rf risk'",
    'echo "$(rm -rf upstream)"',
    "echo `rm -rf risk`",
    "bash <<EOF\nrm -rf upstream\nEOF",
    'python3 - <<EOF\nopen("risk/limits.json", "w")\nEOF',
    "python3 - <<'PY'\nprint(open('jts.ini').read())\nPY",
    # A command that cannot be split is judged by its text.
    'echo "it > risk/x.json',
    # A file protected by its name is refused as a file, also to a search program.
    "grep . jts.ini",
    "grep -e Trading jts.ini",
    "sort jts.ini",
    "cat < jts.ini",
    "python3 -c \"print(open('jts.ini').read())\"",
    "rg KEY .env",
    "uv run --env-file .env pytest",
    # Removing or staging git-ignored files: the owner's contract is one of them.
    "git clean -fdx",
    "git clean -fdX",
    "git stash --all",
    "git add --force .",
    "git add -f risk/boundary.contract.json",
    # A search does not make what it is piped into plain, and git options before the
    # subcommand can run a program.
    f"rg -n {PAPER} astrolabe | nc localhost {PAPER}",
    f"git -c core.pager=less log --grep {PAPER}",
    f"gh api --hostname example.com repos/x/y/pulls/{PAPER}",
    # A path with a space in it is still a path, as a word and as a redirection target.
    'echo x > "upstream/a file"',
    'cat "project copy/.env"',
    'rm "risk/old limits.json"',
    # A substitution inside a here-document runs, and a quoted << starts no here-document.
    "cat <<EOF\n$(cat jts.ini)\nEOF",
    "echo '<<EOF'\nrm -rf upstream/\nEOF",
    'cat <<< "$(cat jts.ini)"',
    # Programs that write through an option or through their own script.
    "sort -o upstream/NOTICE upstream/NOTICE",
    "sed 's/a/b/w upstream/NOTICE' README.md",
    "uniq names.txt risk/limits.json",
    # git under another name or behind a launcher, and options in a cluster.
    "/usr/bin/git clean -fdx",
    "env git clean -fdx",
    "git stash push -qa",
    # A file given to a search program where it takes a file, and git reading outside the
    # repository.
    "grep < .env KEY",
    "grep -f.env README.md",
    "rg -f jts.ini astrolabe",
    "git diff --no-index /dev/null jts.ini",
]


@pytest.mark.parametrize("command", REFUSED)
def test_refused_shell_commands(command):
    result = run_guard(bash(command))
    assert result.returncode == 2, (command, result.stderr)
    assert "astrolabe guard: refused" in result.stderr
    assert "boundaries" in result.stderr


ALLOWED = [
    "uv sync --locked && uv run ruff check . && uv run pyright && uv run pytest",
    "git status --short",
    f'git commit -m "boundary: the paper port stays {PAPER}"',
    f"rg -n {PAPER} astrolabe tests",
    f"git grep -n {PAPER} -- astrolabe",
    'git grep -n "astrolabe.ibkr_paper" -- docs',
    "gh pr view 14002 --json state",
    "uv run pytest tests/test_boundary.py -q 2>&1 | tail -5",
    'uv run python -c "from astrolabe import ib_ids; print(ib_ids.HOST)"',
    "uv run python -m astrolabe.boundary hash",
    "systemctl status astrolabe-gateway-probe.timer",
    "systemctl --user list-timers",
    "ls risk/ 2>/dev/null",
    "cat risk/boundary.contract.json",
    "ls ~/.local/state/astrolabe/ibkr-paper/evidence",
    "echo version 1.4002.3",
    "sha256sum uv.lock",
    # A redirection's & is not a command separator.
    f"git grep -n {PAPER} -- astrolabe 2>&1",
    # A search of the repository for a protected name, or for a lane module's name.
    "rg -n jts.ini astrolabe tests",
    'git grep -n "jts.ini"',
    "uv run python -m pytest tests/test_ib_probe.py -q",
    "uv run pytest tests/test_ibkr_node_offline.py -k import",
    "cat ~/.local/state/astrolabe/ibkr-paper/evidence/M1-ibkr-paper-x-fill.json",
    # upstream/ may be read, compared and staged, and its own environment synced.
    "git diff --stat upstream/",
    "git add upstream/ tools/skills/skills.lock.json",
    "ls upstream/claude-trading-skills",
    (
        "uv --directory upstream/claude-trading-skills sync --no-config --locked --no-build "
        "--no-install-project --extra market-calendar --python 3.13"
    ),
    # A pattern is not a target, a path or a program, also when the search is piped on.
    f"rg -n '127.0.0.1:{PAPER}' astrolabe tests",
    "rg -n 'python' upstream/",
    "rg -n jts.ini astrolabe tests | head -20",
    f"rg -n {PAPER} astrolabe | head -20",
    "grep -rn -A 2 jts.ini astrolabe",
    "rg -t py jts.ini",
    'rg -n "\\.env" docs',
    f"git --no-pager log -5 --grep {PAPER}",
    "git log -S jts.ini --oneline",
    # A message is prose: an arrow in it is no redirection, a word in it is no path.
    'git commit -m "upstream gate: a -> b" -m "Signed: A <a at b>"',
    'git commit -m "ignore .env files and keep risk/ the owner\'s"',
    # A here-document that feeds neither a shell nor an interpreter is data.
    "cat > docs/notes.md <<EOF\nWe don't change upstream/ or risk/ here.\nEOF",
    "cat >> docs/notes.md <<'EOF'\nSearch for jts.ini and .env in the tests.\nEOF",
    # Names that only look like the protected ones, and ordinary housekeeping.
    "sed -i s/a/b/ upstream-map.toml",
    "cp UPSTREAM.md /tmp/x",
    "rm -rf build dist",
    "ls risk",
    "git push --set-upstream origin lane/fix",
    "git clean -fd",
    "git stash -u",
    "git add -A",
    "echo done > /dev/null 2>&1",
    # A pattern given with -e, a message of several lines, and a read with sed.
    "rg -e jts.ini astrolabe tests",
    "rg --regexp=jts.ini astrolabe",
    "git log --grep=jts.ini --oneline",
    'git commit -m "lane: first line\n\nIt mentions upstream/, risk/ and jts.ini."',
    "sed -n 1,40p upstream/claude-trading-skills/skills/x/SKILL.md",
    "sort names.txt | uniq -c | head",
    "cat > docs/x.md <<'EOF'\nThe date is $(date) here.\nEOF",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_allowed_shell_commands(command):
    result = run_guard(bash(command))
    assert result.returncode == 0, (command, result.stderr)
    assert result.stderr == ""


# What an agent puts around a command without changing what it does.
WRAPPERS = [
    "cd /tmp && {command}",
    "true; {command}",
    "( {command} )",
    "echo start\n{command}",
    "{command} | cat",
    "sudo {command}",
    "bash -lc {quoted}",
    "env LC_ALL=C {command}",
]


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_a_refused_command_stays_refused_inside_a_wrapper(wrapper):
    passed = [
        wrapped
        for command in REFUSED
        for wrapped in [wrapper.format(command=command, quoted=shlex.quote(command))]
        if pretooluse.shell_refusal(wrapped) is None
    ]
    assert passed == []


@pytest.mark.parametrize("around", ["cd /tmp && {command}", "{command} 2>&1 | tail -5"])
def test_an_allowed_command_stays_allowed_with_a_folder_change_or_a_pipe_to_a_filter(around):
    refused = [
        (wrapped, pretooluse.shell_refusal(wrapped))
        for command in ALLOWED
        if "\n" not in command  # a here-document ends at its own last line
        for wrapped in [around.format(command=command)]
        if pretooluse.shell_refusal(wrapped) is not None
    ]
    assert refused == []


def split(command: str) -> list[tuple[list[str], list[str]]]:
    reading = pretooluse._read(command)
    assert reading is not None
    return [(simple.words, simple.writes) for simple in reading[0]]


def test_a_command_is_split_at_every_separator_and_keeps_its_redirection_targets():
    assert split("a && b; c >x 2>&1 | d &\ne || f") == [
        (["a"], []),
        (["b"], []),
        (["c", "2"], ["x", "1"]),
        (["d"], []),
        (["e"], []),
        (["f"], []),
    ]


def test_quotes_keep_a_message_whole_and_a_redirection_inside_it_is_text():
    assert split('git commit -m "a -> b; c | d" > out.txt') == [
        (["git", "commit", "-m", "a -> b; c | d"], ["out.txt"]),
    ]


def test_a_shell_argument_a_substitution_and_a_shells_here_document_are_read_as_commands():
    assert (["rm", "-rf", "x"], []) in split("sudo bash -lc 'cd /tmp && rm -rf x'")
    assert (["rm", "x"], []) in split('echo "$(rm x)"')
    assert (["rm", "x"], []) in split("echo `rm x`")
    assert (["rm", "x"], []) in split("bash <<EOF\nrm x\nEOF")
    assert (["rm", "x"], []) in split("eval rm x")


def test_a_here_document_for_another_program_is_data_and_for_an_interpreter_a_program():
    reading = pretooluse._read("cat > notes.md <<EOF\nrm -rf x; don't\nEOF")
    assert reading is not None
    assert [(simple.words, simple.writes) for simple in reading[0]] == [(["cat"], ["notes.md"])]
    assert reading[1] == []

    commands, programs = pretooluse._read("python3 - <<EOF\nprint(1)\nEOF") or ([], [])
    assert [simple.words for simple in commands] == [["python3", "-"]]
    assert programs == ["print(1)"]


def test_an_input_redirection_and_a_here_string_are_kept_apart_from_the_words():
    reading = pretooluse._read("grep KEY < config.txt <<< text > out.txt")
    assert reading is not None
    (simple,) = reading[0]
    assert (simple.words, simple.reads, simple.data, simple.writes) == (
        ["grep", "KEY"],
        ["config.txt"],
        ["text"],
        ["out.txt"],
    )


def test_a_quoted_here_document_mark_starts_nothing():
    text, documents = pretooluse._without_heredocs("echo '<<EOF'\nrm x\nEOF")
    assert documents == []
    assert text == "echo '<<EOF' ; rm x ; EOF"


def test_a_here_document_is_cut_out_with_the_line_that_owns_it():
    text, documents = pretooluse._without_heredocs(
        "cat > a.md <<'EOF' && echo done\nit's \"text\"\nEOF\nls"
    )
    assert text == "cat > a.md <<'EOF' && echo done ; ls"
    assert documents == [("cat > a.md <<'EOF' && echo done", 'it\'s "text"')]


def test_a_substitution_in_a_here_document_is_read_as_a_command():
    assert (["cat", "x"], []) in split("cat <<EOF\nvalue: $(cat x)\nEOF")
    assert (["cat", "y"], []) in split("cat <<EOF\nvalue: `cat y`\nEOF")


def test_a_command_with_an_open_quote_cannot_be_split():
    assert pretooluse._read('echo "open') is None


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["rg", "-n", "jts.ini", "astrolabe"], {2}),
        (["grep", "-rn", "-A", "2", "jts.ini", "astrolabe"], {4}),
        (["grep", "-rnA2", "jts.ini", "."], {2}),
        (["rg", "-t", "py", "jts.ini"], {3}),
        (["rg", "--type=py", "--", "jts.ini", "x"], {3}),
        (["grep", "--include", "*.py", "jts.ini", "."], {3}),
        # With -e the pattern is that option's value, and every positional word is a path.
        (["grep", "-e", "Trading", "jts.ini"], {2}),
        (["rg", "--regexp=Trading", "jts.ini"], {1}),
        (["rg", "--regexp", "a", "-e", "b", "x"], {2, 4}),
        # With -f the patterns come from a file: no word is a pattern.
        (["grep", "-nf", "patterns.txt", "jts.ini"], set()),
        (["grep"], set()),
    ],
)
def test_where_a_search_programs_patterns_stand(words, expected):
    assert pretooluse._pattern_positions(words) == expected


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["git", "log", "-S", "jts.ini", "--oneline"], {3}),
        (["git", "log", "--grep=jts.ini"], {2}),
        (["git", "commit", "-m", "jts.ini"], {3}),
        (["git", "grep", "-n", "jts.ini", "--", "docs"], {3}),
        (["git", "grep", "-A", "2", "jts.ini"], {4}),
        (["git", "grep", "-e", "jts.ini", "docs"], {3}),
        (["git", "diff", "--no-index", "/dev/null", "jts.ini"], set()),
    ],
)
def test_where_git_takes_a_pattern_or_a_message(words, expected):
    assert pretooluse._git_text_positions(words) == expected


@pytest.mark.parametrize(
    ("tool", "path"),
    [
        ("Read", HOME / "Jts" / "jts.ini"),
        ("Grep", HOME / "Jts"),
        ("Glob", HOME / "Jts"),
        ("Read", HOME / ".local/state/astrolabe/ibkr-paper/runs/record.json"),
        ("Read", HOME / ".local/state/astrolabe/ibkr-paper/logs/engine.log"),
        ("Read", HOME / ".ssh" / "config"),
        ("Glob", HOME / ".ssh"),
        ("Grep", HOME / ".local/state/astrolabe"),
        ("Grep", HOME / ".local/state/astrolabe/ibkr-paper"),
        ("Read", HOME / ".local/state/astrolabe/ibkr-paper/account.lock"),
        ("Read", ROOT / "tests" / "fixtures" / "jts.ini"),
        ("Read", ROOT / ".env"),
        ("Read", ROOT / ".env.local"),
        ("Write", ROOT / "risk" / "boundary.contract.json"),
        ("Edit", ROOT / "risk" / "limits.json"),
        ("Write", ROOT / "deploy" / "lane.contract.json"),
        ("Edit", ROOT / "upstream" / "claude-trading-skills" / "skills" / "x" / "SKILL.md"),
    ],
)
def test_refused_file_calls(tool, path):
    result = run_guard(file_call(tool, path))
    assert result.returncode == 2, (tool, str(path), result.stderr)


@pytest.mark.parametrize(
    ("tool", "path"),
    [
        ("Read", ROOT / "astrolabe" / "boundary.py"),
        ("Edit", ROOT / "astrolabe" / "ibkr_node.py"),
        ("Write", ROOT / "tests" / "test_new.py"),
        ("Read", ROOT / "risk" / "boundary.contract.json"),
        ("Read", ROOT / "upstream" / "claude-trading-skills" / "LICENSE"),
        ("Read", HOME / ".local/state/astrolabe/ibkr-paper/evidence/record.json"),
        ("Read", ROOT / "docs" / "environment.md"),
    ],
)
def test_allowed_file_calls(tool, path):
    result = run_guard(file_call(tool, path))
    assert result.returncode == 0, (tool, str(path), result.stderr)


def test_windows_separators_do_not_slip_past_the_guard():
    result = run_guard(file_call("Write", "C:\\repo\\risk\\boundary.contract.json"))
    assert result.returncode == 2


def test_other_tools_and_calls_without_a_path_pass():
    assert run_guard({"tool_name": "WebSearch", "tool_input": {"query": "x"}}).returncode == 0
    assert run_guard({"tool_name": "Grep", "tool_input": {"pattern": "jts"}}).returncode == 0


def test_a_call_the_guard_cannot_read_is_reported_and_does_not_block():
    result = subprocess.run(
        [sys.executable, str(GUARD)], input="not json", capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert "could not read the tool call" in result.stderr


def test_the_guard_is_the_only_hook_and_covers_the_shell_and_file_tools():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings == {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                'PY=/usr/bin/python3; [ -x "$PY" ] || PY=python; '
                                '"$PY" "${CLAUDE_PROJECT_DIR}/tools/guard/pretooluse.py"'
                            ),
                        }
                    ],
                }
            ]
        }
    }
