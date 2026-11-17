"""
IBKR paper lane, milestone M1: one DAY LIMIT order placed by the engine.

    uv run python -m astrolabe.ibkr_paper order      # reconcile, place one DAY LIMIT, stop
    uv run python -m astrolabe.ibkr_paper fill       # XNYS session: buy 1 so it fills, close it
    uv run python -m astrolabe.ibkr_paper restart    # reconcile only; places nothing

Ported from meridian@014f706d:meridian/ibkr_paper.py. Changed: paths come from
``astrolabe.settings.Settings``; fill mode uses the XNYS calendar (``astrolabe.market_hours``)
in place of a weekday clock; a reviewed-code check and boundary check 5 (exclusive use) are
added; records live outside the git tree with new fields (see ``RunRecord``).

This module is the gate and the run record. It is own design and uses only the standard
library. The engine is in ``astrolabe.ibkr_node``, which is imported only after the gate
has passed, so a refused start never loads it.

Order of events in every run (the checks are described in ``astrolabe.boundary``):
  0. refuse unless the checkout is reviewed code: clean, on ``main``, and at the commit the
     local ``origin/main`` names (no fetch); fill mode also refuses outside an XNYS session;
  1. take the account lock and refuse when another API client or a lane of the owner's other
     project is attached (check 5);
  2. read the optional ``risk/boundary.contract.json`` (owner-created, if at all);
  3. refuse unless the Gateway's jts.ini says Paper Trading and the Gateway listens only
     on the paper port;
  4. ask the Gateway which accounts it manages, and refuse unless that is exactly one
     account of the paper form (and on the allowlist, if there is one);
  5. in order and fill mode, refuse unless a person at a terminal types the confirmation
     word (fill mode checks the session again), then run check 5 and every paper check again
     and refuse if anything changed while the prompt waited;
  6. only then build and run the node, with the account the Gateway reported; the engine's
     own output goes to a local log in ``Settings.logs_dir``, not to the terminal;
  7. run the paper checks again after the run, and write no evidence if anything changed.

An order run passes only on the path M1 used: the order is accepted, then cancelled without
a fill. A fill is a failure there, because order mode has no second order to close it.
A fill run passes only when its BUY filled, its one closing SELL filled, and the lane's
instrument is flat at stop. Every run also needs the state read after the node shut down to
show the instrument flat with nothing working. A restart run after a passed fill run that
started inside the reconciliation window also compares what the venue reconciled with that
run's two orders (``matches_last_fill_pass``).

Exit codes: 0 pass, 1 failure, 2 refusal (nothing was sent).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from astrolabe import boundary, ib_ids, ib_probe, market_hours
from astrolabe.settings import Settings

MODES = ("order", "fill", "restart")
CONFIRMED_MODES = ("order", "fill")
OUTSIDE_HOURS = (
    "fill mode runs only inside a regular XNYS session, from the open to 5 minutes before the close"
)
# Order statuses that end an order without a fill (NautilusTrader's OrderStatus names).
_ENDED_UNFILLED = frozenset({"CANCELED", "REJECTED", "DENIED", "EXPIRED"})
# Client id for the account probe. Distinct from the node's data and execution clients.
PROBE_CLIENT_ID = ib_ids.ACCOUNT_PROBE_CLIENT_ID
RECORD_SCHEMA = "astrolabe.run-record/1"
MILESTONE = "M1"
LANE = "ibkr-paper"
INSTRUMENT = "AAPL.XNAS"
CONFIRM_WORD = boundary.CONFIRM_WORD
# IB error 103: the order-id lockout (nautilus_trader issue 5277).
ORDER_ID_LOCKOUT_CODE = 103
GATEWAY_HINT = (
    "(check that the Gateway is running and logged in to Paper Trading, that its API "
    f"socket port is {ib_ids.PAPER_PORT}, and that Read-Only API is unticked in Configure > "
    "Settings > API)"
)
PULL_HINT = "run `git pull --ff-only` on main with a clean checkout, then start again"
# The account lock of this process (check 5), kept until the process exits.
_held: list[boundary.ExclusiveUse] = []


class CodeNotReviewed(RuntimeError):
    """The checkout is not the reviewed code on main."""


_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _utc_now() -> str:
    return datetime.now(UTC).strftime(_UTC_FORMAT)


def ns_to_utc(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _git(repo_root: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise CodeNotReviewed(f"git could not be run: {type(e).__name__}") from e
    if done.returncode != 0:
        raise CodeNotReviewed(f"git {args[0]} failed")
    return done.stdout


def reviewed_code(repo_root: Path) -> str:
    """The commit the checkout is at, if it is clean, on ``main`` and equal to the local
    ``origin/main``. Reads local refs only: nothing is fetched."""
    if _git(repo_root, "status", "--porcelain"):
        raise CodeNotReviewed("the checkout has uncommitted or untracked changes")
    branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "main":
        raise CodeNotReviewed(f"the checkout is on {branch!r}, not on main")
    head = _git(repo_root, "rev-parse", "--verify", "HEAD^{commit}").strip()
    try:
        upstream = _git(
            repo_root, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"
        ).strip()
    except CodeNotReviewed:
        raise CodeNotReviewed("there is no local origin/main to compare with") from None
    if head != upstream:
        raise CodeNotReviewed("HEAD is not the commit origin/main names")
    return head


def fill_session(now: datetime | None = None) -> market_hours.Session | None:
    """The XNYS session fill mode may run in now, or None. Raises MarketHoursError."""
    return market_hours.fill_session(datetime.now(UTC) if now is None else now)


def gate(settings: Settings | None = None) -> tuple[boundary.IbkrBoundary, str]:
    """Return the boundary and the one allowed account, or raise. Builds no engine."""
    bounds = boundary.local_paper_checks(settings)  # before any connection
    reported = ib_probe.managed_accounts(bounds.host, bounds.port, PROBE_CLIENT_ID)
    return bounds, boundary.allowed_account(reported, bounds)


def _at_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


PROMPTS = {
    "order": "This places ONE DAY LIMIT BUY of 1 share on the PAPER account, then cancels it.",
    "fill": (
        "This places ONE DAY LIMIT BUY of 1 share on the PAPER account, priced to fill, and\n"
        "when it fills, ONE DAY LIMIT SELL of that share, priced to fill, so the run ends flat."
    ),
}


def owner_confirms(mode: str = "order", prompt: str | None = None) -> bool:
    """Order and fill mode need a person at a terminal. A pipe or a tool call has none."""
    if not _at_terminal():
        return False
    answer = input(f"{PROMPTS[mode] if prompt is None else prompt}\nType {CONFIRM_WORD} to go on: ")
    return answer.strip() == CONFIRM_WORD


def scrubbed_json(data: dict[str, Any], forbidden: list[str]) -> str:
    """Serialise a scrubbed pass for private sync; refuse any forbidden string."""
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    lowered = text.lower()
    for value in forbidden:
        if value and value.lower() in lowered:
            raise RuntimeError("the run record contains an account identifier; not written")
    return text


def _write_private(path: Path, text: str) -> None:
    """Write a new file with mode 0600 in a 0700 folder. An existing file is never replaced."""
    boundary.private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with open(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(text)


def _record_name(mode: str, stamp: str) -> str:
    return f"{MILESTONE}-{LANE}-{stamp}-{mode}.json"


def last_fill_pass(evidence_dir: Path) -> dict[str, Any] | None:
    """The newest scrubbed PASS record of a fill run in ``evidence_dir``, with its stamp under
    ``"stamp"``; None when there is none. Records that do not parse are skipped."""
    prefix, suffix = f"{MILESTONE}-{LANE}-", "-fill.json"
    try:
        names = sorted(
            (p.name for p in evidence_dir.iterdir() if p.name.startswith(prefix)),
            reverse=True,
        )
    except FileNotFoundError:
        return None
    for name in names:
        if not name.endswith(suffix):
            continue
        try:
            data = json.loads((evidence_dir / name).read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue
        if (
            isinstance(data, dict)
            and data.get("schema") == RECORD_SCHEMA
            and data.get("mode") == "fill"
            and data.get("result") == "pass"
        ):
            return {**data, "stamp": name[len(prefix) : -len(suffix)]}
    return None


class RunRecord:
    """What happened in one run, as normalised fields only (never raw broker text)."""

    def __init__(
        self,
        mode: str,
        gateway_port: int,
        allowlist_present: bool = False,
        gateway_bind: str = "",
        source_commit: str | None = None,
    ) -> None:
        self.data: dict[str, Any] = {
            "schema": RECORD_SCHEMA,
            "milestone": MILESTONE,
            "lane": LANE,
            "mode": mode,
            "run_started_utc": _utc_now(),
            # The reviewed commit the run's code is at (clean, on main, equal to origin/main).
            "source_commit": source_commit,
            "engine": {},
            # The node's settings, set when it is built (ibkr_node.build).
            "node": None,
            # How the Gateway's port is bound ("all interfaces" is its normal bind), never the
            # address itself.
            "gateway": {"port": gateway_port, "bind": gateway_bind},
            # Observed, not assumed: every paper check before the run and again after it.
            "boundary": {
                "paper_checks_passed_before_run": True,
                "paper_checks_passed_after_run": None,
                "allowlist_contract_present": allowlist_present,
            },
            # Check 5: the account lock and the attached-client and other-lane checks.
            "exclusive_use": None,
            "startup_reconciliation": {"requested": True, "trader_started": False},
            "engine_error": False,
            # Orders the lane's own guard refused to send; the reasons are kept locally.
            "orders_refused": 0,
            "order": None,
            "events": [],
            "at_stop": None,
            # The same snapshot again, read from the node's cache once it has shut down.
            "after_shutdown": None,
        }
        if mode == "fill":
            self.data["close_order"] = None
            self.data["market_hours"] = None
        if mode == "restart":
            self.data["reconciled"] = None
        # Free text from the broker or the engine. Written to runs/ only, never to evidence/.
        self.local: dict[str, Any] = {"reasons": []}

    def trader_started(
        self,
        *,
        instrument_open_orders: int,
        instrument_open_positions: int,
        account_open_orders: int,
        account_open_positions: int,
    ) -> None:
        self.data["startup_reconciliation"].update(
            trader_started=True,
            instrument_open_orders_at_start=instrument_open_orders,
            instrument_open_positions_at_start=instrument_open_positions,
            account_open_orders_at_start=account_open_orders,
            account_open_positions_at_start=account_open_positions,
        )

    def exclusive_use(self, checks_passed: int) -> None:
        self.data["exclusive_use"] = {
            "account_lock_held": True,
            "other_api_clients_seen": 0,
            "other_project_lanes_seen": 0,
            "checks_passed_before_run": checks_passed,
        }

    def market_hours(self, session: market_hours.Session) -> None:
        if self.data["mode"] != "fill":
            raise RuntimeError("only a fill run records its market session")
        self.data["market_hours"] = session.as_record()

    def reconciled(
        self,
        *,
        instrument_orders_by_status: dict[str, int],
        filled_venue_order_ids: list[str],
        filled_client_order_ids: list[str] | None = None,
    ) -> None:
        if self.data["mode"] != "restart":
            raise RuntimeError("only a restart run records what reconciliation left")
        self.data["reconciled"] = {
            "instrument_orders_by_status": dict(instrument_orders_by_status),
            "filled_venue_order_ids": list(filled_venue_order_ids),
            "filled_client_order_ids": list(filled_client_order_ids or ()),
        }

    def order_built(self, **fields: str) -> None:
        self.data["order"] = {**fields, "venue_order_id": None}

    def close_order_built(self, **fields: str) -> None:
        if self.data["mode"] != "fill":
            raise RuntimeError("only a fill run places a closing order")
        self.data["close_order"] = {**fields, "venue_order_id": None}

    def venue_order_id(self, client_order_id: str, venue_order_id: str) -> None:
        """Note the venue's id for one of this run's orders; other orders are ignored."""
        for key in ("order", "close_order"):
            order = self.data.get(key)
            if order is not None and order.get("client_order_id") == client_order_id:
                order["venue_order_id"] = venue_order_id

    def event(self, kind: str, ts_ns: int, **fields: Any) -> None:
        self.data["events"].append({"type": kind, "ts_event_utc": ns_to_utc(ts_ns), **fields})

    def reason(self, kind: str, text: str) -> None:
        self.local["reasons"].append({"type": kind, "reason": text})

    def at_stop(self, **fields: Any) -> None:
        self.data["at_stop"] = fields

    def after_shutdown(self, **fields: Any) -> None:
        """The snapshot taken once the node has shut down: statuses, counts, quantities and
        venue order ids only, as ``at_stop``."""
        self.data["after_shutdown"] = fields

    def engine_error(self, text: str) -> None:
        self.data["engine_error"] = True
        self.local["engine_error"] = text

    def order_refused(self, reason: str) -> None:
        """The lane's guard refused to send an order. Any refusal fails the run."""
        self.data["orders_refused"] += 1
        self.local.setdefault("orders_refused", []).append(reason)

    def paper_checks_after_run(self, passed: bool) -> None:
        self.data["boundary"]["paper_checks_passed_after_run"] = passed

    def _fill_pass_in_window(self, last: dict[str, Any], lookback_mins: int) -> bool:
        """Did the fill run start at most ``lookback_mins - 2`` minutes before this run did?
        Both times are the records' own ``run_started_utc`` (``_utc_now``'s format). A time
        that is missing or does not parse, or a fill run that started after this run, counts
        as outside the window."""
        try:
            fill_started = datetime.strptime(str(last.get("run_started_utc")), _UTC_FORMAT)
            this_started = datetime.strptime(str(self.data.get("run_started_utc")), _UTC_FORMAT)
            window = timedelta(minutes=int(lookback_mins) - 2)
        except TypeError, ValueError, OverflowError:
            return False
        return timedelta(0) <= this_started - fill_started <= window

    def compare_with_fill_pass(self, last: dict[str, Any] | None, *, lookback_mins: int) -> None:
        """Restart mode: does the venue report the last passed fill run's BUY and SELL as
        FILLED, with the instrument flat? Without such a run the fields are left out.

        With a lookback the engine drops closed orders whose last update is older than the
        window (v2.0.0rc9 crates/live/src/execution/reports.rs:28-41), so a fill run that
        started more than ``lookback_mins - 2`` minutes before this run is not compared:
        ``matches_last_fill_pass`` is None and no problem is added.

        An order counts as reported FILLED when its venue order id is among the filled venue
        ids or its client order id among the filled client order ids: the venue id the fill
        run saw may be the bare IB order id where the reconciled one is ``PERM-<permId>``
        (crates/adapters/interactive_brokers/src/execution/parse.rs:440-446). Which key
        matched is recorded per order under ``last_fill_pass_matched_by``.
        """
        if self.data["mode"] != "restart":
            raise RuntimeError("only a restart run compares with the last fill run")
        if last is None:
            return
        self.data["last_fill_pass_stamp"] = last.get("stamp")
        in_window = self._fill_pass_in_window(last, lookback_mins)
        self.data["last_fill_pass_in_window"] = in_window
        if not in_window:
            self.data["matches_last_fill_pass"] = None
            return
        reconciled = self.data.get("reconciled") or {}
        filled_venue = set(reconciled.get("filled_venue_order_ids") or ())
        filled_client = set(reconciled.get("filled_client_order_ids") or ())
        matched_by: dict[str, str | None] = {}
        for key in ("order", "close_order"):
            order = last.get(key) or {}
            venue_id, client_id = order.get("venue_order_id"), order.get("client_order_id")
            if venue_id and venue_id in filled_venue:
                matched_by[key] = "venue_order_id"
            elif client_id and client_id in filled_client:
                matched_by[key] = "client_order_id"
            else:
                matched_by[key] = None
        start = self.data["startup_reconciliation"]
        stop = self.data["at_stop"] or {}
        flat = (
            start.get("instrument_open_positions_at_start") == 0
            and stop.get("instrument_open_positions") == 0
        )
        self.data["last_fill_pass_matched_by"] = matched_by
        self.data["matches_last_fill_pass"] = bool(all(matched_by.values()) and flat)

    def rejection_codes(self) -> list[int]:
        return [
            e["ib_error_code"]
            for e in self.data["events"]
            if e["type"] == "OrderRejected" and e.get("ib_error_code") is not None
        ]

    def problems(self) -> list[str]:
        d = self.data
        found: list[str] = []
        if d["boundary"]["paper_checks_passed_after_run"] is not True:
            found.append("after the run the paper checks did not pass again for the same account")
        if d["engine_error"]:
            found.append("the node stopped with an error")
        if d["orders_refused"]:
            found.append(
                f"the lane refused to send {d['orders_refused']} order(s) "
                "(the reasons are in the local record under runs/)"
            )
        if ORDER_ID_LOCKOUT_CODE in self.rejection_codes():
            found.append(
                "IB refused an order with error 103 (the order-id lockout of the execution "
                "client's id): recovery is the next spare execution id in astrolabe/ib_ids.py "
                "(SPARE_EXECUTION_CLIENT_IDS), moved through a reviewed change, or the owner's "
                '"Reset API order ID sequence" in the Gateway'
            )

        start = d["startup_reconciliation"]
        if not start["trader_started"]:
            found.append(
                "the trader never started: the connection or startup reconciliation failed "
                + GATEWAY_HINT
            )
        elif (
            start["instrument_open_orders_at_start"] or start["instrument_open_positions_at_start"]
        ):
            found.append(
                "the account already had an open order or position in the lane's instrument "
                "at start"
            )

        stop = d["at_stop"]
        if stop is None:
            found.append("no state was captured at stop")
        else:
            if stop.get("instrument_open_orders") != 0:
                found.append("an order in the lane's instrument was still open at stop")
            if stop.get("instrument_inflight_orders", 0) != 0:  # absent in older records
                found.append("an order in the lane's instrument was still in flight at stop")
            if stop.get("instrument_open_positions") != 0:
                found.append("the position in the lane's instrument was not flat at stop")

        # The engine processes events for a few seconds after on_stop, and the stop-time
        # cancel takes effect there, so the last word is the snapshot after shutdown.
        after = d.get("after_shutdown")
        if after is None:
            found.append("no state was captured after the node shut down")
        else:
            if after.get("instrument_open_positions") != 0:
                found.append(
                    "the position in the lane's instrument was not flat after the node shut "
                    "down: a share may be open in the paper account"
                )
            if (
                after.get("instrument_open_orders") != 0
                or after.get("instrument_inflight_orders") != 0
            ):
                found.append(
                    "an order in the lane's instrument was open or in flight after the node "
                    "shut down: run `restart` before closing anything by hand"
                )

        if d["mode"] == "order":
            order = d["order"]
            kinds = [e["type"] for e in d["events"]]
            if order is None:
                found.append("no order was submitted")
            elif order.get("type") != "LIMIT" or order.get("time_in_force") != "DAY":
                found.append("the order was not a DAY LIMIT order")
            if order is not None and "OrderAccepted" not in kinds:
                found.append("the venue never accepted the order " + GATEWAY_HINT)
            status = None if stop is None else stop.get("order_status")
            after_order = after or {}
            filled_after = after_order.get("order_status") == "FILLED" or after_order.get(
                "filled_qty"
            ) not in (None, "0")
            if "OrderFilled" in kinds or status == "FILLED" or filled_after:
                found.append(
                    "the order filled: one share is open in the paper account and has to be "
                    "closed by hand"
                )
            elif order is not None and status != "CANCELED":
                found.append("the order did not reach a terminal state")

        if d["mode"] == "fill":
            found += self._fill_problems(stop)

        if d["mode"] == "restart" and d.get("matches_last_fill_pass") is False:
            found.append(
                f"the venue does not report fill run {d['last_fill_pass_stamp']}'s BUY and SELL "
                f"as FILLED with {INSTRUMENT} flat (inside the reconciliation window this can "
                "also mean that the Gateway's API timestamps are not in UTC)"
            )
        return found

    def _events_of(self, order: dict[str, Any] | None, kind: str) -> list[dict[str, Any]]:
        if order is None:
            return []
        oid = order.get("client_order_id")
        # Position events carry no order id, so look it up with .get.
        return [
            e for e in self.data["events"] if e["type"] == kind and e.get("client_order_id") == oid
        ]

    def _after_stop_cancel(self, prefix: str) -> str:
        """What the snapshot after shutdown says of an order (``prefix`` "" or "close_") that
        was still working at stop."""
        after = self.data.get("after_shutdown") or {}
        status = after.get(f"{prefix}order_status")
        settled = (
            status in _ENDED_UNFILLED
            and after.get(f"{prefix}filled_qty") == "0"
            and after.get("instrument_open_orders") == 0
            and after.get("instrument_inflight_orders") == 0
            and after.get("instrument_open_positions") == 0
        )
        if settled:
            return (
                f"after the node shut down it had ended unfilled ({status}) and {INSTRUMENT} "
                "was flat with nothing working"
            )
        return (
            "the state after the node shut down is missing or still shows an order working or "
            "a position; run `restart`, whose record shows any open order or position in the "
            "lane's instrument, before closing anything by hand"
        )

    def _fill_problems(self, stop: dict[str, Any] | None) -> list[str]:
        """What went wrong in a round trip, and whether a share may be open.

        The stop snapshot is taken just before the stop-time cancel; the cancel takes effect
        while the node shuts down, and the snapshot after shutdown shows its result. So an
        order that was still working at stop is reported as such, never as "did not fill":
        when the snapshot after shutdown shows that order ended unfilled with nothing working
        and the instrument flat, the line says so; when that snapshot is missing or still shows
        something working or a position, it advises running `restart` before closing anything
        by hand.
        """
        open_share = "one share is open in the paper account and has to be closed by hand"
        stop = stop or {}
        buy, sell = self.data["order"], self.data.get("close_order")
        found: list[str] = []
        if buy is None:
            return ["no opening order was submitted"]
        if (buy.get("type"), buy.get("time_in_force"), buy.get("side")) != ("LIMIT", "DAY", "BUY"):
            found.append("the opening order was not a DAY LIMIT BUY")
        if not self._events_of(buy, "OrderAccepted"):
            found.append("the venue never accepted the opening order " + GATEWAY_HINT)

        buy_fill_seen = bool(self._events_of(buy, "OrderFilled"))
        bought = (
            buy_fill_seen
            or sell is not None  # the closing order is only built on the opening order's fill
            or stop.get("order_status") == "FILLED"
            or stop.get("filled_qty") not in (None, "0")
            or stop.get("instrument_open_positions") not in (None, 0)
        )
        if not bought:
            if stop.get("order_status") in _ENDED_UNFILLED:
                found.append("the opening order did not fill")
            else:
                found.append(
                    "the opening order was still working when the run stopped: "
                    + self._after_stop_cancel("")
                )
            return found
        if not buy_fill_seen:
            found.append("the opening order's fill was not recorded")
        if sell is None:
            return [*found, f"no closing order was submitted: {open_share}"]

        if (sell.get("type"), sell.get("time_in_force"), sell.get("side")) != (
            "LIMIT",
            "DAY",
            "SELL",
        ):
            found.append("the closing order was not a DAY LIMIT SELL")
        if sell.get("quantity") != buy.get("quantity"):
            found.append("the closing order's quantity is not the opening order's")
        if not self._events_of(sell, "OrderAccepted"):
            found.append("the venue never accepted the closing order")
        if not self._events_of(sell, "OrderFilled"):
            status = stop.get("close_order_status")
            if status == "FILLED":
                found.append("the closing order's fill was not recorded")
            elif status in _ENDED_UNFILLED:
                found.append(f"the closing order did not fill: {open_share}")
            else:
                found.append(
                    "the closing order was still working when the run stopped: "
                    + self._after_stop_cancel("close_")
                )
        if stop and (
            stop.get("order_status") != "FILLED" or stop.get("close_order_status") != "FILLED"
        ):
            found.append("an order of the round trip did not end FILLED")
        return found

    def write(self, forbidden: list[str], settings: Settings | None = None) -> tuple[bool, Path]:
        """Write the full record to ``settings.runs_dir`` and, for a pass, the scrubbed record
        to ``settings.evidence_dir``. Folders 0700, files 0600, both outside the git tree."""
        settings = Settings.current() if settings is None else settings
        problems = self.problems()
        self.data["result"] = "fail" if problems else "pass"
        self.data["problems"] = problems
        self.data["run_ended_utc"] = _utc_now()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        name = _record_name(self.data["mode"], stamp)

        local_path = settings.runs_dir / name
        _write_private(
            local_path,
            json.dumps({**self.data, "local": self.local}, indent=2, sort_keys=True) + "\n",
        )
        if problems:
            return False, local_path

        text = scrubbed_json(self.data, forbidden)
        evidence_path = settings.evidence_dir / name
        _write_private(evidence_path, text)
        return True, evidence_path


def _shown(path: Path) -> str:
    """A path for the terminal, with the home folder written as ~."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _run_node(
    mode: str,
    bounds: boundary.IbkrBoundary,
    account: str,
    settings: Settings,
    *,
    source_commit: str,
    session: market_hours.Session | None,
    exclusive_checks: int,
) -> int:
    from astrolabe import ibkr_node  # the engine is loaded only after the gate has passed

    record = RunRecord(
        mode,
        bounds.port,
        bounds.paper_accounts_sha256 is not None,
        bounds.gateway_bind,
        source_commit=source_commit,
    )
    record.exclusive_use(exclusive_checks)
    if session is not None:
        record.market_hours(session)
    ibkr_node.run(mode, account, record, settings)

    # Every paper check again now that the run is over: still a paper login, the same account,
    # and the same contract (or still none)? Check 5 is not repeated: this run holds the lock
    # and its own connections may still be closing.
    try:
        bounds_after, account_after = gate(settings)
        record.paper_checks_after_run(account_after == account and bounds_after == bounds)
    except boundary.BoundaryError, ib_probe.ProbeError:
        record.paper_checks_after_run(False)

    if mode == "restart":
        record.compare_with_fill_pass(
            last_fill_pass(settings.evidence_dir),
            lookback_mins=ibkr_node.RECONCILIATION_LOOKBACK_MINS,
        )

    passed, path = record.write([account, boundary.account_hash(account)], settings)
    shown = _shown(path)
    if record.data.get("last_fill_pass_in_window") is False:
        print(
            f"The last passed fill run ({record.data['last_fill_pass_stamp']}) is outside the "
            "reconciliation window and was not compared."
        )
    if record.data.get("matches_last_fill_pass") is True:
        print(
            f"Reconciled: the venue reports fill run {record.data['last_fill_pass_stamp']}'s "
            f"BUY and SELL as FILLED and {INSTRUMENT} is flat"
        )
    if passed:
        print(f"PASS: scrubbed run record written to {shown}")
        return 0
    print("FAIL:", file=sys.stderr)
    for problem in record.data["problems"]:
        print(f"  - {problem}", file=sys.stderr)
    print(f"Full local record (not for the evidence repository): {shown}", file=sys.stderr)
    return 1


def bind_note(bind: str) -> str:
    """The line printed about how the Gateway's port is bound. The lane cannot read the
    Gateway's localhost-only box, so for any bind but loopback it asks the owner to check it."""
    if bind == "loopback":
        return "The port is bound to loopback only."
    return (
        f"The port is bound to {bind or 'an unknown scope'}. Only the Gateway's own \"Allow "
        'connections from localhost only" box keeps other hosts out, and the lane cannot read '
        "it: check that it is ticked in the Gateway window."
    )


def _refused(why: object) -> int:
    print(f"REFUSED TO START: {why}", file=sys.stderr)
    return 2


def _session_or_refuse() -> tuple[market_hours.Session | None, str | None]:
    """The fill session, or the reason fill mode must not start now."""
    try:
        session = fill_session()
    except market_hours.MarketHoursError as e:
        return None, f"the market calendar cannot place this moment: {e}"
    if session is None:
        return None, OUTSIDE_HOURS
    return session, None


def _release_held() -> None:
    while _held:
        _held.pop().lock.close()


def _hold(exclusive: boundary.ExclusiveUse) -> None:
    """Keep the account lock until the process exits."""
    _release_held()
    _held.append(exclusive)


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="astrolabe.ibkr_paper",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("mode", choices=MODES)
    args = parser.parse_args(argv)
    settings = Settings.current() if settings is None else settings

    try:
        source_commit = reviewed_code(settings.repo_root)
    except CodeNotReviewed as e:
        return _refused(f"the checkout is not the reviewed code ({e}): {PULL_HINT}")
    print(f"Code: main at {source_commit}, clean, equal to origin/main")

    session = None
    if args.mode == "fill":
        session, why = _session_or_refuse()
        if why is not None:
            return _refused(why)
    _release_held()  # main runs once per process; a test may call it again
    try:
        exclusive = boundary.require_exclusive_use(settings)
    except boundary.BoundaryError as e:
        return _refused(e)
    _hold(exclusive)
    try:
        bounds, account = gate(settings)
    except (boundary.BoundaryError, ib_probe.ProbeError) as e:
        return _refused(e)
    allowlisted = ", on the owner's allowlist" if bounds.paper_accounts_sha256 is not None else ""
    print(
        "Paper checks passed: the Gateway's jts.ini says Paper Trading, the Gateway listens "
        f"only on port {bounds.port}, and it reports one account of the paper form{allowlisted}."
    )
    print("Exclusive use: the account lock is held and no other API client is attached.")
    print(bind_note(bounds.gateway_bind))
    contract = settings.contract_path
    if contract.is_relative_to(settings.repo_root):
        contract = contract.relative_to(settings.repo_root)
    if bounds.paper_accounts_sha256 is None:
        print(f"Allowlist: none (no file at {contract}).")
    else:
        print(f"Allowlist: {contract}.")

    exclusive_checks = 1
    if args.mode in CONFIRMED_MODES:
        if not owner_confirms(args.mode):
            return _refused(
                f"{args.mode} mode needs the owner at a terminal, typing {CONFIRM_WORD} to confirm."
            )
        if args.mode == "fill":
            session, why = _session_or_refuse()
            if why is not None:
                return _refused(f"after the confirmation, {why}")
        # The prompt can wait for any length of time: check everything again, right before
        # the engine connects, and refuse unless nothing changed (the same Gateway process
        # and socket, login mode, account and contract, and still no other API client).
        try:
            boundary.require_exclusive_use(settings, held=exclusive)
            bounds_now, account_now = gate(settings)
        except (boundary.BoundaryError, ib_probe.ProbeError) as e:
            return _refused(f"after the confirmation, {e}")
        if (bounds_now, account_now) != (bounds, account):
            return _refused("the Gateway changed while the confirmation was pending.")
        exclusive_checks = 2
    return _run_node(
        args.mode,
        bounds,
        account,
        settings,
        source_commit=source_commit,
        session=session,
        exclusive_checks=exclusive_checks,
    )


if __name__ == "__main__":
    raise SystemExit(main())
