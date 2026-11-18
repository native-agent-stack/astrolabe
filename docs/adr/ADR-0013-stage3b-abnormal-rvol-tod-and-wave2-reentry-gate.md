# ADR-0013: Stage-3B Abnormal Time-of-Day `RVOL` Size-Bias Purge & Wave-2 Re-Entry Gate

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
Raw volume sorting admits mega-caps trading at ordinary normal rates (`1.01x` / `0.70x` of 20-day time-of-day volume) ahead of genuine abnormal volume shocks, while post-harvest (`50/50` Core + Runner) Wave-2 pullback re-entries require Zero-Loss floor survival inside `ADR-0022 <= 2.00%` and `<= 10.0` bps collar limits.

## Decision Outcome
1. **Abnormal `RVOL-TOD` & Wave-2 Gate (`evaluate_stage3b_wave2_reentry_and_rvol_tod_gate`):** Purge `< 1.65x` normal-rate size bias on `ib_ids.PAPER_PORT`, require `stage1_rvol_tod >= 2.20` + `OFI >= 0.25` for Wave-1, and gate Wave-2 re-entries on `core_harvest_completed` + `pullback_held_zero_loss_floor` (`standalone_alpha_claim = False`).
