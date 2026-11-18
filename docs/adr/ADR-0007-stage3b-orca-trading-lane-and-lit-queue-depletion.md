# ADR-0007: Stage-3B Dual-Machine Orca Trading Lane & Lit-Only Queue Depletion (`H090`)

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
Pre-registered Trial `#90` (`H090`, `<= 2026-10-15`, `+20.84` bps net `1x` / `+15.43` bps net `2x` on tight-spread `<= 10.0` bps passive bids under fixed `ADR-0022` `2.00%` catastrophe stops) and its adversarial audit surfaced an execution-parity law: off-exchange `FINRA` `TRF`/`ADF` prints (`x == 'D'`) and odd-lots (`'I'`) never deplete lit exchange L1 queues (`89.0%` vs `63.6%` true lit fill rate). Stage-3B also required an explicit `T3B_IBKR_4002_NAUTILUS_PAPER` Dual-Machine Orca lane validator.

## Decision Outcome
1. **Lit-Only Round-Lot Queue Depletion (`is_lit_round_lot_queue_print`):** Exclude `exchange_code == 'D'`, `'I'` odd-lot condition prints, and `share_size < 100` from advancing `hftbacktest::RiskAdverseQueueModel` L1 queue position.
2. **Stage-3B Dual-Machine Orca Trading-Lane Gate (`validate_stage3b_orca_trading_lane`):** Enforce `ib_ids.PAPER_PORT` only, `singleActiveBrokerHostOnly` split-brain rejection across paired workstations, `0 < max_stop_pct <= 2.00%`, and `300` msg/min protective exit headroom out of `3,000` msg/min while offloading `56`-thread pre-wall sweeps onto `Noesis-Carbon` `ext4`.
