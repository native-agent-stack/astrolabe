"""Dispatch behavior offline; Orca's process boundary is a fake executable.

Ported from meridian@014f706d:tests/test_lead_dispatch.py; changed: astrolabe's names, the
model id from models.toml, and DeepSeek refused as a builder (its start-to-end cases go).
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from git_scratch import REPO_ROOT

SCRIPTS = REPO_ROOT / ".agents/skills/lead/scripts"

START_PREAMBLE = "You are a dispatched worker.\n\n=== TASK ===\nBuild the small fixture.\n"
TERMINAL_INCARNATION = "5f0c1f3e-0d2a-4b7c-9e61-2a8b3c4d5e6f"
OTHER_INCARNATION = "8a1d2e4f-6b7c-4d90-8e21-3f5a6b7c8d90"

# Synthetic worker-start receipt; Orca 1.4.258 field names, values invented.
START_RECEIPT = {
    "runId": "run-1",
    "taskId": "task-1",
    "dispatchId": "ctx-1",
    "state": "ready",
    "turnStart": "unsupported",
    "effects": [
        {"kind": "worktree", "action": "created_top_level", "id": "repo::/worker"},
        {"kind": "terminal", "role": "agent", "action": "created", "id": "term-worker"},
    ],
    "prompt": {
        "requestId": "request-start",
        "provider": "unsupported",
        "observation": "unsupported",
        "stages": ["input_accepted"],
        "processIncarnation": TERMINAL_INCARNATION,
    },
    "residualResources": [],
}


def worker_show(
    activity="idle",
    incarnation=TERMINAL_INCARNATION,
    handle="term-worker",
    agent_identity="dsh",
):
    """Worker-show result shape; Orca 1.4.258 field names, values invented."""
    return {
        "dispatch": {"id": "ctx-1", "status": "dispatched", "dispatchedAt": "2026-11-14 19:00:00"},
        "observation": {"exactWorker": True, "status": "live", "agentWait": None},
        "terminal": {
            "handle": handle,
            "ptyId": "pty-1",
            "incarnationId": incarnation,
            "agentIdentity": agent_identity,
            "writable": True,
            "connected": True,
        },
        "projection": {
            "stage": {"worker": "ready", "detail": "input_accepted", "activity": activity},
            "evidence": {"liveStatus": "fresh"},
        },
    }


@pytest.fixture
def sandbox(tmp_path: Path):
    scripts = tmp_path / ".agents/skills/lead/scripts"
    scripts.mkdir(parents=True)
    source = SCRIPTS / "dispatch.py"
    if source.exists():
        shutil.copy(source, scripts)
    shutil.copy(SCRIPTS.parent / "worker-rules.md", scripts.parent)
    shutil.copy(SCRIPTS.parent / "models.toml", scripts.parent)
    (tmp_path / "task.txt").write_text("Build the small fixture.\n")
    # The real dollar scan; only oh-my-codex's keyword detector is modelled below.
    shutil.copy(SCRIPTS / "trigger_words.py", scripts / "real_trigger_words.py")
    (scripts / "trigger_words.py").write_text("""
import json, os, sys
from pathlib import Path
sys.dont_write_bytecode = True
from real_trigger_words import mentions
text = sys.stdin.read()
with open("scans.jsonl", "a") as out:
    out.write(json.dumps(text) + "\\n")
if os.getenv("DETECTOR_FAIL"):
    sys.exit("detector unavailable")
matches = []
found = [] if os.getenv("NO_MENTIONS") else mentions(text)
# Model the installed detector's explicit-invocation masking.
if text.startswith("$ralplan"):
    matches = [{"keyword": "$ralplan", "skill": "ralplan", "priority": 11}]
elif "investigate" in text:
    matches = [{"keyword": "investigate", "skill": "analyze", "priority": 12}]
reply = {"selected": matches[0] if matches else None, "matches": matches, "mentions": found}
print(os.getenv("DETECTOR_REPLY", json.dumps(reply)))
""")
    (scripts / "gate.py").write_text("""
import json, os, sys
from pathlib import Path
Path("registered.json").write_text(json.dumps(sys.argv[1:]))
if os.getenv("GATE_FAIL"):
    sys.exit("registration refused")
print("registered")
""")
    fake = tmp_path / "orca"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open("orca.jsonl", "a") as out:
    out.write(json.dumps(args) + "\\n")
def worker_agent():
    first = json.loads(Path("orca.jsonl").read_text().splitlines()[0])
    return first[first.index("--agent") + 1] if "--agent" in first else "claude"
if args[:2] == ["orchestration", "worker-start"]:
    Path("delivered.txt").write_text(Path(args[args.index("--spec-file") + 1]).read_text())
    result = {"runId": "run-1", "taskId": "task-1", "dispatchId": "ctx-1",
              "state": "ready", "stage": "ready", "turnStart": "unsupported",
              "setup": {"state": "completed"}, "effects": [
                  {"kind": "worktree", "action": "created_top_level", "id": "repo::/worker"},
                  {"kind": "terminal", "role": "agent", "action": "created",
                   "id": "term-worker"}],
              "prompt": {"requestId": "request-start", "provider": "unsupported",
                         "observation": "unsupported", "stages": ["input_accepted"],
                         "processIncarnation": "5f0c1f3e-0d2a-4b7c-9e61-2a8b3c4d5e6f"},
              "residualResources": []}
    print(os.getenv("START_REPLY", json.dumps({"ok": True, "result": result})))
    sys.exit(int(os.getenv("START_EXIT", "0")))
if args[:2] == ["worktree", "show"]:
    assert args[args.index("--worktree") + 1] == "id:repo::/worker"
    result = {"worktree": {"branch": "refs/heads/owner/actual-branch"}}
    print(os.getenv("SHOW_REPLY", json.dumps({"ok": True, "result": result})))
    sys.exit(0)
if args[:2] == ["orchestration", "worker-show"]:
    assert args[args.index("--dispatch") + 1] == "ctx-1"
    count = Path("observations.txt")
    n = int(count.read_text()) + 1 if count.exists() else 1
    count.write_text(str(n))
    incarnation = "5f0c1f3e-0d2a-4b7c-9e61-2a8b3c4d5e6f"
    if os.getenv("CHANGED_WORKER") and n > 1:
        incarnation = "8a1d2e4f-6b7c-4d90-8e21-3f5a6b7c8d90"
    terminal = {"handle": "term-worker", "ptyId": "pty-1", "incarnationId": incarnation,
                "agentIdentity": worker_agent(), "writable": True, "connected": True}
    result = {"dispatch": {"id": "ctx-1", "status": "dispatched",
                           "dispatchedAt": "2026-11-14 19:00:00"},
              "observation": {"exactWorker": True, "status": "live", "agentWait": None},
              "terminal": terminal,
              "projection": {"stage": {"worker": "ready", "detail": "input_accepted",
                                       "activity": os.getenv("ACTIVITY", "done")},
                             "evidence": {"liveStatus": os.getenv("FRESHNESS", "fresh")}}}
    print(os.getenv("WORKER_REPLY", json.dumps({"ok": True, "result": result})))
    sys.exit(0)
if args[:2] == ["terminal", "wait"]:
    assert args[args.index("--terminal") + 1] == "term-worker"
    assert args[args.index("--for") + 1] == "tui-idle"
    assert 0 < int(args[args.index("--timeout-ms") + 1]) <= 60000
    if os.getenv("WAIT_HANG"):
        import time
        time.sleep(30)
    result = {"wait": {"satisfied": not bool(os.getenv("WAIT_TIMEOUT")),
                        "condition": "tui-idle", "status": "running"}}
    print(json.dumps({"ok": True, "result": result}))
    sys.exit(0)
if args[:2] == ["orchestration", "worker-read"]:
    assert args[args.index("--source") + 1] == "transcript"
    assert args[args.index("--dispatch") + 1] == "ctx-1"
    result = {"dispatchId": "ctx-1", "source": "transcript", "sourceExact": True,
              "provider": "claude", "clipping": ["message_limit_or_scan_window"],
              "transcript": {"messages": [{"role": "assistant", "timestamp": 1794682801000,
                  "blocks": [{"type": "text", "text": "Ready.\\n[ASTROLABE_WAIT]"}]}]}}
    result.update(json.loads(os.getenv("READ_PATCH", "{}")))
    print(os.getenv("READ_REPLY", json.dumps({"ok": True, "result": result})))
    sys.exit(0)
if args[:2] == ["terminal", "send"]:
    assert Path("registered.json").exists()
    assert args[args.index("--terminal") + 1] == "term-worker"
    Path("activation.txt").write_text(args[args.index("--text") + 1])
    print(os.getenv("SEND_REPLY", json.dumps({"ok": True, "result": {"send": {
        "accepted": True, "prompt": {"requestId": "request-1", "stages": ["input_accepted"]}}}})))
    if os.getenv("SEND_EXIT"):
        print("transport warning", file=sys.stderr)
    if os.getenv("SEND_HANG"):
        import time
        sys.stdout.flush()
        time.sleep(30)
    sys.exit(int(os.getenv("SEND_EXIT", "0")))
sys.exit("unexpected Orca command")
"""
    )
    fake.chmod(0o755)
    return tmp_path


def dispatch(root: Path, runtime="codex", heavy="no", *flags, **env):
    # A home outside the fixture repository: the recovery preamble lives under its cache.
    home = root.parent / f"{root.name}-home"
    return subprocess.run(
        [
            sys.executable,
            str(root / ".agents/skills/lead/scripts/dispatch.py"),
            "task.txt",
            "test-worker",
            runtime,
            "--design-heavy",
            heavy,
            *flags,
        ],
        cwd=root,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "ORCA_CLI_COMMAND": str(root / "orca"),
            "HOME": str(home),
            **env,
        },
    )


def calls(root: Path):
    path = root / "orca.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("runtime,agent", [("claude", "claude"), ("codex", "codex")])
def test_dispatch_delivers_rules_and_registers_actual_branch(sandbox, runtime, agent):
    run = dispatch(sandbox, runtime)
    assert run.returncode == 0, run.stderr
    sent = (sandbox / "delivered.txt").read_text()
    assert sent.endswith((SCRIPTS.parent / "worker-rules.md").read_text())
    assert "Build the small fixture." in sent
    found = calls(sandbox)
    start = found[0]
    if runtime == "deepseek":
        assert [call[:2] for call in found] == [
            ["orchestration", "worker-start"],
            ["worktree", "show"],
            ["orchestration", "worker-show"],
        ]
    else:
        assert len(found) == 2
    assert start[start.index("--agent") + 1] == agent
    assert start[start.index("--worktree") + 1] == "new-top-level"
    assert start[start.index("--base-branch") + 1] == "origin/main"
    assert start[start.index("--name") + 1] == "test-worker"
    assert "--full" in start
    if runtime == "claude":
        assert start[start.index("--model") + 1] == "claude-opus-5-5"  # models.toml
    else:
        assert "--model" not in start
    assert json.loads((sandbox / "registered.json").read_text()) == [
        "register",
        "owner/actual-branch",
        "--builder",
        runtime,
        "--task",
        "task-1",
    ]


@pytest.mark.parametrize("heavy,flags", [("yes", ()), ("no", ("--multi-file",))])
def test_codex_planning_scans_raw_task_before_adding_invocation(sandbox, heavy, flags):
    run = dispatch(sandbox, "codex", heavy, *flags)
    assert run.returncode == 0, run.stderr
    scans = [json.loads(line) for line in (sandbox / "scans.jsonl").read_text().splitlines()]
    assert scans[0].startswith("Build the small fixture.\n")
    assert scans[0].endswith((SCRIPTS.parent / "worker-rules.md").read_text())
    assert scans[1].startswith("$ralplan\n")
    assert scans[1] == (sandbox / "delivered.txt").read_text()


def test_claude_lifecycle_waits_for_assistant_output_then_sends_once(sandbox):
    run = dispatch(sandbox, "claude", "yes")
    assert run.returncode == 0, run.stderr
    first = (sandbox / "delivered.txt").read_text().splitlines()[0]
    assert "/autopilot --workflow astrolabe-full" in first
    assert "[ASTROLABE_WAIT]" in first
    assert (
        (sandbox / "activation.txt").read_text().startswith("/autopilot --workflow astrolabe-full ")
    )
    commands = [c[:2] for c in calls(sandbox)]
    assert commands == [
        ["orchestration", "worker-start"],
        ["worktree", "show"],
        ["orchestration", "worker-show"],
        ["terminal", "wait"],
        ["orchestration", "worker-read"],
        ["orchestration", "worker-show"],
        ["terminal", "send"],
    ]
    assert "--enter" in calls(sandbox)[-1]
    assert "input accepted" in run.stdout
    assert "turn started" not in run.stdout.lower()


@pytest.mark.parametrize(
    "role,blocks,stamp",
    [
        ("user", [{"type": "text", "text": "[ASTROLABE_WAIT]"}], 1794682801000),
        ("tool", [{"type": "text", "text": "[ASTROLABE_WAIT]"}], 1794682801000),
        ("assistant", [{"type": "tool-call", "input": "[ASTROLABE_WAIT]"}], 1794682801000),
        ("assistant", [{"type": "text", "text": "Task says [ASTROLABE_WAIT]"}], 1794682801000),
        ("assistant", [{"type": "text", "text": "[ASTROLABE_WAIT]"}], 1794682799000),
    ],
)
def test_lifecycle_ignores_echo_tools_inline_markers_and_stale_output(sandbox, role, blocks, stamp):
    patch = {"transcript": {"messages": [{"role": role, "blocks": blocks, "timestamp": stamp}]}}
    run = dispatch(sandbox, "claude", "yes", READ_PATCH=json.dumps(patch))
    assert run.returncode == 0, run.stderr
    assert not (sandbox / "activation.txt").exists()
    assert "/autopilot --workflow astrolabe-full" in run.stdout
    assert "manual" in run.stdout.lower()


@pytest.mark.parametrize(
    "env",
    [
        {"WAIT_TIMEOUT": "1"},
        {"WORKER_REPLY": "{}"},
        {"READ_REPLY": "garbage"},
        {"READ_PATCH": '{"source":"terminal"}'},
        {"READ_PATCH": '{"sourceExact":false}'},
        {"READ_PATCH": '{"clipping":["transcript_payload"]}'},
        {"CHANGED_WORKER": "1"},
        {"ACTIVITY": "working"},
        {"FRESHNESS": "stale"},
        {"WORKER_REPLY": '{"ok":true,"result":{"terminal":null}}'},
    ],
)
def test_uncertain_lifecycle_prints_manual_fallback_without_sending(sandbox, env):
    run = dispatch(sandbox, "claude", "yes", **env)
    assert run.returncode == 0, run.stderr
    assert not (sandbox / "activation.txt").exists()
    assert "manual" in run.stdout.lower()
    assert "/autopilot --workflow astrolabe-full" in run.stdout


@pytest.mark.parametrize(
    "env",
    [
        {"SEND_EXIT": "1"},
        {"SEND_REPLY": "garbage"},
        {"SEND_REPLY": '{"ok":true,"result":{"send":{"accepted":false}}}'},
    ],
)
def test_uncertain_send_never_retries_or_invites_blind_duplicate(sandbox, env):
    run = dispatch(sandbox, "claude", "yes", **env)
    assert run.returncode == 0, run.stderr
    assert sum(c[:2] == ["terminal", "send"] for c in calls(sandbox)) == 1
    assert "before typing" in run.stdout
    assert "input accepted" not in run.stdout
    if "SEND_EXIT" in env:
        assert "request-1" in run.stdout  # Keep the receipt even when stderr is also present.


@pytest.mark.parametrize("hang", ["WAIT_HANG", "SEND_HANG"])
def test_lifecycle_deadline_bounds_a_hung_orca_command(sandbox, monkeypatch, capsys, hang):
    spec = importlib.util.spec_from_file_location(
        "dispatch_deadline", sandbox / ".agents/skills/lead/scripts/dispatch.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv(hang, "1")
    (sandbox / "registered.json").write_text("[]")
    start = time.monotonic()
    module.activate_lifecycle(str(sandbox / "orca"), "ctx-1", timeout=2)
    assert time.monotonic() - start < 5
    output = capsys.readouterr().out
    assert "manual" in output.lower()
    if hang == "SEND_HANG":
        assert "request-1" in output and "before typing" in output
        assert sum(c[:2] == ["terminal", "send"] for c in calls(sandbox)) == 1
    else:
        assert not (sandbox / "activation.txt").exists()


@pytest.mark.parametrize("heavy", ["yes", "no"])
def test_unintended_raw_trigger_refuses_before_orca_even_when_planning(sandbox, heavy):
    (sandbox / "task.txt").write_text("investigate the fixture")
    run = dispatch(sandbox, "codex", heavy)
    assert run.returncode != 0
    assert "trigger" in run.stderr.lower()
    assert calls(sandbox) == []


@pytest.mark.parametrize("heavy", ["yes", "no"])
def test_trigger_in_appended_rules_is_also_refused(sandbox, heavy):
    rules = sandbox / ".agents/skills/lead/worker-rules.md"
    rules.write_text(rules.read_text() + "\ninvestigate the fixture\n")
    run = dispatch(sandbox, "codex", heavy)
    assert run.returncode != 0
    assert calls(sandbox) == []


@pytest.mark.parametrize(
    "env",
    [
        {"DETECTOR_FAIL": "1"},
        {"DETECTOR_REPLY": "garbage"},
        {"DETECTOR_REPLY": "{}"},
        {"DETECTOR_REPLY": '{"matches": [], "mentions": [], "selected": {}}'},
        {"DETECTOR_REPLY": '{"matches": [], "selected": null}'},
    ],
)
def test_missing_or_malformed_detector_never_launches(sandbox, env):
    run = dispatch(sandbox, **env)
    assert run.returncode != 0
    assert calls(sandbox) == []


@pytest.mark.parametrize(
    "env",
    [
        {"START_EXIT": "1", "START_REPLY": '{"ok":false,"error":"agent_readiness"}'},
        {"START_REPLY": '{"ok":true,"result":{"state":"outcome_unknown"}}'},
        {"START_REPLY": '{"ok":true,"result":{"state":"ready"}}'},
        {"START_REPLY": "garbage"},
        {"SHOW_REPLY": '{"ok":true,"result":{"worktree":{"branch":null}}}'},
        {"GATE_FAIL": "1"},
    ],
)
def test_failed_start_or_registration_keeps_evidence_without_retry(sandbox, env):
    run = dispatch(sandbox, **env)
    assert run.returncode != 0
    assert "Do not repeat" in run.stderr
    assert run.stdout.strip()  # Original receipt survives parsing/registration failures.
    assert sum(c[:2] == ["orchestration", "worker-start"] for c in calls(sandbox)) == 1


@pytest.mark.parametrize(
    "heavy,flags,task,where,ok",
    [
        ("yes", (), "Build the small fixture.\n", "", True),
        ("no", ("--multi-file",), "Build the small fixture.\n", "", True),
        ("yes", (), "Build it.\nUse `$ralplan` for this.\n", "task", False),
        ("no", ("--multi-file",), "Build it.\n", "rules", False),
        ("no", (), "Build it.\nUse `$ralplan` for this.\n", "task", False),
        ("no", (), "Build it.\n", "rules", False),
        ("no", (), "Build the small fixture.\n", "", True),
    ],
)
def test_codex_dispatch_holds_exactly_the_requested_planning_mention(
    sandbox, heavy, flags, task, where, ok
):
    """Asked: one explicit mention, the first line; not asked: none; else refused by line."""
    (sandbox / "task.txt").write_text(task)
    rules = sandbox / ".agents/skills/lead/worker-rules.md"
    if where == "rules":
        rules.write_text(rules.read_text() + "Codex: use `$ralplan` for a build.\n")
    run = dispatch(sandbox, "codex", heavy, *flags)
    if not ok:
        assert run.returncode != 0
        assert calls(sandbox) == []
        # The unplanned scan of task, blank line and rules: the appended rule is the last line.
        lines = len(task.splitlines()) + 1 + len(rules.read_text().splitlines())
        line = 2 if where == "task" else lines
        assert f"$ralplan at line {line}: " in run.stderr
        assert "`$ralplan`" in run.stderr
        assert "${NAME}" in run.stderr and "${1}" in run.stderr and "plain name" in run.stderr
        return
    assert run.returncode == 0, run.stderr
    sent = (sandbox / "delivered.txt").read_text()
    asked = heavy == "yes" or "--multi-file" in flags
    assert sent.count("$ralplan") == int(asked)
    assert sent.startswith("$ralplan\n") == asked


def test_codex_dispatch_refuses_a_missing_planning_mention(sandbox):
    run = dispatch(sandbox, "codex", "yes", NO_MENTIONS="1")
    assert run.returncode != 0
    assert "the planning line is missing" in run.stderr
    assert calls(sandbox) == []


@pytest.mark.parametrize(
    "line,ok",
    [
        ("Reference `$lead`, this repository's skill.", False),
        ("See [$alias](.agents/skills/lead/SKILL.md).", False),
        ("Use `$custom-review`, nested or renamed.", False),
        ("Try `$doctor` even if disabled.", False),
        ('Set "$HOME/bin" first.', False),
        ("```\necho $path\n```", False),
        ("Shell: `$1` is the first argument.", False),
        ("Linked: `[$1](.agents/skills/lead/SKILL.md)`.", False),
        ("Shell: `${HOME}/bin`, `${1}`, `$(pwd)`, end$.", True),
    ],
)
@pytest.mark.parametrize("heavy", ["yes", "no"])
def test_codex_dispatch_refuses_every_token_of_the_rule_and_allows_the_rest(
    sandbox, line, ok, heavy
):
    """No inventory to out-think: every token of the rule refuses; brace/paren/punctuation pass."""
    (sandbox / "task.txt").write_text(f"Build it.\n{line}\n")
    run = dispatch(sandbox, "codex", heavy)
    if ok:
        assert run.returncode == 0, run.stderr
        return
    assert run.returncode != 0 and calls(sandbox) == []
    number = 3 if line.startswith("```") else 2  # In the block: the line after the fence.
    assert f" at line {number}: " in run.stderr


# Orca 1.4.258 `worker-dispatch-preamble.ts:120` (stablyai/orca v1.4.258): the prompt is its
# preamble, a blank line, this marker line, then the spec dispatch.py wrote, unchanged.
TASK_MARKER = "=== TASK ==="


def delivered_prompt(spec: str) -> str:
    preamble = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\n"
    return f"{preamble}\n=== CLI COMMANDS ===\n...\n\n{TASK_MARKER}\n{spec}"


@pytest.mark.parametrize(
    "runtime,heavy,flags,first,rule",
    [
        (
            "codex",
            "no",
            ("--multi-file",),
            "$ralplan",
            "the task text, the lines after Orca's `=== TASK ===` marker, begins with the "
            "planning skill's dollar mention",
        ),
        (
            "claude",
            "yes",
            (),
            "This task runs through `/autopilot --workflow astrolabe-full`",
            "says in the first line of its task text (the line after Orca's `=== TASK ===` "
            "marker) that it runs through `/autopilot --workflow astrolabe-full`",
        ),
    ],
)
def test_the_rules_point_at_the_line_after_orcas_task_marker(
    sandbox, runtime, heavy, flags, first, rule
):
    """Review of 9dbcf16: Orca's preamble comes first, so the prompt's first line is never it."""
    run = dispatch(sandbox, runtime, heavy, *flags)
    assert run.returncode == 0, run.stderr
    prompt = delivered_prompt((sandbox / "delivered.txt").read_text()).split("\n")
    after_marker = prompt[prompt.index(TASK_MARKER) + 1]
    assert after_marker.startswith(first)
    assert not prompt[0].startswith(first)
    rules = " ".join((SCRIPTS.parent / "worker-rules.md").read_text().split())
    assert rule in rules


def test_without_planning_the_line_after_the_marker_is_the_task(sandbox):
    run = dispatch(sandbox, "codex", "no")
    assert run.returncode == 0, run.stderr
    prompt = delivered_prompt((sandbox / "delivered.txt").read_text()).split("\n")
    assert prompt[prompt.index(TASK_MARKER) + 1] == "Build the small fixture."


def test_empty_task_never_launches(sandbox):
    (sandbox / "task.txt").write_text(" \n")
    run = dispatch(sandbox)
    assert run.returncode != 0
    assert calls(sandbox) == []


class FakeClock:
    """A monotonic clock and sleep that advance together; no wall time passes."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeRequests:
    """Result objects with the fields the helper reads."""

    def __init__(
        self,
        *,
        clock=None,
        activity="idle",
        turn_after_polls=None,
        incarnation=TERMINAL_INCARNATION,
        worker_handle="term-worker",
        agent_identity="dsh",
        preamble=START_PREAMBLE,
        fail=None,
        fail_once=None,
        timeout_step=None,
        bad_worker_show=False,
    ):
        self.clock = clock
        self.activity = activity
        self.turn_after_polls = turn_after_polls
        self.incarnation = incarnation
        self.worker_handle = worker_handle
        self.agent_identity = agent_identity
        self.preamble = preamble
        self.fail = fail
        self.fail_once = fail_once
        self.timeout_step = timeout_step
        self.bad_worker_show = bad_worker_show
        self.calls = []
        self.seen = {}
        self.snapshots = 0

    def _raise_if(self, step, timeout):
        self.seen[step] = self.seen.get(step, 0) + 1
        hit = self.fail == step
        if self.fail_once and self.fail_once[0] == step and self.seen[step] <= self.fail_once[1]:
            hit = True
        if not hit:
            return
        if self.timeout_step == step:
            raise subprocess.TimeoutExpired(["orca"], timeout)
        raise ValueError(f"injected {step} failure")

    def __call__(self, args, timeout):
        self.calls.append(
            {"args": list(args), "timeout": timeout, "at": self.clock.now if self.clock else None}
        )
        if args[:2] == ["orchestration", "worker-show"]:
            self._raise_if("worker-show", timeout)
            if self.bad_worker_show:
                return []
            self.snapshots += 1
            activity = self.activity
            if self.turn_after_polls is not None and self.snapshots >= self.turn_after_polls:
                activity = "working"
            return worker_show(activity, self.incarnation, self.worker_handle, self.agent_identity)
        if args[:2] == ["orchestration", "dispatch-show"]:
            self._raise_if("dispatch-show", timeout)
            return {"preamble": self.preamble}
        raise AssertionError(args)


@pytest.fixture
def dispatch_module(sandbox, tmp_path, monkeypatch):
    home = tmp_path.parent / f"{tmp_path.name}-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location(
        "dispatch_observed", sandbox / ".agents/skills/lead/scripts/dispatch.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call_steps(fake):
    return [call["args"][:2] for call in fake.calls]


def start(module, fake, clock):
    return module.observe_deepseek_start(
        START_RECEIPT, request=fake, clock=clock, sleep=clock.sleep
    )


def test_a_started_activity_is_started(dispatch_module):
    for activity in ("working", "monitoring", "waiting", "blocked", "done"):
        clock = FakeClock()
        fake = FakeRequests(clock=clock, activity=activity)
        result = start(dispatch_module, fake, clock)
        assert result.outcome == "started", activity
        assert call_steps(fake) == [["orchestration", "worker-show"]]
        assert clock.sleeps == []


def test_a_non_started_activity_ends_not_started(dispatch_module):
    for activity in ("idle", "unverifiable", "unknown"):
        clock = FakeClock()
        fake = FakeRequests(clock=clock, activity=activity)
        result = start(dispatch_module, fake, clock)
        assert result.outcome == "not_started", activity
        assert activity in result.reason
        assert call_steps(fake).count(["orchestration", "dispatch-show"]) == 1
        assert clock.now == 60


def test_a_first_turn_seen_late_inside_the_bound(dispatch_module):
    clock = FakeClock()
    fake = FakeRequests(clock=clock, turn_after_polls=5)
    result = start(dispatch_module, fake, clock)
    assert result.outcome == "started"
    assert [call["at"] for call in fake.calls] == [0, 2, 4, 6, 8]


def test_start_bound_is_respected_with_a_fake_clock(dispatch_module):
    clock = FakeClock()
    fake = FakeRequests(clock=clock)
    result = start(dispatch_module, fake, clock)
    assert result.outcome == "not_started"
    polls = [
        call["at"] for call in fake.calls if call["args"][:2] == ["orchestration", "worker-show"]
    ]
    assert polls == list(range(0, 60, 2))
    assert clock.now == 60
    assert clock.sleeps == [2] * 30
    assert call_steps(fake)[-1] == ["orchestration", "dispatch-show"]


def test_a_failed_poll_does_not_end_the_watch(dispatch_module):
    clock = FakeClock()
    fake = FakeRequests(clock=clock, fail_once=("worker-show", 1))
    result = start(dispatch_module, fake, clock)
    assert result.outcome == "not_started"
    polls = [c for c in fake.calls if c["args"][:2] == ["orchestration", "worker-show"]]
    assert len(polls) == 30
    assert clock.now == 60


def test_all_failed_polls_end_not_started_with_the_commands(dispatch_module):
    clock = FakeClock()
    fake = FakeRequests(clock=clock, fail="worker-show")
    result = start(dispatch_module, fake, clock)
    assert result.outcome == "not_started"
    assert "could not be read" in result.reason
    lines = dispatch_module.describe_start(result, "orca")
    assert any(line.startswith("NOT STARTED:") for line in lines)
    assert any("terminal read --terminal term-worker --screen" in line for line in lines)
    assert any("dispatch-show --task task-1 --preamble > " in line for line in lines)
    assert any("read the terminal first" in line for line in lines)
    assert (dispatch_module.PREAMBLE_DIR / "task-1.txt").read_text() == START_PREAMBLE


@pytest.mark.parametrize(
    "kwargs",
    [
        {"incarnation": OTHER_INCARNATION},
        {"worker_handle": "other-terminal"},
        {"agent_identity": "claude"},
    ],
)
def test_a_worker_show_that_is_not_the_receipt_terminal_ends_not_started(dispatch_module, kwargs):
    clock = FakeClock()
    fake = FakeRequests(clock=clock, **kwargs)
    result = start(dispatch_module, fake, clock)
    assert result.outcome == "not_started"
    assert "does not match" in result.reason
    assert (dispatch_module.PREAMBLE_DIR / "task-1.txt").exists()


def test_a_non_object_worker_show_ends_not_started(dispatch_module):
    clock = FakeClock()
    fake = FakeRequests(clock=clock, bad_worker_show=True)
    result = start(dispatch_module, fake, clock)
    assert result.outcome == "not_started"
    assert "could not be read" in result.reason


@pytest.mark.parametrize("step", ["worker-show", "dispatch-show"])
@pytest.mark.parametrize("mode", ["failure", "timeout"])
def test_cli_failure_or_timeout_at_each_step_ends_not_started(dispatch_module, step, mode):
    clock = FakeClock()
    fake = FakeRequests(clock=clock, fail=step, timeout_step=step if mode == "timeout" else None)
    result = start(dispatch_module, fake, clock)
    assert result.outcome == "not_started"
    assert result.reason
    lines = dispatch_module.describe_start(result, "orca")
    assert any(line.startswith("NOT STARTED:") for line in lines)
    assert any("terminal read --terminal term-worker --screen" in line for line in lines)
    assert any("dispatch-show --task task-1 --preamble > " in line for line in lines)
    path = dispatch_module.PREAMBLE_DIR / "task-1.txt"
    assert path.exists() == (step == "worker-show")


def test_preamble_file_holds_dispatch_show_output_and_prunes_old_files(
    dispatch_module, monkeypatch
):
    directory = dispatch_module.PREAMBLE_DIR
    directory.mkdir(parents=True)
    old = directory / "task-old.txt"
    old.write_text("old")
    old_time = time.time() - dispatch_module.PREAMBLE_MAX_AGE_SECONDS - 60
    os.utime(old, (old_time, old_time))
    fresh = directory / "task-fresh.txt"
    fresh.write_text("fresh")
    path = dispatch_module.preamble_path("task-1")
    assert path == directory / "task-1.txt"
    dispatch_module.save_preamble(path, START_PREAMBLE)
    assert path.read_text() == START_PREAMBLE
    assert not old.exists()
    assert fresh.exists()


def test_start_outcome_lines(dispatch_module, tmp_path):
    preamble = tmp_path / "task-1.txt"
    started = dispatch_module.StartResult(
        outcome="started", reason="", task_id="task-1", handle="term-worker", preamble=preamble
    )
    assert dispatch_module.describe_start(started, "orca") == ["Start: started (first turn seen)."]
    stopped = dispatch_module.StartResult(
        outcome="not_started",
        reason="no first turn within 60 seconds (activity 'idle')",
        task_id="task-1",
        handle="term-worker",
        preamble=preamble,
    )
    lines = dispatch_module.describe_start(stopped, "orca")
    assert lines[0] == "NOT STARTED: no first turn within 60 seconds (activity 'idle')."
    assert "Do not repeat worker-start: it may already have started." in lines
    assert any("orca terminal read --terminal term-worker --screen" in line for line in lines)
    assert any(
        f"orca orchestration dispatch-show --task task-1 --preamble > {preamble}" in line
        for line in lines
    )
    assert any(
        "read the terminal first" in line
        and "Escape" in line
        and "Your task is in the file" in line
        for line in lines
    )


def test_orca_request_refuses_a_non_object_reply(dispatch_module, monkeypatch):
    monkeypatch.setattr(dispatch_module, "run", lambda *args, **kwargs: "[]")
    with pytest.raises(ValueError):
        dispatch_module.orca_request("orca", ["orchestration", "worker-show"], 5)


def test_orca_request_refuses_a_refused_reply(dispatch_module, monkeypatch):
    monkeypatch.setattr(
        dispatch_module, "run", lambda *args, **kwargs: '{"ok": false, "result": {}}'
    )
    with pytest.raises(ValueError):
        dispatch_module.orca_request("orca", ["orchestration", "worker-show"], 5)


def test_orca_request_keeps_a_failed_command(dispatch_module, monkeypatch):
    def fake_run(command, *, text=None, timeout=None):
        raise ValueError("command failed")

    monkeypatch.setattr(dispatch_module, "run", fake_run)
    with pytest.raises(ValueError):
        dispatch_module.orca_request("orca", ["orchestration", "worker-show"], 5)


def test_orca_request_passes_the_timeout(dispatch_module, monkeypatch):
    seen = {}

    def fake_run(command, *, text=None, timeout=None):
        seen["timeout"] = timeout
        return '{"ok": true, "result": {}}'

    monkeypatch.setattr(dispatch_module, "run", fake_run)
    assert dispatch_module.orca_request("orca", ["orchestration", "worker-show"], 7) == {}
    assert seen["timeout"] == 7


def test_deepseek_is_refused_as_a_builder_before_orca(sandbox):
    """DeepSeek Harness's completion gate works only in meridian checkouts today: one clear
    sentence, and no worker, registration or scan."""
    run = dispatch(sandbox, "deepseek")
    assert run.returncode != 0
    assert run.stderr.strip().splitlines() == [
        "REFUSED: deepseek is not a builder here: DeepSeek Harness's completion gate works "
        "only in meridian checkouts today; start a claude or codex worker"
    ]
    assert calls(sandbox) == []
    assert not (sandbox / "registered.json").exists()
    assert not (sandbox / "scans.jsonl").exists()


def test_the_claude_model_comes_from_models_toml(sandbox):
    models = sandbox / ".agents/skills/lead/models.toml"
    models.write_text(models.read_text().replace("claude-opus-5-5", "claude-test-1"))
    run = dispatch(sandbox, "claude")
    assert run.returncode == 0, run.stderr
    start = calls(sandbox)[0]
    assert start[start.index("--model") + 1] == "claude-test-1"


def test_skill_step_2_states_the_deepseek_refusal():
    step = next(
        line
        for line in (SCRIPTS.parent / "SKILL.md").read_text().splitlines()
        if line.startswith("2. **Dispatch.**")
    )
    assert "refuses `deepseek` as a builder" in step
    assert "do not repeat worker-start" in step


def test_the_dsh_start_lessons_name_existing_tests():
    records = [
        json.loads(line)
        for line in (REPO_ROOT / "docs/learnings.jsonl").read_text().splitlines()
        if "dsh-start-observed" in line
    ]
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-n",
            "0",
            "tests/test_lead_dispatch.py",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert collected.returncode == 0, collected.stderr
    node_ids = {
        line.strip()
        for line in collected.stdout.splitlines()
        if line.strip().startswith("tests/test_lead_dispatch.py::")
    }
    assert node_ids
    for record in records:
        if record.get("kind") != "lesson":
            continue
        path = None
        for part in record["where"].split(" and "):
            part = part.strip()
            if "::" in part:
                named_path, _, name = part.partition("::")
                if named_path:
                    path = named_path
            else:
                name = part.lstrip(":")
            assert path and name, record["id"]
            assert f"{path}::{name}" in node_ids, (record["id"], name)
