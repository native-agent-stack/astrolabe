# Roadmap

What is next, kept by the lead. A line changes when it stops being true. Every item lands
through the merge gate with its upstream sources, its evidence and the way to undo it.

## Now: the first result

One engine-placed paper fill that the venue's own records confirm, in runs the owner starts
(`docs/RUNBOOK.md`).

- [ ] The first task through the gate: another model family reviews the last fixes to the
      agent guard (`tools/guard/`) and to the process scan of check 5, made after the third
      review round of the first commit.
- [ ] The main branch's rules on GitHub (the owner).
- [ ] Run 1, `restart`: the paper checks pass and nothing is placed.
- [ ] Run 2, `order`: one order accepted, then cancelled.
- [ ] Run 3, `fill`: a buy and a sell both filled, flat at stop.
- [ ] Run 4, `restart`: the venue reports run 3's orders as filled.
- [ ] The scrubbed records in the private evidence repository (the owner).

## Track L: the learning loop (the core practice)

- [x] The first lessons from the bootstrap reviews are in `docs/learnings.jsonl` (twenty,
      each held by a test).
- [x] The first promotions: eleven lessons that recurred became the worker rules section
      "Tests that can fail", with a property test for each of the two heuristics.
- [ ] The private register for strategy lessons, with its trial ledger and session reviews.
- [ ] "Astrolabe learning", nightly: the mover study, the day's replays and reviews, candidate
      lessons, a short report. Created disabled; the owner enables it.
- [ ] "Astrolabe review", weekly: the week's records, lessons due for promotion, next studies.

## Track S: skills in the native workflow

- [ ] The skills tooling: selection rule, lock, install, check and removal, a dispatcher, a
      separate environment for skill scripts, and its tests.
- [ ] Wave 1, backtest: the strategy-research workflow with its skills, and the adapters that
      feed it from the catalog and from simulation results.
- [ ] Wave 2, early movers: the mover study and the momentum screens, offline on catalog data.
- [ ] Wave 3, paper sessions: the trade-memory loop, risk and regime skills.
- [ ] Wave 4, research: the equity-research skills and the fact-check method.
- [ ] Own skills: `skill-runtime`, `research-pipeline`, `nautilus-docs`, `ibkr-tws-docs`.

## Track R: automation around the manual login

- [ ] `deploy/setup-repo.sh` and the read-only Gateway probe with its unit, timer and tests.
- [ ] Evidence sync to the private evidence repository.
- [ ] The Orca automations "Astrolabe landing" and "Astrolabe upkeep", created disabled.
- [ ] Alerting that leaves the machine, if the owner opens the account for it.
- [ ] A check of the order id against a stored high-water mark before each run.

## Track M: the mover lane

Early entry on extreme movers, with automatic gain protection and automatic entry and re-entry
in the early stage, in all four sessions. Every piece runs first in the simulation, then on
live data with simulated fills, then on attended paper.

- [ ] 1. Feed and catalog: the market-wide data client (data only), the recorder, the private
      catalog seeded from history.
- [ ] 2. Radar: per-minute features and ranking, replayed on history and run live as a report.
- [ ] 3. A simulation venue shaped like IBKR: session order rules, halts, costs, the message
      pacer and the efficiency-ratio meter, and the pre-registration ledger.
- [ ] 4. Setups in the simulation: the opening-range breakout on stocks in play first, then
      early-volume ignition and the first pullback with the protection ladder and re-entry,
      then the episodic pivot. Each is a pre-registered study.
- [ ] 5. Sandbox lane: the live feed with simulated fills, all four sessions.
- [ ] 6. Attended paper, one session kind at a time: regular, pre-market, after-hours,
      overnight. Each needs its own passed record.
- [ ] 7. IBKR's own feeds: the scanner, halt and shortable ticks through a read-only reader,
      and quote parity. Then order-book depth, if a study calls for it.

## Later, each behind the owner's decision

- Unattended paper order runs, only after agents have no root, interop is off, and the Gateway
  and the lane run under their own user in a private network namespace.
- The engine's next release, seven days after its upload, with the lane tests run again.
- The first change after 2026-11-17T21:00Z removes the engine's cooldown exemption from
  `pyproject.toml` and from the upstream gate.
