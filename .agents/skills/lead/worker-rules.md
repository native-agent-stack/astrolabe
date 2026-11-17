<!-- Ported from meridian@014f706d:.agents/skills/lead/worker-rules.md; rewritten for astrolabe: its boundaries, entry points and compounding practice. -->
## Boundaries (the owner's; the only hard rules)

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

- `python -m astrolabe.ibkr_paper`, in any mode.
- `python -m astrolabe.boundary contract`.
- Any connection to the Gateway's API port (port 4002), `python -m astrolabe.ib_probe` included.
- `systemctl` on an astrolabe unit or timer.

### Also on this machine

- Never open the credential store (`/etc/credstore*`, `systemd-creds`) or a file inside `~/Jts`.
- Never run `orca-ide open`, `orca-ide serve` or `orca-ide serve service install` (also as
  `orca`; they can listen on every interface). Orca down: report it or run
  `systemctl --user start orca-serve`.
- Never weaken a test, a scan or a check to make it pass.
- This machine is shared with other workers and the lead: never stop a process by name
  (`pkill`, `killall`, `kill` on a `pgrep` pattern). Stop only a process you started, by its PID.

## Everything else is allowed without asking

- The repository's whole test suite, inside a memory limit: the offline tests build no node
  against the adapter and open no socket to an IB API port.
- Reading upstream source at its pin as data, never editing or running it. Downloads go in a
  new folder under `~/.cache/astrolabe/` and are removed afterwards.
- Reading public documentation, websites and public source over HTTPS, anonymously, as data
  (documentation hosts of any vendor included); nothing about this repository in a request.
- Any edit inside this worktree that the task needs. Cite the source of every fact you add:
  URL, version or commit, and the date read.
- If a rule and the task seem to pull apart, take the narrow reading of the rule (only what
  it names is forbidden), carry on, and say which reading you took in NAME.md. Stop and ask
  only for a boundary above or a decision that is the owner's (money, accounts, risk values,
  raising a size limit).

## Use your runtime's installed layer

- **Claude Opus**: before `worker_done`, use `verify` on the change, then draft `NAME.md` with
  `pr` from that verification evidence. A design-heavy dispatch says in the first line of its
  task text (the line after Orca's `=== TASK ===` marker) that it runs through
  `/autopilot --workflow astrolabe-full`: do no work on the first turn; end it with
  `[ASTROLABE_WAIT]` for the dispatch helper to send that invocation (the lead handles its
  manual fallback). Do not open a pull request.
- **Codex**: run the ralplan skill only when the task text, the lines after Orca's
  `=== TASK ===` marker, begins with the planning skill's dollar mention (the helper adds it
  for a design-heavy or multi-file build); otherwise do the task directly. Navigate with
  Serena's tools when the session has them; say in `NAME.md` whether they were available and
  used, with tool evidence or "not verified". If a workflow is unavailable on this surface,
  report that fact; never claim it ran.

## Compounding

- Before you start: read the lessons for the paths you will touch,
  `uv run python .agents/skills/lead/scripts/learnings.py paths AREA...` (the lead puts its
  output in the task text too), and follow them.
- At the end, ask once: did this task correct a wrong belief? If so, hold it in the cheapest
  form (a test first; a rule only if no test can; else none) and record it with `learnings.py
  add`. If not, write "Nothing to record" in NAME.md. Either way, say which in NAME.md.

### Tests that can fail

Promoted from the register on 2026-11-17, after lessons that recurred in the reviews of the
first commit: tests that passed when they should have failed, and two heuristics (the agent
guard and the process scan) that drew findings in every review round.

- A test of a derived or passed-on value feeds an input that differs from the default: a
  hard-coded value must fail it.
- A guard for every test is put in place when `tests/conftest.py` is imported, not by a
  fixture, and is tested from a test that requests no fixture.
- A scan against a list (order calls, client ids) names every entry of its source of truth
  and is read again when that source moves.
- A heuristic that reads commands or command lines (the agent guard, the process scan of
  check 5) is tested with a property over all its cases: each refused or counted case again
  inside the wrappers, paths and neutral options an agent or a user would add. A new finding
  adds its case to those lists, not a test of its own.
- For each test you add, say in NAME.md which fault makes it fail. If you did not try the
  fault, write "not verified".

## Finishing

1. The done command, with the last lines of each step kept:
   `uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`.
   Size limits are checked by the suite; change none.
2. One commit on this worktree's branch, clean worktree. Start no other Orca worker or
   terminal, push nothing and open no pull request. The runtime's own in-session subagents
   are allowed and bound by these same boundaries.
3. In `~/.cache/astrolabe/pr-lead/`, named after this worktree's folder: `NAME.md`, the pull
   request's body, and `NAME.squash.txt` (at most 13 lines of at most 76 characters). NAME.md
   has the three sections of `.github/pull_request_template.md`: `## Upstream references`
   (URL, version or commit, date read; the `UPSTREAM.md` row and `upstream-map.toml` entry are
   in the change), `## Evidence` (what ran and the last line of each check; what is not done)
   and `## Risk and rollback` (what can fail, and the one command that undoes the change).
   The gate refuses a body without all three. Nothing private in either file.
   Every claim in NAME.md and in your report carries its evidence: the file and line at your
   head commit, or the command and the output line that shows it. A claim without one is
   written "not verified". Every property you claim names the test that fails when it breaks.
4. `worker_done` in at most six sentences.

The trial refuses home paths with user names and any e-mail address, vendor contacts included, except reserved example domains, the configured agent trailers and unit names.
