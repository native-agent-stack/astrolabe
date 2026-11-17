"""Start an astrolabe worker through Orca, with rules, routing checks and registration.

Ported from meridian@014f706d:.agents/skills/lead/scripts/dispatch.py; changed: project names
from PROJECT, the Claude model id from models.toml, and DeepSeek is refused as a builder.

Sources: Orca's installed `skills get orchestration` and `worker-start --help`;
1.4.258 worker-start receipt (viu), creation effects (You), worktree-show branch, and the
worker-show projection. Runtime instructions: worker-rules.md. No lifecycle or retry
engine here.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[3]
PROJECT = "astrolabe"
LIFECYCLE = f"/autopilot --workflow {PROJECT}-full"
WAIT_TOKEN = f"[{PROJECT.upper()}_WAIT]"
MODELS_FILE = SCRIPTS.parent / "models.toml"
# The lead's finding of 2026-11-17, not checked by this script: DeepSeek Harness's completion
# gate works only in meridian checkouts today, so a dsh worker here would finish unchecked.
DEEPSEEK_REFUSAL = (
    "deepseek is not a builder here: DeepSeek Harness's completion gate works only in "
    "meridian checkouts today; start a claude or codex worker"
)
PLAN = "$ralplan"  # Codex planning skill, only with --design-heavy yes or --multi-file
START_OBSERVATION_SECONDS = 60.0
START_POLL_SECONDS = 2.0
START_REQUEST_SECONDS = 20.0
PREAMBLE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
PREAMBLE_DIR = Path.home() / ".cache" / PROJECT / "dispatch"
RECOVERY_INSTRUCTION = "Your task is in the file {path} : read the whole file and execute it."
STARTED_ACTIVITY = frozenset({"working", "monitoring", "waiting", "blocked", "done"})
REQUEST_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    IndexError,
    AttributeError,
    subprocess.TimeoutExpired,
)


def run(command: list[str], *, text: str | None = None, timeout: float | None = None) -> str:
    result = subprocess.run(
        command, cwd=ROOT, input=text, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode:
        raise ValueError(f"{shlex.join(command)} failed: {result.stderr}{result.stdout}")
    return result.stdout


def activate_lifecycle(orca: str, dispatch_id: str, timeout: float = 60) -> None:
    """Orca 1.4.258: exact worker-show, tui-idle wait, role-labelled worker-read transcript.

    Only this explicitly requested slash activation uses terminal input; task mail stays
    on orchestration. Missing evidence falls back to the lead, without a second send.
    """
    deadline = time.monotonic() + timeout
    line = f"{LIFECYCLE} Execute the task already dispatched, with all its rules."
    attempted = False

    def request(*args: str) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("lifecycle wait timed out")
        raw = run([orca, *args, "--json"], timeout=remaining)
        if args[:2] == ("terminal", "send"):
            print(raw, end="", flush=True)
        reply = json.loads(raw)
        if reply["ok"] is not True:
            raise ValueError(f"Orca refused {args[1]}: {reply}")
        return reply["result"]

    def worker() -> dict:
        shown = request("orchestration", "worker-show", "--dispatch", dispatch_id, "--full")
        observation, terminal = shown["observation"], shown["terminal"]
        if not (
            shown["dispatch"]["id"] == dispatch_id
            and shown["dispatch"]["status"] == "dispatched"
            and observation["exactWorker"] is True
            and observation["status"] == "live"
            and observation["agentWait"] is None
            and terminal["agentIdentity"] == "claude"
            and terminal["connected"] is True
            and terminal["writable"] is True
            and terminal["incarnationId"]
        ):
            raise ValueError("worker identity or input readiness is unverified")
        return shown

    try:
        shown = worker()
        terminal = shown["terminal"]
        handle = terminal["handle"]
        dispatched_at = (
            datetime.fromisoformat(shown["dispatch"]["dispatchedAt"])
            .replace(tzinfo=UTC)
            .timestamp()
            * 1000
        )
        waited = request(
            "terminal",
            "wait",
            "--terminal",
            handle,
            "--for",
            "tui-idle",
            "--timeout-ms",
            str(max(1, int((deadline - time.monotonic()) * 1000))),
        )
        if waited["wait"]["satisfied"] is not True:
            raise ValueError("worker did not become idle")
        read = request(
            "orchestration",
            "worker-read",
            "--dispatch",
            dispatch_id,
            "--source",
            "transcript",
            "--limit",
            "1",
            "--full",
        )
        if not (
            read["dispatchId"] == dispatch_id
            and read["source"] == "transcript"
            and read["sourceExact"] is True
            and read["provider"] == "claude"
            and not set(read.get("clipping", [])) - {"message_limit_or_scan_window"}
        ):
            raise ValueError("last assistant output is unavailable or clipped")
        message = read["transcript"]["messages"][-1]
        blocks = message["blocks"]
        if not (
            message["role"] == "assistant"
            and message["timestamp"] >= dispatched_at
            and blocks
            and all(block["type"] == "text" for block in blocks)
            and "\n".join(block["text"] for block in blocks).strip().splitlines()[-1] == WAIT_TOKEN
        ):
            raise ValueError("last assistant message does not end with the wait marker")
        current = worker()
        if not (
            current["terminal"]["handle"] == handle
            and current["terminal"]["incarnationId"] == terminal["incarnationId"]
            and current["projection"]["stage"]["activity"] == "done"
            and current["projection"]["evidence"]["liveStatus"] == "fresh"
        ):
            raise ValueError("worker changed or its completed turn is unverified")
        attempted = True
        sent = request("terminal", "send", "--terminal", handle, "--text", line, "--enter")
        if sent["send"]["accepted"] is not True:
            raise ValueError("lifecycle input was not accepted")
        print("Lifecycle input accepted; the receipt records whether a turn was observed.")
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired) as exc:
        print(f"Lifecycle manual fallback: {exc}")
        if isinstance(exc, subprocess.TimeoutExpired) and exc.stdout:
            print(exc.stdout.decode(errors="replace"))
        if attempted:
            print("Inspect the send receipt before typing: input may already have arrived.")
        else:
            print(f"After confirming the worker's first turn ended with {WAIT_TOKEN}, type:")
        print(line)


@dataclass(frozen=True)
class StartResult:
    """One dsh start observation: started, or not, with the lines the lead needs."""

    outcome: str
    reason: str = ""
    task_id: str = ""
    handle: str = ""
    preamble: Path | None = None


@dataclass(frozen=True)
class _Watch:
    """The last identity, activity and read problem of one watch."""

    identity: tuple[str, str] | None
    activity: str
    problem: str


def orca_request(orca: str, args: list[str], timeout: float) -> dict:
    """Run one Orca JSON command and return its result object."""
    reply = json.loads(run([orca, *args, "--json"], timeout=timeout))
    if not isinstance(reply, dict):
        raise ValueError(f"Orca returned a non-object reply for {args[0]} {args[1]}")
    result = reply.get("result")
    if reply.get("ok") is not True or not isinstance(result, dict):
        raise ValueError(f"Orca refused {args[0]} {args[1]}: {reply}")
    return result


def receipt_terminal(receipt: dict) -> tuple[str, str | None]:
    """The agent terminal handle in the start effects and the prompt's process incarnation."""
    handles = [
        effect["id"]
        for effect in receipt.get("effects", [])
        if isinstance(effect, dict)
        and effect.get("kind") == "terminal"
        and effect.get("role") == "agent"
        and isinstance(effect.get("id"), str)
        and effect["id"]
    ]
    if len(handles) != 1:
        raise ValueError("the start receipt does not name one agent terminal")
    prompt = receipt.get("prompt")
    incarnation = prompt.get("processIncarnation") if isinstance(prompt, dict) else None
    if not isinstance(incarnation, str) or incarnation == "unknown":
        incarnation = None
    return handles[0], incarnation


def terminal_identity(
    shown: dict, dispatch_id: str, handle: str, incarnation: str | None
) -> tuple[str, str] | None:
    """The dsh terminal identity worker-show reports for this dispatch, or None."""
    dispatch, observation, terminal = (
        shown.get("dispatch"),
        shown.get("observation"),
        shown.get("terminal"),
    )
    if not (
        isinstance(dispatch, dict)
        and isinstance(observation, dict)
        and isinstance(terminal, dict)
        and dispatch.get("id") == dispatch_id
        and observation.get("exactWorker") is True
        and observation.get("status") == "live"
        and terminal.get("handle") == handle
        and terminal.get("agentIdentity") == "dsh"
        and isinstance(terminal.get("incarnationId"), str)
        and terminal["incarnationId"]
    ):
        return None
    current = terminal["incarnationId"]
    if incarnation is not None and incarnation != current:
        return None
    return handle, current


def activity_state(shown: dict) -> str:
    """The worker's activity, or unknown when worker-show does not carry one."""
    projection = shown.get("projection")
    stage = projection.get("stage") if isinstance(projection, dict) else None
    activity = stage.get("activity") if isinstance(stage, dict) else None
    return activity if isinstance(activity, str) else "unknown"


def first_turn_seen(shown: dict) -> bool:
    """Whether worker-show's projection.stage.activity is a started activity."""
    return activity_state(shown) in STARTED_ACTIVITY


def preamble_path(task_id: str) -> Path:
    """The recovery file's path under the cache, named by the task id."""
    if not task_id or any(not (char.isalnum() or char in "._-") for char in task_id):
        raise ValueError("the task id cannot name a preamble file")
    return PREAMBLE_DIR / f"{task_id}.txt"


def save_preamble(path: Path, text: str) -> None:
    """Write the prompt outside the repository and drop the directory's week-old files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - PREAMBLE_MAX_AGE_SECONDS
    for old in path.parent.glob("*.txt"):
        if old == path:
            continue
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            continue
    path.write_text(text, encoding="utf-8")


def fetch_preamble(task_id: str, request: Callable[[list[str], float], dict]) -> str:
    """The preamble that dispatch-show --preamble renders for the task."""
    result = request(
        ["orchestration", "dispatch-show", "--task", task_id, "--preamble"],
        START_REQUEST_SECONDS,
    )
    preamble = result.get("preamble") if isinstance(result, dict) else None
    if not isinstance(preamble, str) or not preamble.strip():
        raise ValueError("dispatch-show returned no preamble")
    return preamble


def watch_worker(
    shown: Callable[[float], dict],
    dispatch_id: str,
    handle: str,
    incarnation: str | None,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> _Watch:
    """Read worker-show until a started activity or the bound, tolerating a failed read."""
    identity: tuple[str, str] | None = None
    activity = "unknown"
    problem = ""
    deadline = clock() + START_OBSERVATION_SECONDS
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return _Watch(identity, activity, problem)
        current: tuple[str, str] | None = None
        try:
            answer = shown(max(1.0, min(START_REQUEST_SECONDS, remaining)))
            if not isinstance(answer, dict):
                raise ValueError("worker-show returned a non-object result")
            expected = None if identity is None else identity[1]
            current = terminal_identity(answer, dispatch_id, handle, expected or incarnation)
        except REQUEST_ERRORS as exc:
            problem = f"the worker could not be read: {exc}"
        else:
            if current is None:
                problem = "the worker terminal does not match the start receipt"
            elif identity is None:
                identity = current
                activity = activity_state(answer)
                problem = ""
            else:
                activity = activity_state(answer)
                problem = ""
            if identity is not None and current == identity and first_turn_seen(answer):
                return _Watch(identity, activity, "")
        sleep(min(START_POLL_SECONDS, max(0.0, deadline - clock())))


def observe_deepseek_start(
    receipt: dict,
    *,
    request: Callable[[list[str], float], dict],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> StartResult:
    """Watch the dsh worker's first turn for the bound, save the prompt, report the outcome.

    The signal is worker-show's projection.stage.activity on the terminal the start receipt
    names. When the turn is not seen, the dispatched prompt is saved under the cache and the
    result is NOT STARTED with the lines the lead needs to recover it by hand.
    """
    task_id, dispatch_id = receipt.get("taskId"), receipt.get("dispatchId")
    if not (isinstance(task_id, str) and isinstance(dispatch_id, str)):
        return StartResult("not_started", "the start receipt has no task and dispatch identity")
    try:
        path = preamble_path(task_id)
    except ValueError as exc:
        return StartResult("not_started", str(exc), task_id=task_id)
    try:
        handle, incarnation = receipt_terminal(receipt)
    except (ValueError, TypeError) as exc:
        return StartResult("not_started", str(exc), task_id=task_id, preamble=path)

    def shown(timeout: float) -> dict:
        return request(
            ["orchestration", "worker-show", "--dispatch", dispatch_id, "--full"], timeout
        )

    first = watch_worker(shown, dispatch_id, handle, incarnation, clock=clock, sleep=sleep)
    if first.activity in STARTED_ACTIVITY:
        return StartResult("started", "", task_id=task_id, handle=handle, preamble=path)
    try:
        preamble = fetch_preamble(task_id, request)
        save_preamble(path, preamble)
    except REQUEST_ERRORS as exc:
        reason = f"the task preamble could not be saved: {exc}"
    else:
        if first.identity is None:
            reason = first.problem or "the worker terminal could not be identified"
        else:
            reason = (
                f"no first turn within {int(START_OBSERVATION_SECONDS)} seconds "
                f"(activity {first.activity!r})"
            )
    return StartResult(
        "not_started",
        " ".join(reason.split()),
        task_id=task_id,
        handle=handle,
        preamble=path,
    )


def describe_start(result: StartResult, orca: str) -> list[str]:
    """The outcome's lines: started, or the readings and the by-hand step for the lead."""
    if result.outcome == "started":
        return ["Start: started (first turn seen)."]
    handle = result.handle or "<terminal>"
    task = result.task_id or "<task>"
    lines = [
        f"NOT STARTED: {result.reason}.",
        "Do not repeat worker-start: it may already have started.",
        "Read the terminal: "
        + shlex.join([orca, "terminal", "read", "--terminal", handle, "--screen"]),
        "Save the prompt: "
        + shlex.join([orca, "orchestration", "dispatch-show", "--task", task, "--preamble"]),
    ]
    if result.preamble is not None:
        lines[-1] += " > " + shlex.quote(str(result.preamble))
    lines.append(
        "By hand: read the terminal first; on the plain start screen press Escape if a draft "
        "is in the box, then send: " + RECOVERY_INSTRUCTION.format(path=result.preamble or "<file>")
    )
    return lines


def claude_model() -> str:
    """The Claude workers' model id from models.toml, the one place the gate reads too."""
    model = tomllib.loads(MODELS_FILE.read_text(encoding="utf-8"))["models"]["claude"]
    if not isinstance(model, str) or not model:
        raise ValueError(f"{MODELS_FILE.name} names no Claude model")
    return model


def check_triggers(text: str, planned: bool) -> None:
    """Planned: exactly the one PLAN line, as keyword and as dollar mention; else none.

    Codex loads a skill for a `$` + name anywhere in the text, backticks included, while OMX's
    keyword detector skips backticks: both lists are checked on the full text sent. Whether
    the planning skill is installed is check_codex's job on the machine, not this one's.
    """
    reply = json.loads(run([sys.executable, str(SCRIPTS / "trigger_words.py"), "-"], text=text))
    matches, found = reply["matches"], reply["mentions"]
    if not isinstance(matches, list) or not isinstance(found, list):
        raise ValueError("malformed trigger matches")
    expected = [{"token": PLAN, "line": 1, "text": PLAN}] if planned else []
    if found != expected:
        extra = [m for m in found if m not in expected]
        named = "; ".join(
            f"{m['token']} at line {m['line']}: {m['text']}" if isinstance(m, dict) else str(m)
            for m in extra
        )
        raise ValueError(
            f"dollar mention in worker text: {named or 'none; the planning line is missing'}; "
            "write a shell variable or positional parameter in the brace form (${NAME}, "
            "${1}) and a skill by its plain name"
        )
    if planned:
        valid = (
            len(matches) == 1
            and matches[0].get("keyword") == PLAN
            and matches[0].get("skill") == "ralplan"
            and reply["selected"] == matches[0]
        )
    else:
        valid = not matches and reply["selected"] is None
    if not valid:
        raise ValueError(f"unintended or missing trigger: {reply}; reword or quote the task")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("name")
    parser.add_argument("runtime", choices=("claude", "codex", "deepseek"))
    parser.add_argument("--design-heavy", choices=("yes", "no"), required=True)
    parser.add_argument("--multi-file", action="store_true", help="Codex build needs planning")
    args = parser.parse_args(argv)
    orca = os.environ.get("ORCA_CLI_COMMAND") or (
        "orca-dev"
        if os.environ.get("ORCA_DEV_REPO_ROOT")
        else "orca-ide"
        if sys.platform == "linux"
        else "orca"
    )
    started = False
    try:
        if args.runtime == "deepseek":
            raise ValueError(DEEPSEEK_REFUSAL)
        task = args.task.read_text(encoding="utf-8")
        if not task.strip():
            raise ValueError("empty task")
        rules = (SCRIPTS.parent / "worker-rules.md").read_text(encoding="utf-8")
        spec = task.rstrip() + "\n\n" + rules
        lifecycle = args.runtime == "claude" and args.design_heavy == "yes"
        if args.runtime == "codex":
            # Explicit invocations mask implicit matches in OMX: scan before adding one.
            check_triggers(spec, planned=False)
            if args.design_heavy == "yes" or args.multi_file:
                spec = PLAN + "\n" + spec
                check_triggers(spec, planned=True)
        elif lifecycle:
            spec = (
                f"This task runs through `{LIFECYCLE}`; before doing any work, end your "
                f"first turn with {WAIT_TOKEN} so the dispatch helper can send "
                "the invocation.\n\n" + spec
            )
        with tempfile.TemporaryDirectory(prefix=f"{PROJECT}-dispatch-") as temp:
            prompt = Path(temp) / "task.txt"
            prompt.write_text(spec, encoding="utf-8")
            command = [
                orca,
                "orchestration",
                "worker-start",
                "--spec-file",
                str(prompt),
                "--worktree",
                "new-top-level",
                "--base-branch",
                "origin/main",
                "--name",
                args.name,
                "--agent",
                "dsh" if args.runtime == "deepseek" else args.runtime,
                "--json",
                "--full",
            ]
            if args.runtime == "claude":
                command += ["--model", claude_model()]
            started = True
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            # A failed or malformed reply may still have created a worker: keep the evidence.
            print(result.stdout, end="", flush=True)
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            if result.returncode:
                raise ValueError("worker-start failed; follow the receipt's recovery commands")
            reply = json.loads(result.stdout)
            if reply["ok"] is not True or reply["result"]["state"] != "ready":
                raise ValueError("worker-start did not confirm ready; inspect the receipt")
            receipt = reply["result"]
            task_id, dispatch_id = receipt["taskId"], receipt["dispatchId"]
            if not all(isinstance(x, str) and x for x in (task_id, dispatch_id)):
                raise ValueError("worker-start omitted task or dispatch identity")
            worktrees = [
                effect["id"]
                for effect in receipt["effects"]
                if effect["kind"] == "worktree" and effect.get("action") == "created_top_level"
            ]
            if len(worktrees) != 1 or not isinstance(worktrees[0], str) or not worktrees[0]:
                raise ValueError("worker-start omitted the unique created worktree")
            shown = json.loads(
                run(
                    [
                        orca,
                        "worktree",
                        "show",
                        "--worktree",
                        f"id:{worktrees[0]}",
                        "--json",
                    ]
                )
            )
            branch = shown["result"]["worktree"]["branch"]
            if shown["ok"] is not True or not isinstance(branch, str) or not branch:
                raise ValueError("worktree-show omitted the branch")
            branch = branch.removeprefix("refs/heads/")
            register = [
                sys.executable,
                str(SCRIPTS / "gate.py"),
                "register",
                branch,
                "--builder",
                args.runtime,
                "--task",
                task_id,
            ]
            print("Register: " + shlex.join(register), flush=True)
            print(run(register), end="")
            print(f"Started {dispatch_id}: task {task_id}, branch {branch}.")
            if lifecycle:
                activate_lifecycle(orca, dispatch_id)
            outcome_lines: list[str] = []
            if args.runtime == "deepseek":
                observation = observe_deepseek_start(
                    receipt, request=lambda args, timeout: orca_request(orca, args, timeout)
                )
                outcome_lines = describe_start(observation, orca)
                if observation.outcome == "not_started":
                    print("\n".join(outcome_lines), file=sys.stderr)
                    return 1
            print("Next: follow the worker receipt; on completion use the lead skill's merge gate.")
            for outcome_line in outcome_lines:
                print(outcome_line)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        if started:
            print(
                "Do not repeat worker-start: it may already have started. Inspect the receipt; "
                "if only registration failed, rerun the printed register command.",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
