"""Tests for ADR-0016 Post-ANT merit intake, ADR-0064 quarantine & shared lossless parity."""

from __future__ import annotations

from decimal import Decimal

import pytest

from astrolabe.research_convergence import (
    POST_ANT_ERA_ID,
    build_p4_canonical_client_order_id,
    evaluate_post_ant_merit_target_for_ibkr_p4,
)


def test_post_ant_merit_target_admitted_with_4x_exact_decimal_cap_and_canonical_coid() -> None:
    res = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="AVGO",
        strategy_id="BJZZ_SUBPENNY_INST_DARK_SWEEP",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=5,
        entry_px=Decimal("212.40"),
        seq=7,
        sec_dilution_clean=True,
        requested_stop_pct=Decimal("1.60"),
        position_leverage_x=Decimal("4.0"),
        nbbo_spread_bps=Decimal("6.0"),
        session_mode="RTH_CORE",
    )
    assert res["approved"] is True
    assert res["era"] == POST_ANT_ERA_ID
    assert res["client_order_id"] == "sota-bjzz_dark_sweep-p4-7"
    assert res["take_profit_cap_enabled"] is False
    assert res["promotion_track"] == "PROTECTIVE_FILTER_OR_GOVERNANCE"
    assert res["standalone_alpha_claim"] is False
    assert Decimal(res["effective_stop_pct"]) == Decimal("0.3375")
    assert res["trail_spec"]["exit_order_type"] == "TRAIL LIMIT"


def test_quarantined_variants_exact_294_risk_cap_tier1_parity_and_failclosed_sec() -> None:
    # Family variant quarantine + omitted SEC clean defaults fail-closed
    q = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="NVDA",
        strategy_id="POST_VIX_REVERSAL_V2",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=5,
        entry_px="145.00",
        seq=1,
    )
    assert q["approved"] is False
    assert any("QUARANTINED_ADR0064" in r for r in q["rejection_reasons"])
    assert "SEC_90D_DILUTION_UNVERIFIED_OR_BLOCKED" in q["rejection_reasons"]

    # Codex P1-3 exact counterexample: $2.94 entry -> $2.93 stop @ 3.9694x = 1.350136% > 1.35%!
    exact_breach = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="EDGE",
        strategy_id="BJZZ_SUBPENNY_INST_DARK_SWEEP",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=5,
        entry_px=Decimal("2.94"),
        seq=2,
        sec_dilution_clean=True,
        requested_stop_pct=Decimal("1.60"),
        position_leverage_x=Decimal("3.9694"),
        nbbo_spread_bps=Decimal("5.0"),
    )
    assert exact_breach["approved"] is False
    assert any(
        "SUBTICK_EXCEEDS_LEVERAGE_OR_STOP_CAP" in r for r in exact_breach["rejection_reasons"]
    )

    # Codex P1-5 & P2-3: Tier 1 rejects 3 pillars, >15 bps spread, or >6 impossible pillars
    t1_bad = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="AVGO",
        strategy_id="BJZZ_SUBPENNY_INST_DARK_SWEEP",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=3,
        entry_px="212.40",
        seq=3,
        sec_dilution_clean=True,
        nbbo_spread_bps="20.0",
    )
    assert t1_bad["approved"] is False
    assert any("TIER1_REQUIRES_4_DF_PILLARS" in r for r in t1_bad["rejection_reasons"])
    assert any("SPREAD_ABOVE_TIER_OR_SESSION_CAP" in r for r in t1_bad["rejection_reasons"])

    bad_pillars = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="AVGO",
        strategy_id="BJZZ_SUBPENNY_INST_DARK_SWEEP",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=9,
        entry_px="212.40",
        seq=4,
        sec_dilution_clean=True,
    )
    assert bad_pillars["approved"] is False
    assert any("INVALID_DF_PILLARS_COUNT" in r for r in bad_pillars["rejection_reasons"])

    ipo_lev = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="NEWX",
        strategy_id="H01_PRE_TRAVEL_POCKET_PIVOT",
        merit_tier="MERIT_TIER_2_ACTIVE",
        df_pillars_manifested=4,
        entry_px="50.00",
        seq=5,
        sec_dilution_clean=True,
        position_leverage_x="2.0",
        nbbo_spread_bps="8.0",
        is_new_issue_30d=True,
    )
    assert ipo_lev["approved"] is False
    assert "FINRA_4210_NEW_ISSUE_30D_CASH_ONLY" in ipo_lev["rejection_reasons"]

    # Pass-2 Finding 3: <= 1.35% equity-risk ceiling at 1x & 1.5x AND zero widening on 0.01% stop!
    lev_1x = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="SPY",
        strategy_id="BJZZ_SUBPENNY_INST_DARK_SWEEP",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=5,
        entry_px=Decimal("100.00"),
        seq=6,
        sec_dilution_clean=True,
        requested_stop_pct=Decimal("1.60"),
        position_leverage_x=Decimal("1.0"),
        nbbo_spread_bps=Decimal("4.0"),
    )
    assert lev_1x["approved"] is True
    assert Decimal(lev_1x["trail_spec"]["protective_stop_px"]) == Decimal("98.65")

    lev_15x = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="SPY",
        strategy_id="BJZZ_SUBPENNY_INST_DARK_SWEEP",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=5,
        entry_px=Decimal("100.00"),
        seq=7,
        sec_dilution_clean=True,
        requested_stop_pct=Decimal("1.60"),
        position_leverage_x=Decimal("1.5"),
        nbbo_spread_bps=Decimal("4.0"),
    )
    assert lev_15x["approved"] is True
    assert Decimal(lev_15x["trail_spec"]["protective_stop_px"]) == Decimal("99.10")

    tight_001 = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="SPY",
        strategy_id="BJZZ_SUBPENNY_INST_DARK_SWEEP",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=5,
        entry_px=Decimal("100.00"),
        seq=8,
        sec_dilution_clean=True,
        requested_stop_pct=Decimal("0.01"),
        position_leverage_x=Decimal("1.0"),
        nbbo_spread_bps=Decimal("4.0"),
    )
    assert tight_001["approved"] is True
    assert Decimal(tight_001["trail_spec"]["protective_stop_px"]) == Decimal("99.99")

    # Pass-2 Finding 4: omitted nbbo_spread_bps fails closed (never invents 8 bps!)
    no_spr = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="AVGO",
        strategy_id="BJZZ_SUBPENNY_INST_DARK_SWEEP",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=5,
        entry_px="212.40",
        seq=9,
        sec_dilution_clean=True,
    )
    assert no_spr["approved"] is False
    assert "MISSING_NBBO_SPREAD_OBSERVATION" in no_spr["rejection_reasons"]

    # Pass-2 Finding 7: per-strategy ADR-0064 contract parity (H090 30bps & BJZZ short rejected)
    h090_wide = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="NVDA",
        strategy_id="H090_TIGHT_SPREAD_HIGH_OFI_PASSIVE_BID",
        merit_tier="MERIT_TIER_2_ACTIVE",
        df_pillars_manifested=4,
        entry_px="140.00",
        seq=10,
        sec_dilution_clean=True,
        nbbo_spread_bps="30.0",
    )
    assert h090_wide["approved"] is False
    assert any("STRATEGY_SPREAD_EXCEEDS_CONTRACT" in r for r in h090_wide["rejection_reasons"])

    bjzz_short = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="NVDA",
        strategy_id="BJZZ_SUBPENNY_INST_DARK_SWEEP",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=5,
        entry_px="140.00",
        seq=11,
        sec_dilution_clean=True,
        nbbo_spread_bps="5.0",
        side="SELL",
    )
    assert bjzz_short["approved"] is False
    assert any("STRATEGY_LONG_ONLY_DIRECTION" in r for r in bjzz_short["rejection_reasons"])

    # Pass-3 Finding 7: Noise-VWAP enforces 0.50% stop ceiling ($99.50 at $100) & VIX symbol scope
    vwap_spy = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="SPY",
        strategy_id="ZARATTINI_2025_NOISE_VWAP",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=5,
        entry_px=Decimal("100.00"),
        seq=12,
        sec_dilution_clean=True,
        nbbo_spread_bps=Decimal("2.0"),
    )
    assert vwap_spy["approved"] is True
    assert Decimal(vwap_spy["effective_stop_pct"]) == Decimal("0.50")
    assert Decimal(vwap_spy["trail_spec"]["protective_stop_px"]) == Decimal("99.50")

    vix_non_etf = evaluate_post_ant_merit_target_for_ibkr_p4(
        symbol="NVDA",
        strategy_id="VIX_SPIKE_AND_CRUSH_REGIME_SURFER",
        merit_tier="MERIT_TIER_1_CORE",
        df_pillars_manifested=5,
        entry_px=Decimal("140.00"),
        seq=13,
        sec_dilution_clean=True,
        nbbo_spread_bps=Decimal("5.0"),
    )
    assert vix_non_etf["approved"] is False
    assert any("STRATEGY_SYMBOL_NOT_ALLOWED" in r for r in vix_non_etf["rejection_reasons"])

    with pytest.raises(ValueError, match="seq must be int >= 1"):
        build_p4_canonical_client_order_id("BJZZ_SUBPENNY_INST_DARK_SWEEP", 0)
