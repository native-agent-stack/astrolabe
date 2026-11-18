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
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
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
