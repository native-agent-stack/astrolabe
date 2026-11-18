# ADR-0010: Stage-3B Loopback Gateway & Staged-Basket Readiness Probe

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
When IB Gateway Paper is offline on `127.0.0.1` (`ib_ids.PAPER_PORT`), Stage-3B orders stage cleanly in `sota_ibkr_staged_basket.csv` without silent live fallback or unhandled socket exceptions.

## Decision Outcome
1. **Fail-Closed Readiness Probe (`probe_stage3b_ibkr_paper_readiness`):** Verify `ib_ids.HOST == "127.0.0.1"` and `ib_ids.PAPER_PORT`, reporting `READY_PAPER_4002` when open and `GATEWAY_OFFLINE_STAGED_ONLY` otherwise (`paper_only = True`).
