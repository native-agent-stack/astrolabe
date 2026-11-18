# ADR-0009: Stage-3B Protective Entry Shield & `H091` Disjoint-Split CPCV Boundary

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
Trial `H091` (`56`-thread `Noesis-Carbon`, `286/435 = 65.75%` lit round-lot fill rate, `15/15` unique `C(6,2)` disjoint split Sharpes, `-23.73` bps `1x` net, `t = -1.2532 < 4.1760`) falsified relaxing `H090` (`<= 10.0` bps, `ADV_20d >= $20M`, `OFI >= 0.25`, `+20.84` bps `1x`) and raised the cumulative HLZ hurdle to `t*(92) = 4.1785`.

## Decision Outcome
1. **Stage-3B Protective Entry Shield (`evaluate_stage3b_protective_entry_shield`):** Enforce `PROTECTIVE_FILTER_OR_GOVERNANCE` (`standalone_alpha_claim = False`) on `ib_ids.PAPER_PORT` inside `ADR-0022 <= 2.00%`: `09:30`–`09:35` ET non-`8-K` taker whipsaw blackout, `<= 10.0` bps RTH / `<= 28.0` bps outside-RTH spread, `ADV_20d >= $20M`, `OFI >= 0.25`, and `1,800` s cooldown after `>= 5` consecutive losses (`0.25x` probation when `half_kelly <= 0` and `profit_factor < 0.75`).
