# ADR-0005: IBKR Paper Queue Parity, `RiskAdverseQueueModel` Round-Lot Scaling & Orca Co-op Isolation

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
Converging `native-agent-stack/astrolabe` with the `424`-repository research catalog (`Canon 0169..0176`, `Meridian 0021`/`0035`/`0037`/`0057`, `nautechsystems/nautilus_trader` `v2.0.0rc9`, `erdewit/ib_async`, `nkaz001/hftbacktest`, `stablyai/orca`, and `ComposioHQ/agent-orchestrator`) requires strict passive-queue realism, explicit protective-exit rate reservation on loopback port `4002`, and single-writer broker isolation across the portable Apple M5 Max and stationary `Noesis` workstations.

## Decision Outcome
1. **CTA/UTP `100x` Round-Lot Queue Parity (`hftbacktest::RiskAdverseQueueModel`):** Pre-promotion passive-limit simulations for IBKR Paper execution must scale consolidated `SIP` round-lot quote sizes (`bid_size * 100.0`, `ask_size * 100.0`) so passive queue depletion and Glosten–Milgrom adverse selection match live venue fills.
2. **Protective Exit Rate Reserve & Native Gateway Boundary (`Meridian ADR-0021` / `ADR-0037`):** Reserve `300 msg/min` (`IBKR_PROTECTIVE_EXIT_RESERVE_MPM = 300`, capping new entries at `2,700 msg/min` within the `3,000 msg/min` loopback `127.0.0.1:4002` budget) for `ADR-0022 <= 2.00%` `StopLimitOrder` protective exits (`25..75 bps` Rule 612 collars), while rejecting unattended third-party `IBC` credential injection (`Meridian ADR-0021`) and hard-refusing real-money ports (`4001`, `7496`).
3. **Single-Active-Broker-Host Orca Co-op (`stablyai/orca`):** Enforce `singleActiveBrokerHostOnly: true` across the Mac M5 Max <-> `Noesis` (`orca serve --no-pairing --refuse-public`) mesh so stationary `Noesis` executes simulation and `CPCV` workers without opening a competing IBKR Paper writer while another host holds the active broker lease.
