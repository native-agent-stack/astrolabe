"""The register of learnings: one verified lesson per record, given to the next task.

Ported from meridian@014f706d:.agents/skills/lead/scripts/learnings.py; changed: the register
is docs/learnings.jsonl and the cites log is under ~/.cache/astrolabe/.

Usage: learnings.py [--register FILE] [--today YYYY-MM-DD] JOB ...
  add       record one confirmed lesson (refuses class `unclassified`)
  mark      set a lesson's status, and on promotion its new form and where it lives
  branch    record a branch once, so the scorecard counts branches without lessons
  draft     print one `add` command per open finding in gate output or a review record
  paths     the lessons for given paths or areas, newest first and capped (for dispatch)
  due       recurrences per class and area, what is due for promotion, the revisit measure
  stale     lessons cited by no task for ninety days: candidates to retire
  scorecard per builder runtime: branches, review findings, classes, rounds to land
The register is docs/learnings.jsonl; cites go to a local log outside the repository.
The practice follows compound-engineering's `ce-compound` (UPSTREAM.md); nothing is installed.
"""

import argparse
import json
import re
import shlex
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
REGISTER = ROOT / "docs" / "learnings.jsonl"
CITES = Path.home() / ".cache" / "astrolabe" / "lead-tools" / "learnings-cites.jsonl"
CLASSES = (
    "live-divergence",  # passes offline, fails or differs against the live engine or system
    "status-miscount",  # a failure, partial or missing result counted as a success or a run
    "bound-breach",  # a cap, limit or inclusive bound not held, rounding at the edge included
    "data-completeness",  # partial, stale or unconfirmed data trusted as complete or current
    "input-trust",  # untrusted input reaches behaviour it should not
    "state-lifecycle",  # state left behind or carried wrong across runs, retries or restarts
    "check-gap",  # a test, scan or gate check that passes when it should fail
    "doc-drift",  # a document or record states what the tree does not support
    "boundary",  # crosses an owner boundary or the public-repository rule
)
FORMS = ("test", "rule", "adr", "skill", "check", "none")
STATUSES = ("open", "promoted", "retired")
BUILDERS = ("codex", "claude", "deepseek")
# Every family the gate may assign as a reviewer (gate.py's REVIEWERS values). The test
# test_the_script_reviews_with_every_family_the_gate_may_assign keeps the two sets equal.
REVIEWERS = ("codex", "claude", "deepseek")
STRONG = {"test", "rule", "skill", "check"}
LADDER = ((3, {"skill", "check"}, "skill or gate check"), (2, STRONG, "test or rule"))
LOCAL = re.compile(r"/home/|/root/|/tmp/|/Users/|(?<![\w.])~/|\b[A-Za-z]:\\")
LANDED = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
FINDING = re.compile(r"^\s*(\d+)\.\s+(\{.*\})\s*$")
RECURRENCE_DAYS = 30
RECENT_BRANCHES = 20


class Refused(Exception):
    pass


def slug(branch: str) -> str:
    """The branch's last path component: a login or milestone prefix never lands here."""
    return branch.rstrip("/").rsplit("/", 1)[-1]


def problems(record: dict[str, Any]) -> list[str]:
    """Every reason a record is not a valid register line; empty when it is."""
    kind = record.get("kind")
    found = []
    texts = [v for v in record.values() if isinstance(v, str)]
    texts += [a for a in record.get("areas") or [] if isinstance(a, str)]
    if any(LOCAL.search(t) for t in texts):
        found.append("a local or absolute path: name it by role (e.g. 'the landing lock')")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(record.get("date", ""))):
        found.append("date must be YYYY-MM-DD")
    if kind == "status":  # a later status for a lesson: appended, so a union merge keeps it
        if set(record) != {"kind", "id", "date", "status", "form", "where"}:
            found.append("a status record has exactly kind, id, date, status, form and where")
        if record.get("status") not in STATUSES or record.get("form") not in FORMS:
            found.append("a status record has a known status and form")
        if record.get("status") == "promoted" and (
            record.get("form") == "none" or not record.get("where")
        ):
            found.append("a promoted lesson has a form other than none and says where it lives")
        return found
    branch = record.get("branch")
    if not isinstance(branch, str) or not branch or "/" in branch:
        found.append("branch must be the branch's last path component")
    pr = record.get("pr")
    if pr is not None and (not isinstance(pr, int) or isinstance(pr, bool) or pr < 1):
        found.append("pr must be a pull request number or null")
    if kind == "branch":
        if record.get("builder") not in BUILDERS:
            found.append(f"builder must be one of {', '.join(BUILDERS)}")
        fields = {"kind", "branch", "date", "builder", "pr", "landed", "rounds"}
        if set(record) != fields:
            found.append(f"a branch record has exactly {', '.join(sorted(fields))}")
        landing = [record.get("pr"), record.get("landed"), record.get("rounds")]
        rounds = record.get("rounds")
        if any(v is not None for v in landing) and (
            any(v is None for v in landing)
            or not LANDED.fullmatch(str(record.get("landed")))
            or not isinstance(rounds, int)
            or isinstance(rounds, bool)
            or rounds < 1
        ):
            found.append(
                "a landing gives pr, landed (the merge time, YYYY-MM-DDTHH:MM:SSZ) and rounds "
                "(the review rounds it took, 1 or more) together, or none of them"
            )
        return found
    if kind != "lesson":
        return [*found, "kind must be lesson, status or branch"]
    expected = {
        "kind", "id", "date", "pr", "branch", "builder", "reviewer", "round",
        "class", "areas", "lesson", "form", "where", "status", "source",
    }  # fmt: skip
    if set(record) != expected:
        found.append(f"a lesson has exactly: {', '.join(sorted(expected))}")
    if record.get("builder") not in BUILDERS:
        found.append(f"builder must be one of {', '.join(BUILDERS)}")
    if record.get("reviewer") not in (*REVIEWERS, None):
        found.append(f"reviewer must be one of {', '.join(REVIEWERS)} or null")
    rnd = record.get("round")
    if not isinstance(rnd, int) or isinstance(rnd, bool) or rnd < 0:
        found.append("round must be 0 (not from review) or the review round, 1 or more")
    if record.get("class") not in CLASSES:
        found.append(f"class must be one of {', '.join(CLASSES)}")
    areas = record.get("areas")
    if not isinstance(areas, list) or not areas:
        found.append("areas must be a non-empty list of repository paths")
    else:
        for area in areas:
            if (
                not isinstance(area, str)
                or not area
                or area.startswith("/")
                or area == "UNKNOWN"
                or ".." in PurePosixPath(area).parts
            ):
                found.append(f"area must be a repository-relative path: {area!r}")
    lesson = record.get("lesson")
    if (
        not isinstance(lesson, str)
        or not 10 <= len(lesson) <= 300
        or "\n" in lesson
        or not lesson.endswith(".")
    ):
        found.append("lesson must be one sentence of 10 to 300 characters ending with '.'")
    form, where, status = record.get("form"), record.get("where"), record.get("status")
    if form not in FORMS:
        found.append(f"form must be one of {', '.join(FORMS)}")
    if not isinstance(where, str):
        found.append("where must be a string")
    elif form not in ("none", None) and not where:
        found.append("a form other than none says where it lives")
    if status not in STATUSES:
        found.append(f"status must be one of {', '.join(STATUSES)}")
    if status == "promoted" and form == "none":
        found.append("a promoted lesson has a form other than none")
    if not isinstance(record.get("source"), str) or not record["source"]:
        found.append("source must say what proves the lesson (e.g. 'pr#72 review')")
    if not isinstance(record.get("id"), str) or not record["id"]:
        found.append("id is missing")
    return found


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Refused(f"{path.name}:{number}: not JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise Refused(f"{path.name}:{number}: a record is a JSON object")
        records.append(record)
    return records


def lessons(records: list[dict[str, Any]], live: bool = True) -> list[dict[str, Any]]:
    """Lessons in register order, each with its latest status record applied."""
    folded = {r["id"]: dict(r) for r in records if r.get("kind") == "lesson"}
    changes = [r for r in records if r.get("kind") == "status" and r.get("id") in folded]
    for change in sorted(changes, key=lambda r: r["date"]):  # stable: same day keeps file order
        folded[change["id"]].update(
            status=change["status"], form=change["form"], where=change["where"]
        )
    return [r for r in folded.values() if not (live and r["status"] == "retired")]


def append(path: Path, record: dict[str, Any]) -> None:
    found = problems(record)
    if found:
        raise Refused("; ".join(found))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False) + "\n")


def add(args: argparse.Namespace) -> None:
    if args.cls == "unclassified":
        raise Refused("class is unclassified: confirm the finding and choose its class first")
    records = load(args.register)
    branch, today = slug(args.branch), args.today.isoformat()
    ids = {r.get("id") for r in records}
    n = 1
    while f"{today}-{branch}-{n}" in ids:
        n += 1
    record = {
        "kind": "lesson",
        "id": f"{today}-{branch}-{n}",
        "date": today,
        "pr": args.pr,
        "branch": branch,
        "builder": args.builder,
        "reviewer": args.reviewer,
        "round": args.round,
        "class": args.cls,
        "areas": args.area,
        "lesson": args.lesson.strip(),
        "form": args.form,
        "where": args.where,
        "status": "open",
        "source": args.source,
    }
    append(args.register, record)
    print(record["id"])


def mark(args: argparse.Namespace) -> None:
    """Append a status record: lines are never rewritten, so branches merge as a union."""
    hits = [r for r in lessons(load(args.register), live=False) if r["id"] == args.id]
    if len(hits) != 1:
        raise Refused(f"no single lesson {args.id}")
    form = hits[0]["form"] if args.form is None else args.form
    where = hits[0]["where"] if args.where is None else args.where
    change = {"kind": "status", "id": args.id, "date": args.today.isoformat()}
    append(args.register, {**change, "status": args.status, "form": form, "where": where})
    print(f"{args.id}: {args.status}")


def branch(args: argparse.Namespace) -> None:
    """Record a branch once at dispatch; after its merge, append its landing: the pull request,
    the merge time (the landing order) and the review rounds it took."""
    name = slug(args.branch)
    landing = (args.pr, args.landed, args.rounds)
    if any(v is not None for v in landing) and any(v is None for v in landing):
        raise Refused("a landing gives --pr, --landed and --rounds together")
    known = [r for r in load(args.register) if r.get("kind") == "branch" and r["branch"] == name]
    prs = {r["pr"] for r in known if r.get("landed") is not None}
    if known and (args.pr is None or args.pr in prs):
        print(f"{name}: already recorded" + (f" (landed, pr {min(prs)})" if prs else ""))
        return
    if prs:
        raise Refused(f"{name} already landed with pr {min(prs)}, not {args.pr}")
    record = {"kind": "branch", "branch": name, "date": args.today.isoformat(), "pr": args.pr}
    record |= {"landed": args.landed, "rounds": args.rounds}
    append(args.register, {**record, "builder": args.builder})
    print(name + (f": landed, pr {args.pr}" if args.pr is not None else ""))


def findings_in(text: str) -> list[dict[str, Any]]:
    """Open findings from a review record (JSON) or from gate output (numbered lines)."""
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        record = None
    found = []
    if isinstance(record, dict) and isinstance(record.get("findings"), list):
        for item in record["findings"]:
            if not isinstance(item, dict):
                raise Refused("a review record's findings are objects with text and status")
            if (
                str(item.get("status", "open")).startswith("closed")
                or item.get("status") == "refuted"
            ):
                continue  # closed by the lead, or refuted by the gate's second pass: no lesson
            try:
                found.append(json.loads(item["text"]))
            except json.JSONDecodeError, KeyError, TypeError:
                found.append({"summary": str(item.get("text", ""))})
        return found
    for line in text.splitlines():
        if m := FINDING.match(line):
            try:
                item = json.loads(m.group(2))
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                found.append(item)
    return found


def draft(args: argparse.Namespace) -> None:
    text = sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8")
    found = findings_in(text)
    if not found:
        print("no open findings", file=sys.stderr)
    for item in found:
        summary = " ".join(str(item.get("summary", "")).split())
        first = re.split(r"(?<=[.!?])\s", summary, maxsplit=1)[0]
        command = [
            "uv", "run", "python", ".agents/skills/lead/scripts/learnings.py",
            "add", "--branch", slug(args.branch), "--builder", args.builder,
            "--reviewer", args.reviewer, "--round", str(args.round), "--class", "unclassified",
            "--area", str(item.get("file") or "UNKNOWN"), "--lesson", first,
            "--form", "none", "--source", args.source,
        ]  # fmt: skip
        if args.pr is not None:
            command[5:5] = ["--pr", str(args.pr)]
        print(shlex.join(command))


def concerns(area: str, query: str) -> bool:
    a, q = area.rstrip("/"), query.rstrip("/")
    return (
        a == q
        or fnmatchcase(q, a)
        or fnmatchcase(a, q)
        or q.startswith(a + "/")
        or a.startswith(q + "/")
    )


def newest_first(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {id(r): n for n, r in enumerate(records)}
    return sorted(records, key=lambda r: (r["date"], order[id(r)]), reverse=True)


def paths(args: argparse.Namespace) -> None:
    hits = [
        r
        for r in lessons(load(args.register))
        if any(concerns(a, q) for a in r["areas"] for q in args.path)
    ]
    shown = newest_first(hits)[: args.limit]
    for r in shown:
        held = f" [{r['form']}: {r['where']}]" if r["form"] != "none" else ""
        print(f"{r['id']} {r['class']} {', '.join(r['areas'])}: {r['lesson']}{held}")
    if args.cite and shown:
        args.cites.parent.mkdir(parents=True, exist_ok=True)
        with args.cites.open("a", encoding="utf-8") as out:
            for r in shown:
                cite = {"id": r["id"], "task": args.cite, "date": args.today.isoformat()}
                out.write(json.dumps(cite) + "\n")


def area_key(area: str) -> str:
    """The folder an area belongs to: a folder itself, a file's folder, a glob's fixed part.
    A top-level file keeps its own name, so the root files do not all group together."""
    parts = PurePosixPath(area.rstrip("/")).parts
    fixed = next((n for n, part in enumerate(parts) if any(c in part for c in "*?[")), None)
    if fixed is not None:
        return "/".join(parts[:fixed]) or area
    path = "/".join(parts)
    on_disk = ROOT / path
    folder = on_disk.is_dir() or (not on_disk.is_file() and not PurePosixPath(path).suffix)
    if folder or len(parts) == 1:
        return path
    return str(PurePosixPath(path).parent)


def keys(record: dict[str, Any]) -> set[str]:
    """The areas a lesson concerns; its tests count only when it names nothing else."""
    code = [a for a in record["areas"] if not a.startswith("tests/")]
    return {area_key(a) for a in code or record["areas"]}


def groups(records: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    found: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        for key in sorted(keys(r)):
            found[(r["class"], key)].append(r)
    return found


def landing_order(records: list[dict[str, Any]]) -> dict[str, tuple[bool, str, str, str]]:
    """Branches by landing order: the merge time a landing record gives (a pull request's
    number is not its merge order). A lesson's own pr does not count, so a branch with no
    landing record is still open and sorts after every landed one: (open, merged, first, name)."""
    merged: dict[str, str] = {}
    first: dict[str, str] = {}
    for r in records:
        if r.get("kind") in ("lesson", "branch"):
            first[r["branch"]] = min(first.get(r["branch"], r["date"]), r["date"])
            if r.get("kind") == "branch" and isinstance(r.get("landed"), str):
                merged[r["branch"]] = min(merged.get(r["branch"], r["landed"]), r["landed"])
    return {b: (b not in merged, merged.get(b, ""), first[b], b) for b in first}


def recurrence(records: list[dict[str, Any]]) -> tuple[int, int]:
    """Of the review lessons from the last 20 landed branches, how many repeat (same class and
    area) a lesson of another branch that landed before it, within 30 days. An open branch
    (no pull request yet) is neither measured nor a source: it has not landed."""
    order = landing_order(records)
    landed = sorted((b for b in order if not order[b][0]), key=order.__getitem__)
    recent = set(landed[-RECENT_BRANCHES:])
    every = lessons(records, live=False)
    measured = [r for r in every if r["reviewer"] is not None and r["branch"] in recent]
    recurring = 0
    for r in measured:
        mine = keys(r)
        for earlier in every:
            days = (date.fromisoformat(r["date"]) - date.fromisoformat(earlier["date"])).days
            if (
                earlier["branch"] != r["branch"]
                and not order[earlier["branch"]][0]
                and order[earlier["branch"]] < order[r["branch"]]
                and earlier["class"] == r["class"]
                and mine & keys(earlier)
                and 0 <= days <= RECURRENCE_DAYS
            ):
                recurring += 1
                break
    return recurring, len(measured)


def due(args: argparse.Namespace) -> None:
    records = load(args.register)
    live = lessons(records)
    for (cls, key), group in sorted(groups(live).items()):
        forms = {r["form"] for r in group}
        for count, stronger, step in LADDER:
            if len(group) >= count and not forms & stronger:
                ids = ", ".join(r["id"] for r in group)
                print(f"due: {cls} {key}: {len(group)} lessons ({ids}) -> {step}")
                break
    totals = Counter(r["class"] for r in live)
    print("classes: " + ", ".join(f"{c} {totals[c]}" for c in CLASSES if totals[c]))
    k, n = recurrence(records)
    share = f"{100 * k / n:.0f}%" if n else "n/a"
    print(
        f"recurrence within {RECURRENCE_DAYS} days: {k} of {n} review lessons "
        f"from the last {RECENT_BRANCHES} landed branches ({share})"
    )


def stale(args: argparse.Namespace) -> None:
    last: dict[str, str] = {}
    for cite in load(args.cites):
        if isinstance(cite.get("id"), str) and isinstance(cite.get("date"), str):
            last[cite["id"]] = max(last.get(cite["id"], cite["date"]), cite["date"])
    for r in lessons(load(args.register)):
        seen = max(r["date"], last.get(r["id"], r["date"]))
        idle = (args.today - date.fromisoformat(seen)).days
        if idle > args.days:
            print(f"stale: {r['id']} {r['class']}: last cited or recorded {seen} ({idle} days)")


def scorecard(args: argparse.Namespace) -> None:
    records = load(args.register)
    every = lessons(records, live=False)
    builders = sorted({r["builder"] for r in records if r.get("kind") in ("lesson", "branch")})
    print("builder   branches  landed  findings  rounds(median/max)  classes")
    for b in builders:
        names = {r["branch"] for r in records if r.get("builder") == b and "branch" in r}
        found = [r for r in every if r["builder"] == b and r["reviewer"] is not None]
        landed: dict[str, int] = {}  # rounds to land: recorded at landing, never inferred
        for r in records:
            if r.get("kind") == "branch" and r.get("builder") == b and r.get("landed"):
                landed.setdefault(r["branch"], r["rounds"])
        rounds = list(landed.values())
        shown = f"{statistics.median(rounds):g}/{max(rounds)}" if rounds else "-"
        classes = Counter(r["class"] for r in found)
        listed = ", ".join(f"{c} {n}" for c, n in classes.most_common()) or "-"
        print(f"{b:<9} {len(names):>8}  {len(landed):>6}  {len(found):>8}  {shown:<18}  {listed}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--register", type=Path, default=REGISTER)
    p.add_argument("--cites", type=Path, default=CITES)
    p.add_argument("--today", type=date.fromisoformat, default=date.today())
    jobs = p.add_subparsers(dest="job", required=True)
    a = jobs.add_parser("add")
    a.add_argument("--branch", required=True)
    a.add_argument("--builder", choices=BUILDERS, required=True)
    a.add_argument("--reviewer", choices=REVIEWERS)
    a.add_argument("--pr", type=int)
    a.add_argument("--round", type=int, default=0)
    a.add_argument("--class", dest="cls", choices=(*CLASSES, "unclassified"), required=True)
    a.add_argument("--area", action="append", required=True)
    a.add_argument("--lesson", required=True)
    a.add_argument("--form", choices=FORMS, default="none")
    a.add_argument("--where", default="")
    a.add_argument("--source", required=True)
    m = jobs.add_parser("mark")
    m.add_argument("id")
    m.add_argument("--status", choices=STATUSES, required=True)
    m.add_argument("--form", choices=FORMS)
    m.add_argument("--where")
    b = jobs.add_parser("branch")
    b.add_argument("--branch", required=True)
    b.add_argument("--builder", choices=BUILDERS, required=True)
    b.add_argument("--pr", type=int, help="after the merge: the pull request it landed in")
    b.add_argument("--landed", help="after the merge: its merge time, YYYY-MM-DDTHH:MM:SSZ")
    b.add_argument("--rounds", type=int, help="after the merge: the review rounds it took")
    d = jobs.add_parser("draft")
    d.add_argument("file", type=Path, help="gate output or a review record; '-' for stdin")
    d.add_argument("--branch", required=True)
    d.add_argument("--builder", choices=BUILDERS, required=True)
    d.add_argument("--reviewer", choices=REVIEWERS, required=True)
    d.add_argument("--pr", type=int)
    d.add_argument("--round", type=int, default=1)
    d.add_argument("--source", default="gate log")
    s = jobs.add_parser("paths")
    s.add_argument("path", nargs="+")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--cite", metavar="TASK", help="log that this task (its worktree name) got them")
    jobs.add_parser("due")
    t = jobs.add_parser("stale")
    t.add_argument("--days", type=int, default=90)
    jobs.add_parser("scorecard")
    return p


JOBS = {
    "add": add,
    "mark": mark,
    "branch": branch,
    "draft": draft,
    "paths": paths,
    "due": due,
    "stale": stale,
    "scorecard": scorecard,
}


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        JOBS[args.job](args)
    except (Refused, OSError) as exc:
        print(f"learnings: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
