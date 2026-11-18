# ADR-0003: Upstream-First Public Open-Core & v3.0.0 Org Convergence

- **Status:** Accepted
- **Date:** 2026-11-18
- **Evidence Grade:** E3

## Context and Problem Statement
Maintaining duplicate execution adapters across repositories causes drift against pinned upstream sources and wastes private CI quota.

## Decision Outcome
Designate `astrolabe` as the single Stage-3B public open-core IBKR paper engine in `github.com/native-agent-stack`, gated by `upstream-map.toml`, `40`-char SHA-pinned GitHub Actions, and zero-leak tree scans.
