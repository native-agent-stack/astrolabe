---
name: learning-loop
description: "Use before starting a task and after a review, a landing, a study or a paper or sandbox session: read the lessons for the paths you touch, record what was learned with its evidence, and promote a lesson that recurs."
---

# The learning loop

Compounding is this project's core practice (AGENTS.md). A lesson is captured once, with its
evidence, handed to the next task that touches the same area, and promoted when it recurs.
There are two registers and one rule.

The command is `uv run python .agents/skills/lead/scripts/learnings.py`, called `learnings.py`
below. Its jobs are `add`, `mark`, `branch`, `draft`, `paths`, `due`, `stale` and `scorecard`;
run it with `--help` for the arguments.

## Before a task

1. Read the lessons for the paths you will touch:
   `learnings.py paths <path>... --cite <your worktree name>`.
   The dispatch helper already puts them into a worker's task text; read them there first.
2. For strategy work, your task text names the study records and session reviews you may use.
   They live in the private evidence repository. Use what the task text quotes and nothing
   else from there.

## After a review, a landing or a task

1. For each finding a review confirmed, decide what would have caught it: a test first, a rule
   only if no test can, a skill or a gate check once it has recurred. Record it with
   `learnings.py add`, giving the branch, the builder, the class, the area, the lesson in one
   sentence, the form it took and where that lives, and the source.
2. `learnings.py draft` turns gate output or a review record into one `add` command per open
   finding. Read each before running it.
3. If nothing was learned, say so in your report. An empty lesson is not recorded.
4. `learnings.py due` lists what has recurred and is due for promotion. The lead turns each
   into a task, and `learnings.py mark` records the promotion.

## The strategy loop

Strategies improve only through these five steps, in this order.

1. **Observe.** The radar's record, the daily mover study, and every sandbox and paper session
   with its replay.
2. **Hypothesize.** State the hypothesis, its parameter space, its data windows, its holdout
   and its metric in the trial ledger before any test runs.
3. **Test on history.** A walk-forward study in the simulation. Every trial counts. The owner's
   pass-mark, written before any figure was seen, decides.
4. **Test on paper.** A challenger runs on live data with simulated fills, then on attended
   paper, beside the current champion. The gap between a paper session and its replay is
   measured, and it calibrates the simulation.
5. **Learn and promote.** A verified lesson becomes a new study, a strategy variant, a
   calibration of the simulation, a test or a rule. A parameter value reaches the paper lane
   only as a revision of the owner's values file, which the owner accepts. Every promotion is
   one revert.

## What never happens

- No result is looked at before its study is registered.
- Holdout windows stay untouched during a search.
- Nobody who has seen a figure proposes a pass-mark or a value. Those are the owner's.
- Tuned values, per-symbol results and lessons that carry figures never enter this repository.
  They go to the private evidence repository.
- A lesson never changes a boundary. Only the owner does.
