# ADR-0002: NautilusTrader Execution & Atomic Stop Geometry

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E3

## Context and Problem Statement
Unprotected equity entries expose paper capital to gap and tail risk if client processes disconnect mid-bar.

## Decision Outcome
Run Stage-3B execution on pinned `NautilusTrader 2.0.0rc9` with pre-trade risk limits (`<= 2.00%` stop cap) and rate-budgeted (`<= 3,000 msg/min`) order management verified offline by `tests/test_risk_engine.py`.
