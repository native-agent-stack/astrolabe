# ADR-0001: IBKR Paper-Only Loopback Port 4002 Firewall

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E3

## Context and Problem Statement
Autonomous paper execution on Interactive Brokers must be structurally incapable of routing orders to live accounts or external interfaces.

## Decision Outcome
Bind IB Gateway paper sessions exclusively to loopback port `4002` with paper account identifiers verified before any order submission, enforced by `astrolabe.ib_ids` and `tests/test_paper_only.py`.
