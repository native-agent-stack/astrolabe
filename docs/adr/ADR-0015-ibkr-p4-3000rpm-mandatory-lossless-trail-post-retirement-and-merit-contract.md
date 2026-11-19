# ADR-0015: IBKR Paper `P4 (:4002)` `3,000 req/min` TWS-Compliant Mandatory Lossless Trailing, `POST` Retirement & Merit-Ladder Feeder Contract

- **Status:** Accepted
- **Date:** 2026-11-19
- **Evidence Grade:** E4

## Context and Problem Statement
Converging `astrolabe` (`ib_ids.PAPER_PORT == 4002`, `NautilusTrader v2.0.0rc9`, `ib_async v2.1.0`) with the `24/5` Paper Side & `24/7` Pre-Wall Sim merit architecture requires exact-`Decimal` TWS-compliant mandatory lossless trailing on every Long and Short order across all sessions, dual-window rate pacing (`3,000 req/min` & `50 msgs/sec` with a locked `10%` protective-exit reserve), hard retirement of already-`POST` late chases (`POST_INCENTIVE_RETIRED`), slow-building `VDU` coil admission, live `BOATS >= $150k` isolation, and dimensionless (`%`/`bps`/`R`) merit-feeder receipts.

## Decision Outcome
1. **Exact-`Decimal` Session-Aware Lossless Trailing (`quantize_ibkr_p4_lossless_trail_order`):** Enforce loopback `127.0.0.1:4002`, Reg-NMS Rule 612 `ROUND_CEILING` (Long) / `ROUND_FLOOR` (Short) wire stops inside `ADR-0022 <= 2.00%`, `RTH_CORE` `SMART TRAIL LIMIT` (`outsideRth=False`) with strictly positive parent-`minTick`-quantized `lmtPriceOffset > 0`, `PRE_MARKET`/`POST_CLOSE` `SMART STP LMT` (`outsideRth=True`, `>= 1 tick` collar), `OVERNIGHT_BOATS` `exchange="OVERNIGHT" LMT` (`tif="DAY"`) + mandatory active `P4` guardian, and Dec-6 `20:00-21:00 ET` pause fail-closed rejection.
2. **Atomic Dual-Window Pacer & Setup / Merit Gates (`IbkrP4DualWindowRatePacer`, `evaluate_ibkr_p4_post_retirement_and_setup_gate`, `build_ibkr_p4_merit_feeder_receipt`):** Reserve `300/min` & `5/sec` strictly for protective exits, hard-retire `POST` late incentives, gate live `BOATS >= $150k`, enforce single-use `900s` Wave-2 pullback re-entry, and emit dimensionless `EB kappa=40` receipts pinned to `nautilus_trader`/`ib_async`/`hftbacktest`/`skfolio`/`ml4t-diagnostic`/`edgartools`/`statsmodels` under `PROTECTIVE_FILTER_OR_GOVERNANCE` (`standalone_alpha_claim = False`) until `HLZ` clears.
