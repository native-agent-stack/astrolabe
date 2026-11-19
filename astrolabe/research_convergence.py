"""Research-convergence provenance, Reg NMS Rule 612 paper stop-limit ticks, and feed regimes.

Own design on Python's standard library (`decimal`, `datetime`, `zoneinfo`, `math`, `re`) and
`astrolabe.ib_ids`, enforcing `ADR-0004`:
1. 3-tier provenance verification (`64`-hex `input_sha256`, `40`-hex pinned commits for
   `nautilus_trader` and `ib_async`, `E0..E4` evidence grade, and philosophy-only sibling rule).
2. Reg NMS Rule 612 tick-quantized IBKR paper stop-limit geometry inside `ADR-0022 <= 2.00%`,
   bound to `ib_ids.PAPER_PORT`, rejecting `outside_rth` entries when `nbbo_spread_bps > 28.0`.
3. `XNYS` regular, `04:00-20:00 ET` `SIP`, and `20:00-04:00 ET` `BOATS` / `IBKR_OUTSIDE_RTH`
   regime resolution with the `2026-12-06` `23/5` `SIP` cutover flag.
"""

from __future__ import annotations

import math
import re
from collections import deque
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from astrolabe import ib_ids

ADR0022_MAX_STOP_PCT: float = 2.00
OUTSIDE_RTH_MAX_SPREAD_BPS: float = 28.0
DEC6_23X5_CUTOVER_DATE = date(2026, 12, 6)
PINNED_NAUTILUS_TRADER_COMMIT = "da6b57aefdd62b44f675e434ffcfefca7f4025dd"
PINNED_IB_ASYNC_COMMIT = "ab629f34c1823ea4c1542f32f07377208a86bdfd"

VALID_EVIDENCE_GRADES: frozenset[str] = frozenset({"E0", "E1", "E2", "E3", "E4"})
REQUIRED_UPSTREAM_KEYS: tuple[str, ...] = ("nautilus_trader", "ib_async")
PHILOSOPHY_ONLY_REPOS: frozenset[str] = frozenset(
    {
        "canon",
        "edge",
        "meridian",
        "movers",
        "sota-dynamic-trail-c",
        "sota-mover-ranker",
        "trading-sota",
        "tsl",
    }
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA1_40_RE = re.compile(r"^[0-9a-f]{40}$")
_NY_TZ = ZoneInfo("America/New_York")


def _require_finite_float(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    fval = float(value)
    if not math.isfinite(fval):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return fval


def validate_research_convergence_provenance(
    input_sha256: str,
    upstream_commits: dict[str, str],
    evidence_grade: str,
    *,
    other_repos_authority: str = "PHILOSOPHY_ONLY",
) -> None:
    """Enforce 64-hex input SHA-256, 40-hex upstream commits, E0-E4 grade, and ADR-0004 rule."""
    if not isinstance(input_sha256, str) or not _SHA256_RE.fullmatch(input_sha256):
        raise ValueError("input_sha256 must be a 64-character lowercase hex SHA-256 digest")

    if not isinstance(upstream_commits, dict) or not upstream_commits:
        raise ValueError("upstream_commits must be a non-empty dictionary of {repo: 40-hex sha}")

    for raw_key, commit_sha in upstream_commits.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError("upstream_commits keys must be non-empty repository names")
        norm_key = raw_key.strip().lower()
        if norm_key in PHILOSOPHY_ONLY_REPOS:
            raise ValueError(
                f"ADR-0004 violation: sibling repository {raw_key!r} is PHILOSOPHY_ONLY "
                "and cannot be cited as an upstream shelf commit"
            )
        if not isinstance(commit_sha, str) or not _SHA1_40_RE.fullmatch(commit_sha):
            raise ValueError(
                f"upstream_commits[{raw_key!r}] must be a 40-character lowercase hex commit SHA"
            )

    missing = [req for req in REQUIRED_UPSTREAM_KEYS if req not in upstream_commits]
    if missing:
        raise ValueError(f"upstream_commits missing required pinned keys: {missing}")

    if not isinstance(evidence_grade, str) or evidence_grade not in VALID_EVIDENCE_GRADES:
        raise ValueError(f"evidence_grade must be one of E0..E4, got {evidence_grade!r}")

    if other_repos_authority != "PHILOSOPHY_ONLY":
        raise ValueError(
            f"ADR-0004 violation: other_repos_authority must be 'PHILOSOPHY_ONLY', "
            f"got {other_repos_authority!r}"
        )


def _nms_tick(price: Decimal) -> Decimal:
    return Decimal("0.01") if price >= Decimal("1.00") else Decimal("0.0001")


def quantize_ibkr_paper_stop_limit(
    *,
    entry_px: float,
    stop_pct: float,
    nbbo_spread_bps: float,
    side: str = "BUY",
    outside_rth: bool = False,
) -> dict[str, Any]:
    """Quantize an IBKR paper stop-limit order to Rule 612 ticks inside ADR-0022 <= 2.00%."""
    if ib_ids.HOST != "127.0.0.1" or str(ib_ids.PAPER_PORT) != "4002":
        raise ValueError("IBKR paper firewall requires loopback host 127.0.0.1 and PAPER_PORT")
    if not isinstance(outside_rth, bool):
        raise ValueError("outside_rth must be an explicit bool")

    e_val = _require_finite_float(entry_px, "entry_px")
    s_pct = _require_finite_float(stop_pct, "stop_pct")
    spread_bps = _require_finite_float(nbbo_spread_bps, "nbbo_spread_bps")

    if e_val < 0.05:
        raise ValueError(f"entry_px must be >= $0.05, got {entry_px!r}")
    if s_pct <= 0.0 or s_pct > ADR0022_MAX_STOP_PCT:
        raise ValueError(
            f"ADR-0022 violation: stop_pct={s_pct} lies outside (0.0, {ADR0022_MAX_STOP_PCT}]%"
        )
    if spread_bps < 0.0:
        raise ValueError(f"nbbo_spread_bps must be >= 0.0, got {nbbo_spread_bps!r}")
    if outside_rth and spread_bps > OUTSIDE_RTH_MAX_SPREAD_BPS:
        raise ValueError(
            f"outside_rth rejected: nbbo_spread_bps={spread_bps} exceeds "
            f"{OUTSIDE_RTH_MAX_SPREAD_BPS} bps cap"
        )

    if not isinstance(side, str):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    norm_side = side.strip().upper()
    if norm_side in ("BUY", "LONG"):
        norm_side = "BUY"
        stop_order_side = "SELL"
    elif norm_side in ("SELL", "SHORT"):
        norm_side = "SELL"
        stop_order_side = "BUY"
    else:
        raise ValueError(f"Unsupported side {side!r}; expected 'BUY' or 'SELL'")

    d_entry = Decimal(str(e_val))
    entry_tick = _nms_tick(d_entry)
    entry_round = ROUND_FLOOR if norm_side == "BUY" else ROUND_CEILING
    q_entry = (d_entry / entry_tick).to_integral_value(rounding=entry_round) * entry_tick

    stop_ratio = Decimal(str(s_pct)) / Decimal("100")
    if norm_side == "BUY":
        raw_stop = q_entry * (Decimal("1") - stop_ratio)
        stop_tick = _nms_tick(raw_stop)
        q_stop = (raw_stop / stop_tick).to_integral_value(rounding=ROUND_CEILING) * stop_tick
        if q_stop >= q_entry:
            step = Decimal("0.01") if q_entry > Decimal("1.00") else Decimal("0.0001")
            q_stop = q_entry - step
    else:
        raw_stop = q_entry * (Decimal("1") + stop_ratio)
        stop_tick = _nms_tick(raw_stop)
        q_stop = (raw_stop / stop_tick).to_integral_value(rounding=ROUND_FLOOR) * stop_tick
        if q_stop <= q_entry:
            step = Decimal("0.01") if q_entry >= Decimal("1.00") else Decimal("0.0001")
            q_stop = q_entry + step

    realized_stop_pct = float(abs(q_entry - q_stop) / q_entry) * 100.0
    if realized_stop_pct <= 0.0 or realized_stop_pct > ADR0022_MAX_STOP_PCT + 1e-9:
        raise ValueError(
            f"NMS tick quantization exceeded ADR-0022 <= 2.00% at entry={q_entry} "
            f"({realized_stop_pct:.4f}%)"
        )

    # Conservative marketable stop-limit collar bounded by spread and outside-RTH liquidity.
    d_spread = Decimal(str(spread_bps))
    collar_bps = (
        min(Decimal("30.0"), max(Decimal("8.0"), d_spread * Decimal("1.25")))
        if outside_rth
        else min(Decimal("20.0"), max(Decimal("5.0"), d_spread))
    )
    collar_ratio = collar_bps / Decimal("10000")
    if norm_side == "BUY":
        raw_limit = q_stop * (Decimal("1") - collar_ratio)
        limit_tick = _nms_tick(raw_limit)
        q_limit = (raw_limit / limit_tick).to_integral_value(rounding=ROUND_CEILING) * limit_tick
        if q_limit > q_stop:
            q_limit = q_stop
        if q_limit <= Decimal("0"):
            q_limit = Decimal("0.0001")
    else:
        raw_limit = q_stop * (Decimal("1") + collar_ratio)
        limit_tick = _nms_tick(raw_limit)
        q_limit = (raw_limit / limit_tick).to_integral_value(rounding=ROUND_FLOOR) * limit_tick
        if q_limit < q_stop:
            q_limit = q_stop

    return {
        "gateway_host": ib_ids.HOST,
        "paper_port": ib_ids.PAPER_PORT,
        "side": norm_side,
        "stop_order_side": stop_order_side,
        "order_type": "STP_LMT",
        "outside_rth": outside_rth,
        "entry_px": q_entry,
        "stop_px": q_stop,
        "limit_px": q_limit,
        "limit_offset_px": abs(q_stop - q_limit),
        "collar_bps": float(collar_bps),
        "realized_stop_pct": round(realized_stop_pct, 6),
        "nbbo_spread_bps": round(spread_bps, 4),
        "tick_size": _nms_tick(q_stop),
    }


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm for Western Easter Sunday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    d = date(year, month, day)
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _xnys_holidays(year: int) -> frozenset[date]:
    holidays = {
        _nth_weekday(year, 1, 0, 3),  # MLK Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed_fixed_holiday(year, 7, 4),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving Day
        _observed_fixed_holiday(year, 12, 25),  # Christmas Day
    }
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(year, 6, 19))  # Juneteenth
    nyd = _observed_fixed_holiday(year, 1, 1)
    if nyd.year == year:
        holidays.add(nyd)
    next_nyd = _observed_fixed_holiday(year + 1, 1, 1)
    if next_nyd.year == year:
        holidays.add(next_nyd)
    return frozenset(holidays)


def _is_xnys_session(d: date) -> bool:
    return d.weekday() < 5 and d not in _xnys_holidays(d.year)


def _xnys_rth_close_et(d: date) -> time:
    """Return 13:00 ET on standard XNYS early-close sessions, else 16:00 ET."""
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + timedelta(days=1):
        return time(13, 0)
    if d.month == 12 and d.day == 24 and _is_xnys_session(d):
        return time(13, 0)
    if d.month == 7 and d.day == 3 and _is_xnys_session(d) and d.weekday() in (0, 1, 2, 3):
        return time(13, 0)
    return time(16, 0)


def resolve_equities_datafeed_regime(
    ts_utc: datetime,
    *,
    enable_dec6_23x5_sip: bool = False,
) -> dict[str, Any]:
    """Classify an instant into XNYS RTH, 04:00-20:00 ET SIP, or 20:00-04:00 ET BOATS/IBKR."""
    if ts_utc.tzinfo is None or ts_utc.utcoffset() is None:
        raise ValueError("ts_utc must carry an explicit time zone")
    if not isinstance(enable_dec6_23x5_sip, bool):
        raise ValueError("enable_dec6_23x5_sip must be an explicit bool")

    utc_dt = ts_utc.astimezone(UTC)
    ny_dt = utc_dt.astimezone(_NY_TZ)
    ny_date = ny_dt.date()
    ny_tod = ny_dt.timetz().replace(tzinfo=None)

    post_cutover = enable_dec6_23x5_sip and ny_date >= DEC6_23X5_CUTOVER_DATE
    cutover_regime = "post_2026_12_06_23x5_sip" if post_cutover else "pre_2026_12_06_24x5_boats"

    session = "CLOSED"
    primary_feed = "NONE"
    execution_venue = "NONE"
    outside_rth = False
    is_rth = False

    if time(4, 0) <= ny_tod < time(20, 0):
        if _is_xnys_session(ny_date):
            rth_close = _xnys_rth_close_et(ny_date)
            if time(9, 30) <= ny_tod < rth_close:
                session = "XNYS_RTH"
                primary_feed = "SIP"
                execution_venue = "IBKR_RTH"
                outside_rth = False
                is_rth = True
            else:
                session = "EXTENDED_SIP"
                primary_feed = "SIP"
                execution_venue = "IBKR_OUTSIDE_RTH"
                outside_rth = True
                is_rth = False
    else:
        # 20:00-24:00 ET belongs to tomorrow's XNYS session; 00:00-04:00 ET belongs to today's.
        target_session_date = ny_date + timedelta(days=1) if ny_tod >= time(20, 0) else ny_date
        overnight_active = ny_date.weekday() != 5 and _is_xnys_session(target_session_date)
        if overnight_active:
            outside_rth = True
            execution_venue = "IBKR_OUTSIDE_RTH"
            if post_cutover and time(20, 0) <= ny_tod < time(21, 0):
                session = "SIP_23X5_PAUSE"
                primary_feed = "IBKR_OUTSIDE_RTH"
            elif post_cutover:
                session = "OVERNIGHT"
                primary_feed = "SIP"
            else:
                session = "OVERNIGHT"
                primary_feed = "BOATS"

    return {
        "calendar": "XNYS",
        "timestamp_utc": utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ny_local": ny_dt.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session": session,
        "primary_feed": primary_feed,
        "execution_venue": execution_venue,
        "is_rth": is_rth,
        "outside_rth": outside_rth,
        "cutover_date": DEC6_23X5_CUTOVER_DATE.isoformat(),
        "cutover_regime": cutover_regime,
        "paper_port": ib_ids.PAPER_PORT,
    }


IBKR_PAPER_MAX_MSG_PER_MIN: int = 3000
IBKR_PROTECTIVE_EXIT_RESERVE_MPM: int = 300


def is_lit_round_lot_queue_print(
    exchange_code: str,
    conditions: tuple[str, ...] | list[str] | None,
    share_size: int | float,
) -> bool:
    """Return True iff print is a lit exchange non-odd-lot print eligible to deplete L1 queue.

    FINRA TRF/ADF off-exchange prints (`exchange_code == 'D'`) and odd-lots (`'I'` condition
    or `share_size < 100`) never execute against lit exchange L1 order books (`ADR-0007`).
    """
    sz = _require_finite_float(share_size, "share_size")
    if sz <= 0.0:
        raise ValueError("share_size must be positive")
    if not isinstance(exchange_code, str) or not exchange_code.strip():
        raise ValueError("exchange_code must be a non-empty venue code")
    if exchange_code.strip().upper() == "D":
        return False
    if conditions and "I" in conditions:
        return False
    return sz >= 100.0


def validate_stage3b_orca_trading_lane(
    *,
    port: int,
    candidate_host: str,
    active_broker_host: str | None,
    max_stop_pct: float,
    planned_entry_msg_per_min: int,
) -> dict[str, Any]:
    """Enforce `T3B_IBKR_4002_NAUTILUS_PAPER` Orca lane, rate reserve, and `ADR-0022`."""
    if not isinstance(port, int) or isinstance(port, bool) or port != ib_ids.PAPER_PORT:
        raise PermissionError(
            f"Stage-3B trading lane strictly requires ib_ids.PAPER_PORT ({ib_ids.PAPER_PORT})"
        )
    if not isinstance(candidate_host, str) or not candidate_host.strip():
        raise ValueError("candidate_host must be non-empty")
    if active_broker_host is not None and active_broker_host != candidate_host:
        raise PermissionError(
            f"Dual-machine Stage-3B split-brain hard-refused: {active_broker_host!r} "
            "already holds the paper lease"
        )
    stop_v = _require_finite_float(max_stop_pct, "max_stop_pct")
    if not (0.0 < stop_v <= ADR0022_MAX_STOP_PCT):
        raise ValueError(
            f"ADR-0022 violation: max_stop_pct={stop_v} outside (0, {ADR0022_MAX_STOP_PCT}]"
        )
    if isinstance(planned_entry_msg_per_min, bool) or not isinstance(
        planned_entry_msg_per_min, int
    ):
        raise ValueError("planned_entry_msg_per_min must be an integer")
    max_entry_budget = IBKR_PAPER_MAX_MSG_PER_MIN - IBKR_PROTECTIVE_EXIT_RESERVE_MPM
    if not (0 <= planned_entry_msg_per_min <= max_entry_budget):
        raise ValueError(
            f"planned_entry_msg_per_min={planned_entry_msg_per_min} exceeds {max_entry_budget} "
            f"(reserving {IBKR_PROTECTIVE_EXIT_RESERVE_MPM}/{IBKR_PAPER_MAX_MSG_PER_MIN} msg/min)"
        )
    return {
        "lane_id": "T3B_IBKR_4002_NAUTILUS_PAPER",
        "paper_port": ib_ids.PAPER_PORT,
        "approved_host": candidate_host,
        "entry_budget_mpm": max_entry_budget,
        "protective_reserve_mpm": IBKR_PROTECTIVE_EXIT_RESERVE_MPM,
        "max_stop_pct": stop_v,
    }


RTH_PROTECTIVE_MAX_SPREAD_BPS: float = 10.0
MIN_PROTECTIVE_ADV_20D_USD: float = 20_000_000.0
MIN_PROTECTIVE_OFI: float = 0.25
STREAK_COOLDOWN_LOSSES: int = 5
STREAK_COOLDOWN_SEC: float = 1800.0
PROBATION_SIZE_MULTIPLIER: float = 0.25


def evaluate_stage3b_protective_entry_shield(
    *,
    ts_utc: datetime,
    nbbo_spread_bps: float,
    stop_pct: float,
    adv_20d_usd: float,
    ofi: float,
    consecutive_losses: int = 0,
    elapsed_since_loss_sec: float = 3600.0,
    rolling_trades: int = 0,
    half_kelly_cap_pct: float = 1.0,
    profit_factor: float = 1.2,
    is_taker_chase: bool = False,
    is_first_hand_8k: bool = False,
) -> dict[str, Any]:
    """Enforce `ADR-0009` Stage-3B `PROTECTIVE_FILTER_OR_GOVERNANCE` discipline gate."""
    if ts_utc.tzinfo is None or ts_utc.utcoffset() is None:
        raise ValueError("ts_utc must carry an explicit time zone")
    spread_v = _require_finite_float(nbbo_spread_bps, "nbbo_spread_bps")
    stop_v = _require_finite_float(stop_pct, "stop_pct")
    adv_v = _require_finite_float(adv_20d_usd, "adv_20d_usd")
    ofi_v = _require_finite_float(ofi, "ofi")
    elapsed_v = _require_finite_float(elapsed_since_loss_sec, "elapsed_since_loss_sec")
    hk_v = _require_finite_float(half_kelly_cap_pct, "half_kelly_cap_pct")
    pf_v = _require_finite_float(profit_factor, "profit_factor")

    def _deny(reason: str) -> dict[str, Any]:
        return {
            "approved": False,
            "reason": reason,
            "size_multiplier": 0.0,
            "paper_port": ib_ids.PAPER_PORT,
            "promotion_track": "PROTECTIVE_FILTER_OR_GOVERNANCE",
            "standalone_alpha_claim": False,
        }

    if not (0.0 < stop_v <= ADR0022_MAX_STOP_PCT):
        return _deny("adr0022_stop_cap_violation")
    if spread_v <= 0.0:
        return _deny("invalid_nonpositive_spread")
    if adv_v < MIN_PROTECTIVE_ADV_20D_USD:
        return _deny("illiquid_adv_below_20m")
    if consecutive_losses >= STREAK_COOLDOWN_LOSSES and elapsed_v < STREAK_COOLDOWN_SEC:
        return _deny("consecutive_loss_cooldown_active")

    ny_tod = ts_utc.astimezone(_NY_TZ).timetz().replace(tzinfo=None)
    is_rth = time(9, 30) <= ny_tod < time(16, 0)
    if (
        time(9, 30) <= ny_tod < time(9, 35)
        and is_taker_chase
        and not (is_first_hand_8k and spread_v <= 8.0)
    ):
        return _deny("rth_opening_5m_taker_whipsaw_blackout")

    max_spr = RTH_PROTECTIVE_MAX_SPREAD_BPS if is_rth else OUTSIDE_RTH_MAX_SPREAD_BPS
    if spread_v > max_spr:
        return _deny("spread_exceeds_protective_cap")
    if ofi_v < MIN_PROTECTIVE_OFI:
        return _deny("insufficient_positive_ofi")

    probation = rolling_trades >= 30 and hk_v <= 0.0 and pf_v < 0.75
    mult = PROBATION_SIZE_MULTIPLIER if probation else 1.0
    return {
        "approved": True,
        "reason": "approved_probation_025x" if probation else "approved_full_protective",
        "size_multiplier": mult,
        "paper_port": ib_ids.PAPER_PORT,
        "promotion_track": "PROTECTIVE_FILTER_OR_GOVERNANCE",
        "standalone_alpha_claim": False,
    }


def probe_stage3b_ibkr_paper_readiness(
    *,
    socket_open: bool | None = None,
    staged_rows: int = 0,
) -> dict[str, Any]:
    """Fail-closed Stage-3B loopback `ib_ids.PAPER_PORT` & staged-basket readiness (`ADR-0010`)."""
    if isinstance(staged_rows, bool) or not isinstance(staged_rows, int) or staged_rows < 0:
        raise ValueError("staged_rows must be a non-negative integer")
    if ib_ids.HOST != "127.0.0.1" or str(ib_ids.PAPER_PORT) != "4002":
        raise PermissionError("Stage-3B readiness requires loopback 127.0.0.1 and PAPER_PORT")
    if socket_open is None:
        import socket

        try:
            with socket.create_connection((ib_ids.HOST, ib_ids.PAPER_PORT), timeout=0.35):
                is_open = True
        except OSError:
            is_open = False
    else:
        if not isinstance(socket_open, bool):
            raise ValueError("socket_open must be bool or None")
        is_open = socket_open
    return {
        "lane_id": "T3B_IBKR_4002_NAUTILUS_PAPER",
        "host": ib_ids.HOST,
        "paper_port": ib_ids.PAPER_PORT,
        "paper_only": True,
        "gateway_socket_open": is_open,
        "staged_basket_rows": staged_rows,
        "status": "READY_PAPER_4002" if is_open else "GATEWAY_OFFLINE_STAGED_ONLY",
    }


def evaluate_stage3b_two_stage_collar_escalation(
    *,
    bid_px: float,
    ask_px: float,
    resting_elapsed_sec: float,
    passive_filled: bool,
    as_of_utc: datetime,
    enable_dec6_23x5_sip: bool = False,
    passive_window_sec: float = 15.0,
    max_collar_spread_bps: float = RTH_PROTECTIVE_MAX_SPREAD_BPS,
) -> dict[str, Any]:
    """Evaluate 2-stage `15s` lit-bid -> `<= 10 bps` collar & Dec-6 pause (`ADR-0011`)."""
    bid_v = _require_finite_float(bid_px, "bid_px")
    ask_v = _require_finite_float(ask_px, "ask_px")
    elap_v = _require_finite_float(resting_elapsed_sec, "resting_elapsed_sec")
    win_v = _require_finite_float(passive_window_sec, "passive_window_sec")
    cap_v = _require_finite_float(max_collar_spread_bps, "max_collar_spread_bps")
    if bid_v <= 0.0 or ask_v < bid_v or elap_v < 0.0 or win_v <= 0.0:
        raise ValueError("Invalid bid/ask/elapsed/window inputs for 2-stage collar escalation")
    if not (0.0 < cap_v <= RTH_PROTECTIVE_MAX_SPREAD_BPS):
        raise ValueError(f"max_collar_spread_bps must lie in (0, {RTH_PROTECTIVE_MAX_SPREAD_BPS}]")
    if as_of_utc.tzinfo is None or as_of_utc.utcoffset() is None:
        raise ValueError("as_of_utc must be timezone-aware")
    if ib_ids.HOST != "127.0.0.1" or str(ib_ids.PAPER_PORT) != "4002":
        raise PermissionError(
            "Stage-3B 2-stage collar escalation requires loopback PAPER_PORT 4002"
        )

    ny_dt = as_of_utc.astimezone(_NY_TZ)
    in_dec6_pause = bool(
        enable_dec6_23x5_sip
        and ny_dt.date() >= DEC6_23X5_CUTOVER_DATE
        and ny_dt.weekday() != 5
        and ny_dt.hour == 20
    )
    mid_px = 0.5 * (bid_v + ask_v)
    spread_bps = ((ask_v - bid_v) / mid_px) * 10_000.0
    base = {
        "paper_port": ib_ids.PAPER_PORT,
        "spread_bps": round(spread_bps, 4),
        "in_dec6_2000_2100_et_pause": in_dec6_pause,
        "promotion_track": "PROTECTIVE_FILTER_OR_GOVERNANCE",
        "standalone_alpha_claim": False,
    }
    if in_dec6_pause:
        return {
            **base,
            "stage": "BLOCKED_SIP_23X5_DAILY_PAUSE",
            "escalate_to_collar": False,
            "limit_px": None,
        }
    if passive_filled:
        return {
            **base,
            "stage": "STAGE1_PASSIVE_BID_FILLED",
            "escalate_to_collar": False,
            "limit_px": round(bid_v, 4),
        }
    if elap_v < win_v:
        return {
            **base,
            "stage": "STAGE1_RESTING_LIT_BID",
            "escalate_to_collar": False,
            "limit_px": round(bid_v, 4),
        }
    if 0.0 < spread_bps <= cap_v:
        collar_px = min(ask_v, bid_v * (1.0 + cap_v / 10_000.0))
        return {
            **base,
            "stage": "STAGE2_MARKETABLE_COLLAR_LIMIT",
            "escalate_to_collar": True,
            "limit_px": round(collar_px, 4),
        }
    return {
        **base,
        "stage": "EXPIRED_SPREAD_EXCEEDS_COLLAR_CAP",
        "escalate_to_collar": False,
        "limit_px": None,
    }


MIN_H096_ISO_F_BUY_SHARE: float = 0.15
MIN_H096_BJZZ_DARK_SHARE: float = 0.12


def evaluate_stage3b_dry_powder_15s_collar(
    *,
    bid_px: float,
    ask_px: float,
    entry_ask_px: float,
    elapsed_sec: float,
    ofi_entry: float,
    iso_f_buy_share: float = 0.0,
    bjzz_inst_dark_share: float = 0.0,
    resting_passive_bid_0_15s: bool = False,
    bid_survived_15s: bool = True,
    as_of_utc: datetime,
    enable_dec6_23x5_sip: bool = False,
    dry_powder_window_sec: float = 15.0,
    max_collar_spread_bps: float = RTH_PROTECTIVE_MAX_SPREAD_BPS,
) -> dict[str, Any]:
    """Evaluate `ADR-0012` (`H096`) pure `15s` dry-powder bid-survival & informed-flow collar."""
    bid_v = _require_finite_float(bid_px, "bid_px")
    ask_v = _require_finite_float(ask_px, "ask_px")
    e_ask_v = _require_finite_float(entry_ask_px, "entry_ask_px")
    elap_v = _require_finite_float(elapsed_sec, "elapsed_sec")
    ofi_v = _require_finite_float(ofi_entry, "ofi_entry")
    iso_v = _require_finite_float(iso_f_buy_share, "iso_f_buy_share")
    bjzz_v = _require_finite_float(bjzz_inst_dark_share, "bjzz_inst_dark_share")
    win_v = _require_finite_float(dry_powder_window_sec, "dry_powder_window_sec")
    cap_v = _require_finite_float(max_collar_spread_bps, "max_collar_spread_bps")
    if bid_v <= 0.0 or ask_v < bid_v or e_ask_v <= 0.0 or elap_v < 0.0 or win_v <= 0.0:
        raise ValueError("Invalid quote or elapsed inputs for Stage-3B dry-powder 15s collar")
    if not (0.0 < cap_v <= RTH_PROTECTIVE_MAX_SPREAD_BPS):
        raise ValueError(f"max_collar_spread_bps must lie in (0, {RTH_PROTECTIVE_MAX_SPREAD_BPS}]")
    if not isinstance(resting_passive_bid_0_15s, bool) or not isinstance(bid_survived_15s, bool):
        raise ValueError("resting_passive_bid_0_15s and bid_survived_15s must be explicit bools")
    if as_of_utc.tzinfo is None or as_of_utc.utcoffset() is None:
        raise ValueError("as_of_utc must be timezone-aware")
    if ib_ids.HOST != "127.0.0.1" or str(ib_ids.PAPER_PORT) != "4002":
        raise PermissionError("Stage-3B dry-powder collar requires loopback PAPER_PORT")

    ny_dt = as_of_utc.astimezone(_NY_TZ)
    in_dec6_pause = bool(
        enable_dec6_23x5_sip
        and ny_dt.date() >= DEC6_23X5_CUTOVER_DATE
        and ny_dt.weekday() != 5
        and ny_dt.hour == 20
    )
    mid_px = 0.5 * (bid_v + ask_v)
    spread_bps = ((ask_v - bid_v) / mid_px) * 10_000.0
    ask_drift_bps = ((ask_v - e_ask_v) / e_ask_v) * 10_000.0
    informed_ok = ofi_v >= MIN_PROTECTIVE_OFI and (
        iso_v >= MIN_H096_ISO_F_BUY_SHARE or bjzz_v >= MIN_H096_BJZZ_DARK_SHARE
    )
    base = {
        "paper_port": ib_ids.PAPER_PORT,
        "spread_bps": round(spread_bps, 4),
        "ask_drift_bps": round(ask_drift_bps, 4),
        "informed_flow_joint_ok": informed_ok,
        "in_dec6_2000_2100_et_pause": in_dec6_pause,
        "promotion_track": "PROTECTIVE_FILTER_OR_GOVERNANCE",
        "standalone_alpha_claim": False,
    }
    if in_dec6_pause:
        return {
            **base,
            "stage": "BLOCKED_SIP_23X5_DAILY_PAUSE",
            "approved": False,
            "limit_px": None,
        }
    if resting_passive_bid_0_15s:
        return {
            **base,
            "stage": "BLOCKED_RESTING_BID_SWEEP_RISK",
            "approved": False,
            "limit_px": None,
        }
    if not informed_ok:
        return {**base, "stage": "BLOCKED_UNINFORMED_FLOW", "approved": False, "limit_px": None}
    if not bid_survived_15s:
        return {**base, "stage": "BLOCKED_BID_SWEEP_TOXICITY", "approved": False, "limit_px": None}
    if elap_v < win_v:
        return {**base, "stage": "DRY_POWDER_OBSERVING_0_15S", "approved": False, "limit_px": None}
    if 0.0 < spread_bps <= cap_v and ask_drift_bps <= cap_v:
        collar_px = min(ask_v, e_ask_v * (1.0 + cap_v / 10_000.0))
        return {
            **base,
            "stage": "DRY_POWDER_15S_COLLAR_READY",
            "approved": True,
            "limit_px": round(collar_px, 4),
        }
    return {**base, "stage": "EXPIRED_COLLAR_SPREAD_OR_DRIFT", "approved": False, "limit_px": None}


MIN_ABNORMAL_RVOL_TOD_FIRST_SIGN: float = 1.65
MIN_ABNORMAL_RVOL_TOD_IGNITION: float = 2.20


def evaluate_stage3b_wave2_reentry_and_rvol_tod_gate(
    *,
    stage1_rvol_tod: float,
    ofi_entry: float,
    stop_pct: float,
    collar_spread_bps: float,
    core_harvest_completed: bool = False,
    pullback_held_zero_loss_floor: bool = False,
) -> dict[str, Any]:
    """Enforce `ADR-0013` Stage-3B abnormal `RVOL-TOD` size-bias purge & Wave-2 re-entry."""
    rvol_v = _require_finite_float(stage1_rvol_tod, "stage1_rvol_tod")
    ofi_v = _require_finite_float(ofi_entry, "ofi_entry")
    stop_v = _require_finite_float(stop_pct, "stop_pct")
    spr_v = _require_finite_float(collar_spread_bps, "collar_spread_bps")
    if rvol_v < 0.0 or spr_v <= 0.0:
        raise ValueError("stage1_rvol_tod must be >= 0 and collar_spread_bps > 0")
    if not (0.0 < stop_v <= ADR0022_MAX_STOP_PCT):
        raise ValueError(f"ADR-0022 violation: stop_pct={stop_v} exceeds {ADR0022_MAX_STOP_PCT}%")
    if not isinstance(core_harvest_completed, bool) or not isinstance(
        pullback_held_zero_loss_floor, bool
    ):
        raise ValueError("Wave-2 flags must be explicit bools")
    if ib_ids.HOST != "127.0.0.1" or str(ib_ids.PAPER_PORT) != "4002":
        raise PermissionError("Stage-3B Wave-2 & RVOL-TOD gate requires loopback PAPER_PORT")

    flow_ok = ofi_v >= MIN_PROTECTIVE_OFI
    collar_ok = spr_v <= RTH_PROTECTIVE_MAX_SPREAD_BPS
    wave1_ok = rvol_v >= MIN_ABNORMAL_RVOL_TOD_IGNITION and flow_ok and collar_ok
    wave2_ok = (
        core_harvest_completed
        and pullback_held_zero_loss_floor
        and rvol_v >= MIN_ABNORMAL_RVOL_TOD_FIRST_SIGN
        and flow_ok
        and collar_ok
    )
    return {
        "paper_port": ib_ids.PAPER_PORT,
        "megacap_normal_rate_size_bias_purged": rvol_v < MIN_ABNORMAL_RVOL_TOD_FIRST_SIGN,
        "approved_wave1_early_ignition": wave1_ok,
        "approved_wave2_reentry": wave2_ok,
        "promotion_track": "PROTECTIVE_FILTER_OR_GOVERNANCE",
        "standalone_alpha_claim": False,
    }


RANKING_DATAFEEDS_12_SCHEMA_V310: str = "sota-symbol-ranking-datafeeds.v3.10"
MIN_STAGE3B_WAVE2_COOLDOWN_SEC: float = 900.0


def evaluate_stage3b_12feed_bitemporal_ranking_handoff(
    *,
    ts_event_ns: int,
    ts_recv_ns: int,
    decision_ns: int,
    stage1_rvol_tod: float,
    vol_z_tod: float,
    ofi_entry: float,
    stop_pct: float,
    collar_spread_bps: float,
    is_wave2_reentry: bool = False,
    cooldown_elapsed_sec: float = 900.0,
    pullback_held_zero_loss_floor: bool = True,
) -> dict[str, Any]:
    """Enforce `ADR-0014` DF1..DF12 bitemporal PIT ordering & Stage-3B IBKR `:4002` parity."""
    rvol_v = _require_finite_float(stage1_rvol_tod, "stage1_rvol_tod")
    vz_v = _require_finite_float(vol_z_tod, "vol_z_tod")
    ofi_v = _require_finite_float(ofi_entry, "ofi_entry")
    stop_v = _require_finite_float(stop_pct, "stop_pct")
    spr_v = _require_finite_float(collar_spread_bps, "collar_spread_bps")
    cool_v = _require_finite_float(cooldown_elapsed_sec, "cooldown_elapsed_sec")
    if not (0.0 < stop_v <= ADR0022_MAX_STOP_PCT):
        raise ValueError(f"ADR-0022 violation: stop_pct={stop_v} exceeds {ADR0022_MAX_STOP_PCT}%")
    if spr_v <= 0.0:
        raise ValueError("collar_spread_bps must be positive")
    if ib_ids.HOST != "127.0.0.1" or str(ib_ids.PAPER_PORT) != "4002":
        raise PermissionError("Stage-3B DF1..DF12 handoff requires loopback PAPER_PORT 4002")

    bitemporal_ok = ts_event_ns <= ts_recv_ns <= decision_ns
    tod_ok = rvol_v >= MIN_ABNORMAL_RVOL_TOD_IGNITION and vz_v >= 2.3
    flow_ok = ofi_v >= MIN_PROTECTIVE_OFI and spr_v <= RTH_PROTECTIVE_MAX_SPREAD_BPS
    wave2_ok = (not is_wave2_reentry) or (
        pullback_held_zero_loss_floor and cool_v >= MIN_STAGE3B_WAVE2_COOLDOWN_SEC
    )
    return {
        "schema_version": RANKING_DATAFEEDS_12_SCHEMA_V310,
        "feeds_count": 12,
        "paper_port": ib_ids.PAPER_PORT,
        "bitemporal_pit_ok": bitemporal_ok,
        "approved": bool(bitemporal_ok and tod_ok and flow_ok and wave2_ok),
        "promotion_track": "PROTECTIVE_FILTER_OR_GOVERNANCE",
        "standalone_alpha_claim": False,
    }


# ---------------------------------------------------------------------------
# ADR-0015: IBKR Paper P4 (:4002) 3,000 req/min TWS-Compliant Mandatory
# Lossless Trailing, POST Retirement, Slow-Coil & Merit-Ladder Feeder Contract
# ---------------------------------------------------------------------------

IBKR_P4_ADR0015_VERSION: str = "ADR-0015-P4-LOSSLESS-v1"
IBKR_P4_MAX_REQ_PER_MIN: int = 3000
IBKR_P4_EXIT_RESERVE_PER_MIN: int = 300
IBKR_P4_MAX_MSG_PER_SEC: int = 50
IBKR_P4_EXIT_RESERVE_PER_SEC: int = 5
IBKR_P4_RTH_MAX_SPREAD_BPS: Decimal = Decimal("50.0")
IBKR_P4_EXTENDED_MAX_SPREAD_BPS: Decimal = Decimal("28.0")
IBKR_P4_LIVE_BOATS_MIN_DVOL_USD: Decimal = Decimal("150000")
IBKR_P4_WAVE2_TTL_SEC: float = 900.0
IBKR_P4_EB_PRIOR_KAPPA: float = 40.0
IBKR_P4_PREWALL_MAX_DATE = date(2026, 10, 15)

IBKR_P4_VALID_SESSIONS: frozenset[str] = frozenset(
    {"RTH_CORE", "PRE_MARKET", "POST_CLOSE", "OVERNIGHT_BOATS"}
)
IBKR_P4_ADMITTED_ARCHETYPES: frozenset[str] = frozenset(
    {
        "PRE_TRAVEL_EARLY_VOLUME_SHOCK",
        "SLOW_BUILDING_COIL_VDU_ACCUMULATION",
        "FIRST_HAND_24X5_INCENTIVE_IGNITION",
        "PEAK_CLIMAX_REVERSE_SHORT_SAR",
        "FAST_SELL_WAVE2_PULLBACK_REENTRY",
    }
)
IBKR_P4_RETIRED_INCENTIVE_TIERS: frozenset[str] = frozenset(
    {
        "POST",
        "POST_INCENTIVE_RETIRED",
        "TIER2_INTRADAY_POST_NEWS_DRIFT",
        "TIER3_STALE_OR_REHASH",
    }
)
IBKR_P4_SOTA_UPSTREAM_PINS: dict[str, str] = {
    "nautilus_trader": PINNED_NAUTILUS_TRADER_COMMIT,
    "ib_async": PINNED_IB_ASYNC_COMMIT,
    "hftbacktest": "5f3ec40b2afb764e0fea112f941ed85523ef4e88",
    "skfolio": "90e3fb907fab39d244d899f3caf4e9676889309c",
    "ml4t-diagnostic": "19e76072e64a18c9ba56130e4a7bcc0bc871f57e",
    "edgartools": "574c2bd15f6c629bceef03b6550ea8ac8abf3473",
    "statsmodels": "278ff9950636cdd4939b4055e339a8e681d79cab",
}


def _require_finite_decimal(value: Decimal | int | float | str, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite numeric value, got bool {value!r}")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite, got {value!r}")
        d = Decimal(str(value))
    elif isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{name} invalid decimal string {value!r}") from exc
    else:
        raise ValueError(f"{name} must be Decimal/int/float/str, got {type(value).__name__}")
    if not d.is_finite():
        raise ValueError(f"{name} must be finite, got {value!r}")
    return d


def _quantize_to_tick(price: Decimal, tick: Decimal, *, rounding: str) -> Decimal:
    return (price / tick).to_integral_value(rounding=rounding) * tick


def _assert_ibkr_paper_loopback_firewall() -> None:
    if ib_ids.HOST != "127.0.0.1" or str(ib_ids.PAPER_PORT) != "4002":
        raise PermissionError(
            "IBKR P4 firewall requires loopback 127.0.0.1 and PAPER_PORT 4002 (live forbidden)"
        )


class IbkrP4DualWindowRatePacer:
    """Atomic 3,000 req/min & 50 msgs/sec pacer preserving 10% (300/min & 5/sec) exit reserve."""

    def __init__(
        self,
        *,
        max_per_min: int = IBKR_P4_MAX_REQ_PER_MIN,
        exit_reserve_per_min: int = IBKR_P4_EXIT_RESERVE_PER_MIN,
        max_per_sec: int = IBKR_P4_MAX_MSG_PER_SEC,
        exit_reserve_per_sec: int = IBKR_P4_EXIT_RESERVE_PER_SEC,
    ) -> None:
        for param_name, param_val in (
            ("max_per_min", max_per_min),
            ("exit_reserve_per_min", exit_reserve_per_min),
            ("max_per_sec", max_per_sec),
            ("exit_reserve_per_sec", exit_reserve_per_sec),
        ):
            if isinstance(param_val, bool) or not isinstance(param_val, int):
                raise ValueError(f"{param_name} must be an integer, got {param_val!r}")
        if max_per_min <= 0 or max_per_min > IBKR_P4_MAX_REQ_PER_MIN:
            raise ValueError(f"max_per_min must lie in (0, {IBKR_P4_MAX_REQ_PER_MIN}]")
        if max_per_sec <= 0 or max_per_sec > IBKR_P4_MAX_MSG_PER_SEC:
            raise ValueError(f"max_per_sec must lie in (0, {IBKR_P4_MAX_MSG_PER_SEC}]")
        min_res_m = (max_per_min + 9) // 10
        min_res_s = (max_per_sec + 9) // 10
        if exit_reserve_per_min < min_res_m or exit_reserve_per_min >= max_per_min:
            raise ValueError(f"exit_reserve_per_min must be in [{min_res_m}, {max_per_min - 1}]")
        if exit_reserve_per_sec < min_res_s or exit_reserve_per_sec >= max_per_sec:
            raise ValueError(f"exit_reserve_per_sec must be in [{min_res_s}, {max_per_sec - 1}]")
        self.max_per_min = max_per_min
        self.exit_reserve_per_min = exit_reserve_per_min
        self.max_per_sec = max_per_sec
        self.exit_reserve_per_sec = exit_reserve_per_sec
        self._min_window: deque[float] = deque()
        self._sec_window: deque[float] = deque()
        self._last_mono: float | None = None
        self._lock = Lock()

    def _evict_locked(self, now_mono: float) -> None:
        min_cut = now_mono - 60.0
        while self._min_window and self._min_window[0] <= min_cut:
            self._min_window.popleft()
        sec_cut = now_mono - 1.0
        while self._sec_window and self._sec_window[0] <= sec_cut:
            self._sec_window.popleft()

    def try_acquire(
        self,
        *,
        is_protective_exit: bool = False,
        weight: int = 1,
        now_mono: float,
    ) -> dict[str, Any]:
        """Validate monotonic clock & 60s/1s ceilings before committing timestamps atomically."""
        if not isinstance(is_protective_exit, bool):
            raise ValueError("is_protective_exit must be an explicit bool")
        if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
            raise ValueError("weight must be a positive integer")
        now_v = _require_finite_float(now_mono, "now_mono")
        if now_v < 0.0:
            raise ValueError("now_mono must be non-negative")

        cap_m = (
            self.max_per_min if is_protective_exit else self.max_per_min - self.exit_reserve_per_min
        )
        cap_s = (
            self.max_per_sec if is_protective_exit else self.max_per_sec - self.exit_reserve_per_sec
        )
        with self._lock:
            if self._last_mono is not None and now_v < self._last_mono:
                raise ValueError(
                    f"now_mono must be monotonic non-decreasing ({now_v} < {self._last_mono})"
                )
            self._last_mono = now_v
            self._evict_locked(now_v)
            used_m = len(self._min_window)
            used_s = len(self._sec_window)
            if used_m + weight > cap_m or used_s + weight > cap_s:
                reason = (
                    "PROTECTIVE_EXIT_RESERVE_GUARD"
                    if not is_protective_exit
                    else "IBKR_P4_HARD_CAP_EXCEEDED"
                )
                return {
                    "admitted": False,
                    "reason": reason,
                    "is_protective_exit": is_protective_exit,
                    "used_per_min": used_m,
                    "cap_per_min": cap_m,
                    "used_per_sec": used_s,
                    "cap_per_sec": cap_s,
                }
            for _ in range(weight):
                self._min_window.append(now_v)
                self._sec_window.append(now_v)
            return {
                "admitted": True,
                "reason": "ADMITTED",
                "is_protective_exit": is_protective_exit,
                "used_per_min": len(self._min_window),
                "cap_per_min": cap_m,
                "used_per_sec": len(self._sec_window),
                "cap_per_sec": cap_s,
            }


def quantize_ibkr_p4_lossless_trail_order(
    *,
    entry_px: Decimal | int | float | str,
    stop_pct: Decimal | int | float | str,
    nbbo_spread_bps: Decimal | int | float | str,
    side: str,
    session_mode: str,
    trail_pct: Decimal | int | float | str | None = None,
    in_dec6_pause: bool = False,
) -> dict[str, Any]:
    """Build exact-Decimal TWS-compliant mandatory lossless trailing protection for Long & Short.

    - `RTH_CORE`: `SMART` native `TRAIL LIMIT` (`outsideRth=False`), strictly positive
      `lmtPriceOffset > 0` quantized to parent stock Rule-612 `minTick` (avoids TWS Error 110),
      with initial `trailingPercent` aligned to `stop_pct` unless an explicit tighter value is set.
    - `PRE_MARKET` / `POST_CLOSE`: `SMART` atomic `STP LMT` (`outsideRth=True`, `>= 1 tick`
      collar) + live-sync `P4` monotonic 3-tier ratchet.
    - `OVERNIGHT_BOATS` (`20:00-03:50 ET`): `exchange="OVERNIGHT"` collared `LMT` (`tif="DAY"`,
      `outsideRth=True`) + mandatory active `P4` live-sync guardian.
    """
    _assert_ibkr_paper_loopback_firewall()
    if not isinstance(in_dec6_pause, bool):
        raise ValueError("in_dec6_pause must be an explicit bool")
    if (
        not isinstance(session_mode, str)
        or session_mode.strip().upper() not in IBKR_P4_VALID_SESSIONS
    ):
        raise ValueError(
            f"session_mode must be one of {sorted(IBKR_P4_VALID_SESSIONS)}, got {session_mode!r}"
        )
    sess = session_mode.strip().upper()
    if in_dec6_pause:
        raise ValueError("Order rejected: Dec-6 23/5 20:00-21:00 ET SIP daily pause active")

    if not isinstance(side, str):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    s_norm = side.strip().upper()
    if s_norm in ("BUY", "LONG"):
        entry_action, exit_action = "BUY", "SELL"
    elif s_norm in ("SELL", "SHORT"):
        entry_action, exit_action = "SELL", "BUY"
    else:
        raise ValueError(f"Unsupported side {side!r}; expected BUY/LONG or SELL/SHORT")

    d_entry = _require_finite_decimal(entry_px, "entry_px")
    d_stop_pct = _require_finite_decimal(stop_pct, "stop_pct")
    d_spread_bps = _require_finite_decimal(nbbo_spread_bps, "nbbo_spread_bps")

    if d_entry < Decimal("0.05"):
        raise ValueError(f"entry_px must be >= 0.05, got {d_entry}")
    if d_stop_pct <= Decimal("0") or d_stop_pct > Decimal("2.00"):
        raise ValueError(f"ADR-0022 violation: stop_pct={d_stop_pct} outside (0, 2.00]%")
    if d_spread_bps < Decimal("0"):
        raise ValueError(f"nbbo_spread_bps must be >= 0, got {d_spread_bps}")
    if sess == "RTH_CORE" and d_spread_bps > IBKR_P4_RTH_MAX_SPREAD_BPS:
        raise ValueError(
            f"RTH entry rejected: nbbo_spread_bps={d_spread_bps} > "
            f"{IBKR_P4_RTH_MAX_SPREAD_BPS} bps cap"
        )
    if sess != "RTH_CORE" and d_spread_bps > IBKR_P4_EXTENDED_MAX_SPREAD_BPS:
        raise ValueError(
            f"Extended/Overnight entry rejected: nbbo_spread_bps={d_spread_bps} > "
            f"{IBKR_P4_EXTENDED_MAX_SPREAD_BPS} bps cap"
        )

    entry_round = ROUND_FLOOR if entry_action == "BUY" else ROUND_CEILING
    q_entry = _quantize_to_tick(d_entry, _nms_tick(d_entry), rounding=entry_round)
    parent_tick = _nms_tick(q_entry)
    q_entry = _quantize_to_tick(q_entry, parent_tick, rounding=entry_round)
    if q_entry <= Decimal("0"):
        raise ValueError("Quantized entry price must be strictly positive")

    # Exact Rule-612 stop quantization: CEILING for Long stop, FLOOR for Short stop -> <= 2.00%
    stop_ratio = d_stop_pct / Decimal("100")
    if entry_action == "BUY":
        raw_stop = q_entry * (Decimal("1") - stop_ratio)
        stop_tick = _nms_tick(raw_stop)
        q_stop = _quantize_to_tick(raw_stop, stop_tick, rounding=ROUND_CEILING)
        if q_stop >= q_entry:
            step = Decimal("0.01") if q_entry > Decimal("1.00") else Decimal("0.0001")
            q_stop = q_entry - step
    else:
        raw_stop = q_entry * (Decimal("1") + stop_ratio)
        stop_tick = _nms_tick(raw_stop)
        q_stop = _quantize_to_tick(raw_stop, stop_tick, rounding=ROUND_FLOOR)
        if q_stop <= q_entry:
            step = Decimal("0.01") if q_entry >= Decimal("1.00") else Decimal("0.0001")
            q_stop = q_entry + step

    realized_stop_pct = (abs(q_entry - q_stop) / q_entry) * Decimal("100")
    if realized_stop_pct <= Decimal("0") or realized_stop_pct > Decimal("2.00"):
        raise ValueError(
            f"Rule-612 wire stop breached ADR-0022 <= 2.00%: realized={realized_stop_pct:.6f}%"
        )

    # Positive TWS lmtPriceOffset quantized strictly to Reg-NMS Rule 612 ticks (no sub-penny >= $1)
    collar_bps = min(
        Decimal("30.0"),
        max(Decimal("8.0"), d_spread_bps * Decimal("1.25")),
    )
    offset_tick = max(parent_tick, _nms_tick(q_stop))
    raw_offset = q_stop * (collar_bps / Decimal("10000"))
    q_offset = max(offset_tick, _quantize_to_tick(raw_offset, offset_tick, rounding=ROUND_CEILING))
    if exit_action == "SELL":
        q_stop_limit = max(offset_tick, q_stop - q_offset)
        q_offset = q_stop - q_stop_limit
    else:
        raw_limit = q_stop + q_offset
        limit_tick = _nms_tick(raw_limit)
        q_stop_limit = _quantize_to_tick(raw_limit, limit_tick, rounding=ROUND_CEILING)
        q_offset = q_stop_limit - q_stop

    if q_offset <= Decimal("0") or q_stop_limit % _nms_tick(q_stop_limit) != Decimal("0"):
        raise ValueError(
            f"Rule-612 wire limit quantization failed: stop={q_stop}, limit={q_stop_limit}"
        )

    # Effective trailing percent: strictly >= 0.01% and <= realized wire stop <= 2.00%
    max_trail_pct = realized_stop_pct.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    if max_trail_pct < Decimal("0.01"):
        raise ValueError(
            f"Realized wire stop {realized_stop_pct:.4f}% is below 0.01% TWS trailing minimum"
        )
    if trail_pct is None:
        eff_trail_pct = min(Decimal("2.00"), max_trail_pct)
    else:
        d_trail = _require_finite_decimal(trail_pct, "trail_pct")
        if d_trail < Decimal("0.01") or d_trail > Decimal("2.00"):
            raise ValueError(f"trail_pct={d_trail} outside [0.01, 2.00]%")
        if d_trail > realized_stop_pct:
            raise ValueError(
                f"trail_pct={d_trail}% exceeds realized stop_pct={realized_stop_pct:.4f}%"
            )
        eff_trail_pct = min(d_trail, max_trail_pct).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
        if eff_trail_pct < Decimal("0.01"):
            raise ValueError(f"Quantized trailing_percent={eff_trail_pct} below 0.01% minimum")

    if sess == "RTH_CORE":
        exchange = "SMART"
        exit_order_type = "TRAIL LIMIT"
        outside_rth = False
        tif = "DAY"
        guardian_mode = "NATIVE_TWS_TRAIL_LIMIT_PLUS_P4_RATCHET"
    elif sess in ("PRE_MARKET", "POST_CLOSE"):
        exchange = "SMART"
        exit_order_type = "STP LMT"
        outside_rth = True
        tif = "DAY"
        guardian_mode = "ATOMIC_STP_LMT_PLUS_P4_LIVE_SYNC_RATCHET"
    else:
        exchange = "OVERNIGHT"
        exit_order_type = "LMT"
        outside_rth = True
        tif = "DAY"
        guardian_mode = "MANDATORY_P4_LIVE_SYNC_OVERNIGHT_GUARDIAN"

    return {
        "contract_version": IBKR_P4_ADR0015_VERSION,
        "paper_host": ib_ids.HOST,
        "paper_port": ib_ids.PAPER_PORT,
        "session_mode": sess,
        "exchange": exchange,
        "tif": tif,
        "outside_rth": outside_rth,
        "entry_action": entry_action,
        "exit_action": exit_action,
        "exit_order_type": exit_order_type,
        "parent_min_tick": str(parent_tick),
        "entry_limit_px": str(q_entry),
        "protective_stop_px": str(q_stop),
        "protective_limit_px": str(q_stop_limit),
        "lmt_price_offset": str(q_offset),
        "trailing_percent": str(eff_trail_pct),
        "realized_stop_pct": str(realized_stop_pct.quantize(Decimal("0.0001"))),
        "lossless_ratchet_tiers_pct": {
            "tier1_be_arm_min": "0.65",
            "tier1_be_arm_max": "1.10",
            "tier1_be_floor": "0.18",
            "tier2_core_harvest_50pct": "1.25",
            "tier2_runner_floor": "0.25",
            "tier3_mfe_chandelier_keep_pct": ["65", "75", "82", "86"],
        },
        "guardian_mode": guardian_mode,
        "transmit_on_spec": False,
        "promotion_track": "PROTECTIVE_FILTER_OR_GOVERNANCE",
        "standalone_alpha_claim": False,
    }


def evaluate_ibkr_p4_post_retirement_and_setup_gate(
    *,
    archetype: str,
    incentive_tier: str,
    session_mode: str,
    boats_active: bool,
    boats_dvol_usd: Decimal | int | float | str = Decimal("0"),
    move_pct: Decimal | int | float | str = Decimal("0"),
    rvol_tod: Decimal | int | float | str = Decimal("0"),
    pullback_dryup_ratio: Decimal | int | float | str = Decimal("1.0"),
    nbbo_spread_bps: Decimal | int | float | str | None = None,
    wave2_used: bool = False,
    wave2_elapsed_sec: float = 0.0,
) -> dict[str, Any]:
    """Hard-retire already-POST incentives & isolate live BOATS >= $150k from stale POST tape."""
    _assert_ibkr_paper_loopback_firewall()
    if not isinstance(boats_active, bool) or not isinstance(wave2_used, bool):
        raise ValueError("boats_active and wave2_used must be explicit bools")
    for s_name, s_val in (
        ("archetype", archetype),
        ("incentive_tier", incentive_tier),
        ("session_mode", session_mode),
    ):
        if not isinstance(s_val, str) or not s_val.strip():
            raise ValueError(f"{s_name} must be a non-empty string, got {s_val!r}")
    arch = archetype.strip().upper()
    tier = incentive_tier.strip().upper()
    sess = session_mode.strip().upper()
    if sess not in IBKR_P4_VALID_SESSIONS:
        raise ValueError(f"Unsupported session_mode {session_mode!r}")

    d_boats_dvol = _require_finite_decimal(boats_dvol_usd, "boats_dvol_usd")
    d_move = _require_finite_decimal(move_pct, "move_pct")
    d_rvol = _require_finite_decimal(rvol_tod, "rvol_tod")
    d_vdu = _require_finite_decimal(pullback_dryup_ratio, "pullback_dryup_ratio")
    elapsed_v = _require_finite_float(wave2_elapsed_sec, "wave2_elapsed_sec")
    if (
        d_boats_dvol < Decimal("0")
        or d_rvol < Decimal("0")
        or d_vdu < Decimal("0")
        or elapsed_v < 0.0
    ):
        raise ValueError("Volume, RVOL, VDU ratio, and elapsed seconds must be non-negative")

    reasons: list[str] = []
    if nbbo_spread_bps is not None:
        d_spr = _require_finite_decimal(nbbo_spread_bps, "nbbo_spread_bps")
        if d_spr < Decimal("0"):
            raise ValueError("nbbo_spread_bps must be non-negative")
        max_spr = (
            IBKR_P4_RTH_MAX_SPREAD_BPS if sess == "RTH_CORE" else IBKR_P4_EXTENDED_MAX_SPREAD_BPS
        )
        if d_spr > max_spr:
            reasons.append("SPREAD_ABOVE_SESSION_CAP")
    if tier in IBKR_P4_RETIRED_INCENTIVE_TIERS:
        reasons.append("POST_INCENTIVE_RETIRED")
    if arch not in IBKR_P4_ADMITTED_ARCHETYPES:
        reasons.append(f"UNRECOGNIZED_ARCHETYPE:{arch}")
    if (
        boats_active or sess == "OVERNIGHT_BOATS"
    ) and d_boats_dvol < IBKR_P4_LIVE_BOATS_MIN_DVOL_USD:
        reasons.append("LIVE_BOATS_BELOW_150K_FLOOR")
    if arch == "SLOW_BUILDING_COIL_VDU_ACCUMULATION":
        if d_vdu > Decimal("0.80") or not (Decimal("1.35") <= d_rvol <= Decimal("4.50")):
            reasons.append("SLOW_COIL_VDU_OR_RVOL_OUT_OF_BAND")
        if not (Decimal("0.25") <= d_move <= Decimal("5.50")):
            reasons.append("SLOW_COIL_EARLY_MOVE_OUT_OF_BAND")
    elif arch == "FAST_SELL_WAVE2_PULLBACK_REENTRY" and (
        wave2_used or elapsed_v > IBKR_P4_WAVE2_TTL_SEC
    ):
        reasons.append("WAVE2_SINGLE_USE_OR_TTL_EXPIRED")

    return {
        "contract_version": IBKR_P4_ADR0015_VERSION,
        "paper_port": ib_ids.PAPER_PORT,
        "archetype": arch,
        "session_mode": sess,
        "post_incentive_retired": tier in IBKR_P4_RETIRED_INCENTIVE_TIERS,
        "approved": len(reasons) == 0,
        "rejection_reasons": reasons,
        "promotion_track": "PROTECTIVE_FILTER_OR_GOVERNANCE",
        "standalone_alpha_claim": False,
    }


def build_ibkr_p4_merit_feeder_receipt(
    *,
    closed_roundtrips: int,
    distinct_sessions: int,
    distinct_accounts: int,
    mean_r_multiple: float,
    profit_factor: float,
    mean_net_1x_bps: float,
    mean_net_2x_bps: float,
    sim_events: int,
    sim_distinct_dates: int,
    sim_as_of_date: str,
    hlz_t_stat: float,
    hlz_t_hurdle: float,
    dsr_probability: float,
    cpcv_positive_pct: float,
    input_sha256: str,
) -> dict[str, Any]:
    """Emit strictly dimensionless (% / bps / R) 24/7 Sim + 24/5 Paper merit receipt."""
    _assert_ibkr_paper_loopback_firewall()
    validate_research_convergence_provenance(
        input_sha256,
        IBKR_P4_SOTA_UPSTREAM_PINS,
        "E4",
        other_repos_authority="PHILOSOPHY_ONLY",
    )
    for int_name, int_val in (
        ("closed_roundtrips", closed_roundtrips),
        ("distinct_sessions", distinct_sessions),
        ("distinct_accounts", distinct_accounts),
        ("sim_events", sim_events),
        ("sim_distinct_dates", sim_distinct_dates),
    ):
        if isinstance(int_val, bool) or not isinstance(int_val, int) or int_val < 0:
            raise ValueError(f"{int_name} must be a non-negative integer")
    if not isinstance(sim_as_of_date, str) or not sim_as_of_date.strip():
        raise ValueError("sim_as_of_date must be an ISO YYYY-MM-DD string")
    parsed_as_of = date.fromisoformat(sim_as_of_date.strip())
    if parsed_as_of > IBKR_P4_PREWALL_MAX_DATE:
        raise ValueError(
            f"Pre-wall violation: sim_as_of_date={parsed_as_of} > {IBKR_P4_PREWALL_MAX_DATE}"
        )
    mean_r = _require_finite_float(mean_r_multiple, "mean_r_multiple")
    pf = _require_finite_float(profit_factor, "profit_factor")
    net_1x = _require_finite_float(mean_net_1x_bps, "mean_net_1x_bps")
    net_2x = _require_finite_float(mean_net_2x_bps, "mean_net_2x_bps")
    t_val = _require_finite_float(hlz_t_stat, "hlz_t_stat")
    t_hurdle = _require_finite_float(hlz_t_hurdle, "hlz_t_hurdle")
    dsr = _require_finite_float(dsr_probability, "dsr_probability")
    cpcv = _require_finite_float(cpcv_positive_pct, "cpcv_positive_pct")
    if pf < 0.0 or t_hurdle < 3.0 or not (0.0 <= dsr <= 1.0) or not (0.0 <= cpcv <= 100.0):
        raise ValueError("Invalid merit metric bounds")

    eb_shrunk_r = (closed_roundtrips * mean_r) / (closed_roundtrips + IBKR_P4_EB_PRIOR_KAPPA)
    sim_cleared = (
        sim_events >= 200
        and sim_distinct_dates >= 60
        and net_1x > 0.0
        and net_2x > 0.0
        and t_val >= t_hurdle
        and dsr >= 0.95
        and cpcv >= 70.0
    )
    paper_cleared = (
        closed_roundtrips >= 30
        and distinct_sessions >= 10
        and distinct_accounts >= 2
        and eb_shrunk_r > 0.0
        and pf >= 1.25
    )
    alpha_cleared = sim_cleared and paper_cleared
    return {
        "schema_version": "ibkr-p4-merit-feeder-receipt.v1",
        "contract_version": IBKR_P4_ADR0015_VERSION,
        "paper_port": ib_ids.PAPER_PORT,
        "dimensionless_only": True,
        "sim_as_of_date": parsed_as_of.isoformat(),
        "sim_events": sim_events,
        "sim_distinct_dates": sim_distinct_dates,
        "closed_roundtrips": closed_roundtrips,
        "distinct_sessions": distinct_sessions,
        "distinct_accounts": distinct_accounts,
        "eb_prior_kappa": IBKR_P4_EB_PRIOR_KAPPA,
        "eb_shrunk_r_multiple": round(eb_shrunk_r, 4),
        "profit_factor": round(pf, 4),
        "mean_net_1x_bps": round(net_1x, 4),
        "mean_net_2x_bps": round(net_2x, 4),
        "hlz_t_stat": round(t_val, 4),
        "hlz_t_hurdle": round(t_hurdle, 4),
        "dsr_probability": round(dsr, 4),
        "cpcv_positive_pct": round(cpcv, 2),
        "sim_cleared_hlz": sim_cleared,
        "paper_cleared_merit": paper_cleared,
        "promotion_track": "ALPHA_STRATEGY_MINOR"
        if alpha_cleared
        else "PROTECTIVE_FILTER_OR_GOVERNANCE",
        "standalone_alpha_claim": bool(alpha_cleared),
        "stage5_live_merit_eligible": bool(alpha_cleared),
        "input_sha256": input_sha256,
        "pinned_upstream_commits": dict(IBKR_P4_SOTA_UPSTREAM_PINS),
    }
