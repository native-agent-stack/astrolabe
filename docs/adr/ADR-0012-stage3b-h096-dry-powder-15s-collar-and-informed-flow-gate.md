# ADR-0012: Stage-3B `H096` Pure `15s` Dry-Powder Collar & Informed-Flow Joint Gate

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E4

## Context and Problem Statement
Trial `#95` (`H095`) showed Stage-1 `[0..15s]` resting bids bleed `-8.736` bps `1x` (`59.7%` seller sweeps), and Trial `#96` (`H096`, `588d27755bad`) proved joint necessity (`+29.564` bps `1x` lift): bare `OFI > 0` fails (`-12.729` bps `1x`), whereas `OFI >= 0.25 & (ISO_F >= 0.15 | BJZZ >= 0.12)` + `15s` pure dry-powder bid-survival + `<= 10.0` bps collar (`n = 52`) earns `+16.835` bps `1x` / `+11.366` bps `2x` (`3/3` years positive, `11/15` disjoint CPCV `> 0`) under `PROTECTIVE_FILTER_OR_GOVERNANCE`.

## Decision Outcome
1. **Pure `15s` Dry-Powder Collar (`evaluate_stage3b_dry_powder_15s_collar`):** Hold `limit_px = None` during `[0, 15s)` (`0` resting orders) on `ib_ids.PAPER_PORT`, enforce `OFI >= 0.25 & (ISO_F >= 0.15 | BJZZ >= 0.12)` + `15s` lit bid-survival, and release a `<= 10.0` bps collar (`standalone_alpha_claim = False`).
