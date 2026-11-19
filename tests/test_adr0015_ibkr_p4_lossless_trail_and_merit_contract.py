"""Tests for ADR-0015 IBKR P4 (:4002) 3,000/min mandatory lossless trailing & merit contract."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest

from astrolabe.research_convergence import (
    IBKR_P4_ADR0015_VERSION,
    IBKR_P4_SOTA_UPSTREAM_PINS,
    IbkrP4DualWindowRatePacer,
    build_ibkr_p4_merit_feeder_receipt,
    evaluate_ibkr_p4_post_retirement_and_setup_gate,
    quantize_ibkr_p4_lossless_trail_order,
)

VALID_SHA256 = "9fa6b7f911850dac15ae51eb26c673dcbcf71f2a0b174d0819e3f625019d30d3"


def _nms_tick(px: Decimal) -> Decimal:
    return Decimal("0.01") if px >= Decimal("1.00") else Decimal("0.0001")


@pytest.mark.parametrize(
    ("entry_px", "side", "stop_pct"),
    [
        ("0.05", "BUY", "2.00"),
        ("0.99", "BUY", "2.00"),
        ("0.99", "SELL", "2.00"),
        ("0.995", "SELL", "0.50"),
        ("0.99995", "SELL", "2.00"),
        ("1.00", "BUY", "2.00"),
        ("1.01", "SELL", "2.00"),
        ("5.00", "BUY", "2.00"),
        ("142.37", "SHORT", "1.80"),
    ],
)
def test_rth_trail_limit_exact_decimal_rule612_positive_offset_and_adr0022_bound(
    entry_px: str,
    side: str,
    stop_pct: str,
) -> None:
    spec = quantize_ibkr_p4_lossless_trail_order(
        entry_px=entry_px,
        stop_pct=stop_pct,
        nbbo_spread_bps="10.0",
        side=side,
        session_mode="RTH_CORE",
    )
    assert spec["contract_version"] == IBKR_P4_ADR0015_VERSION
    assert spec["paper_port"] == 4002
    assert spec["exchange"] == "SMART"
    assert spec["exit_order_type"] == "TRAIL LIMIT"
    assert spec["outside_rth"] is False
    assert spec["transmit_on_spec"] is False
    assert spec["standalone_alpha_claim"] is False

    offset = Decimal(spec["lmt_price_offset"])
    tick = Decimal(spec["parent_min_tick"])
    realized_pct = Decimal(spec["realized_stop_pct"])
    q_entry = Decimal(spec["entry_limit_px"])
    q_stop = Decimal(spec["protective_stop_px"])
    q_limit = Decimal(spec["protective_limit_px"])
    trail = Decimal(spec["trailing_percent"])

    assert tick == _nms_tick(q_entry)
    assert q_entry % tick == Decimal("0")
    assert q_stop % _nms_tick(q_stop) == Decimal("0")
    assert q_limit % _nms_tick(q_limit) == Decimal("0")
    assert offset > Decimal("0")
    assert Decimal("0") < realized_pct <= Decimal("2.00")
    assert Decimal("0.01") <= trail <= realized_pct
    if spec["entry_action"] == "BUY":
        assert q_stop < q_entry
        assert q_limit == q_stop - offset
    else:
        assert q_stop > q_entry
        assert q_limit == q_stop + offset


def test_trail_pct_floor_upper_bound_and_rth_spread_guards() -> None:
    with pytest.raises(ValueError, match="outside"):
        quantize_ibkr_p4_lossless_trail_order(
            entry_px="50.00",
            stop_pct="1.00",
            nbbo_spread_bps="5.0",
            side="BUY",
            session_mode="RTH_CORE",
            trail_pct="0.004",
        )
    with pytest.raises(ValueError, match="exceeds realized"):
        quantize_ibkr_p4_lossless_trail_order(
            entry_px="50.00",
            stop_pct="0.50",
            nbbo_spread_bps="5.0",
            side="BUY",
            session_mode="RTH_CORE",
            trail_pct="1.50",
        )
    with pytest.raises(ValueError, match="50.0 bps cap"):
        quantize_ibkr_p4_lossless_trail_order(
            entry_px="50.00",
            stop_pct="1.50",
            nbbo_spread_bps="55.0",
            side="BUY",
            session_mode="RTH_CORE",
        )


def test_session_aware_pre_post_and_overnight_boats_routing_and_dec6_pause() -> None:
    pre = quantize_ibkr_p4_lossless_trail_order(
        entry_px="24.50",
        stop_pct="1.80",
        nbbo_spread_bps="14.0",
        side="BUY",
        session_mode="PRE_MARKET",
    )
    assert pre["exchange"] == "SMART"
    assert pre["exit_order_type"] == "STP LMT"
    assert pre["outside_rth"] is True
    assert Decimal(pre["lmt_price_offset"]) >= Decimal("0.01")

    boats = quantize_ibkr_p4_lossless_trail_order(
        entry_px="24.50",
        stop_pct="1.80",
        nbbo_spread_bps="22.0",
        side="SHORT",
        session_mode="OVERNIGHT_BOATS",
    )
    assert boats["exchange"] == "OVERNIGHT"
    assert boats["exit_order_type"] == "LMT"
    assert boats["tif"] == "DAY"
    assert boats["outside_rth"] is True

    with pytest.raises(ValueError, match="28.0 bps cap"):
        quantize_ibkr_p4_lossless_trail_order(
            entry_px="24.50",
            stop_pct="1.80",
            nbbo_spread_bps="28.5",
            side="BUY",
            session_mode="OVERNIGHT_BOATS",
        )
    with pytest.raises(ValueError, match="Dec-6"):
        quantize_ibkr_p4_lossless_trail_order(
            entry_px="24.50",
            stop_pct="1.80",
            nbbo_spread_bps="10.0",
            side="BUY",
            session_mode="OVERNIGHT_BOATS",
            in_dec6_pause=True,
        )


def test_dual_window_pacer_protects_exit_reserve_and_rejects_non_monotonic_or_floats() -> None:
    bad_float: Any = 2.9
    with pytest.raises(ValueError, match="integer"):
        IbkrP4DualWindowRatePacer(max_per_min=bad_float)

    pacer = IbkrP4DualWindowRatePacer(
        max_per_min=3000,
        exit_reserve_per_min=300,
        max_per_sec=50,
        exit_reserve_per_sec=5,
    )
    for i in range(45):
        res = pacer.try_acquire(is_protective_exit=False, now_mono=100.0 + i * 0.001)
        assert res["admitted"] is True
    blocked = pacer.try_acquire(is_protective_exit=False, now_mono=100.1)
    assert blocked["admitted"] is False
    assert blocked["reason"] == "PROTECTIVE_EXIT_RESERVE_GUARD"

    with pytest.raises(ValueError, match="monotonic"):
        pacer.try_acquire(is_protective_exit=True, now_mono=99.0)

    for j in range(5):
        ex = pacer.try_acquire(is_protective_exit=True, now_mono=100.2 + j * 0.001)
        assert ex["admitted"] is True
    hard_cap = pacer.try_acquire(is_protective_exit=True, now_mono=100.3)
    assert hard_cap["admitted"] is False
    assert hard_cap["reason"] == "IBKR_P4_HARD_CAP_EXCEEDED"


def test_post_retirement_fail_closed_inputs_slow_coil_and_wave2_single_use() -> None:
    for bad_tier in ("", "   ", None, False, 123):
        with pytest.raises(ValueError, match="incentive_tier"):
            evaluate_ibkr_p4_post_retirement_and_setup_gate(
                archetype="PRE_TRAVEL_EARLY_VOLUME_SHOCK",
                incentive_tier=cast(Any, bad_tier),
                session_mode="RTH_CORE",
                boats_active=False,
            )

    retired = evaluate_ibkr_p4_post_retirement_and_setup_gate(
        archetype="PRE_TRAVEL_EARLY_VOLUME_SHOCK",
        incentive_tier="POST",
        session_mode="RTH_CORE",
        boats_active=False,
    )
    assert retired["approved"] is False
    assert retired["post_incentive_retired"] is True
    assert "POST_INCENTIVE_RETIRED" in retired["rejection_reasons"]

    wide = evaluate_ibkr_p4_post_retirement_and_setup_gate(
        archetype="PRE_TRAVEL_EARLY_VOLUME_SHOCK",
        incentive_tier="FRESH_IGNITION",
        session_mode="RTH_CORE",
        boats_active=False,
        nbbo_spread_bps="60.0",
    )
    assert wide["approved"] is False
    assert "SPREAD_ABOVE_SESSION_CAP" in wide["rejection_reasons"]

    thin_boats = evaluate_ibkr_p4_post_retirement_and_setup_gate(
        archetype="FIRST_HAND_24X5_INCENTIVE_IGNITION",
        incentive_tier="TIER1_FRESH_BOATS",
        session_mode="OVERNIGHT_BOATS",
        boats_active=True,
        boats_dvol_usd="149999.99",
    )
    assert thin_boats["approved"] is False
    assert "LIVE_BOATS_BELOW_150K_FLOOR" in thin_boats["rejection_reasons"]

    coil_ok = evaluate_ibkr_p4_post_retirement_and_setup_gate(
        archetype="SLOW_BUILDING_COIL_VDU_ACCUMULATION",
        incentive_tier="FRESH_COIL",
        session_mode="RTH_CORE",
        boats_active=False,
        move_pct="2.10",
        rvol_tod="2.40",
        pullback_dryup_ratio="0.62",
        nbbo_spread_bps="8.0",
    )
    assert coil_ok["approved"] is True

    wave2_expired = evaluate_ibkr_p4_post_retirement_and_setup_gate(
        archetype="FAST_SELL_WAVE2_PULLBACK_REENTRY",
        incentive_tier="FRESH_IGNITION",
        session_mode="RTH_CORE",
        boats_active=False,
        wave2_used=False,
        wave2_elapsed_sec=901.0,
    )
    assert wave2_expired["approved"] is False
    assert "WAVE2_SINGLE_USE_OR_TTL_EXPIRED" in wave2_expired["rejection_reasons"]


def test_merit_feeder_receipt_enforces_2x_cost_accounts_breadth_and_prewall() -> None:
    # Negative 2x cost must NOT clear Stage-5 Live merit even if 1x and HLZ pass
    neg_2x = build_ibkr_p4_merit_feeder_receipt(
        closed_roundtrips=40,
        distinct_sessions=15,
        distinct_accounts=3,
        mean_r_multiple=0.60,
        profit_factor=1.65,
        mean_net_1x_bps=12.0,
        mean_net_2x_bps=-2.0,
        sim_events=250,
        sim_distinct_dates=80,
        sim_as_of_date="2026-10-15",
        hlz_t_stat=4.45,
        hlz_t_hurdle=4.2041,
        dsr_probability=0.97,
        cpcv_positive_pct=80.0,
        input_sha256=VALID_SHA256,
    )
    assert neg_2x["stage5_live_merit_eligible"] is False
    assert neg_2x["promotion_track"] == "PROTECTIVE_FILTER_OR_GOVERNANCE"

    # Single paper account must NOT clear multi-account merit
    one_acct = build_ibkr_p4_merit_feeder_receipt(
        closed_roundtrips=40,
        distinct_sessions=15,
        distinct_accounts=1,
        mean_r_multiple=0.60,
        profit_factor=1.65,
        mean_net_1x_bps=18.5,
        mean_net_2x_bps=10.5,
        sim_events=250,
        sim_distinct_dates=80,
        sim_as_of_date="2026-10-15",
        hlz_t_stat=4.45,
        hlz_t_hurdle=4.2041,
        dsr_probability=0.97,
        cpcv_positive_pct=80.0,
        input_sha256=VALID_SHA256,
    )
    assert one_acct["stage5_live_merit_eligible"] is False

    # Post-wall date fails closed
    with pytest.raises(ValueError, match="Pre-wall"):
        build_ibkr_p4_merit_feeder_receipt(
            closed_roundtrips=40,
            distinct_sessions=15,
            distinct_accounts=2,
            mean_r_multiple=0.60,
            profit_factor=1.65,
            mean_net_1x_bps=18.5,
            mean_net_2x_bps=10.5,
            sim_events=250,
            sim_distinct_dates=80,
            sim_as_of_date="2026-10-16",
            hlz_t_stat=4.45,
            hlz_t_hurdle=4.2041,
            dsr_probability=0.97,
            cpcv_positive_pct=80.0,
            input_sha256=VALID_SHA256,
        )

    cleared = build_ibkr_p4_merit_feeder_receipt(
        closed_roundtrips=40,
        distinct_sessions=15,
        distinct_accounts=2,
        mean_r_multiple=0.60,
        profit_factor=1.65,
        mean_net_1x_bps=18.5,
        mean_net_2x_bps=11.0,
        sim_events=250,
        sim_distinct_dates=80,
        sim_as_of_date="2026-10-15",
        hlz_t_stat=4.45,
        hlz_t_hurdle=4.2041,
        dsr_probability=0.97,
        cpcv_positive_pct=80.0,
        input_sha256=VALID_SHA256,
    )
    assert cleared["dimensionless_only"] is True
    assert cleared["promotion_track"] == "ALPHA_STRATEGY_MINOR"
    assert cleared["standalone_alpha_claim"] is True
    assert cleared["stage5_live_merit_eligible"] is True
    assert cleared["eb_shrunk_r_multiple"] == pytest.approx(0.30, abs=1e-4)
    assert cleared["pinned_upstream_commits"] == IBKR_P4_SOTA_UPSTREAM_PINS
