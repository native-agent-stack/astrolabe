# ADR-0004: Research-Converged DataFeed & Strategy Contract

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E3

## Context and Problem Statement
IBKR paper execution across regular, extended (`04:00-20:00 ET`), and overnight (`20:00-04:00 ET`) sessions requires immutable provenance, Reg NMS Rule 612 tick-safe stop-limit collars, and explicit regime routing across the `2026-12-06` `23/5` `SIP` cutover.

## Decision Outcome
Enforce `validate_research_convergence_provenance` (`64`-hex input hash, `40`-hex `nautilus_trader` and `ib_async` pins, `E0-E4` grade, and `PHILOSOPHY_ONLY` sibling rule), `quantize_ibkr_paper_stop_limit` (`ADR-0022 <= 2.00%` stop cap and `<= 28.0 bps` `outside_rth` spread gate on `ib_ids.PAPER_PORT`), and `resolve_equities_datafeed_regime` in `astrolabe/research_convergence.py`.
