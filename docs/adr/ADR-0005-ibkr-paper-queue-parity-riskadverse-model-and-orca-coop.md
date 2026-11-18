# ADR-0005: IBKR Paper Queue Parity, `RiskAdverseQueueModel` Round-Lot Scaling & Orca Co-op Isolation

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
Converging `astrolabe` with the `424`-repository research catalog (`nautechsystems/nautilus_trader` `v2.0.0rc9`, `erdewit/ib_async`, `nkaz001/hftbacktest`, `stablyai/orca`, and `ComposioHQ/agent-orchestrator`) requires strict passive-queue realism, explicit protective-exit rate reservation on `ib_ids.PAPER_PORT`, and single-writer broker isolation across the portable Apple M5 Max cockpit and the stationary Threadripper / RTX 4090 workstation.

## Decision Outcome
1. **CTA/UTP `100x` Round-Lot Queue Parity (`hftbacktest::RiskAdverseQueueModel`):** Pre-promotion passive-limit simulations for IBKR Paper execution must scale consolidated `SIP` round-lot quote sizes (`bid_size * 100.0`, `ask_size * 100.0`) so passive queue depletion and Glosten–Milgrom adverse selection match venue fills.
2. **Protective Exit Rate Reserve & Native Gateway Boundary:** Reserve `300 msg/min` (`IBKR_PROTECTIVE_EXIT_RESERVE_MPM = 300`, capping new entries at `2,700 msg/min` within the `3,000 msg/min` loopback `ib_ids.PAPER_PORT` budget) for `ADR-0022 <= 2.00%` `StopLimitOrder` protective exits (`25..75 bps` Rule 612 collars), while rejecting unattended third-party `IBC` credential injection and hard-refusing non-paper Gateway or TWS endpoints.
3. **Single-Active-Broker-Host Orca Co-op (`stablyai/orca`):** Enforce `singleActiveBrokerHostOnly: true` across the portable and stationary workstation mesh (`orca serve --no-pairing --refuse-public`) so the stationary workstation executes simulation and `CPCV` workers without opening a competing IBKR Paper writer while another host holds the active broker lease.
