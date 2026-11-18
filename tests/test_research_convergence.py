"""Offline unit tests for astrolabe.research_convergence (ADR-0004)."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from astrolabe import ib_ids, research_convergence
from astrolabe.research_convergence import (
    PINNED_IB_ASYNC_COMMIT,
    PINNED_NAUTILUS_TRADER_COMMIT,
    quantize_ibkr_paper_stop_limit,
    resolve_equities_datafeed_regime,
    validate_research_convergence_provenance,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VALID_INPUT_SHA = "c" * 64
VALID_UPSTREAMS = {
    "nautilus_trader": PINNED_NAUTILUS_TRADER_COMMIT,
    "ib_async": PINNED_IB_ASYNC_COMMIT,
}


@pytest.mark.parametrize("grade", ["E0", "E1", "E2", "E3", "E4"])
def test_valid_provenance_passes_all_evidence_grades(grade: str) -> None:
    validate_research_convergence_provenance(VALID_INPUT_SHA, VALID_UPSTREAMS, grade)


@pytest.mark.parametrize(
    ("input_sha", "commits", "grade", "authority", "match"),
    [
        ("short", VALID_UPSTREAMS, "E3", "PHILOSOPHY_ONLY", "64-character"),
        ("C" * 64, VALID_UPSTREAMS, "E3", "PHILOSOPHY_ONLY", "64-character"),
        (VALID_INPUT_SHA, {}, "E3", "PHILOSOPHY_ONLY", "non-empty"),
        (
            VALID_INPUT_SHA,
            {"nautilus_trader": PINNED_NAUTILUS_TRADER_COMMIT},
            "E3",
            "PHILOSOPHY_ONLY",
            "ib_async",
        ),
        (
            VALID_INPUT_SHA,
            {"nautilus_trader": "bad", "ib_async": PINNED_IB_ASYNC_COMMIT},
            "E3",
            "PHILOSOPHY_ONLY",
            "40-character",
        ),
        (
            VALID_INPUT_SHA,
            {**VALID_UPSTREAMS, "meridian": "a" * 40},
            "E3",
            "PHILOSOPHY_ONLY",
            "PHILOSOPHY_ONLY",
        ),
        (
            VALID_INPUT_SHA,
            {**VALID_UPSTREAMS, "sota-mover-ranker": "b" * 40},
            "E3",
            "PHILOSOPHY_ONLY",
            "PHILOSOPHY_ONLY",
        ),
        (VALID_INPUT_SHA, VALID_UPSTREAMS, "E5", "PHILOSOPHY_ONLY", "E0..E4"),
        (VALID_INPUT_SHA, VALID_UPSTREAMS, "E3", "DIRECT_IMPORT", "PHILOSOPHY_ONLY"),
    ],
)
def test_provenance_rejects_violations(
    input_sha: str,
    commits: dict[str, str],
    grade: str,
    authority: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        validate_research_convergence_provenance(
            input_sha,
            commits,
            grade,
            other_repos_authority=authority,
        )


@pytest.mark.parametrize(
    ("entry_px", "stop_pct", "spread_bps", "side", "outside_rth", "expected_tick"),
    [
        (152.347, 1.80, 6.5, "BUY", False, Decimal("0.01")),
        (24.10, 2.00, 18.0, "SELL", True, Decimal("0.01")),
        (0.8523, 1.50, 22.0, "BUY", True, Decimal("0.0001")),
        (1.01, 2.00, 12.0, "BUY", False, Decimal("0.0001")),
    ],
)
def test_quantize_ibkr_paper_stop_limit_invariants(
    entry_px: float,
    stop_pct: float,
    spread_bps: float,
    side: str,
    outside_rth: bool,
    expected_tick: Decimal,
) -> None:
    order = quantize_ibkr_paper_stop_limit(
        entry_px=entry_px,
        stop_pct=stop_pct,
        nbbo_spread_bps=spread_bps,
        side=side,
        outside_rth=outside_rth,
    )
    assert order["paper_port"] == ib_ids.PAPER_PORT == 4002
    assert order["gateway_host"] == "127.0.0.1"
    assert order["order_type"] == "STP_LMT"
    assert 0.0 < order["realized_stop_pct"] <= 2.0
    assert order["tick_size"] == expected_tick

    q_entry = order["entry_px"]
    q_stop = order["stop_px"]
    q_limit = order["limit_px"]
    if side == "BUY":
        assert order["stop_order_side"] == "SELL"
        assert Decimal("0") < q_limit <= q_stop < q_entry
    else:
        assert order["stop_order_side"] == "BUY"
        assert q_entry < q_stop <= q_limit


def test_quantize_rejects_outside_rth_wide_spread_and_adr0022_breaches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="28.0 bps cap"):
        quantize_ibkr_paper_stop_limit(
            entry_px=50.0,
            stop_pct=1.5,
            nbbo_spread_bps=28.01,
            side="BUY",
            outside_rth=True,
        )
    # Regular session permits spreads > 28 bps while keeping tight collar.
    rth_ok = quantize_ibkr_paper_stop_limit(
        entry_px=50.0,
        stop_pct=1.5,
        nbbo_spread_bps=32.0,
        side="BUY",
        outside_rth=False,
    )
    assert rth_ok["outside_rth"] is False

    with pytest.raises(ValueError, match="ADR-0022"):
        quantize_ibkr_paper_stop_limit(
            entry_px=50.0,
            stop_pct=2.01,
            nbbo_spread_bps=10.0,
        )
    with pytest.raises(ValueError, match="finite"):
        quantize_ibkr_paper_stop_limit(
            entry_px=float("nan"),
            stop_pct=1.5,
            nbbo_spread_bps=10.0,
        )

    monkeypatch.setattr(ib_ids, "PAPER_PORT", 7497)
    with pytest.raises(ValueError, match="PAPER_PORT"):
        quantize_ibkr_paper_stop_limit(
            entry_px=50.0,
            stop_pct=1.5,
            nbbo_spread_bps=10.0,
        )


def test_resolve_equities_datafeed_regime_across_sessions_and_cutover() -> None:
    # Regular XNYS session: Tuesday 2026-11-17 10:00 ET (15:00 UTC)
    rth = resolve_equities_datafeed_regime(datetime(2026, 11, 17, 15, 0, tzinfo=UTC))
    assert rth["session"] == "XNYS_RTH"
    assert rth["primary_feed"] == "SIP"
    assert rth["execution_venue"] == "IBKR_RTH"
    assert rth["is_rth"] is True
    assert rth["outside_rth"] is False
    assert rth["cutover_regime"] == "pre_2026_12_06_24x5_boats"

    # Premarket 07:00 ET (12:00 UTC) -> EXTENDED_SIP
    pre = resolve_equities_datafeed_regime(datetime(2026, 11, 17, 12, 0, tzinfo=UTC))
    assert pre["session"] == "EXTENDED_SIP"
    assert pre["primary_feed"] == "SIP"
    assert pre["execution_venue"] == "IBKR_OUTSIDE_RTH"
    assert pre["outside_rth"] is True

    # Overnight pre-Dec-6: Tuesday 2026-11-17 22:00 ET (2026-11-18 03:00 UTC) -> BOATS
    boats = resolve_equities_datafeed_regime(datetime(2026, 11, 18, 3, 0, tzinfo=UTC))
    assert boats["session"] == "OVERNIGHT"
    assert boats["primary_feed"] == "BOATS"
    assert boats["execution_venue"] == "IBKR_OUTSIDE_RTH"
    assert boats["outside_rth"] is True

    # Post-Dec-6 23/5 SIP cutover: Sunday 2026-12-06 20:30 ET (2026-12-07 01:30 UTC) -> pause
    pause = resolve_equities_datafeed_regime(
        datetime(2026, 12, 7, 1, 30, tzinfo=UTC),
        enable_dec6_23x5_sip=True,
    )
    assert pause["session"] == "SIP_23X5_PAUSE"
    assert pause["cutover_regime"] == "post_2026_12_06_23x5_sip"

    # Post-Dec-6 23/5 SIP active overnight: Sunday 2026-12-06 21:30 ET (2026-12-07 02:30 UTC)
    sip_23x5 = resolve_equities_datafeed_regime(
        datetime(2026, 12, 7, 2, 30, tzinfo=UTC),
        enable_dec6_23x5_sip=True,
    )
    assert sip_23x5["session"] == "OVERNIGHT"
    assert sip_23x5["primary_feed"] == "SIP"
    assert sip_23x5["execution_venue"] == "IBKR_OUTSIDE_RTH"
    assert sip_23x5["cutover_regime"] == "post_2026_12_06_23x5_sip"

    # Thanksgiving 2026-11-26 -> CLOSED; Black Friday 2026-11-27 13:30 ET -> EXTENDED_SIP
    thx = resolve_equities_datafeed_regime(datetime(2026, 11, 26, 15, 0, tzinfo=UTC))
    assert thx["session"] == "CLOSED"
    bf_post = resolve_equities_datafeed_regime(datetime(2026, 11, 27, 18, 30, tzinfo=UTC))
    assert bf_post["session"] == "EXTENDED_SIP"
    assert bf_post["is_rth"] is False

    # Naive timestamp rejected
    with pytest.raises(ValueError, match="time zone"):
        resolve_equities_datafeed_regime(datetime(2026, 11, 17, 15, 0))


def test_module_stays_pure_stdlib_without_heavy_imports() -> None:
    assert research_convergence.ADR0022_MAX_STOP_PCT == 2.0
    code = (
        "import sys, astrolabe.research_convergence\n"
        "print([m for m in ('exchange_calendars', 'pandas') if m in sys.modules])\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert proc.stdout.strip() == "[]"
