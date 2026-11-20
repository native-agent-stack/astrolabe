# ADR-0016: Post-ANT Whole-Market Merit Intake, `ADR-0064` Quarantine & Shared Lossless Kernel Parity (`v3.11.1`)

- **Status:** Accepted
- **Date:** 2026-11-20
- **Evidence Grade:** E4

## Context and Problem Statement
Transitioning Stage-3B IBKR Paper `P4 (:4002)` to the Post-ANT whole-market era requires symbol-agnostic admission of any `sota.active-market-targets.v1` merited US equity (`MERIT_TIER_1_CORE`/`MERIT_TIER_2_ACTIVE`, `>= 3` manifested `DF1..DF12` pillars), strict `ADR-0064` quarantine of negative-expectancy/noisy strategies, canonical `sota-<slug>-p4-<seq>` client IDs (`sota.strategy-contract.v2`), and exact-`Decimal` `4x` leverage-aware stop capping (`<= 1.35%` equity risk, `ADR-0022 <= 2.00%`, `TP OFF`).

## Decision Outcome
1. **Post-ANT Merit & Quarantine Gate (`evaluate_post_ant_merit_target_for_ibkr_p4`):** Enforce loopback `127.0.0.1:4002`, allowlist the `6` proven SOTA strategy families while hard-blocking quarantined strategies, require `df_pillars_manifested >= 3` and `sec_dilution_clean=True`, enforce universal FINRA 4210 `30d` new-issue `1.0x` cash fallback, and bind exact-`Decimal` `ROUND_FLOOR` `4x` leverage stop bounds into `quantize_ibkr_p4_lossless_trail_order`.
2. **Canonical Attribution (`build_p4_canonical_client_order_id`):** Emit deterministic `sota-<slug>-p4-<seq>` client order IDs under `PROTECTIVE_FILTER_OR_GOVERNANCE` (`standalone_alpha_claim = False`).
