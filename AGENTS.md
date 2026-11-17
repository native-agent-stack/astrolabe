# Astrolabe
IBKR paper-trading lane on NautilusTrader, built and run by agents. State is in files, not in a
conversation: ROADMAP.md (what is next), UPSTREAM.md (what we rest on), docs/RUNBOOK.md (the
owner's runs).

## Boundaries (the owner's; not yours to change)
- Paper only: never create, enable or test a route to a real-money account. For IBKR: the IB
  Gateway's paper port on the loopback address only, never a live port or a Live Trading login.
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

## Compounding (the core practice)
Every study, run and review leaves a record. Every verified lesson is captured once with its
evidence, handed to the next task in the same area, and promoted when it recurs: into a test, a
rule, a skill, a study or the simulation's model. Strategies improve only this way: observe,
state the hypothesis before testing it, test on history, then on paper, compare paper with its
replay, and record what was learned. Procedure: the `learning-loop` skill.

## The lead orchestrates
The lead does not write the code. It takes the next item from ROADMAP.md, starts one worker per
item in its own Orca worktree, has another model family review, lands only a tested tree through
the merge gate, updates the roadmap, and goes on. It asks the owner only when every option
crosses a boundary. Procedure: the `lead` skill.

## A worker delivers one task
Install, integrate or build on a cited, maintained upstream (NautilusTrader first) before writing
your own; its `UPSTREAM.md` row and `upstream-map.toml` entry land with it. Use exact arithmetic
for prices and quantities (`Price`, `Quantity`, `Decimal`), never a float sent to the venue.
Never weaken a test to make it pass. Change a generated file (`uv.lock`, the Orca skill copies)
only through its generator. A check is evidence only for the exact change it ran on. Every pull
request names its upstream sources, shows what ran, and says how to undo it.
Done means this ran and passed and you reported its output:
`uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`
