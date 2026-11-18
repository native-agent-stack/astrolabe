# ADR-0014: Stage-3B Finalized 12-Feed (`DF1..DF12`) Bitemporal Symbol Ranking & 8-Stage Convergence

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
Following `ADR-0013` abnormal ToD ranking and `sota-multi-model-workflows` `ADR-0024` (`sota-symbol-ranking-datafeeds.v3.10`), Stage-3B execution on `ib_ids.PAPER_PORT` (`4002`) must verify strict bitemporal PIT ordering (`ts_event_ns <= ts_recv_ns <= decision_ns`), `DF10` ToD normalization (`RVOL_tod >= 2.20`, `vol_z_tod >= 2.30`), `DF11` `<= 10.0 bps` collar, `900.0s` Wave-2 cooldown, and `ADR-0022 <= 2.00%` stop before paper order emission.

## Decision Outcome
1. **Stage-3B 12-Feed Bitemporal Handoff (`evaluate_stage3b_12feed_bitemporal_ranking_handoff`):** Enforce loopback `ib_ids.PAPER_PORT == 4002`, `ts_event_ns <= ts_recv_ns <= decision_ns`, `RVOL_tod >= 2.20`, `OFI >= 0.25`, `<= 10.0 bps` collar, and `900s` Wave-2 Zero-Loss survival (`standalone_alpha_claim = False`).
