# ruff: noqa: F811 (the fixtures imported from test_lead_gate are parameters here)
"""The gate's review converges (`.agents/skills/lead/scripts/gate.py`), offline, fake reviewers.

A later round reviews the fix since the last reviewed head and answers each earlier finding; from
a branch's second stop on, P3-only findings are recorded (the published record and the local
follow-up ledger) instead of stopping; every command ends in one typed result line.
Fixtures and fakes are test_lead_gate.py's.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_lead_gate import (  # noqa: F401 (the fixtures are parameters here)
    FakeGh,
    any_body,
    commit,
    completed_listing,
    earlier_in,
    fake_ocr,
    fake_reviewer,
    flock_available,
    gate,
    gate_processes,
    is_fix,
    make,
    move_main,
    no_gate_temp_leaks,
    no_installed_ocr,
    prompt_of,
    push_main,
    repo,
    reserve,
    review_output,
    sh,
    verdict_output,
    wt,
)

LINES = [f"line {n}\n" for n in range(1, 21)]


def first_round(repo: Path, monkeypatch: pytest.MonkeyPatch, severity: str = "P2") -> Any:
    """main holds m.txt; the branch changes its line 1; round 1 confirms one finding."""
    push_main(repo, {"m.txt": "".join(LINES)})
    g, fake = make(repo, ["pass"])
    assert g.trial("feature")  # the branch has main's m.txt
    commit(wt(repo, "feature"), "m.txt", "".join(["branch's line 1\n", *LINES[1:]]), "edit 1")
    assert g.trial("feature")
    fake_reviewer(
        monkeypatch,
        review_output("defect A", severity=severity),
        verdicts=verdict_output("confirmed"),
    )
    with pytest.raises(gate.ReviewPending):
        g.review("feature", "codex")
    return g, fake


def fix_commit(repo: Path, line: int = 10) -> None:
    """The worker's fix: line LINE of m.txt, on top of whatever the branch holds now."""
    path = wt(repo, "feature") / "m.txt"
    text = path.read_text().splitlines(keepends=True)
    text[line - 1] = f"fix line {line}\n"
    commit(wt(repo, "feature"), "m.txt", "".join(text), "fix")


def record_of(g: Any) -> dict:
    return json.loads(g.review_file("feature").read_text())


def test_a_second_round_reviews_only_the_fix_past_a_merge_of_main_in_the_same_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    first = g.state("feature")["tested"]
    # main moves in the same file the branch changes; then the worker fixes another line
    push_main(repo, {"m.txt": "".join([*LINES[:-1], "main's line 20\n"])})
    fix_commit(repo)
    assert g.trial("feature")  # merges the moved main on the branch
    assert "main's line 20" in (wt(repo, "feature") / "m.txt").read_text()
    calls = fake_reviewer(monkeypatch, review_output(), earlier="still")
    with pytest.raises(gate.ReviewPending, match="defect A") as pending:
        g.review("feature", "codex")
    assert pending.value.open == {"P2": 1}
    args, options = calls[0]
    assert is_fix(args) and gate.FIX_SCOPE in prompt_of(calls[0])
    diff = options["input"].split("=== DIFF", 1)[1]
    assert "+fix line 10" in diff  # the fix
    assert "main's line 20" not in diff  # main's own change, also in m.txt, is left out
    assert "branch's line 1" not in diff  # the change round 1 reviewed
    assert [e["summary"] for e in earlier_in(options["input"])] == ["defect A"]
    record = record_of(g)
    assert record["round"] == 2 and record["scope"]["kind"] == "fix"
    assert record["scope"]["since"] == first
    assert [(e["answer"], e["round"]) for e in record["earlier"]] == [("still", 1)]
    (carried,) = record["findings"]
    assert "defect A" in carried["text"] and carried["found_by"] == "earlier"
    assert (carried["round"], carried["status"], carried["verdict"]) == (1, "open", "confirmed")
    assert [(r["round"], r["tested"], r["stopped"]) for r in record["rounds"]] == [(1, first, True)]


def test_a_resolved_earlier_finding_is_gone_and_a_changed_one_stays_open(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    fake_reviewer(monkeypatch, review_output(), earlier="resolved")
    assert g.review("feature", "codex")  # nothing left open: no stop
    record = record_of(g)
    assert record["findings"] == [] and record["earlier"][0]["answer"] == "resolved"
    assert record["stopped"] is False
    fix_commit(repo, 11)
    assert g.trial("feature")
    fake_reviewer(monkeypatch, review_output(), earlier="changed")
    assert g.review("feature", "codex")  # round 2 left nothing open, so nothing to check
    assert record_of(g)["round"] == 3 and record_of(g)["earlier"] == []


def test_a_changed_answer_keeps_the_finding_open_and_undecided(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    fake_reviewer(monkeypatch, review_output(), earlier="changed")
    with pytest.raises(gate.ReviewPending, match="defect A"):
        g.review("feature", "codex")
    (carried,) = record_of(g)["findings"]
    assert (carried["status"], carried["verdict"]) == ("open", "undecided")
    assert carried["evidence"].startswith("changed in round 2:")


def test_a_round_without_a_change_of_its_own_asks_no_model_and_keeps_every_finding(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    push_main(repo, {"n.txt": "n\n"})  # only main moves
    assert g.trial("feature")
    calls = fake_reviewer(monkeypatch, review_output("never asked"))
    with pytest.raises(gate.ReviewPending, match="defect A"):
        g.review("feature", "codex")
    assert len(calls) == 0
    record = record_of(g)
    assert record["earlier"][0]["answer"] == "still"
    assert record["passes"][0]["outcome"].startswith("skipped:")


def test_an_earlier_finding_the_lead_closed_is_not_checked_again(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    g.close("feature", [(1, "not reachable from any caller")], "claude")
    fix_commit(repo)
    assert g.trial("feature")
    calls = fake_reviewer(monkeypatch, review_output())
    assert g.review("feature", "codex")
    assert earlier_in(calls[0][1]["input"]) == []


def test_the_forced_full_pass_reviews_the_whole_change(repo: Path, monkeypatch: pytest.MonkeyPatch):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    calls = fake_reviewer(monkeypatch, review_output())
    g.full_review = True
    assert g.review("feature", "codex")
    args, options = calls[0]
    assert gate.FIX_SCOPE not in prompt_of(calls[0])
    whole = sh(wt(repo, "feature"), "diff", "origin/main...HEAD")
    assert options["input"].split("=== DIFF", 1)[1].count(whole) == 1  # the whole change
    assert "branch's line 1" in whole and "+fix line 10" in whole
    # a later round in full still answers each earlier finding
    assert is_fix(args) and gate.FULL_LATER_SCOPE in prompt_of(calls[0])
    assert [e["summary"] for e in earlier_in(options["input"])] == ["defect A"]
    record = record_of(g)
    assert record["scope"] == {**record["scope"], "kind": "full", "why": "--full-review"}
    assert record["round"] == 2 and [e["answer"] for e in record["earlier"]] == ["resolved"]


def test_the_cli_option_forces_the_full_pass(repo: Path, monkeypatch: pytest.MonkeyPatch):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    calls = fake_reviewer(monkeypatch, review_output())
    bind(g, monkeypatch)
    assert gate.main(["review", "feature", "--builder", "codex", "--full-review"]) == 0
    assert gate.FULL_LATER_SCOPE in prompt_of(calls[0])


def test_a_branch_without_an_earlier_record_gets_the_full_pass(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = make(repo, ["pass"])
    assert g.trial("feature")
    calls = fake_reviewer(monkeypatch)
    assert g.review("feature", "codex")
    assert not is_fix(calls[0][0])
    record = record_of(g)
    assert (record["round"], record["scope"]["kind"], record["rounds"]) == (1, "full", [])


def test_a_last_head_that_conflicts_with_main_gets_the_full_pass(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    # main changes the branch's own line: merging the last reviewed head conflicts
    push_main(repo, {"m.txt": "".join(["main's line 1\n", *LINES[1:]])})
    worktree = wt(repo, "feature")
    sh(worktree, "fetch", "-q", "origin")
    assert subprocess.run(["git", "-C", str(worktree), "merge", "-q", "origin/main"]).returncode
    (worktree / "m.txt").write_text("".join(["resolved line 1\n", *LINES[1:]]))
    sh(worktree, "add", "m.txt")
    sh(worktree, "commit", "-q", "--no-edit")
    assert g.trial("feature")
    calls = fake_reviewer(monkeypatch, review_output(), earlier="still")
    with pytest.raises(gate.ReviewPending, match="defect A"):  # still answered, still there
        g.review("feature", "codex")
    assert gate.FULL_LATER_SCOPE in prompt_of(calls[0])
    assert "does not merge cleanly" in record_of(g)["scope"]["why"]


def test_an_answer_missing_for_an_earlier_finding_is_unclassified(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    fake_reviewer(monkeypatch, json.dumps({"findings": [], "earlier": []}))
    with pytest.raises(gate.Refused, match="not one answer per earlier finding"):
        g.review("feature", "codex")
    assert not g.review_file("feature").exists()
    # the chain survives the failed round: the next one still checks round 1's finding
    calls = fake_reviewer(monkeypatch, review_output(), earlier="resolved")
    assert g.review("feature", "codex")
    assert [e["summary"] for e in earlier_in(calls[0][1]["input"])] == ["defect A"]


# --- minor findings stop a landing once --------------------------------------------------------


def test_a_first_stop_with_only_p3_still_stops(repo: Path, monkeypatch: pytest.MonkeyPatch):
    g, _ = make(repo, ["pass"])
    assert g.trial("feature")
    fake_reviewer(
        monkeypatch, review_output("minor", severity="P3"), verdicts=verdict_output("confirmed")
    )
    with pytest.raises(gate.ReviewPending, match="minor") as pending:
        g.review("feature", "codex")
    assert pending.value.open == {"P3": 1}
    assert record_of(g)["findings"][0]["status"] == "open"
    assert gate.MINOR_HEADING not in record_of(g)


def test_a_p2_in_a_second_stop_still_stops(repo: Path, monkeypatch: pytest.MonkeyPatch):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    fake_reviewer(
        monkeypatch,
        review_output("new minor", severity="P3")[:-2]
        + ', {"file": "f.py", "line": 2, "severity": "P2", "summary": "new P2", '
        '"failure_scenario": "x"}]}',
        verdicts=verdict_output("confirmed", "confirmed"),
    )
    with pytest.raises(gate.ReviewPending, match="new P2") as pending:
        g.review("feature", "codex")
    assert pending.value.open == {"P2": 1, "P3": 1}
    assert {f["status"] for f in record_of(g)["findings"]} == {"open"}


def posting(g: Any, fake: Any) -> list[str]:
    """The review records the gate posts on the pull request, in order."""
    posted: list[str] = []

    def gh(args):
        if args[:2] == ["pr", "comment"]:
            posted.append(Path(args[args.index("--body-file") + 1]).read_text())
        return fake(args)

    g.gh = gh
    return posted


def test_a_p3_only_second_stop_lands_with_the_record_and_the_ledger(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    g, fake = first_round(repo, monkeypatch)  # stop 1: a P2
    fix_commit(repo)
    body = tmp_path / "body"
    body.write_text("body")
    posted = posting(g, fake)
    fake_reviewer(
        monkeypatch,
        review_output("a stale name", severity="P3"),
        verdicts=verdict_output("confirmed"),
        earlier="resolved",
    )
    assert g.auto("feature", "codex", "title", body)  # stop 2 would be P3 only: it lands
    assert sum(c[:2] == ["pr", "merge"] for c in fake.calls) == 1
    record = json.loads(posted[-1])
    (recorded,) = record[gate.MINOR_HEADING]
    assert (recorded["summary"], recorded["file"], recorded["line"]) == (
        "a stale name",
        "file.py",
        1,
    )
    assert "ledger" not in recorded and "id" not in recorded  # no ledger id in the record
    assert "f-" not in posted[-1]
    assert [f["status"] for f in record["findings"]] == ["recorded"]
    (line,) = [json.loads(t) for t in (g.home / gate.FOLLOWUPS).read_text().splitlines()]
    assert line["id"].startswith("f-")  # allocated once, at landing, in the ledger
    assert (line["branch"], line["pr"], line["file"], line["line"]) == ("feature", 7, "file.py", 1)
    assert line["summary"] == "a stale name" and line["date"] and line["done"] is None


def test_closures_that_leave_only_p3_past_the_first_stop_record_them(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    g, fake = first_round(repo, monkeypatch)
    fix_commit(repo)
    body = tmp_path / "body"
    body.write_text("body")
    fake_reviewer(
        monkeypatch,
        review_output("a stale name", severity="P3"),
        verdicts=verdict_output("confirmed"),
        earlier="still",  # round 1's P2 is still there: stop 2
    )
    assert not g.auto("feature", "codex", "title", body)
    assert [f["severity"] for f in record_of(g)["findings"]] == ["P2", "P3"]
    g.close("feature", [(1, "the guard at m.txt:1 covers it")], "claude")
    assert [f["status"] for f in record_of(g)["findings"]] == [
        "closed: the guard at m.txt:1 covers it",
        "recorded",
    ]
    assert g.auto("feature", "codex", "title", body)
    assert len((g.home / gate.FOLLOWUPS).read_text().splitlines()) == 1


def test_closures_at_the_first_stop_record_nothing(repo: Path, monkeypatch: pytest.MonkeyPatch):
    g, _ = make(repo, ["pass"])
    assert g.trial("feature")
    fake_reviewer(
        monkeypatch,
        review_output("a P2", "a P3")[:0]
        + json.dumps(
            {
                "findings": [
                    {"file": "f", "line": 1, "severity": s, "summary": s, "failure_scenario": "x"}
                    for s in ("P2", "P3")
                ]
            }
        ),
        verdicts=verdict_output("confirmed", "confirmed"),
    )
    with pytest.raises(gate.ReviewPending):
        g.review("feature", "codex")
    g.close("feature", [(1, "not reachable")], "claude")
    assert record_of(g)["findings"][1]["status"] == "open"


def test_the_ledger_lists_and_marks_done_and_writes_nothing_twice(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    g, _ = make(repo, ["pass"])
    item = {"file": "f.py", "line": 3, "summary": "a stale name"}
    (first,) = g.ledger_add("feature", 7, "a" * 40, [item])
    assert g.ledger_add("feature", 7, "a" * 40, [item]) == [first]  # a retried open
    assert len((g.home / gate.FOLLOWUPS).read_text().splitlines()) == 1
    bind(g, monkeypatch)
    g.say = gate.say_prose
    before = (g.home / gate.FOLLOWUPS).read_text()
    assert gate.main(["followups"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"{first} ") and "feature #7 f.py:3 a stale name" in out[0]
    assert json.loads(out[-1].removeprefix(gate.RESULT_PREFIX))["open_lines"] == 1
    assert (g.home / gate.FOLLOWUPS).read_text() == before  # read-only
    head = sh(repo, "rev-parse", "HEAD")
    assert gate.main(["followup-done", first, "HEAD"]) == 0
    capsys.readouterr()
    (line,) = [json.loads(t) for t in (g.home / gate.FOLLOWUPS).read_text().splitlines()]
    assert line["done"]["commit"] == head
    assert gate.main(["followups"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 1  # only the result line
    assert gate.main(["followups", "--all"]) == 0
    assert f"[done {head[:12]}" in capsys.readouterr().out
    assert gate.main(["followup-done", "f-0000000000", "HEAD"]) == 1
    assert gate.main(["followup-done", first, "no-such-commit"]) == 1


@pytest.mark.parametrize(
    "finding,rounds",
    [
        ({"severity": "P2"}, [{"round": 1, "stopped": True}]),  # only P3 is recorded
        ({"severity": "P3"}, []),  # never at the first stop
        ({"severity": "P3"}, [{"round": 1, "stopped": False}]),
    ],
)
def test_a_forged_recorded_status_is_refused(
    repo: Path, tmp_path: Path, finding: dict, rounds: list
):
    g, _ = make(repo, ["pass"])
    assert g.trial("feature")
    record = {
        "version": 2,
        "builder": "codex",
        "reviewer": "claude",
        "model": "claude-opus-5-5",
        "tested": g.state("feature")["tested"],
        "summary": "s",
        "rounds": rounds,
        "findings": [{"text": "t", "status": "recorded", **finding}],
    }
    with pytest.raises(gate.Refused, match="only a P3 finding after an earlier stop"):
        g.validate_record(g.state("feature"), record)
    record["findings"][0].update(severity="P3")
    record["rounds"] = [{"round": 1, "stopped": True}]
    assert g.validate_record(g.state("feature"), record)


# --- the typed result line ---------------------------------------------------------------------


def results(out: str) -> list[dict]:
    """Every line that starts with the prefix, parsed."""
    return [
        json.loads(line.removeprefix(gate.RESULT_PREFIX))
        for line in out.splitlines()
        if line.startswith(gate.RESULT_PREFIX)
    ]


def ends_in_one_result(out: str, command: str | None) -> dict:
    lines = out.splitlines()
    assert lines and lines[-1].startswith(gate.RESULT_PREFIX), lines[-3:]
    (result,) = results(out)  # exactly one, and it is the last line
    assert result["outcome"] in gate.OUTCOMES and result["command"] == command
    # exactly the fields its outcome declares, and the merge facts once a merge ran
    assert list(result) == list(gate.result_fields(result["outcome"], bool(result.get("merged"))))
    return result


def bind(g: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """main's Gate(Path.cwd()) is G; Gate's own static calls still reach the class."""

    class Bound(gate.Gate):
        def __new__(cls, _path):
            return g

    monkeypatch.setattr(gate, "Gate", Bound)


def cli(g: Any, monkeypatch: pytest.MonkeyPatch, capsys, argv: list[str]) -> tuple[int, dict]:
    bind(g, monkeypatch)
    g.say = gate.say_prose
    capsys.readouterr()
    try:
        code = gate.main(argv)
    except SystemExit as exit_:
        assert isinstance(exit_.code, int)
        code = exit_.code
    out = capsys.readouterr().out
    command = argv[0] if argv and argv[0] in gate.COMMANDS else None
    return code, ends_in_one_result(out, command)


def prepared(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str):
    """A gate, the argv of CASE, and the outcome it must end in."""
    g, fake = make(repo, ["pass"])
    body = tmp_path / "body"
    body.write_text("body")
    auto = ["auto", "feature", "--builder", "codex", "title", str(body)]
    fake_reviewer(monkeypatch)
    if case in {"open", "land"}:
        assert g.trial("feature")
        g.review("feature", "codex")
        if case == "land":
            g.open("feature", "t", body, wait=False)
    if case in {"close", "close refused"}:
        assert g.trial("feature")
        fake_reviewer(monkeypatch, review_output("x"))
        with pytest.raises(gate.ReviewPending):
            g.review("feature", "codex")
    if case == "review lead":
        assert g.trial("feature")
        fake_reviewer(monkeypatch, review_output("x", severity="P1"))
    if case == "review":
        assert g.trial("feature")
    if case == "auto lead":
        fake_reviewer(monkeypatch, review_output("x"))
    if case == "auto worker":
        g.checks = [["test", "-f", "missing.txt"]]
    if case == "auto busy":
        sh(repo, "worktree", "add", "-q", "-b", "second", str(wt(repo, "second")), "feature")
        reserve(g, "second")
    if case == "auto failed":
        monkeypatch.setattr(g, "trial", lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")))
    if case == "trial failed":
        monkeypatch.setattr(g, "trial", lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")))
    if case == "trial waiting":
        monkeypatch.setattr(
            g, "trial", lambda *_a: (_ for _ in ()).throw(gate.TrialTimeout("no slot"))
        )
    if case == "trial busy":
        monkeypatch.setattr(
            g, "trial", lambda *_a: (_ for _ in ()).throw(gate.BranchBusy("running"))
        )
    if case == "land owner":

        def owner_only(*_a, **_k):
            exc = gate.OwnerDecides("the change touches risk/x")
            exc.owner_only = True  # gate-owner-only's class of path
            raise exc

        monkeypatch.setattr(g, "land", owner_only)
    if case == "pending failed":
        monkeypatch.setattr(g, "pending", lambda *_a: (_ for _ in ()).throw(gate.Refused("x")))
    if case == "pending":
        monkeypatch.setattr(g, "pending", lambda *_a: False)
    if case == "pending found":

        def found(*_a):
            g.facts["branches"] = ["feature"]
            return True

        monkeypatch.setattr(g, "pending", found)
    if case.startswith("auto decided") or case.startswith("auto decision"):
        if case != "auto decision unneeded":
            commit(wt(repo, "feature"), "setup.sh", "systemctl status example\n", "operation")
        head = sh(repo, "rev-parse", "feature")
        auto += ["--decided-by", "lead", "--decided-head", head]
        if case == "auto decision changed":
            commit(wt(repo, "feature"), "new.txt", "new worker work\n", "new change")
        if case == "auto decision unrelated":
            auto[-1] = sh(repo, "commit-tree", "feature^{tree}", "-p", "main", "-m", "other")
        if case == "auto decision owner":
            auto[-3] = "owner"
        if case == "auto decided refreshed":
            assert g.trial("feature")
            g.review("feature", "codex")
            move_main(repo)
    if case.startswith("auto refresh"):
        assert g.trial("feature")
        g.review("feature", "codex")
        move_main(repo)
        checks = g._run_checks
        refreshes = []

        def refresh(work, branch):
            assert g._connection is None and flock_available(g)
            refreshes.append(branch)
            if case == "auto refresh paused":
                move_main(repo, f"external-{len(refreshes)}.txt")
            if case == "auto refresh worker":
                return [{"check": "refresh", "passed": False, "last": "bad change"}]
            return checks(work, branch)

        monkeypatch.setattr(g, "_run_checks", refresh)
    if case.startswith("open refresh"):
        assert g.trial("feature")
        g.review("feature", "codex")
        if case == "open refresh stale":
            commit(wt(repo, "feature"), "fix.txt", "worker fix\n", "fix")
            assert g.trial("feature")
        move_main(repo)
        if case == "open refresh worker":
            g.checks = [["test", "-f", "missing.txt"]]
    if case == "land moved":
        assert g.trial("feature")
        g.review("feature", "codex")
        g.open("feature", "t", body, wait=False)
        move_main(repo)
    if case == "auto queue waiting":
        sh(repo, "worktree", "add", "-q", "-b", "second", str(wt(repo, "second")), "feature")
        reserve(g, "second")
        auto += ["--wait-timeout", "0.01"]
    table = {
        "usage": [],
        "unknown command": ["nope"],
        "help": ["--help"],
        "bad builder": ["trial", "feature", "--builder", "gemini"],
        "trial": ["trial", "feature"],
        "trial worker": ["trial", "feature"],
        "trial failed": ["trial", "feature"],
        "trial waiting": ["trial", "feature"],
        "trial busy": ["trial", "feature"],
        "review": ["review", "feature", "--builder", "codex"],
        "review refused": ["review", "feature", "--builder", "codex"],
        "review lead": ["review", "feature", "--builder", "codex"],
        "auto": auto,
        "auto lead": auto,
        "auto worker": auto,
        "auto busy": [*auto, "--no-wait"],
        "auto failed": auto,
        "register": ["register", "feature", "--builder", "codex"],
        "register refused": ["register", "feature", "--builder", "claude"],
        "open": ["open", "feature", "t", str(body)],
        "open refused": ["open", "feature", "t", str(body)],
        "open refresh": ["open", "feature", "t", str(body)],
        "open refresh stale": ["open", "feature", "t", str(body)],
        "open refresh worker": ["open", "feature", "t", str(body)],
        "land moved": ["land", "feature"],
        "land": ["land", "feature"],
        "land refused": ["land", "feature"],
        "land owner": ["land", "feature"],
        "close": ["close", "feature", "1", "not reachable", "--by", "claude"],
        "close refused": ["close", "feature", "9", "not reachable", "--by", "claude"],
        "release": ["release", "feature"],
        "pending": ["pending"],
        "pending found": ["pending"],
        "pending failed": ["pending"],
        "followups": ["followups"],
        "followup-done": ["followup-done", "f-0", "HEAD"],
    }
    table.update(
        {
            name: auto
            for name in EXPECTED
            if name.startswith(("auto decided", "auto decision", "auto refresh", "auto queue"))
        }
    )
    if case == "trial worker":
        g.checks = [["test", "-f", "missing.txt"]]
    return g, fake, table[case], *EXPECTED[case]


EXPECTED = {
    "usage": ("refused", 2),
    "unknown command": ("refused", 2),
    "help": ("done", 0),
    "bad builder": ("refused", 2),
    "trial": ("done", 0),
    "trial worker": ("worker", 1),
    "trial failed": ("failed", 1),
    "trial waiting": ("waiting", 1),
    "trial busy": ("busy", 1),
    "review": ("done", 0),
    "review refused": ("refused", 1),
    "review lead": ("lead", 1),
    "auto": ("landed", 0),
    "auto lead": ("lead", 1),
    "auto worker": ("worker", 1),
    "auto busy": ("busy", 1),
    "auto failed": ("failed", 1),
    "auto decided": ("landed", 0),
    "auto decided refreshed": ("landed", 0),
    "auto decision changed": ("lead", 1),
    "auto decision unrelated": ("lead", 1),
    "auto decision unneeded": ("lead", 1),
    "auto decision owner": ("refused", 2),
    "auto refresh paused": ("paused", 1),
    "auto refresh worker": ("worker", 1),
    "auto queue waiting": ("waiting", 1),
    "register": ("done", 0),
    "register refused": ("refused", 1),
    "open": ("done", 0),
    "open refused": ("refused", 1),
    "open refresh": ("done", 0),
    "open refresh stale": ("refused", 1),
    "open refresh worker": ("worker", 1),
    "land moved": ("paused", 1),
    "land": ("landed", 0),
    "land refused": ("refused", 1),
    "land owner": ("owner", 1),
    "close": ("done", 0),
    "close refused": ("refused", 1),
    "release": ("done", 0),
    "pending": ("idle", 1),
    "pending found": ("pending", 0),
    "pending failed": ("failed", 2),
    "followups": ("done", 0),
    "followup-done": ("refused", 1),
}  # case: the outcome and exit code it must end in
CASES = list(EXPECTED)


@pytest.mark.parametrize("case", CASES)
def test_every_exit_path_of_every_command_ends_in_one_result_line(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, case: str
):
    g, _fake, argv, outcome, code = prepared(repo, tmp_path, monkeypatch, case)
    got, result = cli(g, monkeypatch, capsys, argv)
    assert (result["outcome"], got) == (outcome, code), result.get("reason")
    if code != 2 and argv[0] in set(gate.COMMANDS) - {"pending", "followups", "followup-done"}:
        assert result["branch"] == argv[1]
    if outcome == "landed":
        assert result["pr"] == 7 and len(result["commit"]) == 40
    if case.startswith(("auto refresh", "open refresh")):
        assert g.holder() is None or case == "open refresh"
        assert not g._queue()
    if case == "auto refresh paused":
        assert "outside the landing queue" in result["reason"]
    if case == "review lead":
        assert result["open"] == {"P1": 1} and result["waits_for"] == "lead dispositions"
    if case == "auto lead":
        assert result["step"] == "review" and result["open"] == {"P2": 1}
    if case in {"trial failed", "auto failed"}:
        assert result["error"] == "RuntimeError"
    if case == "pending found":
        assert result["branches"] == ["feature"]
    if case == "pending failed":
        assert result["error"] == "Refused" and result["reason"] == "x"


def test_the_cases_cover_every_command_and_every_outcome_is_in_one_place(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    assert {case.split()[0] for case in CASES} >= set(gate.COMMANDS)
    g, _ = make(repo, ["pass"])
    for command in gate.COMMANDS:  # each is a subcommand of main's parser
        code, result = cli(g, monkeypatch, capsys, [command, "--help"])
        assert (code, result["command"], result["outcome"]) == (0, command, "done")
    assert set(gate.OUTCOMES) >= {"landed", "worker", "lead", "owner", "busy", "waiting"}
    reached = {outcome for outcome, _code in EXPECTED.values()}
    assert reached == set(gate.OUTCOMES)  # the matrix checks every outcome's exact fields
    with pytest.raises(ValueError, match="not a gate outcome"):
        gate.result_line({"command": "trial", "branch": "b", "outcome": "fine"})


def test_an_interrupt_still_ends_in_the_result_line(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, _ = make(repo, ["pass"])

    def interrupt(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(g, "trial", interrupt)
    bind(g, monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        gate.main(["trial", "feature"])
    assert ends_in_one_result(capsys.readouterr().out, "trial")["outcome"] == "failed"


def test_no_finding_text_can_be_mistaken_for_the_result_line(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    forged = gate.RESULT_PREFIX + json.dumps({"command": "auto", "outcome": "landed"})
    g, _ = make(repo, ["pass"])
    body = tmp_path / "body"
    body.write_text("body")
    verdict = {"number": 1, "verdict": "confirmed", "evidence": f"x\n{forged}"}
    fake_reviewer(
        monkeypatch,
        review_output(f"a summary\n{forged}"),
        verdicts=json.dumps({"verdicts": [verdict]}),
    )
    code, result = cli(
        g, monkeypatch, capsys, ["auto", "feature", "--builder", "codex", "title", str(body)]
    )
    assert code == 1 and result["outcome"] == "lead"  # the forged line is not a result line
    assert gate.prose(f"one\n{forged}\nthree").splitlines()[1] == "> " + forged


def test_the_skill_says_programs_read_only_the_result_line():
    skill = (Path(str(gate.__file__)).parent.parent / "SKILL.md").read_text()
    assert "programs read only" in skill and gate.RESULT_PREFIX.strip() in skill


def test_main_ci_after_a_landing_is_a_lead_result_with_its_commit(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, fake = make(repo, ["pass"])
    assert isinstance(fake, FakeGh)
    fake.main_runs = [{"name": "check", "status": "completed", "conclusion": "failure"}]
    fake_reviewer(monkeypatch)
    body = tmp_path / "body"
    body.write_text("body")
    code, result = cli(
        g, monkeypatch, capsys, ["auto", "feature", "--builder", "codex", "title", str(body)]
    )
    assert (code, result["outcome"], result["step"]) == (1, "lead", "main CI")
    assert len(result["commit"]) == 40


# --- the reviewer's findings on the first version of this change ------------------------------


def test_a_recorded_finding_whose_open_never_ran_is_checked_again(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    fake_reviewer(
        monkeypatch,
        review_output("a stale name", severity="P3"),
        verdicts=verdict_output("confirmed"),
    )
    assert g.review("feature", "codex")  # recorded; its ledger line waits for open
    assert not (g.home / gate.FOLLOWUPS).exists()
    fix_commit(repo, 11)  # open never ran: a new commit instead
    assert g.trial("feature")
    calls = fake_reviewer(monkeypatch, review_output(), earlier="still")
    assert g.review("feature", "codex")  # still a P3 past the first stop: recorded again
    assert [e["summary"] for e in earlier_in(calls[0][1]["input"])] == ["a stale name"]
    assert [f["status"] for f in record_of(g)["findings"]] == ["recorded"]


def test_a_new_landing_of_a_landed_branch_starts_a_new_chain(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    g, fake = first_round(repo, monkeypatch)
    g.close("feature", [(1, "not reachable from any caller")], "claude")
    body = tmp_path / "body"
    body.write_text("body")
    fake_reviewer(monkeypatch)
    assert g.auto("feature", "codex", "title", body)  # lands round 1
    commit(wt(repo, "feature"), "later.txt", "later\n", "new work after the landing")
    fake_reviewer(
        monkeypatch, review_output("minor", severity="P3"), verdicts=verdict_output("confirmed")
    )
    assert not g.auto("feature", "codex", "title", body)  # a first stop: P3 stops it
    record = record_of(g)
    assert (record["round"], record["scope"]["kind"], record["rounds"]) == (1, "full", [])
    assert record["findings"][0]["status"] == "open"


def test_a_retired_and_registered_again_branch_starts_a_new_chain(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    fake_reviewer(monkeypatch, review_output(), earlier="resolved")
    assert g.review("feature", "codex")
    assert g.rounds_file("feature").exists()
    g.register("feature", retire="work moved")
    g.register("feature", "codex", "task-2")
    assert not g.rounds_file("feature").exists()


def test_the_ledger_id_is_the_same_on_another_tested_commit(repo: Path):
    g, _ = make(repo, ["pass"])
    item = {"file": "f.py", "line": 3, "summary": "a stale name"}
    assert g.ledger_add("feature", 7, "a" * 40, [item]) == g.ledger_add(
        "feature", 7, "b" * 40, [item]
    )
    assert len((g.home / gate.FOLLOWUPS).read_text().splitlines()) == 1


def test_auto_with_full_review_reviews_again_over_a_record_of_the_same_commit(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = make(repo, ["pass"])
    body = tmp_path / "body"
    body.write_text("body")
    calls = fake_reviewer(monkeypatch, review_output("x"))
    assert not g.auto("feature", "codex", "title", body)
    assert not g.auto("feature", "codex", "title", body)
    assert len(calls) == 1  # the record of the tested commit is reused
    g.full_review = True
    assert not g.auto("feature", "codex", "title", body)
    assert len(calls) == 2
    assert record_of(g)["round"] == 2  # the reviewed commit's round is followed, not replaced


def test_the_second_pass_of_a_fix_round_reads_the_fix(repo: Path, monkeypatch: pytest.MonkeyPatch):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    calls = fake_reviewer(
        monkeypatch, review_output("new"), verdicts=verdict_output("confirmed"), earlier="resolved"
    )
    with pytest.raises(gate.ReviewPending, match="new"):
        g.review("feature", "codex")
    (refute,) = calls.refute
    assert prompt_of(refute) == gate.REFUTE_FIX_PROMPT
    diff = refute[1]["input"].split("=== DIFF", 1)[1]
    assert diff.startswith(" " + gate.FIX_DIFF) and "+fix line 10" in diff
    assert "branch's line 1" not in diff


def test_a_last_reviewed_head_that_is_gone_gets_the_full_pass(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    chain = (
        json.loads(g.rounds_file("feature").read_text())
        if g.rounds_file("feature").exists()
        else []
    )
    record = record_of(g)
    record["tested"] = "0" * 40  # a head no longer in the repository
    g.review_file("feature").write_text(json.dumps(record))
    assert chain == []
    fix_commit(repo)
    assert g.trial("feature")
    calls = fake_reviewer(monkeypatch, review_output(), earlier="resolved")
    assert g.review("feature", "codex")
    assert "is gone" in record_of(g)["scope"]["why"]
    assert gate.FULL_LATER_SCOPE in prompt_of(calls[0])


def test_every_line_separator_is_guarded_in_prose():
    forged = gate.RESULT_PREFIX + "{}"
    for sep in ("\n", "\r", "\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029"):
        parts = gate.prose(f"x{sep}{forged}").splitlines()
        assert not any(part.startswith(gate.RESULT_PREFIX) for part in parts), repr(sep)
    assert "\u2028" not in gate.result_line(
        {"command": None, "branch": "a\u2028b", "outcome": "done"}
    )


def test_each_stop_class_has_its_outcome():
    owner = gate.OwnerDecides("owner path")
    assert gate.classify(owner, "auto") == ("lead", {"waits_for": "land --decided-by owner|lead"})
    owner.owner_only = True  # an owner-only path (gate-owner-only's class)
    assert gate.classify(owner, "land")[0] == "owner"
    assert gate.classify(gate.QueueTimeout("gave up"), "open")[0] == "waiting"
    assert gate.classify(gate.TrialTimeout("no slot"), "trial")[0] == "waiting"
    assert gate.classify(gate.Busy("held"), "open")[0] == "busy"
    assert gate.classify(gate.Refused("no"), "auto")[0] == "lead"
    assert gate.classify(gate.Refused("no"), "open")[0] == "refused"
    assert gate.classify(KeyError("k"), "open")[0] == "failed"


def test_a_land_that_refuses_after_the_merge_is_a_lead_result(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, fake = make(repo, ["pass"])
    assert g.trial("feature")
    fake_reviewer(monkeypatch)
    g.review("feature", "codex")
    body = tmp_path / "body"
    body.write_text("body")
    g.open("feature", "t", body, wait=False)
    fake.after_merge = lambda: push_main(repo, {"z.txt": "z\n"})  # main moves during the merge
    code, result = cli(g, monkeypatch, capsys, ["land", "feature"])
    assert (code, result["outcome"], result["step"]) == (1, "lead", "after the merge")
    assert result["merged"] is True and result["pr"] == 7


# --- the gate's second round on df22612 (Codex, ten confirmed) ---------------------------------


def test_1_a_forced_full_review_of_a_reviewed_commit_answers_its_own_findings(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    fake_reviewer(
        monkeypatch,
        review_output("a known P1", severity="P1"),
        verdicts=verdict_output("confirmed"),
        earlier="resolved",
    )
    with pytest.raises(gate.ReviewPending, match="a known P1"):
        g.review("feature", "codex")  # round 2 at this commit finds a P1
    calls = fake_reviewer(monkeypatch, review_output(), earlier="still")
    g.full_review = True
    with pytest.raises(gate.ReviewPending, match="a known P1"):  # still open: still a stop
        g.review("feature", "codex")
    assert [e["summary"] for e in earlier_in(calls[0][1]["input"])] == ["a known P1"]
    record = record_of(g)
    assert record["round"] == 3 and [r["round"] for r in record["rounds"]] == [1, 2]
    assert [e["answer"] for e in record["earlier"]] == ["still"]


def test_1_a_review_of_a_reviewed_commit_without_the_option_answers_its_findings_too(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)  # round 1 at this commit left defect A open
    calls = fake_reviewer(monkeypatch, review_output(), earlier="still")
    with pytest.raises(gate.ReviewPending, match="defect A"):
        g.review("feature", "codex")
    assert "a new review of the reviewed commit" in record_of(g)["scope"]["why"]
    assert [e["summary"] for e in earlier_in(calls[0][1]["input"])] == ["defect A"]


def test_2_registering_again_after_a_landing_starts_a_new_review_history(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)  # a stop, then closed and landed
    g.close("feature", [(1, "not reachable from any caller")], "claude")
    body = tmp_path / "body"
    body.write_text("body")
    fake_reviewer(monkeypatch)
    assert g.auto("feature", "codex", "title", body)
    g.register("feature", "codex", "task-2")  # a new task on the landed branch
    assert "landed" not in g.registration("feature")
    commit(wt(repo, "feature"), "later.txt", "later\n", "the new task's work")
    assert g.trial("feature")
    fake_reviewer(
        monkeypatch, review_output("minor", severity="P3"), verdicts=verdict_output("confirmed")
    )
    with pytest.raises(gate.ReviewPending, match="minor"):  # a first stop: P3 stops it
        g.review("feature", "codex")
    record = record_of(g)
    assert (record["round"], record["scope"]["kind"], record["rounds"]) == (1, "full", [])
    (past,) = record["previous_landings"]  # kept in the record, not in the count
    assert past["landed"] and [r["stopped"] for r in past["rounds"]] == [True]


def test_3_a_recurrence_after_done_is_a_new_open_line_that_names_the_earlier(repo: Path):
    g, _ = make(repo, ["pass"])
    item = {"file": "f.py", "line": 3, "summary": "a stale name"}
    (first,) = g.ledger_add("feature", 7, "a" * 40, [item])
    g.followup_done(first, "HEAD")
    (again,) = g.ledger_add("feature", 9, "b" * 40, [item])
    assert again != first
    lines = {
        line["id"]: line
        for line in map(json.loads, (g.home / gate.FOLLOWUPS).read_text().splitlines())
    }
    assert lines[again]["done"] is None and lines[again]["recurs"] == first
    assert g.followups() == 1 and [line["id"] for line in g.facts["lines"]] == [again]
    assert g.ledger_add("feature", 9, "c" * 40, [item]) == [again]  # open: nothing twice


def test_4_a_recorded_finding_resolved_before_landing_leaves_no_ledger_line(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    g, fake = first_round(repo, monkeypatch)
    fix_commit(repo)
    body = tmp_path / "body"
    body.write_text("body")
    fake.ci = ["fail"]  # CI is red: the branch does not land
    fake_reviewer(
        monkeypatch,
        review_output("a stale name", severity="P3"),
        verdicts=verdict_output("confirmed"),
        earlier="resolved",
    )
    assert not g.auto("feature", "codex", "title", body)
    assert any(c[:2] == ["pr", "comment"] for c in fake.calls)  # opened, record posted
    assert not (g.home / gate.FOLLOWUPS).exists()  # a branch that has not landed: no line
    fix_commit(repo, 12)
    fake.ci = ["pass"]
    fake_reviewer(monkeypatch, review_output(), earlier="resolved")  # the P3 is resolved
    assert g.auto("feature", "codex", "title", body)
    assert not (g.home / gate.FOLLOWUPS).exists()  # landed without it: no line


def test_5_the_followups_result_line_carries_the_records_bounded(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, _ = make(repo, ["pass"])
    items = [{"file": "f.py", "line": n, "summary": f"name {n}"} for n in range(1, 56)]
    ids = g.ledger_add("feature", 7, "a" * 40, items)
    g.followup_done(ids[0], "HEAD")
    code, result = cli(g, monkeypatch, capsys, ["followups", "--all"])
    assert code == 0 and result["cut"] == 55 - gate.FOLLOWUPS_LISTED
    assert (result["open_lines"], result["done_lines"]) == (54, 1)
    first = result["lines"][0]
    assert set(first) == {
        "id",
        "branch",
        "pr",
        "file",
        "line",
        "summary",
        "state",
        "commit",
        "recurs",
    }
    assert (first["id"], first["state"], first["pr"]) == (ids[0], "done", 7)
    assert first["commit"] == sh(repo, "rev-parse", "HEAD")
    code, result = cli(g, monkeypatch, capsys, ["followup-done", result["lines"][1]["id"], "HEAD"])
    assert (code, result["outcome"], result["id"]) == (0, "done", ids[1])  # from the line alone


def test_6_the_pending_result_line_carries_builder_task_and_waits(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, _ = make(repo, ["pass"])
    completed_listing(monkeypatch)
    code, result = cli(g, monkeypatch, capsys, ["pending"])
    assert (code, result["outcome"]) == (0, "pending")
    assert result["branches"] == [{"branch": "feature", "builder": "codex", "task": "task-1"}]
    assert g.trial("feature")
    fake_reviewer(monkeypatch, review_output("x"))
    with pytest.raises(gate.ReviewPending):
        g.review("feature", "codex")
    completed_listing(monkeypatch)
    code, result = cli(g, monkeypatch, capsys, ["pending"])
    assert (code, result["outcome"]) == (1, "idle")
    assert result["waiting"] == {"feature": "waiting for lead dispositions"}


@pytest.mark.parametrize("error", [AttributeError("shape"), KeyboardInterrupt()])
def test_7_once_merged_every_later_result_line_carries_the_merge(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, error: BaseException
):
    g, _ = make(repo, ["pass"])
    assert g.trial("feature")
    fake_reviewer(monkeypatch)
    g.review("feature", "codex")
    body = tmp_path / "body"
    body.write_text("body")
    g.open("feature", "t", body, wait=False)

    def fail(_commit):
        raise error

    monkeypatch.setattr(g, "watch_main", fail)
    bind(g, monkeypatch)
    g.say = gate.say_prose
    capsys.readouterr()
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            gate.main(["land", "feature"])
    else:
        assert gate.main(["land", "feature"]) == 1
    result = ends_in_one_result(capsys.readouterr().out, "land")
    assert result["outcome"] == "failed" and result["merged"] is True and result["pr"] == 7
    assert result["tested"] == g.state("feature")["tested"]
    assert result["commit"] == sh(repo.parent / "origin.git", "rev-parse", "main")


def test_8_every_outcome_carries_exactly_its_declared_fields():
    for outcome, (_means, fields) in gate.OUTCOMES.items():
        line = gate.result_line({"command": "trial", "branch": "b", "outcome": outcome, "x": 1})
        value = json.loads(line.removeprefix(gate.RESULT_PREFIX))
        assert list(value) == [*gate.RESULT_FIELDS, *fields]  # missing ones null, extras gone
        merged = json.loads(
            gate.result_line({"outcome": outcome, "merged": True}).removeprefix(gate.RESULT_PREFIX)
        )
        assert set(gate.MERGE_FIELDS) <= set(merged)


def test_9_a_claude_fix_round_states_one_scope_in_prompt_and_system_prompt(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    calls = fake_reviewer(
        monkeypatch, review_output("new"), verdicts=verdict_output("confirmed"), earlier="resolved"
    )
    with pytest.raises(gate.ReviewPending):
        g.review("feature", "codex")
    for args, _ in (calls[0], calls.refute[0]):
        assert args[0] == "claude"
        system = args[args.index("--append-system-prompt") + 1]
        assert gate.FIX_REVIEWED in system and "HEAD against origin/main" not in system
        assert "origin/main...HEAD" not in args[2]


def test_10_a_fix_round_asks_ocr_for_the_range_it_reviews(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    push_main(repo, {"m.txt": "".join([*LINES[:-1], "main's line 20\n"])})
    fix_commit(repo)
    assert g.trial("feature")
    log = fake_ocr(g, tmp_path)
    fake_reviewer(monkeypatch, review_output(), earlier="resolved")
    assert g.review("feature", "codex")
    preview = next(json.loads(t)["argv"] for t in log.read_text().splitlines() if '"preview"' in t)
    start, end = preview[preview.index("--from") + 1], preview[preview.index("--to") + 1]
    assert sh(repo, "merge-base", start, end) == start  # ocr's range is start..end
    fix = sh(repo, "diff", start, end)
    assert "+fix line 10" in fix and "main's line 20" not in fix and "branch's line 1" not in fix
    assert sh(repo, "rev-parse", f"{end}^{{tree}}") == g.state("feature")["tree"]


# --- the gate's single pass on de581c9: the result line after a merge --------------------------


@pytest.mark.parametrize("broken", ["a directory", "not JSON"])
def test_a_ledger_that_cannot_be_written_after_the_merge_is_a_named_fact(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, broken: str
):
    g, fake = first_round(repo, monkeypatch)
    fix_commit(repo)
    ledger = g.home / gate.FOLLOWUPS

    def break_ledger():  # after the merge call: the landing has happened
        if broken == "a directory":
            ledger.mkdir()  # an OSError on reading it
        else:
            ledger.write_text("not json\n")  # a ValueError on reading it

    fake.after_merge = break_ledger
    posted = posting(g, fake)
    fake_reviewer(
        monkeypatch,
        review_output("a stale name", severity="P3"),
        verdicts=verdict_output("confirmed"),
        earlier="resolved",
    )
    body = tmp_path / "body"
    body.write_text("body")
    bind(g, monkeypatch)
    g.say = gate.say_prose
    capsys.readouterr()
    code = gate.main(["auto", "feature", "--builder", "codex", "title", str(body)])
    out = capsys.readouterr().out
    result = ends_in_one_result(out, "auto")
    assert (code, result["outcome"]) == (gate.LEDGER_UNWRITTEN, "landed")  # it did land
    assert result["merged"] is True and result["ledger_error"]
    assert "PR #7's published review record (recorded_minor), the durable copy" in out
    assert "not written:" not in out  # no records for appending by hand
    (recorded,) = json.loads(posted[-1])[gate.MINOR_HEADING]  # the durable copy
    assert recorded["summary"] == "a stale name"
    skill = (Path(str(gate.__file__)).parent.parent / "SKILL.md").read_text()
    assert "Exit 3 from `land` or `auto`" in skill and "ledger_error" in skill


def test_a_landing_whose_main_moved_during_the_merge_reports_mains_commit(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, fake = make(repo, ["pass"])
    fake_reviewer(monkeypatch)
    fake.after_merge = lambda: push_main(repo, {"z.txt": "z\n"})  # main moves during the merge
    body = tmp_path / "body"
    body.write_text("body")
    code, result = cli(
        g, monkeypatch, capsys, ["auto", "feature", "--builder", "codex", "title", str(body)]
    )
    assert (code, result["outcome"], result["merged"]) == (1, "lead", True)
    tested = g.state("feature")["tested"]
    assert result["tested"] == tested  # the branch's commit, in its own field
    assert result["commit"] == sh(repo.parent / "origin.git", "rev-parse", "main") != tested


def test_a_landing_whose_fetch_fails_after_the_merge_never_reports_the_branch_as_main(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, fake = make(repo, ["pass"])
    fake_reviewer(monkeypatch)
    real_main = g._main
    merged = []
    fake.after_merge = lambda: merged.append(True)

    def fetch():
        if merged:
            raise gate.Refused("fetch failed")
        return real_main()

    monkeypatch.setattr(g, "_main", fetch)
    body = tmp_path / "body"
    body.write_text("body")
    code, result = cli(
        g, monkeypatch, capsys, ["auto", "feature", "--builder", "codex", "title", str(body)]
    )
    assert (code, result["outcome"], result["merged"]) == (1, "lead", True)
    assert result["tested"] == g.state("feature")["tested"] and result["commit"] is None


def test_a_malformed_ledger_line_is_named_not_a_key_error(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, _ = make(repo, ["pass"])
    (first,) = g.ledger_add("feature", 7, "a" * 40, [{"file": "f", "line": 1, "summary": "s"}])
    ledger = g.home / gate.FOLLOWUPS
    record = json.loads(ledger.read_text())
    del record["date"]
    ledger.write_text(ledger.read_text() + json.dumps(record) + "\n")
    code, result = cli(g, monkeypatch, capsys, ["followups"])
    assert (code, result["outcome"]) == (1, "refused")
    assert "line 2 is not a complete ledger record (missing date)" in result["reason"]
    with pytest.raises(gate.Refused, match="line 2"):
        g.ledger_add("feature", 9, "d" * 40, [{"file": "g", "line": 2, "summary": "t"}])


# --- the gate's single pass on 9662f10: round and recorded from the landed record ---------------


def test_a_land_run_as_its_own_command_reports_the_landed_rounds_facts(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, _ = first_round(repo, monkeypatch)  # stop 1
    fix_commit(repo)
    assert g.trial("feature")
    fake_reviewer(
        monkeypatch,
        review_output("a stale name", severity="P3"),
        verdicts=verdict_output("confirmed"),
        earlier="resolved",
    )
    assert g.review("feature", "codex")  # round 2: one P3 recorded
    body = tmp_path / "body"
    body.write_text("body")
    g.open("feature", "t", body, wait=False)
    code, result = cli(g, monkeypatch, capsys, ["land", "feature"])
    assert (code, result["outcome"]) == (0, "landed")
    assert (result["round"], result["recorded"]) == (2, 1)


def test_an_auto_that_reuses_a_saved_review_reports_its_rounds_facts(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, _ = first_round(repo, monkeypatch)  # round 1 at the tested commit, one finding
    g.close("feature", [(1, "not reachable from any caller")], "claude")
    calls = fake_reviewer(monkeypatch)
    body = tmp_path / "body"
    body.write_text("body")
    code, result = cli(
        g, monkeypatch, capsys, ["auto", "feature", "--builder", "codex", "title", str(body)]
    )
    assert len(calls) == 0  # the saved review is reused, no review runs
    assert (code, result["outcome"]) == (0, "landed")
    assert (result["round"], result["recorded"]) == (1, 0)


@pytest.mark.parametrize("severity", ["P2", "P3"])
def test_refresh_reviews_the_fix_outside_the_lock_and_keeps_rounds(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, severity: str
):
    g, fake = first_round(repo, monkeypatch, severity)
    first = record_of(g)
    fix_commit(repo)
    assert g.trial("feature")
    push_main(repo, {"m.txt": "".join([*LINES[:-1], "main's line 20\n"])})
    calls = fake_reviewer(monkeypatch, earlier="still")
    review = g.review
    refreshed = []

    def outside_review(*args):
        assert g._connection is None and flock_available(g)
        assert g._landing_ticket.with_suffix(".paused").exists()
        refreshed.append(g._landing_ticket.name)
        return review(*args)

    monkeypatch.setattr(g, "review", outside_review)
    body = tmp_path / "body"
    body.write_text("body")
    if severity == "P2":
        with pytest.raises(gate.ReviewPending):
            g.open("feature", "title", body, rereview=True)
    else:
        assert g.open("feature", "title", body, rereview=True) == 7
        g.land("feature")
        assert len(g.read_ledger()) == 1
        posted = json.loads(g._state_file("feature").with_suffix(".public-review.json").read_text())
        assert posted[gate.MINOR_HEADING]
    assert len(refreshed) == 1 and len(calls) == 1 and is_fix(calls[0][0])
    diff = calls[0][1]["input"].split("=== DIFF", 1)[1]
    assert "+fix line 10" in diff and "main's line 20" not in diff
    record = record_of(g)
    assert record["round"] == 2 and record["rounds"][0]["tested"] == first["tested"]
    assert record["earlier"][0]["answer"] == "still"
    assert record["change"] == g.state("feature")["change"]
    assert g.holder() is None and not g._queue() and flock_available(g)


def test_a_carried_round_keeps_its_chain_closures_and_stable_digest(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    g, _ = first_round(repo, monkeypatch)
    fix_commit(repo)
    assert g.trial("feature")
    fake_reviewer(monkeypatch, earlier="still")
    with pytest.raises(gate.ReviewPending):
        g.review("feature", "codex")
    g.close("feature", [(1, "not reachable")], "claude")
    original = record_of(g)
    push_main(repo, {"m.txt": "".join([*LINES[:-1], "main's line 20\n"])})
    assert g.trial("feature")
    st = g.state("feature")
    assert g.current_review("feature", st, g.review_file("feature"))
    carried = record_of(g)
    assert carried["rounds"] == original["rounds"]
    assert carried["findings"] == original["findings"]
    assert carried["earlier"] == original["earlier"]
    assert carried["round"] == original["round"] == 2
    assert carried["change"] == original["change"] == st["change"]
    assert carried["reviewed"] == original["tested"] and carried["tested"] == st["tested"]
    # A deliberately requested new round also uses the stable branch-change digest.
    g.review("feature", "codex")
    assert record_of(g)["change"] == st["change"]
    assert record_of(g)["round"] == 3


def test_interrupted_refresh_ends_in_a_result_and_releases_the_turn(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    g, _fake, argv, _outcome, _code = prepared(repo, tmp_path, monkeypatch, "auto refresh worker")

    def interrupt(*args):
        assert g._connection is None and flock_available(g)
        raise KeyboardInterrupt()

    monkeypatch.setattr(g, "_run_checks", interrupt)
    bind(g, monkeypatch)
    capsys.readouterr()
    with pytest.raises(KeyboardInterrupt):
        gate.main(argv)
    result = ends_in_one_result(capsys.readouterr().out, "auto")
    assert result["outcome"] == "failed"
    assert g.holder() is None and not g._queue() and flock_available(g)
