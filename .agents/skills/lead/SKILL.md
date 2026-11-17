---
name: lead
description: "The astrolabe lead's procedure. Use when leading: dispatching roadmap items to Orca workers, answering their reports, or landing a branch through the merge gate."
---
<!-- Ported from meridian@014f706d:.agents/skills/lead/SKILL.md; rewritten for astrolabe: its boundaries, names, owner paths and compounding practice. -->
# Lead

## Boundaries (the owner's; not yours to change)

- Paper only: never create, enable or test a route to a real-money account. For IBKR: the IB
  Gateway's paper port on the loopback address only, never a live port or a Live Trading
  login.
- On real-money accounts: drafts only; the owner submits.
- Only the engine places orders, in a run the owner starts or in a service the owner enabled.
  No agent places, changes or cancels an order or types the confirmation. No agent starts an
  IBKR lane run in any mode, and no agent connects to the Gateway's API port.
- Never read, print, copy or move a key or credential value.
- `risk/` and `*.contract.json` are the owner's; `upstream/` is verbatim. Read, never edit.
- Public repository: no account id, order history or tuned value (those go to
  `astrolabe-evidence`).
- Ask the owner before adding a paid service or an account.
- No automated account rotation or failover: on a rate limit, slow down.

### Never run

The lead never runs, and no task asks a worker to run: `python -m astrolabe.ibkr_paper` in any mode, `python -m astrolabe.boundary contract`, any connection to the Gateway's API port, or `systemctl` on astrolabe units. `worker-rules.md` carries the same list to every worker.

## Procedure

1. **Loop.** Take the next items from `ROADMAP.md`. Start one worker per item in its own worktree through Orca (the `orchestration` skill). Codex and Claude Opus build (code and research); a model family other than the builder's reviews. On every worker report keep going: decide by the recommended option, ask the owner only for a boundary.
2. **Dispatch.** The one way to start a worker is `uv run python .agents/skills/lead/scripts/dispatch.py TASK.txt NAME claude|codex --design-heavy yes|no` (the runtime is one choice). It appends `worker-rules.md` unchanged, creates an Orca worktree from `origin/main` and registers the branch with the gate. Claude uses the model `models.toml` names. The helper refuses `deepseek` as a builder: DeepSeek Harness's completion gate does not work in astrolabe checkouts yet; DeepSeek still reviews through the gate. For a Claude lifecycle task, the helper waits up to 60 seconds for an idle worker and its own final `[ASTROLABE_WAIT]` transcript line, rechecks the completed turn and sends `/autopilot --workflow astrolabe-full` once. If evidence is unavailable it prints the manual line; if delivery is uncertain, inspect the receipt before typing to avoid duplicate activation. After a failed or uncertain start, do not repeat worker-start: keep the receipt and follow Orca's recovery guide. New lane or probe entry points must also land in the rules (`tests/test_worker_rules.py`). **Learnings** (`L="uv run python .agents/skills/lead/scripts/learnings.py"`): add `$L paths AREA... --cite NAME` output (NAME: the worktree) and the line `$L branch --branch NAME --builder B`, with `$L` written out as its command, to the task text.
3. **Codex routing.** Use the same `dispatch.py` helper; add `--multi-file` for a multi-file build. Design-heavy or multi-file Codex builds start with `$ralplan` (the helper adds it as the first line); ordinary tasks keep the runtime default model and effort. Codex loads a skill for a dollar sign and a name anywhere in its prompt, backticks included, and resolves a link whose label begins with one by its path, so text for a worker holds no dollar sign directly followed by a letter, digit, `_`, `-` or `:`, and no link whose label begins with a dollar sign (a superset of what Codex loads, with no exceptions): in a task for a worker, write shell variables and positional parameters in the brace form and skills by their plain name. The helper runs `trigger_words.py` (OMX keyword matches plus every such token with its line) on the task plus rules before adding the invocation, then checks the final prompt: with planning exactly that one first line, without it none; anything else refuses and names the line. Reword unintended matches; an absent detector refuses dispatch. Workers report Serena availability and evidence of the checks actually run.
4. **Land one branch at a time**, from the main checkout (`G="uv run python .agents/skills/lead/scripts/gate.py"`). The helper registers every builder at dispatch with `$G register BRANCH --builder codex|claude|deepseek --task TASK_ID`. Orca's task listing has neither branch nor builder. Codex and Claude review each other; a Claude-built branch goes to Codex unless the lead asks with `--reviewer deepseek` on `auto`/`review`, a DeepSeek-built branch gets Codex, or `--reviewer claude` on `auto`/`review`. The reviewers' model ids are in `models.toml`. `$G pending [--run RUN_ID]` (exit 0: pending, including when another branch has unreadable state; 1: idle with no unreadable state; 2: query/fetch failure or unreadable state with nothing pending) reads each registered task's Run from its dispatch, since Orca lists tasks per Run. Pending names waits for worker changes or lead dispositions; dead runs retry, transient lead failures retry up to three times (`--retry-limit`), then need an explicit auto retry. It fetches only for a remaining candidate.
   - Foreign commits already on main are proved; otherwise use `register BRANCH --landed SHA[,SHA…] --landed-by PR`.
   - Retire with `register BRANCH --retire "REASON"`; it leaves pending, refuses while reserved, and needs a new registration to resume.
   - On worker completion run `$G auto BRANCH --builder B [--reviewer R] [--squash
     SQUASH.txt] "TITLE" BODY.md` in the background, through an Orca terminal so its exit
     wakes the lead. BODY.md is the worker's NAME.md: `open` (and so `auto`) refuses a body
     without the three headings of `.github/pull_request_template.md`, `## Upstream
     references`, `## Evidence` and `## Risk and rollback`. Auto validates BODY and registers
     the branch if unregistered (never replacing a registration), runs trial and the other
     family's headless review without the landing lock, then open and land under it;
     code/check failures print `SEND TO WORKER`; numbered review candidates and other
     failures print `LEAD ACTION`. The review lists every defect (P0-P3) in one pass, then a
     second run of the same family tries to refute each; refuted ones stay in the record and
     stop nothing, confirmed and undecided ones come in one list, most severe first, with
     counts (a failed second pass leaves all undecided). A later round (an earlier record on
     file) reviews only the fix since the last reviewed head, merges of main left out, and
     answers each finding left open resolved, still or changed (only main moved: no model,
     all still); `--full-review` (review or auto) forces the whole change, still with those
     answers. From a branch's second stop on, a P3-only list is recorded instead of
     stopping: under `recorded_minor` in the posted record and, once it lands, in the local
     ledger `astrolabe-gate/followups.jsonl` (`$G followups [--all]`, whose result line
     carries the records, and `$G followup-done ID COMMIT`), which a sweep branch works off;
     P0-P2 always stop. Before the lead sends or closes each listed candidate, a `verifier`
     or `code-reviewer` agent on Claude Opus checks it against the tested tree and records
     evidence; confirmed defects go to the worker with their `$L draft` line (the worker
     records the lesson with the fix), and the lead closes false positives with `$G close
     BRANCH NUMBER "REASON" --by FAMILY [--also NUMBER "REASON"]`; the builder's own family
     and fix claims are refused. Re-run auto after closing all: it reuses the tested commit
     and review, while a new commit needs both again. An owner path needs an explicit
     decision (see below).
   - A protected path stops auto before land: owner-only paths (`risk/` at any depth, `*.contract.json`, AGENTS.md's Boundaries, `tests/test_agents_md.py`, `astrolabe/boundary.py`, `astrolabe/ib_ids.py`, `tests/test_paper_only.py`, `tests/test_public_tree.py`, `upstream/`, `tools/skills/`, live and real-money names) require `$G land BRANCH --decided-by owner`; operational paths (the order-path modules `astrolabe/ibkr_node.py` and `astrolabe/ibkr_paper.py`, `tests/test_order_path.py`, `astrolabe/settings.py`, `tools/guard/` with `tests/test_guard.py`, the test-side guards `tests/conftest.py` and `tests/test_test_guards.py`, `.claude/settings.json`, `pyproject.toml`, the gate itself and `worker-rules.md`, the CI workflows, `.github/dependabot.yml`, the gitleaks configuration, `deploy/systemd/` and shell files naming systemctl, all in `OWNER_PATHS`) also accept `--decided-by lead`. Mixed changes require the owner; the version-2 review record also names the decider and classified paths. Only the owner types `--decided-by owner`; no agent, lead or automation does. The run whose auto stops prints `OWNER ACTION` once: leave it, name it in that run's summary and tell the owner. When a lead runs `pending`, it names the decision wait each time, but the wait alone does not start another run.
   - A moved main pauses the holder's own ticket and releases the landing lock.
     Merge, checks (in a trial slot) and any new review run outside the lock;
     ready tickets may pass. Once ready, the original ticket wins again.
     Main is checked again on reacquisition. Only moves from outside the gate
     spend the three-refresh allowance; verified gate landings do not.
     Refresh exhaustion ends with outcome `paused`; queue timeouts use `waiting`.
     Both need a retry; each command still prints only one final result line.
     A proven clean merge of main preserves the review's mode and all closures,
     including who closed each candidate and why. Worker changes need review.
   - To decide an operational change before its turn, add
     `--decided-by lead --decided-head SHA` to `auto`. SHA is the full commit
     the lead decided on. The review's change digest binds it: merging main
     preserves the decision; a different branch change stops before land.
     Open and land share one turn. The public decision record names the lead,
     commit and paths. A decision with no owner path is refused before queuing.
     Auto never accepts `--decided-by owner`; the owner runs `land`.
     Owner-only paths also refuse an advance lead decision before trial or queuing.
     Without an advance decision, auto stops on owner paths; the recovery is
     `$G land BRANCH --decided-by owner|lead`, subject to the path classes above.
   - By hand: `$G trial BRANCH`, `$G review BRANCH --builder B [--reviewer R]`, `$G open
     BRANCH "TITLE" BODY.md [REVIEW.json] [--builder B] [--wait]`, `$G land BRANCH
     [SQUASH.txt]`. Trial merges main and checks the branch (the done command, then
     `uv run zizmor --offline .github/` and the gitleaks scan of the change range), and
     refuses a commit trailer naming a family other than the builder's; open refuses an
     unregistered branch (`--builder` registers it) and a body without the three sections,
     merges a moved main and checks again, reuses a review whose change digest is
     unchanged, and validates the review before pushing. Land appends " (#N)" to a squash
     subject and merges with `--match-head-commit`. The final record holds the current round
     (no findings, or each `closed: reason`, `refuted` by the second pass, or `recorded`) and
     the chain of earlier rounds; `fixed` and closing reasons claiming a fix are refused.
     Fixed code needs a new trial and review, a new round. Every command's last line is
     `GATE-RESULT {json}` (command, branch, outcome from `OUTCOMES`, exactly the fields that
     outcome declares, and the merge facts once a merge ran; `pending` lists each branch with
     its builder and task); programs read only that line, never the prose above it.
     Exit 3 from `land` or `auto` means landed but the follow-up ledger was not written
     (`ledger_error`): never land again; its recorded findings are in the pull request's
     published review record (`recorded_minor`).
   - Run the gate without an outer flock: its local holder retains
     `~/.cache/astrolabe/landing.lock` with flock from open to land; trial and review take
     the branch's own flock, so a second command on one branch refuses. Checks additionally
     take one of two machine-wide trial slots (`trial.N.lock` beside the landing lock), with
     at most eight pytest workers each; `trial` and `auto` accept `--trial-slots N` and
     `--trial-workers N`, and an inherited `PYTEST_XDIST_AUTO_NUM_WORKERS` can lower the
     latter. Slot waiters in `trial.queue/` serve paused landing re-checks first, in arrival
     order within each group; running trials keep their slots. They print on waiting
     and acquiring, and expire within `--wait-timeout 7200` with a `LEAD ACTION` naming how
     many are ahead; a dead trial frees its slot immediately. Only setup and checks hold a
     slot, including open's re-check when main moves; the merge of main, review, open and
     land otherwise do not. `--no-wait` applies only to the landing reservation. A
     contending `auto` waits its turn in arrival order (ticket files in `landing.queue/`
     beside the lock; one line on joining, one when its turn comes) within `--wait-timeout
     7200`, then a `LEAD ACTION` naming the holder and how many are ahead; `--no-wait`
     refuses at once, and `open --wait` waits for a hand-run sequence. Other contending
     commands refuse immediately; none jumps a ready ticket. The rule: auto starts without a
     reservation (it gives up one naming its branch on entry), runs trial and review without
     one, takes a fresh one through the queue at open and gives it up on every exit; a
     hand-run command keeps it for a retry except at a deadline or a moved-main refresh; any
     landing command run again after its branch merged says so, changes nothing and holds
     nothing; `$G release BRANCH` explicitly abandons it (only a reservation naming BRANCH),
     cleaning dead-holder leftovers only under the shared flock. Land pins the tested head,
     requires green CI with the repository's own `check` job present (third-party checks
     alone are not green) and equal merged/tested trees, then compares main again and marks
     the registration landed. If main moves during the merge, run checks on main. After the
     release, land and auto wait within the CI deadline for `check` on main's new commit:
     green is a landing's last line; red or absent is a `LEAD ACTION` naming the commit.
   - Reviewer ceiling: `--review-timeout 1200` for both passes, the second within it at most `--refute-timeout 300`; `--review-mode precise` (review or auto) is one high-confidence pass (Claude's old ask), used only when asked; CI ceiling: `--ci-timeout 1500` (seconds: the `check` job's 20 minutes plus 5); a first-pass or CI deadline returns to the lead and releases. Claude defaults to `--review-level medium` (`high` available), with a schema and read-only tools; Codex uses read-only `exec --output-schema` with the same findings object (and the same verdicts object in the second pass); DeepSeek runs read-only `dsh --profile headless --json`, gets the schema in the task and its JSON final message is checked here; prose or malformed output is unclassified for every family. Before either reviewer the gate runs `ocr delegate preview` and `ocr delegate rule` in the branch's worktree and puts the file list and rules in the prompt, a floor beside the whole diff; the record's `ocr` names its version and files covered and left out, or a note that it was absent, failed or slow, which never stops a landing.
5. **After each merge**: update `ROADMAP.md`, put `$L branch --branch NAME --builder B --pr N --landed MERGED_AT --rounds R` for the landed branch (its `mergedAt` and review rounds; `$L` written out) in the next task, record each lesson the landing's review confirmed (`$L draft` on the gate's output gives one `add` line per open finding; the worker records it with the fix, or the lead does when none was sent back), read `$L due` and make what is due a promotion item (a test, a rule, a skill or a gate check; `$L stale` for lessons to retire), then take the next item.

## Compounding

Every landing feeds the next task. Before a task, the worker reads the lessons for its paths (`$L paths`, put in the task text at dispatch). At its end it records what was learned with `learnings.py add`, or says in NAME.md that there was nothing to record. Every pull-request body carries the three sections of `.github/pull_request_template.md`: Upstream references (URL, version or commit, and date read, with the `UPSTREAM.md` row and `upstream-map.toml` entry in the same change), Evidence (the done command's output; for lane and strategy changes the name and digest of the run or study record, which stays in `astrolabe-evidence`) and Risk and rollback (what can fail, and the one command that undoes it). The register is `docs/learnings.jsonl`; a lesson that recurs comes due for promotion, and the promotion lands through this gate like any other change.

The trial refuses home paths with user names and any e-mail address, vendor contacts included, except reserved example domains, the configured agent trailers and unit names.
