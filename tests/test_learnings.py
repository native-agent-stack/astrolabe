"""The register of learnings and its command, offline: one test per job, and the real register.

Ported from meridian@014f706d:tests/test_learnings.py; changed: the register is
docs/learnings.jsonl and starts empty, and the cases that read meridian's ADRs and lessons go.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from git_scratch import REPO_ROOT

SCRIPT = REPO_ROOT / ".agents/skills/lead/scripts/learnings.py"
GATE = SCRIPT.with_name("gate.py")
REGISTER = REPO_ROOT / "docs/learnings.jsonl"
# An IBKR account id (meridian@014f706d:tests/test_public_tree.py ACCOUNT_ID, the same pattern).
ACCOUNT_ID = re.compile(r"(?<![A-Za-z0-9])[DUFI][A-Z]{0,2}[0-9]{5,9}(?![A-Za-z0-9])")
_spec = importlib.util.spec_from_file_location("learnings", SCRIPT)
assert _spec is not None and _spec.loader is not None
learnings = importlib.util.module_from_spec(_spec)
_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no __pycache__ in the skill
try:
    _spec.loader.exec_module(learnings)
finally:
    sys.dont_write_bytecode = _bytecode
# The gate's own reviewer table is the source the script must not drift from; it reads
# models.toml at import, so it is loaded here rather than by learnings.py at runtime.
_gate_spec = importlib.util.spec_from_file_location("gate", GATE)
assert _gate_spec is not None and _gate_spec.loader is not None
gate = importlib.util.module_from_spec(_gate_spec)
_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no __pycache__ in the skill
try:
    _gate_spec.loader.exec_module(gate)
finally:
    sys.dont_write_bytecode = _bytecode


@pytest.fixture
def run(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    register, cites = tmp_path / "learnings.jsonl", tmp_path / "cites.jsonl"

    def call(*argv: str, today: str = "2026-11-14") -> tuple[int, str, str]:
        base = ["--register", str(register), "--cites", str(cites), "--today", today]
        code = learnings.main([*base, *argv])
        out = capsys.readouterr()
        return code, out.out, out.err

    call.register = register  # type: ignore[attr-defined]
    call.cites = cites  # type: ignore[attr-defined]
    return call


def add(run, branch="me/alpha", cls="bound-breach", area="astrolabe/mover/strategy.py", **kw):
    argv = ["add", "--branch", branch, "--builder", kw.get("builder", "claude"), "--class", cls]
    argv += ["--area", area, "--lesson", kw.get("lesson", "An inclusive bound holds at the edge.")]
    argv += ["--source", "pr#1 review", "--form", kw.get("form", "none")]
    if kw.get("where"):
        argv += ["--where", kw["where"]]
    if kw.get("pr"):  # landed: its lesson names the pull request, and its landing record too
        argv += ["--pr", str(kw["pr"])]
        today = kw.get("today", "2026-11-14")
        landing = ["--pr", str(kw["pr"]), "--landed", f"{today}T12:00:00Z", "--rounds", "1"]
        run("branch", "--branch", branch, "--builder", kw.get("builder", "claude"), *landing,
            today=today)  # fmt: skip
    if kw.get("reviewer", "codex"):
        argv += ["--reviewer", kw.get("reviewer", "codex"), "--round", str(kw.get("round", 1))]
    code, out, err = run(*argv, today=kw.get("today", "2026-11-14"))
    assert code == 0, err
    return out.strip()


def records(run) -> list[dict]:
    return [json.loads(line) for line in run.register.read_text().splitlines()]


def test_add_appends_a_lesson_with_an_id_that_parallel_branches_cannot_collide_on(run):
    first, second = add(run), add(run)
    assert (first, second) == ("2026-11-14-alpha-1", "2026-11-14-alpha-2")
    assert add(run, branch="other/beta") == "2026-11-14-beta-1"
    saved = records(run)
    assert [r["branch"] for r in saved] == ["alpha", "alpha", "beta"]  # no login prefix
    assert all(learnings.problems(r) == [] for r in saved)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (["--class", "unclassified"], "class is unclassified"),
        (
            ["--lesson", "The lock under /ho" + "me/someone/.cache was stale."],
            "local or absolute path",
        ),
        (["--lesson", "The file at ~/.cache/x was stale."], "local or absolute path"),
        (["--area", "/etc/passwd"], "repository-relative"),
        (["--lesson", "two lines\nhere."], "one sentence"),
        (["--form", "test"], "says where it lives"),
        (["--area", "UNKNOWN"], "repository-relative"),
    ],
)
def test_add_refuses_an_unconfirmed_or_private_record_and_writes_nothing(run, change, expected):
    argv = ["add", "--branch", "b", "--builder", "codex", "--class", "doc-drift", "--source", "s"]
    argv += ["--area", "docs/x.md", "--lesson", "A sentence about the docs."]
    code, _, err = run(*argv, *change)
    assert code == 2 and expected in err
    assert not run.register.exists()


def test_mark_appends_a_status_and_never_rewrites_a_line(run):
    first, lesson, last = add(run), add(run), add(run)
    before = run.register.read_text().splitlines()
    assert run("mark", lesson, "--status", "promoted")[0] == 2  # promotion needs a form
    code, out, _ = run("mark", lesson, "--status", "promoted", "--form", "test", "--where", "t::x")
    assert code == 0 and out.strip() == f"{lesson}: promoted"
    after = run.register.read_text().splitlines()
    assert after[:3] == before and len(after) == 4  # appended, so a union merge keeps both
    folded = {r["id"]: r for r in learnings.lessons(learnings.load(run.register))}
    assert (folded[lesson]["status"], folded[lesson]["where"]) == ("promoted", "t::x")
    assert folded[first]["status"] == folded[last]["status"] == "open"
    assert run("mark", "nope", "--status", "retired")[0] == 2


def test_branch_is_recorded_once(run):
    assert run("branch", "--branch", "me/alpha", "--builder", "deepseek")[1].strip() == "alpha"
    assert "already recorded" in run("branch", "--branch", "alpha", "--builder", "deepseek")[1]
    assert records(run) == [
        {
            "kind": "branch",
            "branch": "alpha",
            "date": "2026-11-14",
            "pr": None,
            "landed": None,
            "rounds": None,
            "builder": "deepseek",
        }
    ]


FINDINGS = [
    {"file": "astrolabe/a.py", "line": 3, "summary": "A bound is off by one. More.", "x": "x"},
    {"file": "deploy/b.sh", "line": 9, "summary": "A failure counts as a run.", "x": "y"},
]
GATE_OUTPUT = (
    "review record: (local path)\n"
    "LEAD ACTION: me/alpha: review failed: unresolved review findings for lead triage:\n"
    + "".join(f"{n}. {json.dumps(f)}\n" for n, f in enumerate(FINDINGS, 1))
    + "Lead: verify candidates.\n"
)


def test_draft_prints_one_unclassified_add_per_open_finding(run, tmp_path):
    out = tmp_path / "auto.log"
    out.write_text(GATE_OUTPUT)
    argv = ["draft", str(out), "--branch", "me/alpha", "--builder", "claude", "--reviewer", "codex"]
    code, printed, _ = run(*argv, "--pr", "7", "--round", "2")
    lines = printed.splitlines()
    assert code == 0 and len(lines) == 2
    assert lines[0].startswith("uv run python .agents/skills/lead/scripts/learnings.py add ")
    assert "--pr 7" in lines[0] and "--round 2" in lines[0] and "--class unclassified" in lines[0]
    assert "--area astrolabe/a.py" in lines[0] and "'A bound is off by one.'" in lines[0]
    assert not run.register.exists()  # a draft writes nothing


def test_draft_accepts_deepseek_as_the_reviewer_of_a_claude_branch(run, tmp_path):
    out = tmp_path / "auto.log"
    out.write_text(GATE_OUTPUT)
    argv = ["draft", str(out), "--branch", "me/alpha", "--builder", "claude"]
    code, printed, err = run(*argv, "--reviewer", "deepseek")
    assert code == 0, err
    assert "--reviewer deepseek" in printed and "--builder claude" in printed
    add(run, builder="claude", reviewer="deepseek")  # the row the draft names is accepted too
    assert records(run)[-1]["reviewer"] == "deepseek"


def test_an_unknown_reviewer_is_still_refused(run, tmp_path):
    out = tmp_path / "auto.log"
    out.write_text(GATE_OUTPUT)
    argv = ["draft", str(out), "--branch", "me/alpha", "--builder", "claude"]
    with pytest.raises(SystemExit) as refused:
        run(*argv, "--reviewer", "llama")
    assert refused.value.code == 2
    assert not run.register.exists()
    record = {
        "kind": "lesson",
        "id": "2026-11-14-b-1",
        "date": "2026-11-14",
        "pr": None,
        "branch": "b",
        "builder": "claude",
        "reviewer": "llama",
        "round": 1,
        "class": "doc-drift",
        "areas": ["docs/x.md"],
        "form": "none",
        "where": "",
        "lesson": "A doc says what the tree does not.",
        "status": "open",
        "source": "pr#1 review",
    }
    assert "reviewer must be one of" in "; ".join(learnings.problems(record))


def test_draft_refuses_a_malformed_review_record(run, tmp_path):
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"findings": ["not an object"]}))
    argv = ["draft", str(review), "--branch", "b", "--builder", "codex", "--reviewer", "claude"]
    assert run(*argv)[0] == 2


def test_draft_skips_findings_the_lead_closed_as_wrong(run, tmp_path):
    record = {
        "findings": [
            {"text": json.dumps({"file": "x.py", "summary": "Real."}), "status": "open"},
            {"text": json.dumps({"file": "y.py", "summary": "Wrong."}), "status": "closed: no"},
            {"text": json.dumps({"file": "z.py", "summary": "Refuted."}), "status": "refuted"},
            {"text": json.dumps({"file": "w.py", "summary": "Odd."}), "status": "refuted?"},
        ]
    }
    review = tmp_path / "review.json"
    review.write_text(json.dumps(record))
    argv = ["draft", str(review), "--branch", "b", "--builder", "codex", "--reviewer", "claude"]
    printed = run(*argv)[1].splitlines()  # only the gate's exact `refuted` needs no lesson
    assert len(printed) == 2 and "x.py" in printed[0] and "w.py" in printed[1]


def test_paths_prints_matching_lessons_newest_first_capped_and_logs_cites(run):
    old = add(run, today="2026-11-01", lesson="The oldest lesson here.")
    mid = add(run, today="2026-11-05", lesson="The middle lesson here.", area="astrolabe/mover/*")
    new = add(run, today="2026-11-10", lesson="The newest lesson here.")
    add(run, area="deploy/systemd/lane.service", lesson="An unrelated lesson.")
    retired = add(run, today="2026-11-12", lesson="A retired lesson here.")
    run("mark", retired, "--status", "retired")
    code, out, _ = run("paths", "astrolabe/mover", "--limit", "2", "--cite", "task_1")
    assert code == 0
    assert [line.split()[0] for line in out.splitlines()] == [new, mid]
    assert old not in out and retired not in out
    cites = [json.loads(line) for line in run.cites.read_text().splitlines()]
    assert [c["id"] for c in cites] == [new, mid] and cites[0]["task"] == "task_1"
    assert run("paths", "astrolabe/mover/strategy.py")[1].count("\n") == 3


def test_due_follows_the_promotion_ladder(run):
    add(run)
    assert "due:" not in run("due")[1]
    add(run, area="astrolabe/mover/rank.py")  # same class, same area: the second occurrence
    assert "-> test or rule" in run("due")[1]
    add(run, form="test", where="tests/test_x.py::test_bound")  # third, one is now a test
    printed = run("due")[1]
    assert "bound-breach astrolabe/mover: 3 lessons" in printed
    assert "-> skill or gate check" in printed
    add(run, form="check", where="gate.py check")
    assert "due:" not in run("due")[1]
    assert "classes: bound-breach 4" in run("due")[1]


def test_due_groups_by_code_area_not_by_the_tests_folder(run):
    add(run, area="astrolabe/a/x.py", lesson="A lesson about code a.")
    argv = ["add", "--branch", "b", "--builder", "codex", "--class", "bound-breach"]
    argv += ["--area", "astrolabe/b/y.py", "--area", "tests/test_y.py"]
    assert run(*argv, "--lesson", "About code b.", "--source", "s")[0] == 0
    add(run, area="tests/test_z.py", lesson="A lesson about a test only.")
    printed = run("due")[1]
    assert "due:" not in printed  # three lessons, three areas: nothing recurs


def test_due_measures_recurrence_within_thirty_days(run):
    add(run, branch="a", today="2026-10-01", pr=1)
    add(run, branch="b", today="2026-10-20", pr=2)  # recurs 19 days after a
    add(run, branch="c", today="2026-12-15", pr=3)  # 56 days after b: not a recurrence
    add(run, branch="d", today="2026-12-16", pr=4, cls="doc-drift")  # other class
    add(run, branch="e", today="2026-12-17", pr=5, reviewer=None)  # not a review finding
    assert "recurrence within 30 days: 1 of 4 review lessons" in run("due")[1]


def test_recurrence_counts_only_another_branch_that_landed_before(run):
    argv = ["--builder", "codex", "--class", "doc-drift", "--area", "docs/x.md", "--source", "s"]
    lesson = ["--lesson", "A doc says what the tree does not."]
    for branch, pr, today in (("x", "5", "2026-11-10"), ("y", "3", "2026-11-12")):
        assert run("add", "--branch", branch, "--pr", pr, *argv, "--reviewer", "claude", *lesson,
                   today=today)[0] == 0  # fmt: skip
    assert run("add", "--branch", "y", "--pr", "3", *argv, "--reviewer", "claude", *lesson,
               today="2026-11-13")[0] == 0  # fmt: skip
    land(run, "y", 3, "2026-11-12T12:00:00Z", 1)
    land(run, "x", 5, "2026-11-13T12:00:00Z", 1)
    # y landed first (pr 3) and x is dated before y's lessons: nothing recurs; y's own repeat
    # is the same branch, and x's lesson has no earlier-landing match within the window
    assert "recurrence within 30 days: 0 of 3 review lessons" in run("due")[1]


def test_stale_lists_lessons_cited_by_no_task_for_ninety_days(run):
    cited = add(run, today="2026-08-01", lesson="This one was given to a task.")
    idle = add(run, today="2026-08-01", lesson="This one was never given out.", area="deploy/x")
    fresh = add(run, today="2026-11-01", lesson="This one is recent.")
    run("paths", "astrolabe/mover", "--cite", "task_9", today="2026-10-01")
    out = run("stale", today="2026-11-14")[1]
    assert idle in out and cited not in out and fresh not in out
    assert cited in run("stale", today="2027-01-15")[1]


def test_scorecard_counts_branches_findings_classes_and_rounds_per_runtime(run):
    add(run, branch="a", builder="codex", round=1)
    add(run, branch="a", builder="codex", round=2, cls="doc-drift", area="docs/x.md")
    land(run, "a", 1, "2026-11-14T10:00:00Z", 3)
    land(run, "b", 2, "2026-11-14T11:00:00Z", 1)
    run("branch", "--branch", "c", "--builder", "claude")
    out = run("scorecard")[1].splitlines()
    codex = next(line for line in out if line.startswith("codex"))
    claude = next(line for line in out if line.startswith("claude"))
    assert codex.split()[:5] == ["codex", "2", "2", "2", "2/3"]  # a took 3 rounds, b took 1
    assert "bound-breach 1" in codex and "doc-drift 1" in codex
    assert claude.split()[:5] == ["claude", "1", "0", "0", "-"]


def test_the_register_is_valid_public_and_merges_as_a_union():
    saved = learnings.load(REGISTER)  # it starts empty; every line it gains is checked
    for record in saved:
        assert learnings.problems(record) == [], record
    ids = [r["id"] for r in saved if r["kind"] == "lesson"]
    assert len(ids) == len(set(ids))
    assert not ACCOUNT_ID.search(REGISTER.read_text(encoding="utf-8"))
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "docs/learnings.jsonl merge=union" in attributes


def test_the_script_reviews_with_every_family_the_gate_may_assign():
    """One family list: a reviewer added to the gate's table is accepted by the lessons script."""
    allowed = {family for families in gate.REVIEWERS.values() for family in families}
    assert set(learnings.REVIEWERS) == allowed


def test_a_branch_recorded_at_dispatch_acquires_its_pr_later(run):
    assert run("branch", "--branch", "me/alpha", "--builder", "codex")[0] == 0  # pr unknown yet
    out = land(run, "alpha", 100, "2026-11-14T20:00:00Z", 2)
    assert "landed, pr 100" in out
    order = learnings.landing_order(learnings.load(run.register))
    assert order["alpha"][:2] == (False, "2026-11-14T20:00:00Z")  # landed, at its merge time
    assert "already recorded" in run("branch", "--branch", "alpha", "--builder", "codex")[1]
    assert "already recorded" in land(run, "alpha", 100, "2026-11-14T20:00:00Z", 2)
    other = ["--pr", "7", "--landed", "2026-11-14T21:00:00Z", "--rounds", "1"]
    assert run("branch", "--branch", "alpha", "--builder", "codex", *other)[0] == 2


def test_unlanded_branches_neither_recur_nor_displace_landed_ones(run, monkeypatch):
    monkeypatch.setattr(learnings, "RECENT_BRANCHES", 2)
    for branch in ("open-b", "open-a"):  # two open branches: same date, class and area
        add(run, branch=branch, today="2026-11-14")
    assert "recurrence within 30 days: 0 of 0 review lessons" in run("due")[1]
    add(run, branch="open-c", pr=None, lesson="A lesson whose pull request is still open.")
    argv = ["add", "--branch", "open-c", "--pr", "50", "--builder", "codex", "--reviewer", "claude"]
    argv += ["--class", "bound-breach", "--area", "astrolabe/mover/strategy.py"]
    assert run(*argv, "--lesson", "A lesson naming its open pull request.", "--source", "s")[0] == 0
    assert "0 of 0 review lessons" in run("due")[1]  # a lesson's pr alone is not a landing
    add(run, branch="landed-a", pr=1)
    add(run, branch="landed-b", pr=2)
    # the window holds the two landed branches; landed-b repeats landed-a
    assert "recurrence within 30 days: 1 of 2 review lessons" in run("due")[1]


def test_a_directory_area_and_a_file_inside_it_group_together(run):
    add(run, area="astrolabe/mover", lesson="A lesson about the whole folder.")
    add(run, area="astrolabe/mover/strategy.py", lesson="A lesson about one file in it.")
    assert "due: bound-breach astrolabe/mover: 2 lessons" in run("due")[1]
    assert learnings.area_key("astrolabe/alpaca/*.py") == "astrolabe/alpaca"
    assert learnings.area_key("UPSTREAM.md") == "UPSTREAM.md"  # root files stay apart
    assert learnings.area_key(".agents/skills/lead/scripts") == ".agents/skills/lead/scripts"


def land(run, branch: str, pr: int, landed: str, rounds: int, builder: str = "codex"):
    argv = ["branch", "--branch", branch, "--builder", builder, "--pr", str(pr)]
    code, out, err = run(*argv, "--landed", landed, "--rounds", str(rounds))
    assert code == 0, err
    return out


def test_recurrence_follows_the_merge_order_not_the_pr_numbers(run):
    argv = ["--builder", "codex", "--reviewer", "claude", "--class", "doc-drift", "--source", "s"]
    argv += ["--area", "docs/x.md", "--lesson", "A doc says what the tree does not."]
    assert run("add", "--branch", "late", "--pr", "101", *argv, today="2026-11-14")[0] == 0
    assert run("add", "--branch", "early", "--pr", "102", *argv, today="2026-11-13")[0] == 0
    land(run, "early", 102, "2026-11-14T10:00:00Z", 1)  # 102 merges first
    land(run, "late", 101, "2026-11-14T12:00:00Z", 2)  # 101 lands after it, repeating it
    order = learnings.landing_order(learnings.load(run.register))
    assert order["early"] < order["late"]
    assert "recurrence within 30 days: 1 of 2 review lessons" in run("due")[1]


def test_rounds_to_land_count_only_landed_branches_and_their_recorded_rounds(run):
    run("branch", "--branch", "just-dispatched", "--builder", "claude")
    add(run, branch="still-open", builder="claude", round=2)  # a round-2 finding, not landed
    out = run("scorecard")[1].splitlines()
    claude = next(line for line in out if line.startswith("claude"))
    assert claude.split()[:5] == ["claude", "2", "0", "1", "-"]  # no landing: no rounds
    land(run, "still-open", 7, "2026-11-15T09:00:00Z", 3, builder="claude")
    claude = next(line for line in run("scorecard")[1].splitlines() if line.startswith("claude"))
    assert claude.split()[:5] == ["claude", "2", "1", "1", "3/3"]  # the recorded rounds
    assert run("branch", "--branch", "x", "--builder", "claude", "--pr", "9")[0] == 2  # partial
