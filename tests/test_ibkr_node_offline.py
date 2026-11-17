"""
The engine side builds against the installed engine without connecting to anything, and its
strategies do what each mode allows and nothing more.

Rewritten from meridian@014f706d:tests/test_ibkr_node_offline.py for the rewritten
``astrolabe/ibkr_node.py``: the node is built with ``LiveNode.builder`` (no upstream example
module), and the strategies are driven through their callbacks with a stand-in cache, the
engine's own ``OrderFactory`` and real ``Price``/``Quantity`` values. Orders are caught at the
engine's ``submit_order``, below the lane's ``_send``, so the lane's own refusal is what is
tested. No test runs a node: a real node is built and disposed, never started.
"""

import ast
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator, MutableMapping
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nautilus_trader.common import Clock, OrderFactory
from nautilus_trader.live import LiveNodeConfig
from nautilus_trader.model import (
    ClientOrderId,
    Currency,
    Equity,
    InstrumentId,
    OrderSide,
    Price,
    Quantity,
    StrategyId,
    Symbol,
    TimeInForce,
    TraderId,
)

from astrolabe import ib_ids, ibkr_node, ibkr_paper
from astrolabe.settings import Settings

FAKE = "DUFAKE0001"
PORT = ib_ids.PAPER_PORT
NOW_NS = 1_794_000_000_000_000_000  # the strategies' test clock
SECOND_NS = 1_000_000_000
NODE_SOURCE = Path(ibkr_node.__file__)


def equity(increment: str = "0.01") -> Equity:
    tick = Price.from_str(increment)
    return Equity(
        InstrumentId.from_str(ibkr_node.INSTRUMENT_ID),
        Symbol("AAPL"),
        Currency.from_str("USD"),
        tick.precision,
        tick,
        0,
        0,
    )


# --- the node, built offline --------------------------------------------------------------


@pytest.mark.parametrize("mode", ["order", "fill", "restart"])
def test_each_mode_builds_a_real_node_offline(mode):
    record = ibkr_paper.RunRecord(mode, PORT)
    node, strategy = ibkr_node.build(mode, FAKE, record)
    assert node is not None
    assert record.data["engine"]["nautilus_trader"] == "2.0.0rc9"
    assert record.data["node"] == {
        "reconciliation_lookback_mins": 60,
        "max_quote_age_seconds": 30,
        "client_ids": {"data": ib_ids.DATA_CLIENT_ID, "execution": ib_ids.EXECUTION_CLIENT_ID},
        "market_data_type": "DELAYED_FROZEN",
    }
    expected = ibkr_node.OneRoundTrip if mode == "fill" else ibkr_node.OneDayLimit
    assert type(strategy) is expected
    assert str(strategy.instrument_id) == "AAPL.XNAS"
    # Registration with the node gave the strategy the id its class names.
    assert str(strategy.strategy_id) == expected.strategy_id_value


class RecordingBuilder:
    """Stands in for the engine's node builder and records every call the lane makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.strategies: list = []
        self.node = SimpleNamespace(add_strategy=self.strategies.append)

    def __getattr__(self, name: str):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.node if name == "build" else self

        return method

    def named(self, name: str) -> list[tuple]:
        return [args for called, args, _ in self.calls if called == name]


@pytest.fixture
def recorded(monkeypatch):
    """Build a mode's node with the recording builder; nothing real is built."""
    started: list[tuple] = []

    def build(mode: str) -> tuple[RecordingBuilder, ibkr_paper.RunRecord]:
        builder = RecordingBuilder()

        def start(*args):
            started.append(args)
            return builder

        monkeypatch.setattr(ibkr_node, "LiveNode", SimpleNamespace(builder=start))
        record = ibkr_paper.RunRecord(mode, PORT)
        ibkr_node.build(mode, FAKE, record)
        return builder, record

    build.started = started  # type: ignore[attr-defined]
    return build


@pytest.mark.parametrize("mode", ["order", "fill", "restart"])
def test_the_builder_is_driven_as_the_lane_intends(recorded, mode):
    builder, record = recorded(mode)
    (name, trader, environment) = recorded.started[-1]
    assert (name, str(trader), environment.name) == (
        "ASTROLABE-IBKR-PAPER",
        "ASTROLABE-001",
        "LIVE",
    )
    # Upstream's timeouts (examples/live/interactive_brokers/_common.py:531-537).
    assert builder.named("with_timeout_connection") == [(15,)]
    assert builder.named("with_timeout_reconciliation") == [(5,)]
    assert builder.named("with_timeout_portfolio") == [(5,)]
    assert builder.named("with_timeout_disconnection_secs") == [(5,)]
    assert builder.named("with_delay_post_stop_secs") == [(5,)]
    # Reconciliation is on, with the lookback the record names.
    assert builder.named("with_reconciliation") == [(True,)]
    assert builder.named("with_reconciliation_lookback_mins") == [(60,)]
    assert ibkr_node.RECONCILIATION_LOOKBACK_MINS == 60
    assert record.data["node"]["reconciliation_lookback_mins"] == 60
    # One data client and one execution client, on the paper target with the lane's ids.
    ((_, factory, data),) = builder.named("add_data_client")
    ((_, exec_factory, execution),) = builder.named("add_exec_client")
    assert (factory.name(), exec_factory.name()) == ("IB", "IB")
    assert (data.host, data.port, data.client_id) == (ib_ids.HOST, PORT, ib_ids.DATA_CLIENT_ID)
    assert (execution.host, execution.port, execution.client_id) == (
        ib_ids.HOST,
        PORT,
        ib_ids.EXECUTION_CLIENT_ID,
    )
    assert str(data.market_data_type) == "MarketDataType.DELAYED_FROZEN"
    assert (data.request_timeout, data.max_reconnect_attempts) == (30, 0)
    assert execution.account_id == FAKE
    assert execution.fetch_all_open_orders is False
    assert execution.max_reconnect_attempts == 0
    for config in (data, execution):
        provider = config.instrument_provider
        assert provider.load_ids == {InstrumentId.from_str("AAPL.XNAS")}
        assert str(provider.symbology_method) == "SymbologyMethod.SIMPLIFIED"
    # Nothing else is configured: no other client, no simulated venue.
    assert builder.named("add_simulated_exec_client") == []
    (strategy,) = builder.strategies
    assert type(strategy) is (ibkr_node.OneRoundTrip if mode == "fill" else ibkr_node.OneDayLimit)


class StrictEnviron(MutableMapping[str, str]):
    """An ``os.environ`` stand-in that fails a test on any read or write."""

    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"the lane read the environment variable {key!r}")

    def get(self, key, default=None):  # type: ignore[override]
        raise AssertionError(f"the lane read the environment variable {key!r}")

    def __setitem__(self, key: str, value: str) -> None:
        raise AssertionError(f"the lane set the environment variable {key!r}")

    def __delitem__(self, key: str) -> None:
        raise AssertionError(f"the lane removed the environment variable {key!r}")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("the lane listed the environment")

    def __len__(self) -> int:
        raise AssertionError("the lane listed the environment")


@pytest.mark.parametrize("mode", ["order", "fill", "restart"])
def test_no_environment_variable_is_read_while_building(recorded, monkeypatch, mode):
    # Only for the call: pytest itself writes os.environ between its phases.
    with monkeypatch.context() as during:
        during.setattr(os, "environ", StrictEnviron())
        recorded(mode)  # the engine's own builder is not run here, the lane's code is


def test_the_engine_module_names_no_environment_variable():
    tree = ast.parse(NODE_SOURCE.read_text(encoding="utf-8"))
    hits = [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv", "putenv"})
        or (isinstance(node, ast.Name) and node.id in {"environ", "getenv"})
    ]
    assert hits == []


def test_the_client_ids_come_from_ib_ids():
    tree = ast.parse(NODE_SOURCE.read_text(encoding="utf-8"))
    names = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ib_ids"
    }
    assert {"HOST", "PAPER_PORT", "DATA_CLIENT_ID", "EXECUTION_CLIENT_ID"} <= names


def test_every_mode_has_its_own_order_budget_and_stop_time():
    assert ibkr_node.MAX_ORDERS == {"order": 1, "fill": 2, "restart": 0}
    assert ibkr_node.AUTO_STOP_SECONDS == {"order": 60, "restart": 25, "fill": 90}
    assert set(ibkr_node.MAX_ORDERS) == set(ibkr_paper.MODES)


def test_client_order_ids_compare_by_value():
    # on_order_accepted and on_order_filled match an event to this run's order by this
    # comparison.
    assert ClientOrderId.from_str("O-1-M1-FILL-1") == ClientOrderId.from_str("O-1-M1-FILL-1")
    assert ClientOrderId.from_str("O-1-M1-FILL-1") != ClientOrderId.from_str("O-1-M1-FILL-2")


def test_an_unknown_mode_builds_nothing(recorded):
    with pytest.raises(ValueError, match="unknown mode"):
        recorded("later")


def test_the_default_risk_engine_is_not_bypassed():
    assert LiveNodeConfig().risk_engine.bypass is False


# --- exact prices ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "increment", "up", "expected"),
    [
        ("195.9902", "0.01", False, "195.99"),
        ("195.9902", "0.01", True, "196.00"),
        ("204.0102", "0.01", True, "204.02"),
        ("204.0102", "0.01", False, "204.01"),
        ("195.02", "0.01", False, "195.02"),  # already on the grid: unchanged either way
        ("195.02", "0.01", True, "195.02"),
        ("195.03", "0.05", False, "195.00"),
        ("195.03", "0.05", True, "195.05"),
        ("195.05", "0.05", True, "195.05"),
    ],
)
def test_prices_are_rounded_onto_the_grid_the_safe_way(value, increment, up, expected):
    assert ibkr_node.on_grid(Decimal(value), Decimal(increment), up=up) == Decimal(expected)
    price = ibkr_node.exact_price(equity(increment), Decimal(value), up=up)
    assert isinstance(price, Price)
    assert price.as_decimal() == Decimal(expected)
    assert price.precision == Price.from_str(increment).precision


def test_order_mode_rests_under_the_bid_and_rounds_down():
    price = ibkr_node.resting_buy_price(equity(), Price.from_str("199.99"))
    assert str(price) == "195.99"  # 199.99 x 0.98 = 195.9902, down


def test_fill_mode_buys_above_the_ask_and_rounds_up():
    price = ibkr_node.marketable_buy_price(
        equity(), Price.from_str("199.00"), Price.from_str("200.01")
    )
    assert str(price) == "204.02"  # 200.01 x 1.02 = 204.0102, up


def test_fill_mode_sells_under_the_reference_and_rounds_down():
    assert str(ibkr_node.marketable_sell_price(equity(), Price.from_str("199.00"))) == "195.02"
    assert str(ibkr_node.marketable_sell_price(equity(), Price.from_str("199.99"))) == "195.99"


@pytest.mark.parametrize(
    ("bid", "ask"),
    [("0.00", "200.00"), ("199.00", "0.00"), ("201.00", "200.00")],
    ids=["no-bid", "no-ask", "crossed"],
)
def test_fill_mode_refuses_to_price_a_buy_from_a_bad_quote(bid, ask):
    with pytest.raises(ValueError):
        ibkr_node.marketable_buy_price(equity(), Price.from_str(bid), Price.from_str(ask))


def test_no_price_is_built_from_a_float():
    tree = ast.parse(NODE_SOURCE.read_text(encoding="utf-8"))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls & {"make_price", "as_double", "from_double"}
    floats = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and type(node.value) is float
    ]
    assert floats == []


# --- the strategies, driven offline -----------------------------------------------------------


class OrderAccepted(SimpleNamespace):
    pass


class OrderFilled(SimpleNamespace):
    pass


class OrderRejected(SimpleNamespace):
    pass


class FakeCache:
    """The cache calls the strategies make, answering from plain lists."""

    def __init__(self) -> None:
        self.open_orders: list = []
        self.open_positions: list = []
        self.inflight: list = []
        self.all_orders: list = []
        self.known: dict = {}
        self.instruments: dict = {}

    def orders_open(self, instrument_id=None, **_):
        return self.open_orders

    def positions_open(self, instrument_id=None, **_):
        return self.open_positions

    def orders_inflight(self, instrument_id=None, **_):
        return self.inflight

    def orders(self, instrument_id=None, **_):
        return self.all_orders

    def order(self, client_order_id):
        return self.known.get(client_order_id)

    def instrument(self, instrument_id):
        return self.instruments.get(instrument_id)


def offline_strategy(
    monkeypatch, mode: str, instrument: Any = None, now_ns: int = NOW_NS
) -> SimpleNamespace:
    """The lane's strategy for ``mode`` outside any node, its clock at ``now_ns``. Every engine
    call it makes is caught and listed in ``calls``; submitted orders are also in ``sent``."""
    record = ibkr_paper.RunRecord(mode, PORT)
    cls = ibkr_node.OneRoundTrip if mode == "fill" else ibkr_node.OneDayLimit
    strategy = cls.create(record)
    cache = FakeCache()
    cache.instruments[strategy.instrument_id] = instrument or equity()
    factory = OrderFactory(
        TraderId.from_str("ASTROLABE-001"),
        StrategyId.from_str(cls.strategy_id_value),
        Clock.new_test(),
    )
    sent: list = []
    calls: list[tuple[str, tuple, dict]] = []
    clock = Clock.new_test()
    clock.set_time(now_ns)

    def catch(name):
        def call(self, *args, **kwargs):
            calls.append((name, args, kwargs))
            if name == "submit_order":
                sent.append(args[0])

        return call

    monkeypatch.setattr(cls, "cache", property(lambda self: cache))
    monkeypatch.setattr(cls, "order_factory", property(lambda self: factory))
    monkeypatch.setattr(cls, "clock", property(lambda self: clock))
    monkeypatch.setattr(cls, "strategy_id", property(lambda self: StrategyId.from_str("S-1")))
    for name in (
        "submit_order",
        "cancel_order",
        "cancel_all_orders",
        "subscribe_quotes",
        "unsubscribe_quotes",
        "request_instrument",
    ):
        monkeypatch.setattr(cls, name, catch(name))

    def quote(bid: str, ask: str, ts_event: int | None = None) -> None:
        """A quote stamped at the strategy's clock, unless ``ts_event`` is given."""
        strategy.on_quote(
            SimpleNamespace(
                instrument_id=strategy.instrument_id,
                bid_price=Price.from_str(bid),
                ask_price=Price.from_str(ask),
                ts_event=clock.timestamp_ns() if ts_event is None else ts_event,
            )
        )

    def named(name: str) -> list[tuple[tuple, dict]]:
        return [(args, kwargs) for called, args, kwargs in calls if called == name]

    return SimpleNamespace(
        strategy=strategy,
        record=record,
        cache=cache,
        sent=sent,
        calls=calls,
        named=named,
        quote=quote,
        clock=clock,
    )


def started(lane: SimpleNamespace) -> SimpleNamespace:
    lane.strategy.on_start()
    return lane


def test_restart_mode_records_the_reconciled_orders_and_sends_nothing(monkeypatch):
    lane = offline_strategy(monkeypatch, "restart")

    def reconciled(status: str, venue_id: str, client_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            status=SimpleNamespace(name=status),
            venue_order_id=venue_id,
            client_order_id=ClientOrderId.from_str(client_id),
        )

    lane.cache.all_orders = [
        reconciled("FILLED", "PERM-102", "O-B"),
        reconciled("FILLED", "101", "O-A"),
        reconciled("CANCELED", "100", "O-C"),
    ]
    started(lane)
    assert lane.record.data["reconciled"] == {
        "instrument_orders_by_status": {"CANCELED": 1, "FILLED": 2},
        "filled_venue_order_ids": ["101", "PERM-102"],
        "filled_client_order_ids": ["O-A", "O-B"],
    }
    lane.quote("199.00", "200.00")  # restart mode never subscribes; this stands in for one
    lane.strategy.on_stop()
    assert lane.calls == []  # no request, no subscription, no order, no cancel
    assert lane.record.data["orders_refused"] == 0  # the quote was ignored, not refused
    lane.strategy._bid = Price.from_str("199.00")
    lane.strategy.instrument = equity()
    lane.strategy.submit_first_order()  # even a direct call reaches no venue
    assert lane.sent == []
    assert lane.record.data["orders_refused"] == 1  # and the run fails, saying why
    assert any("refused to send 1 order" in problem for problem in lane.record.problems())


@pytest.mark.parametrize(("mode", "allowed"), [("order", 1), ("fill", 2), ("restart", 0)])
def test_each_mode_sends_at_most_its_own_number_of_orders(monkeypatch, mode, allowed):
    lane = offline_strategy(monkeypatch, mode)
    lane.strategy.instrument = equity()
    lane.strategy._bid = Price.from_str("199.00")
    lane.strategy._ask = Price.from_str("200.00")
    for _ in range(allowed + 2):
        lane.strategy.submit_first_order()
    assert len(lane.sent) == allowed
    assert lane.record.data["orders_refused"] == 2  # the extra ones were refused


def test_a_mode_without_an_order_budget_sends_nothing(monkeypatch):
    lane = offline_strategy(monkeypatch, "order")
    lane.record.data["mode"] = "some later mode"
    lane.strategy.instrument = equity()
    lane.strategy._bid = Price.from_str("199.00")
    lane.strategy.submit_first_order()
    assert lane.sent == []
    assert lane.record.data["orders_refused"] == 1


@pytest.mark.parametrize("mode", ["order", "fill"])
@pytest.mark.parametrize("held", ["open_orders", "open_positions"])
def test_an_open_order_or_position_at_start_holds_every_order(monkeypatch, mode, held):
    lane = offline_strategy(monkeypatch, mode)
    getattr(lane.cache, held).append(object())
    started(lane)
    assert lane.calls == []  # no instrument request, no subscription
    # So in a run no quote arrives. This quote stands in for one that arrives anyway, with the
    # instrument set: the guard at the point of sending is the layer tested.
    lane.strategy.instrument = equity()
    lane.quote("199.00", "200.00")
    assert lane.sent == []
    assert lane.record.data["orders_refused"] == 1
    assert lane.record.data["startup_reconciliation"]["trader_started"] is True


@pytest.mark.parametrize("mode", ["order", "fill"])
def test_a_clean_start_subscribes_to_the_instrument_once(monkeypatch, mode):
    lane = started(offline_strategy(monkeypatch, mode))
    ((args, kwargs),) = lane.named("subscribe_quotes")
    assert args == (lane.strategy.instrument_id,)
    assert kwargs == {"client_id": ibkr_node.IB_CLIENT_ID}
    lane.strategy.on_instrument(equity())  # a second instrument update subscribes no more
    assert len(lane.named("subscribe_quotes")) == 1


def test_a_start_without_the_instrument_in_the_cache_requests_it(monkeypatch):
    lane = offline_strategy(monkeypatch, "order")
    lane.cache.instruments.clear()
    started(lane)
    assert len(lane.named("request_instrument")) == 1 and lane.named("subscribe_quotes") == []
    lane.strategy.on_instrument(equity())
    assert len(lane.named("subscribe_quotes")) == 1


def test_order_mode_sends_one_day_limit_buy_on_the_first_usable_quote(monkeypatch):
    lane = started(offline_strategy(monkeypatch, "order"))
    lane.quote("0.00", "200.00")  # no bid yet
    assert lane.sent == []
    lane.quote("199.99", "200.00")
    lane.quote("198.00", "199.00")  # a later quote sends nothing more
    (order,) = lane.sent
    assert order.side == OrderSide.BUY and order.time_in_force == TimeInForce.DAY
    assert order.order_type.name == "LIMIT"
    assert str(order.quantity) == "1" and str(order.price) == "195.99"
    assert lane.record.data["order"]["price"] == "195.99"
    assert lane.record.data["orders_refused"] == 0
    ((_, kwargs),) = lane.named("submit_order")
    assert kwargs == {"client_id": ibkr_node.IB_CLIENT_ID}


@pytest.mark.parametrize(
    ("bid", "ask"),
    [("210.00", "200.00"), ("199.99", "0.00")],
    ids=["crossed", "no-ask"],
)
def test_order_mode_ignores_a_crossed_or_one_sided_quote_and_acts_on_the_next(
    monkeypatch, bid, ask
):
    lane = started(offline_strategy(monkeypatch, "order"))
    lane.quote(bid, ask)
    assert lane.sent == [] and lane.record.data["order"] is None
    lane.quote("199.99", "200.00")
    (order,) = lane.sent
    assert str(order.price) == "195.99"  # from the usable quote's bid, under its ask
    assert lane.record.data["orders_refused"] == 0


@pytest.mark.parametrize("mode", ["order", "fill"])
@pytest.mark.parametrize(
    "offset_s",
    [-31, 31, None],
    ids=["older-than-the-bound", "ahead-of-the-clock", "zero-timestamp"],
)
def test_both_modes_ignore_an_old_or_untimed_quote(monkeypatch, mode, offset_s):
    lane = started(offline_strategy(monkeypatch, mode))
    ts_event = 0 if offset_s is None else NOW_NS + offset_s * SECOND_NS
    lane.quote("199.00", "200.00", ts_event=ts_event)
    assert lane.sent == []
    lane.quote("199.00", "200.00", ts_event=NOW_NS - 30 * SECOND_NS)  # at the bound: usable
    assert len(lane.sent) == 1
    assert lane.record.data["orders_refused"] == 0


@pytest.mark.parametrize("mode", ["order", "fill"])
def test_both_modes_ignore_a_zero_timestamp_even_inside_the_age_bound(monkeypatch, mode):
    # With the clock 10 s after zero, only the zero check refuses a quote stamped 0.
    lane = started(offline_strategy(monkeypatch, mode, now_ns=10 * SECOND_NS))
    lane.quote("199.00", "200.00", ts_event=0)
    assert lane.sent == []
    lane.quote("199.00", "200.00", ts_event=1)
    assert len(lane.sent) == 1


def test_the_quote_age_bound_is_thirty_seconds():
    assert ibkr_node.MAX_QUOTE_AGE_SECONDS == 30


def test_order_mode_cancels_its_order_when_it_is_accepted(monkeypatch):
    lane = started(offline_strategy(monkeypatch, "order"))
    lane.quote("199.99", "200.00")
    (order,) = lane.sent
    other = ClientOrderId.from_str("O-OTHER-1")
    lane.strategy.on_order_accepted(OrderAccepted(client_order_id=other))
    assert lane.named("cancel_order") == []  # not this run's order
    lane.strategy.on_order_accepted(OrderAccepted(client_order_id=order.client_order_id))
    ((args, kwargs),) = lane.named("cancel_order")
    assert args == (order.client_order_id,) and kwargs == {"client_id": ibkr_node.IB_CLIENT_ID}


def test_fill_mode_does_not_cancel_on_accept(monkeypatch):
    lane = started(offline_strategy(monkeypatch, "fill"))
    lane.quote("199.00", "200.00")
    (buy,) = lane.sent
    lane.strategy.on_order_accepted(OrderAccepted(client_order_id=buy.client_order_id))
    assert lane.named("cancel_order") == []


def fill_event(client_order_id, last_px: str, qty: str = "1") -> OrderFilled:
    # A stand-in for the engine's OrderFilled event, with only the fields the lane reads.
    return OrderFilled(
        client_order_id=client_order_id,
        last_px=Price.from_str(last_px),
        last_qty=Quantity.from_str(qty),
    )


def test_fill_mode_buys_once_on_the_first_usable_quote(monkeypatch):
    lane = started(offline_strategy(monkeypatch, "fill"))
    lane.quote("0.00", "200.00")  # no bid yet
    lane.quote("201.00", "200.00")  # crossed
    assert lane.sent == []
    lane.quote("199.00", "200.01")
    lane.quote("150.00", "151.00")  # a later quote sends nothing more
    (buy,) = lane.sent
    assert buy.side == OrderSide.BUY and str(buy.quantity) == "1"
    assert str(buy.price) == "204.02"  # 200.01 x 1.02 = 204.0102, rounded up
    assert lane.record.data["order"]["side"] == "BUY"


def test_fill_mode_closes_the_filled_quantity_once(monkeypatch):
    # A BUY of 3 that fills 2: a SELL of ORDER_QTY, or of the BUY's quantity, would not be 2.
    monkeypatch.setattr(ibkr_node, "ORDER_QTY", "3")
    lane = started(offline_strategy(monkeypatch, "fill"))
    lane.quote("199.00", "200.00")
    (buy,) = lane.sent
    assert buy.quantity == Quantity.from_str("3")
    lane.strategy.on_order_filled(fill_event(ClientOrderId.from_str("O-OTHER-1"), "200.00"))
    assert len(lane.sent) == 1  # another order's fill sends nothing
    lane.strategy.on_order_filled(fill_event(buy.client_order_id, "200.50", qty="2"))
    lane.strategy.on_order_filled(fill_event(buy.client_order_id, "200.50", qty="2"))
    assert len(lane.sent) == 2  # no second SELL
    sell = lane.sent[1]
    assert sell.side == OrderSide.SELL and sell.time_in_force == TimeInForce.DAY
    assert sell.quantity == Quantity.from_str("2")  # the filled quantity, from the fill event
    assert str(sell.price) == "195.02"  # 199.00 (the lower of bid and fill) x 0.98, on the grid
    assert lane.record.data["close_order"]["side"] == "SELL"
    assert lane.record.data["orders_refused"] == 0  # the repeat was ignored, not refused


def test_the_sell_prices_from_the_fill_when_the_fill_is_under_the_bid(monkeypatch):
    lane = started(offline_strategy(monkeypatch, "fill"))
    lane.quote("199.00", "200.00")
    (buy,) = lane.sent
    lane.strategy.on_order_filled(fill_event(buy.client_order_id, "190.01"))
    assert str(lane.sent[1].price) == "186.20"  # 190.01 x 0.98 = 186.2098, rounded down


@pytest.mark.parametrize(
    ("later_s", "fill_px", "sell_px"),
    [
        (60, "101.00", "98.98"),  # the bid expired: from the fill, 101.00 x 0.98
        (30, "101.00", "98.00"),  # still fresh at the bound: the lower bid, 100.00 x 0.98
        (0, "99.00", "97.02"),  # a fresh bid above the fill changes nothing: 99.00 x 0.98
    ],
    ids=["expired-bid-under-the-fill", "fresh-bid-under-the-fill", "fresh-bid-above-the-fill"],
)
def test_the_sell_uses_the_bid_only_while_its_quote_is_fresh(
    monkeypatch, later_s, fill_px, sell_px
):
    lane = started(offline_strategy(monkeypatch, "fill"))
    lane.quote("100.00", "101.00")
    (buy,) = lane.sent
    lane.clock.set_time(NOW_NS + later_s * SECOND_NS)  # the fill comes this much later
    lane.strategy.on_order_filled(fill_event(buy.client_order_id, fill_px))
    assert str(lane.sent[1].price) == sell_px
    assert lane.record.data["close_order"]["price"] == sell_px


def test_the_closing_sell_goes_through_the_same_guard(monkeypatch):
    monkeypatch.setitem(ibkr_node.MAX_ORDERS, "fill", 1)  # room for the BUY only
    lane = started(offline_strategy(monkeypatch, "fill"))
    lane.quote("199.00", "200.00")
    (buy,) = lane.sent
    lane.strategy.on_order_filled(fill_event(buy.client_order_id, "200.50"))
    assert lane.sent == [buy]  # the SELL was refused, not sent around the guard
    assert lane.record.data["orders_refused"] == 1


def assert_one_stop_cancel(lane: SimpleNamespace) -> None:
    ((args, kwargs),) = lane.named("cancel_all_orders")
    assert args == (lane.strategy.instrument_id,)
    assert kwargs == {"client_id": ibkr_node.IB_CLIENT_ID, "strategy_only": True}


@pytest.mark.parametrize("mode", ["order", "fill"])
def test_on_stop_cancels_this_strategys_working_orders_and_unsubscribes(monkeypatch, mode):
    lane = started(offline_strategy(monkeypatch, mode))
    lane.quote("199.00", "200.00")
    (order,) = lane.sent
    lane.cache.open_orders = [order]
    lane.strategy.on_stop()
    assert_one_stop_cancel(lane)
    assert len(lane.named("unsubscribe_quotes")) == 1
    assert lane.record.data["at_stop"]["instrument_open_orders"] == 1
    assert lane.record.data["at_stop"]["instrument_inflight_orders"] == 0
    assert lane.strategy.stopped_at is not None


@pytest.mark.parametrize("mode", ["order", "fill"])
def test_on_stop_cancels_an_order_that_is_only_in_flight(monkeypatch, mode):
    # Sent, not yet acknowledged: not "open" to the cache, and still able to rest at the venue.
    lane = started(offline_strategy(monkeypatch, mode))
    lane.quote("199.00", "200.00")
    (order,) = lane.sent
    lane.cache.inflight = [order]
    lane.strategy.on_stop()
    assert_one_stop_cancel(lane)
    assert lane.record.data["at_stop"]["instrument_open_orders"] == 0
    assert lane.record.data["at_stop"]["instrument_inflight_orders"] == 1


@pytest.mark.parametrize("mode", ["order", "fill"])
def test_on_stop_sends_the_cancel_in_order_and_fill_mode_whatever_the_cache_shows(
    monkeypatch, mode
):
    # cancel_all_orders(strategy_only=True) finds this strategy's orders itself, the
    # same-handler local ones included, and only logs when there are none.
    lane = started(offline_strategy(monkeypatch, mode))
    lane.cache.open_orders = [object()]  # someone else's order in the account
    seen: list = []
    monkeypatch.setattr(
        type(lane.strategy),
        "cancel_all_orders",
        lambda self, *a, **k: seen.append((a, k, lane.record.data["at_stop"] is not None)),
    )
    lane.strategy.on_stop()
    # One cancel, sent after the at-stop snapshot was recorded.
    assert seen == [
        (
            (lane.strategy.instrument_id,),
            {"client_id": ibkr_node.IB_CLIENT_ID, "strategy_only": True},
            True,
        )
    ]
    assert lane.record.data["at_stop"]["instrument_open_orders"] == 1


def test_restart_mode_sends_no_cancel_at_stop(monkeypatch):
    lane = started(offline_strategy(monkeypatch, "restart"))
    lane.cache.open_orders = [object()]
    lane.cache.inflight = [object()]
    lane.strategy.on_stop()
    assert lane.named("cancel_all_orders") == []
    assert lane.calls == []
    assert lane.record.data["at_stop"]["instrument_inflight_orders"] == 1


def test_the_stop_state_names_each_order_and_its_venue_id(monkeypatch):
    lane = started(offline_strategy(monkeypatch, "fill"))
    lane.quote("199.00", "200.00")
    (buy,) = lane.sent
    lane.strategy.on_order_filled(fill_event(buy.client_order_id, "200.00"))
    sell = lane.sent[1]
    for order, venue_id in ((buy, "101"), (sell, "102")):
        lane.cache.known[order.client_order_id] = SimpleNamespace(
            status=SimpleNamespace(name="FILLED"),
            filled_qty=Quantity.from_str("1"),
            venue_order_id=venue_id,
        )
    lane.strategy.on_stop()
    stop = lane.record.data["at_stop"]
    assert (stop["order_status"], stop["close_order_status"]) == ("FILLED", "FILLED")
    assert lane.record.data["order"]["venue_order_id"] == "101"
    assert lane.record.data["close_order"]["venue_order_id"] == "102"


def test_order_events_are_recorded_with_the_venue_id_and_the_error_code(monkeypatch):
    lane = started(offline_strategy(monkeypatch, "order"))
    lane.quote("199.99", "200.00")
    (order,) = lane.sent
    lane.strategy.on_order_event(
        OrderAccepted(client_order_id=order.client_order_id, venue_order_id="77", ts_event=0)
    )
    lane.strategy.on_order_event(
        OrderRejected(
            client_order_id=order.client_order_id,
            venue_order_id=None,
            ts_event=0,
            reason=f"Duplicate order id for {FAKE}",
        )
    )
    accepted, rejected = lane.record.data["events"]
    assert accepted["venue_order_id"] == "77"
    assert lane.record.data["order"]["venue_order_id"] == "77"
    assert rejected["ib_error_code"] == 103
    assert FAKE not in json.dumps(lane.record.data)  # the reason stays in the local part
    assert any("error 103" in problem for problem in lane.record.problems())


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("Error 103, reqId 1712001: Duplicate order id", 103),
        ("code: 201 Order rejected - reason: margin", 201),
        ("Duplicate order id", 103),
        ("IB error code=10349", 10349),
        ("Order rejected", None),
        ("", None),
    ],
)
def test_the_ib_error_code_is_read_from_the_rejection_reason(reason, code):
    assert ibkr_node.ib_error_code(reason) == code


# --- the run: the stop timer and the engine log -----------------------------------------------


class FakeHandle:
    def __init__(self, node: FakeNode) -> None:
        self.node = node

    def stop(self) -> None:
        self.node.stop_called.set()


class FakeNode:
    """``run`` returns at once (``wait=False``), or waits until the handle is stopped and then
    stops the strategy, as the engine does."""

    def __init__(
        self,
        strategy: Any,
        *,
        wait: bool,
        fail: BaseException | None = None,
        stopping: Callable[[], None] | None = None,
    ) -> None:
        self.strategy = strategy
        self.wait = wait
        self.fail = fail
        self.stopping = stopping  # what happens while the node stops, after the timer
        self.stop_called = threading.Event()

    def handle(self) -> FakeHandle:
        return FakeHandle(self)

    def run(self) -> None:
        os.write(1, f"AccountState(account_id=IB-{FAKE})\n".encode())
        os.write(2, f"native stderr {FAKE}\n".encode())
        print(f"python stdout {FAKE}")
        print(f"python stderr {FAKE}", file=sys.stderr)
        if self.fail is not None:
            raise self.fail
        if self.wait:
            assert self.stop_called.wait(10), "the timer never stopped the node"
        self.strategy.stopped_at = time.monotonic()
        if self.stopping is not None:
            self.stopping()


def run_with(monkeypatch, tmp_path, *, wait: bool, fail: BaseException | None = None):
    strategy = SimpleNamespace(stopped_at=None)
    node = FakeNode(strategy, wait=wait, fail=fail)
    monkeypatch.setattr(ibkr_node, "build", lambda mode, account, record: (node, strategy))
    monkeypatch.setitem(ibkr_node.AUTO_STOP_SECONDS, "restart", 0.2)
    settings = Settings(repo_root=tmp_path / "repo", jts_dir=tmp_path / "Jts", state_dir=tmp_path)
    record = ibkr_paper.RunRecord("restart", PORT)
    ibkr_node.run("restart", FAKE, record, settings)
    return SimpleNamespace(record=record, node=node, settings=settings)


def lane_timers() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if isinstance(t, threading.Timer) and t.is_alive()]


def test_the_timer_stops_the_node_and_leaves_no_thread(monkeypatch, tmp_path):
    before = lane_timers()
    run = run_with(monkeypatch, tmp_path, wait=True)
    assert run.node.stop_called.is_set()
    assert run.record.data["engine_error"] is False
    assert lane_timers() == before


@pytest.mark.parametrize(
    "fail",
    [None, KeyboardInterrupt(), RuntimeError(f"reconciliation failed for {FAKE}")],
    ids=["stopped-early", "ctrl-c", "engine-error"],
)
def test_a_stop_before_the_timer_fails_the_run_and_cancels_the_timer(monkeypatch, tmp_path, fail):
    before = lane_timers()
    run = run_with(monkeypatch, tmp_path, wait=False, fail=fail)
    assert run.record.data["engine_error"] is True
    text = run.record.local["engine_error"]
    assert (
        text.startswith("interrupted") if not isinstance(fail, RuntimeError) else "Runtime" in text
    )
    assert not run.node.stop_called.is_set()  # the cancelled timer never fired
    time.sleep(0.3)
    assert not run.node.stop_called.is_set()
    assert lane_timers() == before
    assert FAKE not in json.dumps(run.record.data)


def test_engine_output_goes_to_the_private_log_not_the_terminal(monkeypatch, tmp_path, capfd):
    run = run_with(monkeypatch, tmp_path, wait=True)
    out, err = capfd.readouterr()
    assert FAKE not in out + err  # nothing the engine wrote reaches the terminal
    assert "-engine.log" in out
    (log,) = run.settings.logs_dir.glob("*-restart-engine.log")
    text = log.read_text(encoding="utf-8")
    for piece in ("AccountState", "native stderr", "python stdout", "python stderr"):
        assert piece in text
    assert oct(log.stat().st_mode & 0o777) == "0o600"
    assert oct(run.settings.logs_dir.stat().st_mode & 0o777) == "0o700"
    assert run.record.local["engine_log"] == str(log)
    # The terminal works normally again afterwards.
    print("after the run")
    assert "after the run" in capfd.readouterr().out


# --- the state after the node has shut down ----------------------------------------------------


def own_order(status: str, filled: str = "0", venue_id: str | None = "PERM-7") -> SimpleNamespace:
    return SimpleNamespace(
        status=SimpleNamespace(name=status),
        filled_qty=Quantity.from_str(filled),
        venue_order_id=venue_id,
    )


def run_lane(
    monkeypatch,
    tmp_path,
    lane: SimpleNamespace,
    *,
    at_stop: Callable[[], None],
    after_stop: Callable[[], None],
    fail: BaseException | None = None,
) -> ibkr_paper.RunRecord:
    """``ibkr_node.run`` with the lane's offline strategy in a fake node: ``at_stop`` sets the
    cache as the strategy's ``on_stop`` sees it, ``after_stop`` changes it as the engine's
    residual events would before the node returns."""

    def stopping() -> None:
        at_stop()
        lane.strategy.on_stop()
        after_stop()

    node = FakeNode(lane.strategy, wait=fail is None, fail=fail, stopping=stopping)
    monkeypatch.setattr(ibkr_node, "build", lambda mode, account, record: (node, lane.strategy))
    mode = lane.record.data["mode"]
    monkeypatch.setitem(ibkr_node.AUTO_STOP_SECONDS, mode, 0.2)
    settings = Settings(repo_root=tmp_path / "repo", jts_dir=tmp_path / "Jts", state_dir=tmp_path)
    ibkr_node.run(mode, FAKE, lane.record, settings)
    lane.record.paper_checks_after_run(True)
    return lane.record


def test_canceled_at_stop_then_filled_after_shutdown_fails_the_run(monkeypatch, tmp_path):
    lane = started(offline_strategy(monkeypatch, "order"))
    lane.quote("199.99", "200.00")
    (order,) = lane.sent
    oid = order.client_order_id

    def filled_late() -> None:
        lane.cache.known[oid] = own_order("FILLED", "1")
        lane.cache.open_positions = [object()]

    record = run_lane(
        monkeypatch,
        tmp_path,
        lane,
        at_stop=lambda: lane.cache.known.__setitem__(oid, own_order("CANCELED")),
        after_stop=filled_late,
    )
    assert record.data["engine_error"] is False
    assert record.data["at_stop"]["order_status"] == "CANCELED"
    assert record.data["after_shutdown"] == {
        "instrument_open_orders": 0,
        "instrument_open_positions": 1,
        "account_open_orders": 0,
        "account_open_positions": 1,
        "instrument_inflight_orders": 0,
        "order_status": "FILLED",
        "filled_qty": "1",
        "venue_order_id": "PERM-7",
    }
    problems = record.problems()
    assert [p for p in problems if "the order filled" in p] == [
        "the order filled: one share is open in the paper account and has to be closed by hand"
    ]
    assert any("a share may be open in the paper account" in p for p in problems)
    assert FAKE not in json.dumps(record.data)


def test_in_flight_at_stop_then_canceled_after_shutdown_still_fails_and_shows_the_end(
    monkeypatch, tmp_path
):
    lane = started(offline_strategy(monkeypatch, "order"))
    lane.quote("199.99", "200.00")
    (order,) = lane.sent
    oid = order.client_order_id

    def in_flight() -> None:
        lane.cache.inflight = [order]
        lane.cache.known[oid] = own_order("SUBMITTED", venue_id=None)

    def canceled() -> None:
        lane.cache.inflight = []
        lane.cache.known[oid] = own_order("CANCELED")

    record = run_lane(monkeypatch, tmp_path, lane, at_stop=in_flight, after_stop=canceled)
    assert_one_stop_cancel(lane)  # sent although nothing was "open"
    assert record.data["at_stop"]["instrument_inflight_orders"] == 1
    after = record.data["after_shutdown"]
    assert (after["order_status"], after["instrument_inflight_orders"]) == ("CANCELED", 0)
    assert record.data["order"]["venue_order_id"] == "PERM-7"
    problems = record.problems()
    assert "an order in the lane's instrument was still in flight at stop" in problems
    assert not any("after the node shut down" in p for p in problems)


def test_the_state_after_shutdown_is_read_after_the_node_raised(monkeypatch, tmp_path):
    lane = started(offline_strategy(monkeypatch, "order"))
    record = run_lane(
        monkeypatch,
        tmp_path,
        lane,
        at_stop=lambda: None,
        after_stop=lambda: None,
        fail=RuntimeError("disconnect timeout"),
    )
    assert record.data["engine_error"] is True
    assert record.data["after_shutdown"] is not None
    assert record.data["after_shutdown"]["instrument_open_positions"] == 0


def test_a_failed_read_after_shutdown_leaves_the_field_empty_and_fails_the_run(
    monkeypatch, tmp_path
):
    lane = started(offline_strategy(monkeypatch, "order"))

    def broken(**_):
        raise RuntimeError("the cache is gone")

    def break_cache() -> None:
        lane.cache.orders_inflight = broken

    record = run_lane(monkeypatch, tmp_path, lane, at_stop=lambda: None, after_stop=break_cache)
    assert record.data["at_stop"] is not None
    assert record.data["after_shutdown"] is None
    assert "no state was captured after the node shut down" in record.problems()


def test_a_stand_in_strategy_without_the_method_leaves_the_field_empty(monkeypatch, tmp_path):
    run = run_with(monkeypatch, tmp_path, wait=True)  # does not raise
    assert run.record.data["after_shutdown"] is None
