# Astrolabe runbook

The owner's steps. No agent runs any of this: every lane run is started and attended by the
owner. Typing PAPER only proves a terminal, and agents have terminals too.

| Lane | Orders go to | Market data | What keeps it off a real-money account |
|---|---|---|---|
| IBKR paper | the IB Gateway logged in to Paper Trading, loopback, port 4002 | the same Gateway | `astrolabe/boundary.py` refuses unless the Gateway is a paper login, before and after every run |

## The three states of the Gateway session

| State | Gateway | What may run |
|---|---|---|
| Closed (the default) | not running | everything that needs no broker: tests, studies, skills, workers |
| Data session | logged in to paper, Read-Only API ticked | IBKR's own feeds and checks. The Gateway refuses orders from every client while the box is ticked |
| Order session | logged in, Read-Only API unticked, the owner attending | the lane runs below |

Read-Only API stays ticked at every other time. While the Gateway is logged in with the box
unticked, any process on this machine can reach port 4002: nothing inside the distro closes
that on its own. Order sessions are therefore attended and short.

## Before an order session

1. Start the Gateway with `~/Jts/ibgateway/1050/ibgateway` (its window shows through WSLg).
   Log in to **Paper Trading** and approve the login in IBKR Mobile.
2. In the Gateway, Configure > Settings > API > Settings:
   - socket port 4002;
   - Read-Only API unticked for the runs, and ticked again afterwards;
   - "Allow connections from localhost only" ticked;
   - "Master API client ID" empty. A client with that id receives every other client's orders
     (https://www.interactivebrokers.com/docs/tws-api/doc/order-management/master-client-id,
     read 2026-11-16);
   - API timestamps in UTC, which the engine's adapter expects (nautilus_trader v2.0.0rc9,
     crates/adapters/interactive_brokers/README.md; the Gateway's own label for this setting
     was not verified).
3. Not within about ten minutes of the Gateway's daily restart or shutdown time.
4. In IBKR's own window: the paper account has no open order and no position in AAPL, across
   every entry route. The lane refuses to submit if it sees one.
5. The owner's other project is not running a lane against this paper account.
6. `cd ~/src/astrolabe && git pull --ff-only && uv sync --locked`

The lane refuses unless all of this holds: the Gateway's settings file says paper trading; the
Gateway listens on port 4002 only; it reports exactly one account of the paper form; no other
API client is attached; and the checkout is clean, on `main` and equal to `origin/main`.
Details: `astrolabe/boundary.py`.

Optional and stricter: an allowlist of the paper account in `risk/boundary.contract.json`. The
owner creates it; the command shows the reported account and prints the contract only after
PAPER is typed:

```bash
mkdir -p risk && C="$(uv run python -m astrolabe.boundary contract)" && printf '%s\n' "$C" > risk/boundary.contract.json
```

## The four runs of the first result

| # | The owner types | When | A pass |
|---|---|---|---|
| 1 | `uv run python -m astrolabe.ibkr_paper restart` | Gateway logged in | Paper checks pass. Reconcile only, nothing placed. Exit 0 |
| 2 | `uv run python -m astrolabe.ibkr_paper order`, then `PAPER` | any time | One DAY LIMIT BUY of 1 share of AAPL, 2% under the bid: accepted, then cancelled, never filled |
| 3 | `uv run python -m astrolabe.ibkr_paper fill`, then `PAPER` | a regular NYSE session, more than 5 minutes before its close | BUY 2% above the ask and SELL of the filled quantity both filled; flat at stop |
| 4 | `uv run python -m astrolabe.ibkr_paper restart` | within 55 minutes of the start of run 3 | "Reconciled: the venue reports fill run ...'s BUY and SELL as FILLED and AAPL.XNAS is flat" |

Run 4 is the milestone: an engine-placed paper fill that the venue's own records confirm.

The engine asks the venue for the closed orders of the last 60 minutes, so run 4 compares only
when it starts within 58 minutes of the start of run 3. A later `restart` still runs every
check and shows any open order or position; it prints that the last fill run is outside the
reconciliation window and was not compared, and it does not fail for that.

Every run reads the account's state twice: when the engine stops the strategy (`at_stop`), and
again after the engine has shut down (`after_shutdown`). The second reading shows what the
stop-time cancel left and catches a fill that arrived late. A run passes only when both show
nothing working and no position in AAPL.

- **PASS** prints the path of the scrubbed record. Exit code 0.
- **FAIL** (exit 1) prints the problems. If an order may still be working, run `restart` before
  closing anything by hand: its record shows any open order or share. Give the lead only the
  printed problem lines, never the local record or the engine log.
- **REFUSED** (exit 2): nothing was sent. Fix the state the message names and run again.
- In order and fill mode every check runs again after PAPER is typed. A change to the Gateway
  in between (a restart, a new login, another port, another client) refuses.

Records live outside the repository, always under `~/.local/state/astrolabe/ibkr-paper/`
of the user who runs the lane (no environment variable moves them):

- `runs/`: the full local record. It can hold the account id and never leaves the machine.
- `logs/`: the engine's own output. It can show the account id and never leaves the machine.
- `evidence/`: the scrubbed record of each passed run. Only these go to the private evidence
  repository.

## Evidence

For the first result the owner copies the scrubbed records by hand, then commits and pushes:

```bash
gh repo clone "$(gh repo view --json owner --jq .owner.login)/astrolabe-evidence" ~/src/astrolabe-evidence
mkdir -p ~/src/astrolabe-evidence/ibkr-paper
cp ~/.local/state/astrolabe/ibkr-paper/evidence/*.json ~/src/astrolabe-evidence/ibkr-paper/
```

Copy from `evidence/` only. Never copy from `runs/` or `logs/`.

## Once, by the owner

- **The main branch's rules on GitHub,** after the first green `check`. From then on every
  change arrives by pull request with a green `check` from GitHub Actions:

  ```bash
  gh api --method POST "repos/{owner}/{repo}/rulesets" --input - <<'EOF'
  {"name":"main","target":"branch","enforcement":"active","bypass_actors":[],
   "conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}},
   "rules":[{"type":"deletion"},{"type":"non_fast_forward"},
    {"type":"required_status_checks","parameters":{"strict_required_status_checks_policy":true,
     "do_not_enforce_on_create":false,
     "required_status_checks":[{"context":"check","integration_id":15368}]}}]}
  EOF
  ```

  Check it with `gh api "repos/{owner}/{repo}/rules/branches/main" --jq '[.[].type]|sort'`,
  which prints `["deletion","non_fast_forward","required_status_checks"]`.
- **Trust the folder in Claude Code:** start `claude` in `~/src/astrolabe` once and accept. The
  guard hook in `.claude/settings.json` and the project's skills load only in a trusted folder.

## Orca

The command is `orca-ide`; Orca's `orca-cli` and `orchestration` skills are in
`.agents/skills/`. Its runtime is the `orca-serve` user service on the loopback address. If it
is down, report that or run `systemctl --user start orca-serve`; never `orca-ide open`, `serve`
or `serve service install`, which can listen on every interface.
