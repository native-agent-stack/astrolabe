# ADR-0011: Stage-3B Two-Stage Lit-Bid to `<= 10 bps` Collar Escalation & Dec-6 Pause Gate

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
Trial `#94` (`H094`, `meridian` `0061`) proved `100%` passive L1 bid resting misses `33.54%` (`107 / 319`) of immediate lit breakouts (`-97.274` bps adverse selection), motivating Trial `#95` (`H095`) 2-stage `15s` lit-bid -> `<= 10.0` bps collar escalation alongside `2026-12-06` `20:00–21:00` ET `23/5` `SIP` pause gating.

## Decision Outcome
1. **Two-Stage Collar & Pause Gate (`evaluate_stage3b_two_stage_collar_escalation`):** Rest passively on `ib_ids.PAPER_PORT` (`4002`) for `15.0` s, escalate unfilled runaways to `STAGE2_MARKETABLE_COLLAR_LIMIT` strictly when `0 < spread_bps <= 10.0`, and fail closed (`BLOCKED_SIP_23X5_DAILY_PAUSE`) during `20:00–21:00` ET post-`2026-12-06`.
