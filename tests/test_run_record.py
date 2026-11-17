"""The evidence record is scrubbed, a run passes only when M1's conditions hold, and the lane
starts only from reviewed code.

Ported from meridian@014f706d:tests/test_run_record.py. Changed: records are written under a
temporary state folder through ``Settings``; new cases for the 103 problem, the comparison of
a restart with the last passed fill run, the record's new fields and file modes, and the
reviewed-code check on a scratch git repository.
"""

import json
import stat
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import git_scratch
import pytest

from astrolabe import boundary, ib_ids, ibkr_paper
from astrolabe.settings import Settings

ACCOUNT = "DUFAKE0001"
ACCOUNT_HASH = "ab" * 32
FORBIDDEN = [ACCOUNT, ACCOUNT_HASH]
T0 = 1_794_000_000_000_000_000
ORDER_ID = "O-20261117-000000-001-M1-1"
PORT = ib_ids.PAPER_PORT


def state(tmp_path: Path) -> Settings:
    return Settings(repo_root=tmp_path / "repo", jts_dir=tmp_path / "Jts", state_dir=tmp_path)


def runs(tmp_path: Path) -> Path:
    return state(tmp_path).runs_dir


def evidence(tmp_path: Path) -> Path:
    return state(tmp_path).evidence_dir


def counts(instrument_orders=0, instrument_positions=0, account_orders=0, account_positions=0):
    return {
        "instrument_open_orders": instrument_orders,
        "instrument_open_positions": instrument_positions,
        "account_open_orders": account_orders,
        "account_open_positions": account_positions,
    }


def order_run() -> ibkr_paper.RunRecord:
    record = ibkr_paper.RunRecord("order", PORT, source_commit="c" * 40)
    record.trader_started(**counts())
    record.order_built(
        client_order_id=ORDER_ID,
        instrument_id="AAPL.XNAS",
        side="BUY",
        type="LIMIT",
        time_in_force="DAY",
        quantity="1",
        price="200.00",
    )
    for kind in ("OrderSubmitted", "OrderAccepted", "OrderCanceled"):
        record.event(kind, T0, client_order_id=ORDER_ID)
    record.at_stop(**counts(), order_status="CANCELED", filled_qty="0")
    record.after_shutdown(**after_counts(), order_status="CANCELED", filled_qty="0")
    record.paper_checks_after_run(True)
    return record


def after_counts(**changes: int) -> dict:
    """The counts of a snapshot after shutdown, the in-flight count included."""
    return {**counts(), "instrument_inflight_orders": 0, **changes}


def restart_run(at_start: dict | None = None, at_stop: dict | None = None):
    record = ibkr_paper.RunRecord("restart", PORT)
    record.trader_started(**(at_start or counts()))
    record.at_stop(**(at_stop or counts()))
    record.after_shutdown(**after_counts())
    record.paper_checks_after_run(True)
    return record


# --- what gets written ---------------------------------------------------------------------


def test_a_clean_order_run_passes_and_is_written_to_evidence(tmp_path):
    passed, path = order_run().write(FORBIDDEN, state(tmp_path))
    assert passed
    assert path.parent == evidence(tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["result"] == "pass"
    assert written["problems"] == []
    assert written["order"]["time_in_force"] == "DAY"
    assert [e["type"] for e in written["events"]] == [
        "OrderSubmitted",
        "OrderAccepted",
        "OrderCanceled",
    ]
    assert "local" not in written
    assert written["schema"] == "astrolabe.run-record/1"
    assert written["source_commit"] == "c" * 40


def test_records_live_outside_the_checkout_in_private_folders_and_files(tmp_path):
    settings = state(tmp_path)
    passed, path = order_run().write(FORBIDDEN, settings)
    assert passed
    (local,) = settings.runs_dir.iterdir()
    for folder in (settings.lane_dir, settings.runs_dir, settings.evidence_dir):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    for written in (local, path):
        assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert not path.is_relative_to(Path(ibkr_paper.__file__).resolve().parents[1])


def test_broker_free_text_stays_out_of_evidence(tmp_path):
    record = order_run()
    record.reason("OrderCanceled", f"cancelled for account {ACCOUNT}")
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert passed
    assert ACCOUNT not in path.read_text(encoding="utf-8")
    local = next(runs(tmp_path).iterdir()).read_text(encoding="utf-8")
    assert ACCOUNT in local  # kept locally, in runs/, which never leaves the machine


@pytest.mark.parametrize("leak", [ACCOUNT, ACCOUNT.lower(), ACCOUNT_HASH])
def test_a_record_holding_an_account_identifier_is_not_written(tmp_path, leak):
    record = order_run()
    record.data["order"]["client_order_id"] = f"X-{leak}"
    with pytest.raises(RuntimeError):
        record.write(FORBIDDEN, state(tmp_path))
    assert not evidence(tmp_path).exists()


# --- what makes an order run fail --------------------------------------------------------------


def filled(record):
    record.event("OrderFilled", T0, client_order_id=ORDER_ID, last_qty="1", last_px="200.00")
    record.at_stop(**counts(instrument_positions=1), order_status="FILLED", filled_qty="1")


def after(record, **changes):
    record.data["after_shutdown"].update(changes)


ORDER_FAILURES = {
    "trader never started": lambda r: r.data["startup_reconciliation"].update(trader_started=False),
    "no order": lambda r: r.data.update(order=None),
    "not a DAY order": lambda r: r.data["order"].update(time_in_force="GTC"),
    "not a LIMIT order": lambda r: r.data["order"].update(type="MARKET"),
    "never accepted": lambda r: r.data.update(
        events=[e for e in r.data["events"] if e["type"] != "OrderAccepted"]
    ),
    "not terminal": lambda r: r.data["at_stop"].update(order_status="ACCEPTED"),
    "order open at stop": lambda r: r.data["at_stop"].update(instrument_open_orders=1),
    "position open at stop": lambda r: r.data["at_stop"].update(instrument_open_positions=1),
    "no state at stop": lambda r: r.data.update(at_stop=None),
    "filled": filled,
    "filled, by status only": lambda r: r.data["at_stop"].update(order_status="FILLED"),
    "engine error": lambda r: r.engine_error("RuntimeError: disconnect timeout"),
    "order refused": lambda r: r.order_refused("order mode sends at most 1 order(s)"),
    "paper checks failed after the run": lambda r: r.paper_checks_after_run(False),
    "no check after the run": lambda r: r.data["boundary"].update(
        paper_checks_passed_after_run=None
    ),
    "instrument not clean at start": lambda r: r.data["startup_reconciliation"].update(
        instrument_open_positions_at_start=1
    ),
    "no state after shutdown": lambda r: r.data.update(after_shutdown=None),
    "position open after shutdown": lambda r: after(r, instrument_open_positions=1),
    "order open after shutdown": lambda r: after(r, instrument_open_orders=1),
    "order in flight after shutdown": lambda r: after(r, instrument_inflight_orders=1),
    "filled after shutdown": lambda r: after(r, order_status="FILLED", filled_qty="1"),
    "partly filled after shutdown, then cancelled": lambda r: after(r, filled_qty="1"),
}


@pytest.mark.parametrize("name", sorted(ORDER_FAILURES))
def test_an_order_run_that_misses_a_condition_fails_and_writes_no_evidence(tmp_path, name):
    record = order_run()
    ORDER_FAILURES[name](record)
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert not passed
    assert path.parent == runs(tmp_path)
    assert not evidence(tmp_path).exists()


def test_a_fill_is_named_as_the_problem(tmp_path):
    record = order_run()
    filled(record)
    record.write(FORBIDDEN, state(tmp_path))
    assert any("filled" in problem for problem in record.data["problems"])


FILLED_PROBLEM = (
    "the order filled: one share is open in the paper account and has to be closed by hand"
)


def test_a_fill_seen_only_after_shutdown_is_the_fill_problem_once():
    # CANCELED at stop, then FILLED in the engine's last seconds: one share is open.
    record = order_run()
    after(record, order_status="FILLED", filled_qty="1", instrument_open_positions=1)
    problems = record.problems()
    assert [p for p in problems if "the order filled" in p] == [FILLED_PROBLEM]
    record = order_run()
    filled(record)  # and seen at stop too: still named once
    after(record, order_status="FILLED", filled_qty="1")
    assert [p for p in record.problems() if "the order filled" in p] == [FILLED_PROBLEM]


def test_no_state_after_shutdown_is_named_as_the_problem():
    record = order_run()
    record.data["after_shutdown"] = None
    assert record.problems() == ["no state was captured after the node shut down"]


def test_a_position_after_shutdown_says_a_share_may_be_open():
    record = restart_run()
    after(record, instrument_open_positions=1)
    assert record.problems() == [
        "the position in the lane's instrument was not flat after the node shut down: a share "
        "may be open in the paper account"
    ]


@pytest.mark.parametrize("field", ["instrument_open_orders", "instrument_inflight_orders"])
def test_an_order_working_after_shutdown_says_run_restart_first(field):
    record = restart_run()
    after(record, **{field: 1})
    assert record.problems() == [
        "an order in the lane's instrument was open or in flight after the node shut down: "
        "run `restart` before closing anything by hand"
    ]


def test_the_record_starts_with_no_state_after_shutdown():
    for mode in ibkr_paper.MODES:
        assert ibkr_paper.RunRecord(mode, PORT).data["after_shutdown"] is None


def test_the_state_after_shutdown_is_scrubbed_evidence(tmp_path):
    record = order_run()
    after(record, venue_order_id="PERM-7")
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert passed
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["after_shutdown"]["venue_order_id"] == "PERM-7"
    leaking = order_run()
    after(leaking, venue_order_id=f"X-{ACCOUNT}")
    assert leaking.problems() == []  # a pass: the scrub is what refuses it
    with pytest.raises(RuntimeError):
        leaking.write(FORBIDDEN, state(tmp_path / "other"))


def test_a_refused_order_is_named_as_the_problem_and_its_reason_kept_locally(tmp_path):
    record = order_run()
    record.order_refused("order mode sends at most 1 order(s)")
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert not passed
    assert any("refused to send 1 order" in problem for problem in record.data["problems"])
    assert "sends at most" not in json.dumps(record.data)
    assert "sends at most" in path.read_text(encoding="utf-8")  # the local record in runs/


def test_an_engine_error_keeps_its_text_out_of_evidence_data():
    record = order_run()
    record.engine_error(f"RuntimeError: account {ACCOUNT}")
    assert record.data["engine_error"] is True
    assert ACCOUNT not in json.dumps(record.data)


def test_other_open_items_in_the_account_are_recorded_but_do_not_fail_the_run(tmp_path):
    record = order_run()
    record.trader_started(**counts(account_orders=2, account_positions=3))
    record.at_stop(
        **counts(account_orders=2, account_positions=3), order_status="CANCELED", filled_qty="0"
    )
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert passed
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["at_stop"]["account_open_positions"] == 3


# --- a rejection, and IB error 103 -----------------------------------------------------------


def rejected(record, code):
    record.data["events"] = [e for e in record.data["events"] if e["type"] == "OrderSubmitted"]
    record.event("OrderRejected", T0, client_order_id=ORDER_ID, ib_error_code=code)
    record.at_stop(**counts(), order_status="REJECTED", filled_qty="0")


def test_error_103_is_a_problem_of_its_own_that_names_the_recovery(tmp_path):
    record = order_run()
    rejected(record, 103)
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert not passed
    (lockout,) = [p for p in record.data["problems"] if "error 103" in p]
    assert "next spare execution id" in lockout and "astrolabe/ib_ids.py" in lockout
    assert "reviewed change" in lockout
    events = json.loads(path.read_text(encoding="utf-8"))["events"]
    assert [e["ib_error_code"] for e in events if e["type"] == "OrderRejected"] == [103]


@pytest.mark.parametrize("code", [201, None])
def test_another_rejection_fails_without_the_103_problem(tmp_path, code):
    record = order_run()
    rejected(record, code)
    passed, _ = record.write(FORBIDDEN, state(tmp_path))
    assert not passed
    assert not any("error 103" in p for p in record.data["problems"])


def test_the_venue_order_id_is_kept_for_each_of_the_runs_orders():
    record = fill_run()
    record.venue_order_id(BUY_ID, "101")
    record.venue_order_id(SELL_ID, "102")
    record.venue_order_id("someone else's order", "999")
    assert record.data["order"]["venue_order_id"] == "101"
    assert record.data["close_order"]["venue_order_id"] == "102"


def test_the_record_carries_the_new_fields():
    record = ibkr_paper.RunRecord("fill", PORT, source_commit="d" * 40)
    assert record.data["source_commit"] == "d" * 40
    assert record.data["node"] is None  # set by the engine side when the node is built
    assert record.data["exclusive_use"] is None
    assert record.data["market_hours"] is None
    record.exclusive_use(2)
    assert record.data["exclusive_use"] == {
        "account_lock_held": True,
        "other_api_clients_seen": 0,
        "other_project_lanes_seen": 0,
        "checks_passed_before_run": 2,
    }
    assert "reconciled" in ibkr_paper.RunRecord("restart", PORT).data
    assert "market_hours" not in ibkr_paper.RunRecord("order", PORT).data


# --- restart -----------------------------------------------------------------------------------


def test_a_clean_restart_passes(tmp_path):
    passed, _ = restart_run().write(FORBIDDEN, state(tmp_path))
    assert passed


@pytest.mark.parametrize("at_start", [counts(instrument_orders=1), counts(instrument_positions=1)])
def test_a_restart_that_finds_the_instrument_open_at_start_fails(tmp_path, at_start):
    # Clean again by the stop: only the at-start rule can catch this.
    passed, _ = restart_run(at_start=at_start, at_stop=counts()).write(FORBIDDEN, state(tmp_path))
    assert not passed


@pytest.mark.parametrize("at_stop", [counts(instrument_orders=1), counts(instrument_positions=1)])
def test_a_restart_that_ends_with_the_instrument_open_fails(tmp_path, at_stop):
    passed, _ = restart_run(at_stop=at_stop).write(FORBIDDEN, state(tmp_path))
    assert not passed


# --- fill: one BUY that fills, one SELL that closes it -------------------------------------------

BUY_ID = "O-20261117-000000-001-M1-FILL-1"
SELL_ID = "O-20261117-000000-001-M1-FILL-2"


def built(client_order_id: str, side: str, **changes: str) -> dict[str, str]:
    fields = {
        "client_order_id": client_order_id,
        "instrument_id": "AAPL.XNAS",
        "side": side,
        "type": "LIMIT",
        "time_in_force": "DAY",
        "quantity": "1",
        "price": "204.00" if side == "BUY" else "196.00",
    }
    fields.update(changes)
    return fields


def fill_run(buy_venue_id: str = "101", sell_venue_id: str = "102") -> ibkr_paper.RunRecord:
    record = ibkr_paper.RunRecord("fill", PORT)
    record.trader_started(**counts())
    record.order_built(**built(BUY_ID, "BUY"))
    for kind in ("OrderSubmitted", "OrderAccepted", "OrderFilled"):
        record.event(kind, T0, client_order_id=BUY_ID)
    record.close_order_built(**built(SELL_ID, "SELL"))
    for kind in ("OrderSubmitted", "OrderAccepted", "OrderFilled"):
        record.event(kind, T0, client_order_id=SELL_ID)
    record.venue_order_id(BUY_ID, buy_venue_id)
    record.venue_order_id(SELL_ID, sell_venue_id)
    record.at_stop(
        **counts(),
        order_status="FILLED",
        filled_qty="1",
        close_order_status="FILLED",
        close_filled_qty="1",
    )
    record.after_shutdown(
        **after_counts(),
        order_status="FILLED",
        filled_qty="1",
        venue_order_id=buy_venue_id,
        close_order_status="FILLED",
        close_filled_qty="1",
        close_venue_order_id=sell_venue_id,
    )
    record.paper_checks_after_run(True)
    return record


def test_a_clean_round_trip_passes_and_is_written_to_evidence(tmp_path):
    passed, path = fill_run().write(FORBIDDEN, state(tmp_path))
    assert passed
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["mode"] == "fill" and written["result"] == "pass"
    assert (written["order"]["side"], written["close_order"]["side"]) == ("BUY", "SELL")
    assert (written["order"]["venue_order_id"], written["close_order"]["venue_order_id"]) == (
        "101",
        "102",
    )


def drop_events(record: ibkr_paper.RunRecord, kind: str, client_order_id: str) -> None:
    record.data["events"] = [
        e
        for e in record.data["events"]
        if not (e["type"] == kind and e["client_order_id"] == client_order_id)
    ]


def not_opened(record: ibkr_paper.RunRecord) -> None:
    """The BUY never filled and was cancelled; no SELL was ever built."""
    drop_events(record, "OrderFilled", BUY_ID)
    record.data["close_order"] = None
    for kind in ("OrderSubmitted", "OrderAccepted", "OrderFilled"):
        drop_events(record, kind, SELL_ID)
    record.data["at_stop"].update(order_status="CANCELED", filled_qty="0")
    del record.data["at_stop"]["close_order_status"]
    del record.data["at_stop"]["close_filled_qty"]
    record.data["after_shutdown"].update(order_status="CANCELED", filled_qty="0")
    for key in ("close_order_status", "close_filled_qty", "close_venue_order_id"):
        del record.data["after_shutdown"][key]


FILL_FAILURES = {
    "opening order never filled": (not_opened, "the opening order did not fill"),
    "no closing order": (
        lambda r: r.data.update(close_order=None),
        "has to be closed by hand",
    ),
    "closing fill not recorded": (
        lambda r: drop_events(r, "OrderFilled", SELL_ID),
        "closing order's fill was not recorded",
    ),
    "a fill of another order is not the closing fill": (
        lambda r: [
            drop_events(r, "OrderFilled", SELL_ID),
            r.event("OrderFilled", T0, client_order_id="X"),
        ],
        "closing order's fill was not recorded",
    ),
    "opening order not a LIMIT": (
        lambda r: r.data["order"].update(type="MARKET"),
        "not a DAY LIMIT BUY",
    ),
    "opening order a SELL": (lambda r: r.data["order"].update(side="SELL"), "not a DAY LIMIT BUY"),
    "closing order a BUY": (
        lambda r: r.data["close_order"].update(side="BUY"),
        "not a DAY LIMIT SELL",
    ),
    "closing order not DAY": (
        lambda r: r.data["close_order"].update(time_in_force="GTC"),
        "not a DAY LIMIT SELL",
    ),
    "closing quantity differs": (
        lambda r: r.data["close_order"].update(quantity="2"),
        "quantity",
    ),
    "not flat at stop": (
        lambda r: r.data["at_stop"].update(instrument_open_positions=1),
        "not flat",
    ),
    "opening order still working at stop": (
        lambda r: [not_opened(r), r.data["at_stop"].update(order_status="ACCEPTED")],
        "still working when the run stopped",
    ),
    "a fill seen only at stop": (
        lambda r: drop_events(r, "OrderFilled", BUY_ID),
        "fill was not recorded",
    ),
    "closing order still working at stop": (
        lambda r: [
            drop_events(r, "OrderFilled", SELL_ID),
            r.data["at_stop"].update(close_order_status="ACCEPTED"),
        ],
        "still working when the run stopped",
    ),
    "closing order cancelled before stop": (
        lambda r: [
            drop_events(r, "OrderFilled", SELL_ID),
            r.data["at_stop"].update(close_order_status="CANCELED"),
        ],
        "has to be closed by hand",
    ),
    "closing order never accepted": (
        lambda r: drop_events(r, "OrderAccepted", SELL_ID),
        "never accepted the closing order",
    ),
    "opening order not DAY": (
        lambda r: r.data["order"].update(time_in_force="GTC"),
        "not a DAY LIMIT BUY",
    ),
    "closing order not a LIMIT": (
        lambda r: r.data["close_order"].update(type="MARKET"),
        "not a DAY LIMIT SELL",
    ),
    "opening order not FILLED at stop": (
        lambda r: r.data["at_stop"].update(order_status="PARTIALLY_FILLED"),
        "did not end FILLED",
    ),
    "no state at stop": (lambda r: r.data.update(at_stop=None), "no state was captured"),
    "an order in flight at stop": (
        lambda r: r.data["at_stop"].update(instrument_inflight_orders=1),
        "in flight",
    ),
    "closing order left open": (
        lambda r: r.data["at_stop"].update(close_order_status="CANCELED"),
        "did not end FILLED",
    ),
    "opening order never accepted": (
        lambda r: drop_events(r, "OrderAccepted", BUY_ID),
        "never accepted",
    ),
    "paper checks failed after the run": (
        lambda r: r.paper_checks_after_run(False),
        "paper checks",
    ),
    "no state after shutdown": (
        lambda r: r.data.update(after_shutdown=None),
        "no state was captured after the node shut down",
    ),
    "not flat after shutdown": (
        lambda r: after(r, instrument_open_positions=1),
        "a share may be open",
    ),
    "an order working after shutdown": (
        lambda r: after(r, instrument_open_orders=1),
        "run `restart` before closing anything by hand",
    ),
}


@pytest.mark.parametrize("name", sorted(FILL_FAILURES))
def test_a_round_trip_that_misses_a_condition_fails_and_writes_no_evidence(tmp_path, name):
    change, expected = FILL_FAILURES[name]
    record = fill_run()
    change(record)
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert not passed
    assert not evidence(tmp_path).exists()
    assert any(expected in problem for problem in record.data["problems"]), record.data["problems"]


@pytest.mark.parametrize(
    "signal",
    [
        {"order_status": "FILLED"},
        {"filled_qty": "1"},
        {"instrument_open_positions": 1},
    ],
    ids=["status", "filled-qty", "position"],
)
def test_a_fill_seen_only_in_the_stop_state_counts_as_an_open_share(tmp_path, signal):
    # The BUY filled, but its fill was never handled: no fill event, no closing order.
    record = fill_run()
    not_opened(record)
    record.data["at_stop"].update(order_status="ACCEPTED")  # no other sign of a fill...
    record.data["at_stop"].update(signal)  # ...except this one
    record.write(FORBIDDEN, state(tmp_path))
    problems = record.data["problems"]
    assert any("no closing order was submitted" in p and "closed by hand" in p for p in problems)


def test_a_cancelled_opening_order_says_nothing_is_open(tmp_path):
    record = fill_run()
    not_opened(record)
    record.write(FORBIDDEN, state(tmp_path))
    assert "the opening order did not fill" in record.data["problems"]
    assert not any("closed by hand" in p or "still working" in p for p in record.data["problems"])


def buy_working_at_stop() -> ibkr_paper.RunRecord:
    record = fill_run()
    not_opened(record)
    record.data["at_stop"].update(order_status="ACCEPTED")
    return record


def test_a_buy_working_at_stop_that_the_cancel_ended_unfilled_says_so():
    record = buy_working_at_stop()  # after shutdown: CANCELED, nothing filled, flat
    (working,) = [p for p in record.problems() if "still working" in p]
    assert "ended unfilled (CANCELED)" in working and "AAPL.XNAS was flat" in working
    assert "run `restart`" not in working
    assert not any("closed by hand" in p for p in record.problems())


@pytest.mark.parametrize(
    "change",
    [
        {"order_status": "ACCEPTED"},  # still working after shutdown
        {"instrument_open_orders": 1},
        {"instrument_inflight_orders": 1},
        {"instrument_open_positions": 1},
        {"filled_qty": "1"},  # cancelled after a partial fill
        None,  # no snapshot after shutdown
    ],
    ids=["working", "open", "in-flight", "position", "partly-filled", "missing"],
)
def test_a_buy_working_at_stop_without_a_settled_end_says_run_restart_first(change):
    record = buy_working_at_stop()
    if change is None:
        record.data["after_shutdown"] = None
    else:
        after(record, **change)
    (working,) = [p for p in record.problems() if "still working" in p]
    assert "run `restart`" in working and "before closing anything by hand" in working


def test_a_sell_working_at_stop_says_run_restart_first():
    record = fill_run()
    drop_events(record, "OrderFilled", SELL_ID)
    record.data["at_stop"].update(close_order_status="ACCEPTED")
    after(record, close_order_status="CANCELED", close_filled_qty="0", instrument_open_positions=1)
    problems = record.problems()
    (working,) = [p for p in problems if "closing order was still working" in p]
    assert "run `restart`" in working
    assert any("a share may be open" in p for p in problems)


def test_a_round_trip_with_no_state_at_stop_still_writes_its_local_record(tmp_path):
    record = fill_run()
    record.data["at_stop"] = None
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert not passed and path.parent == runs(tmp_path)


def test_position_events_without_an_order_id_do_not_break_the_fill_rules(tmp_path):
    record = fill_run()
    record.event("PositionOpened", T0)  # recorded without a client order id
    record.event("PositionClosed", T0)
    passed, _ = record.write(FORBIDDEN, state(tmp_path))
    assert passed


def test_only_a_fill_run_has_a_closing_order():
    assert "close_order" in ibkr_paper.RunRecord("fill", PORT).data
    for mode in ("order", "restart"):
        record = ibkr_paper.RunRecord(mode, PORT)
        assert "close_order" not in record.data
        with pytest.raises(RuntimeError):
            record.close_order_built(**built(SELL_ID, "SELL"))


def test_a_restart_where_the_trader_never_started_fails(tmp_path):
    record = ibkr_paper.RunRecord("restart", PORT)
    record.paper_checks_after_run(True)
    passed, _ = record.write(FORBIDDEN, state(tmp_path))
    assert not passed


# --- restart after a fill run: matches_last_fill_pass ------------------------------------------


LOOKBACK = 60  # ibkr_node.RECONCILIATION_LOOKBACK_MINS, which _run_node passes in
WINDOW_MINS = LOOKBACK - 2


def minutes_before(record: ibkr_paper.RunRecord, minutes: int) -> str:
    """A ``run_started_utc`` that many minutes before the record's own."""
    started = datetime.strptime(record.data["run_started_utc"], "%Y-%m-%dT%H:%M:%SZ")
    return (started - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_fill_pass(
    tmp_path: Path, buy: str = "101", sell: str = "102", minutes_ago: int | None = None
) -> str:
    record = fill_run(buy, sell)
    if minutes_ago is not None:
        record.data["run_started_utc"] = minutes_before(record, minutes_ago)
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert passed
    return path.name.removeprefix("M1-ibkr-paper-").removesuffix("-fill.json")


def reconciled_restart(
    filled_ids: list[str], positions_at_start: int = 0, filled_client_ids: list[str] | None = None
):
    record = restart_run(at_start=counts(instrument_positions=positions_at_start))
    record.reconciled(
        instrument_orders_by_status={"FILLED": len(filled_ids)},
        filled_venue_order_ids=filled_ids,
        filled_client_order_ids=filled_client_ids,
    )
    return record


def test_without_a_passed_fill_run_the_field_is_left_out(tmp_path):
    record = reconciled_restart(["101", "102"])
    record.compare_with_fill_pass(
        ibkr_paper.last_fill_pass(evidence(tmp_path)), lookback_mins=LOOKBACK
    )
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert passed
    written = json.loads(path.read_text(encoding="utf-8"))
    assert "matches_last_fill_pass" not in written
    assert "last_fill_pass_in_window" not in written


def test_a_restart_that_sees_the_fill_runs_orders_filled_and_flat_matches(tmp_path):
    stamp = write_fill_pass(tmp_path)
    record = reconciled_restart(["100", "101", "102"])
    last = ibkr_paper.last_fill_pass(evidence(tmp_path))
    assert last is not None and last["stamp"] == stamp
    record.compare_with_fill_pass(last, lookback_mins=LOOKBACK)
    passed, path = record.write(FORBIDDEN, state(tmp_path))
    assert passed
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["matches_last_fill_pass"] is True
    assert written["last_fill_pass_in_window"] is True
    assert written["last_fill_pass_stamp"] == stamp
    assert written["last_fill_pass_matched_by"] == {
        "order": "venue_order_id",
        "close_order": "venue_order_id",
    }
    assert written["reconciled"]["filled_venue_order_ids"] == ["100", "101", "102"]


@pytest.mark.parametrize(
    ("filled_venue", "filled_client", "matched_by"),
    [
        (["PERM-9001", "PERM-9002"], [BUY_ID, SELL_ID], ("client_order_id", "client_order_id")),
        (["101", "PERM-9002"], [BUY_ID, SELL_ID], ("venue_order_id", "client_order_id")),
    ],
    ids=["both-by-client-id", "one-by-each"],
)
def test_an_order_whose_venue_id_changed_form_matches_by_its_client_order_id(
    tmp_path, filled_venue, filled_client, matched_by
):
    # The fill run saw the bare IB order ids 101 and 102, before IB's permIds arrived.
    write_fill_pass(tmp_path)
    record = reconciled_restart(filled_venue, filled_client_ids=filled_client)
    record.compare_with_fill_pass(
        ibkr_paper.last_fill_pass(evidence(tmp_path)), lookback_mins=LOOKBACK
    )
    assert record.data["matches_last_fill_pass"] is True
    assert record.data["last_fill_pass_matched_by"] == dict(
        zip(("order", "close_order"), matched_by, strict=True)
    )
    assert record.problems() == []


def test_neither_id_of_an_order_matching_fails_the_comparison(tmp_path):
    write_fill_pass(tmp_path)
    record = reconciled_restart(["PERM-9001", "PERM-9002"], filled_client_ids=[BUY_ID, "O-X"])
    record.compare_with_fill_pass(
        ibkr_paper.last_fill_pass(evidence(tmp_path)), lookback_mins=LOOKBACK
    )
    assert record.data["matches_last_fill_pass"] is False
    assert record.data["last_fill_pass_matched_by"] == {
        "order": "client_order_id",
        "close_order": None,
    }


@pytest.mark.parametrize("minutes_ago", [0, WINDOW_MINS])
def test_a_fill_run_inside_the_window_is_compared(tmp_path, minutes_ago):
    write_fill_pass(tmp_path)
    record = reconciled_restart([])
    last = ibkr_paper.last_fill_pass(evidence(tmp_path))
    assert last is not None
    last["run_started_utc"] = minutes_before(record, minutes_ago)
    record.compare_with_fill_pass(last, lookback_mins=LOOKBACK)
    assert record.data["last_fill_pass_in_window"] is True
    assert record.data["matches_last_fill_pass"] is False  # compared, and nothing was filled


@pytest.mark.parametrize(
    "started",
    ["minutes:59", "minutes:600", "minutes:-1", "2026-11-17 10:00:00", "", None, 12345, "absent"],
    ids=[
        "just-outside",
        "long-ago",
        "after-this-run",
        "other-format",
        "empty",
        "null",
        "number",
        "absent",
    ],
)
def test_a_fill_run_outside_the_window_or_of_unknown_time_is_not_compared(tmp_path, started):
    stamp = write_fill_pass(tmp_path)
    record = reconciled_restart([])  # nothing reconciled: a comparison would fail
    last = ibkr_paper.last_fill_pass(evidence(tmp_path))
    assert last is not None
    if started == "absent":
        del last["run_started_utc"]
    elif isinstance(started, str) and started.startswith("minutes:"):
        last["run_started_utc"] = minutes_before(record, int(started.split(":")[1]))
    else:
        last["run_started_utc"] = started
    record.compare_with_fill_pass(last, lookback_mins=LOOKBACK)
    assert record.data["last_fill_pass_stamp"] == stamp
    assert record.data["last_fill_pass_in_window"] is False
    assert record.data["matches_last_fill_pass"] is None
    assert "last_fill_pass_matched_by" not in record.data
    assert record.problems() == []


@pytest.mark.parametrize(
    ("filled_ids", "positions_at_start"),
    [(["101"], 0), ([], 0), (["101", "102"], 1)],
    ids=["sell-missing", "nothing-filled", "not-flat"],
)
def test_a_restart_that_does_not_see_the_fill_run_fails(tmp_path, filled_ids, positions_at_start):
    stamp = write_fill_pass(tmp_path)
    record = reconciled_restart(filled_ids, positions_at_start)
    record.compare_with_fill_pass(
        ibkr_paper.last_fill_pass(evidence(tmp_path)), lookback_mins=LOOKBACK
    )
    passed, _ = record.write(FORBIDDEN, state(tmp_path))
    assert not passed
    assert record.data["matches_last_fill_pass"] is False
    (problem,) = [p for p in record.data["problems"] if f"fill run {stamp}" in p]
    assert "Gateway's API timestamps are not in UTC" in problem


def test_the_newest_passed_fill_run_is_the_one_compared(tmp_path):
    folder = evidence(tmp_path)
    boundary.private_dir(folder)
    older = fill_run("1", "2").data | {"result": "pass"}
    newer = fill_run("3", "4").data | {"result": "pass"}
    failed = fill_run("5", "6").data | {"result": "fail"}
    for stamp, data in (("20261117T150000Z", older), ("20261117T160000Z", newer)):
        (folder / f"M1-ibkr-paper-{stamp}-fill.json").write_text(json.dumps(data))
    (folder / "M1-ibkr-paper-20261117T170000Z-fill.json").write_text(json.dumps(failed))
    (folder / "M1-ibkr-paper-20261117T180000Z-restart.json").write_text("{}")
    (folder / "M1-ibkr-paper-20261117T190000Z-fill.json").write_text("{not json")
    last = ibkr_paper.last_fill_pass(folder)
    assert last is not None
    assert last["stamp"] == "20261117T160000Z"
    assert last["order"]["venue_order_id"] == "3"


@pytest.fixture
def restart_after_fill(tmp_path, monkeypatch):
    """The real _run_node for a restart, with the engine and the gate replaced."""
    from astrolabe import ibkr_node

    bounds = boundary.IbkrBoundary(host="127.0.0.1", port=PORT, paper_accounts_sha256=None)
    monkeypatch.setattr(ibkr_paper, "gate", lambda settings=None: (bounds, ACCOUNT))

    def run_with(filled_ids: list[str]) -> int:
        def fake_run(mode, account, record, settings=None):
            record.trader_started(**counts())
            record.reconciled(
                instrument_orders_by_status={"FILLED": len(filled_ids)},
                filled_venue_order_ids=filled_ids,
            )
            record.at_stop(**counts())
            record.after_shutdown(**after_counts())

        monkeypatch.setattr(ibkr_node, "run", fake_run)
        return ibkr_paper._run_node(
            "restart",
            bounds,
            ACCOUNT,
            state(tmp_path),
            source_commit="e" * 40,
            session=None,
            exclusive_checks=1,
        )

    return run_with


def test_the_lane_says_when_the_venue_reconciled_the_fill_run(restart_after_fill, tmp_path, capsys):
    stamp = write_fill_pass(tmp_path)
    assert restart_after_fill(["101", "102"]) == 0
    out = capsys.readouterr().out
    assert (
        f"Reconciled: the venue reports fill run {stamp}'s BUY and SELL as FILLED and "
        "AAPL.XNAS is flat"
    ) in out


def test_the_lane_says_nothing_of_a_fill_run_when_there_is_none(restart_after_fill, capsys):
    assert restart_after_fill(["101", "102"]) == 0
    assert "Reconciled" not in capsys.readouterr().out


def test_the_lane_fails_when_the_venue_does_not_show_the_fill_run(
    restart_after_fill, tmp_path, capsys
):
    stamp = write_fill_pass(tmp_path)
    assert restart_after_fill(["101"]) == 1
    captured = capsys.readouterr()
    assert "Reconciled" not in captured.out
    assert f"fill run {stamp}" in captured.err
    assert "outside the reconciliation window" not in captured.out


def test_a_restart_long_after_the_last_fill_run_passes_and_says_why_it_did_not_compare(
    restart_after_fill, tmp_path, capsys
):
    stamp = write_fill_pass(tmp_path, minutes_ago=3 * 60)
    assert restart_after_fill([]) == 0  # nothing reconciled, and not a failure for that
    out = capsys.readouterr().out
    assert (
        f"The last passed fill run ({stamp}) is outside the reconciliation window and was not "
        "compared."
    ) in out
    assert "Reconciled" not in out


@pytest.mark.parametrize(
    ("engine_lookback", "minutes_ago", "compared"),
    [(240, 100, True), (10, 30, False)],
    ids=["wider-window-compares", "narrower-window-does-not"],
)
def test_the_lane_passes_the_engines_lookback_to_the_comparison(
    restart_after_fill, tmp_path, monkeypatch, capsys, engine_lookback, minutes_ago, compared
):
    # With the engine's value changed, a literal 60 in _run_node would decide the other way:
    # a fill run 100 minutes back is outside 60 and inside 240; 30 minutes back is the reverse.
    from astrolabe import ibkr_node

    assert ibkr_node.RECONCILIATION_LOOKBACK_MINS == LOOKBACK
    monkeypatch.setattr(ibkr_node, "RECONCILIATION_LOOKBACK_MINS", engine_lookback)
    stamp = write_fill_pass(tmp_path, minutes_ago=minutes_ago)
    # Nothing reconciled: compared, the run fails; not compared, it passes.
    assert restart_after_fill([]) == (1 if compared else 0)
    captured = capsys.readouterr()
    assert (f"fill run {stamp}'s BUY and SELL" in captured.err) is compared
    assert ("outside the reconciliation window" in captured.out) is not compared


# --- the reviewed-code check ---------------------------------------------------------------


@pytest.fixture
def checkout(tmp_path):
    """A scratch repository on main, committed, with a local origin/main at its HEAD."""
    repo = git_scratch.init(tmp_path)
    (repo / "README").write_text("scratch\n")
    for args in (("add", "README"), ("commit", "-q", "-m", "one")):
        assert git_scratch.git(repo, *args).returncode == 0
    head = git_scratch.git(repo, "rev-parse", "HEAD").stdout.strip()
    assert git_scratch.git(repo, "update-ref", "refs/remotes/origin/main", head).returncode == 0
    return SimpleNamespace(repo=repo, head=head)


def test_a_clean_main_equal_to_origin_main_is_reviewed_code(checkout):
    assert ibkr_paper.reviewed_code(checkout.repo) == checkout.head


@pytest.mark.parametrize("change", ["untracked", "modified", "staged"])
def test_a_dirty_checkout_is_refused(checkout, change):
    if change == "untracked":
        (checkout.repo / "new").write_text("x\n")
    else:
        (checkout.repo / "README").write_text("changed\n")
        if change == "staged":
            assert git_scratch.git(checkout.repo, "add", "README").returncode == 0
    with pytest.raises(ibkr_paper.CodeNotReviewed, match="uncommitted or untracked"):
        ibkr_paper.reviewed_code(checkout.repo)


def test_another_branch_is_refused(checkout):
    assert git_scratch.git(checkout.repo, "switch", "-q", "-c", "topic").returncode == 0
    with pytest.raises(ibkr_paper.CodeNotReviewed, match="not on main"):
        ibkr_paper.reviewed_code(checkout.repo)


def test_a_detached_head_is_refused(checkout):
    assert git_scratch.git(checkout.repo, "switch", "-q", "--detach").returncode == 0
    with pytest.raises(ibkr_paper.CodeNotReviewed, match="not on main"):
        ibkr_paper.reviewed_code(checkout.repo)


def test_a_head_ahead_of_origin_main_is_refused(checkout):
    assert (
        git_scratch.git(checkout.repo, "commit", "-q", "--allow-empty", "-m", "two").returncode == 0
    )
    with pytest.raises(ibkr_paper.CodeNotReviewed, match="not the commit origin/main names"):
        ibkr_paper.reviewed_code(checkout.repo)


def test_a_head_behind_origin_main_is_refused(checkout):
    assert (
        git_scratch.git(checkout.repo, "commit", "-q", "--allow-empty", "-m", "two").returncode == 0
    )
    newer = git_scratch.git(checkout.repo, "rev-parse", "HEAD").stdout.strip()
    git_scratch.git(checkout.repo, "update-ref", "refs/remotes/origin/main", newer)
    git_scratch.git(checkout.repo, "reset", "-q", "--hard", checkout.head)
    with pytest.raises(ibkr_paper.CodeNotReviewed, match="not the commit origin/main names"):
        ibkr_paper.reviewed_code(checkout.repo)


def test_no_origin_main_is_refused(checkout):
    git_scratch.git(checkout.repo, "update-ref", "-d", "refs/remotes/origin/main")
    with pytest.raises(ibkr_paper.CodeNotReviewed, match="no local origin/main"):
        ibkr_paper.reviewed_code(checkout.repo)


def test_a_folder_that_is_not_a_repository_is_refused(tmp_path):
    with pytest.raises(ibkr_paper.CodeNotReviewed):
        ibkr_paper.reviewed_code(tmp_path / "nowhere")


@pytest.mark.parametrize("mode", ["order", "fill", "restart"])
def test_the_lane_refuses_unreviewed_code_before_anything_else(
    checkout, tmp_path, monkeypatch, capsys, mode
):
    (checkout.repo / "new").write_text("x\n")
    settings = Settings(repo_root=checkout.repo, jts_dir=tmp_path / "Jts", state_dir=tmp_path / "s")
    for name in ("fill_session", "gate", "owner_confirms"):
        monkeypatch.setattr(ibkr_paper, name, lambda *a, **k: pytest.fail("reached"))
    monkeypatch.setattr(boundary, "require_exclusive_use", lambda *a, **k: pytest.fail("reached"))
    assert ibkr_paper.main([mode], settings) == 2
    err = capsys.readouterr().err
    assert "REFUSED TO START" in err and "git pull --ff-only" in err
    assert not (tmp_path / "s").exists()
