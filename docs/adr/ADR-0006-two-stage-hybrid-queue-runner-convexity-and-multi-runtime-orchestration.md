# ADR-0006: Two-Stage Hybrid Queue Entry, `50/50` Runner Convexity & Central Multi-Runtime Orchestration

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
Building on `ADR-0005` (`100x` round-lot `RiskAdverseQueueModel` scaling, `IBKR_PROTECTIVE_EXIT_RESERVE_MPM = 300` on `ib_ids.PAPER_PORT`, and `singleActiveBrokerHostOnly: true`), pre-wall (`<= 2026-10-15`) nanosecond `SIP` replay across `587` trading dates and `89` pre-registered trials (`t*(90) = 4.1735`) demonstrated that single-tranche micro gain-locks censor `97.8%` of intraday runners while pure passive queue waiting misses `13.17%` of immediate breakouts.

## Decision Outcome
1. **Two-Stage Hybrid Entry & Post-Gain-Lock `50/50` Runner Convexity:** Pair `50%` passive `hftbacktest::RiskAdverseQueueModel` (`bid_size * 100.0`) entry with `50%` passive queue completion or `120s` firm-ask join (`95.50%` participation), and split filled paper positions after Gain-Lock arming into `50%` Core Harvest plus a `50%` breakeven-floored Runner (`23x` RTH close survival improvement and `-42.4%` date-clustered `SE` reduction) while keeping initial stops strictly `<= 2.00%` (`ADR-0022` Rule 612 `StopLimitOrder`).
2. **Positive `OFI` / Close-Location Selectivity on Reversals:** Block cold reversal entries into negative order-flow imbalance (`OFI`) sweeps unless confirmed by bar close-location and `UCB` rank arbitration.
3. **Central Multi-Runtime Worker Supervision (`stablyai/orca`):** Coordinate isolated child worktree verification and `56`-thread stationary Linux `ext4` `CPCV` offloads under central multi-runtime orchestration (`oneModelPerConversation = true`) while maintaining `singleActiveBrokerHostOnly = true` on `ib_ids.PAPER_PORT`.
