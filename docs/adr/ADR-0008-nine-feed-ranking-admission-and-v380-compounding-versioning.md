# ADR-0008: Nine-Feed PIT Ranking Admission (`DF1`–`DF9`) & `v3.8.0` Two-Track Compounding Versioning

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
Following `ADR-0007` (`T3B_IBKR_4002_NAUTILUS_PAPER` Orca lane and `H090` lit-only queue depletion), `v3.8.0` unified all Stage-3 execution engines onto the Nine-Feed PIT Ranking contract (`sota-ranking-datafeeds.v3.8`, `DF1`–`DF9`) and 4-hash monotonic `sota-version` promotion governance.

## Decision Outcome
1. **Stage-3B `DF6` L1 Quote/Shortable & 6-Pillar Admission:** Bind `astrolabe` paper execution on `ib_ids.PAPER_PORT` to `DF6` L1 NBBO/shortable telemetry (`entry_spread_bps <= 10.0` passive-bid gate) and upstream `DF4` (`XNYS` `RVOL_tod`) + `DF7`/`DF8` (`FINRA` `T-1`/`T+8`) 6-pillar ranking admission.
2. **Two-Track Compounding Versioning (`v3.8.0`):** Require 4-hash SHA-256 provenance (`input_lake_sha256`, `upstream_18_pins_sha256`, `ranking_9feeds_sha256`, `adr_id`), separating `ALPHA_STRATEGY_MINOR` (`DSR >= 0.95`) from `PROTECTIVE_FILTER_OR_GOVERNANCE` (`H090`, `standalone_alpha_claim = False`).
