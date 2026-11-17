"""
The engine side of the IBKR paper lane. Imported only by ``astrolabe.ibkr_paper``, after the
paper gate has passed, and by the offline tests.

Rewritten from meridian@014f706d:meridian/ibkr_node.py on the public API of the
nautilus_trader 2.0.0rc9 wheel: no upstream example file is vendored or imported. The node is
built the way meridian@014f706d:meridian/sandbox_node.py:191-211 builds one, with
``LiveNode.builder``, and the strategies subclass the wheel's ``Strategy``. The lane's
behaviour is meridian's.

Read, not copied (nautilus_trader v2.0.0rc9, read 2026-11-17):
  - examples/live/interactive_brokers/_common.py:83-98 (market data type, default
    delayed-frozen), 417-434 (instrument provider: SIMPLIFIED symbology, load_ids) and 513-570
    (node build: timeouts 15/5/5/5, post-stop delay 5 with an execution client, client
    configs, ``fetch_all_open_orders=False``);
  - examples/live/interactive_brokers/ib_v2_order_strategies.py:403-673 (``IbV2OrderStrategy``:
    the instrument from the cache or a request, a quote subscription, one submission on the
    first quote, cancel on accept, the stop-time cancel and unsubscribe, ``ClientId("IB")``).

Own design:
  - order mode: one DAY LIMIT BUY of 1 share at the bid less 2%, rounded down to the
    instrument's price increment, so it rests; cancelled when the venue accepts it;
  - fill mode (``OneRoundTrip``): one DAY LIMIT BUY at the ask plus 2%, rounded up, so it
    fills; on its fill one DAY LIMIT SELL of the filled quantity at the lower of the fill price
    and the bid (the bid only while its quote is no older than ``MAX_QUOTE_AGE_SECONDS``),
    less 2%, rounded down, so the run ends flat;
  - restart mode: records what reconciliation left and sends nothing; it never subscribes;
  - an open order or position in the instrument at start holds every order;
  - ``MAX_ORDERS`` per mode, enforced in ``OneDayLimit._send``, the one place that submits;
  - both modes act only on a two-sided, uncrossed quote no older than ``MAX_QUOTE_AGE_SECONDS``;
  - on stop: the state is recorded, a cancel of this strategy's orders in the instrument is
    sent (always, in order and fill mode), the quote subscription ends; after the node has
    shut down the state is recorded again from its cache;
  - the node stops itself from a cancellable ``threading.Timer`` on ``LiveNodeHandle.stop``
    (meridian@014f706d:meridian/sandbox_node.py:329-365), not from a detached ``kill``.

Prices are exact: ``Decimal`` arithmetic on ``Price.as_decimal()`` and the instrument's price
increment, turned back into a ``Price`` with ``Price.from_decimal_dp`` at the increment's
precision. No float reaches an order. No environment variable is read here. Never used:
``modify_order`` (nautilus_trader issue 5463) and an IOC time in force (issue 5598).

This is the only module under ``astrolabe/`` that imports the IB adapter,
``nautilus_trader.live`` or ``nautilus_trader.trading``, and the only one with order calls
(ruff TID251 in pyproject.toml; tests/test_order_path.py).
"""

from __future__ import annotations

import contextlib
import gc
import os
import re
import sys
import threading
import time
import traceback
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import nautilus_trader
from nautilus_trader.adapters import interactive_brokers as ib
from nautilus_trader.common import Environment
from nautilus_trader.live import LiveNode, LiveRiskEngineConfig
from nautilus_trader.model import (
    ClientId,
    ClientOrderId,
    InstrumentId,
    OrderSide,
    Price,
    Quantity,
    StrategyId,
    TimeInForce,
    TraderId,
)
from nautilus_trader.trading import Strategy, StrategyConfig

from astrolabe import boundary, ib_ids
from astrolabe.settings import Settings

if TYPE_CHECKING:
    from astrolabe.ibkr_paper import RunRecord

INSTRUMENT_ID = "AAPL.XNAS"  # upstream's stock example instrument
# The adapter's factories register their clients under this name (InteractiveBrokers*Factory
# .name() returns "IB" in the rc9 wheel); commands are routed to it by ClientId.
IB_CLIENT_ID = ClientId.from_str("IB")
ORDER_QTY = "1"
# Order mode: how far under the bid its BUY rests. Fill mode: how far past the quote its two
# limit orders are priced, so they fill at once (the quote is delayed data).
LIMIT_UNDER_BID = Decimal("0.02")
CROSS_BAND = Decimal("0.02")
AUTO_STOP_SECONDS = {"order": 60, "restart": 25, "fill": 90}
# The most orders a run of each mode may send; a mode not named here sends none.
MAX_ORDERS = {"order": 1, "fill": 2, "restart": 0}
# Startup reconciliation asks the venue for closed orders too: the adapter sets
# ``open_only(false)`` with or without a lookback (v2.0.0rc9
# crates/adapters/interactive_brokers/src/execution/core.rs:1436-1440). What the lookback does
# is bound the closed reports to its window, by their last update
# (crates/live/src/execution/reports.rs:28-41, called from the adapter's
# execution/reports.rs:274), and let the adapter claim a complete report window
# (core.rs:1476-1479). So ``restart`` sees a fill run's closed orders only within 60 minutes of
# their last update; ``ibkr_paper._run_node`` passes this number to
# ``RunRecord.compare_with_fill_pass``, which compares only inside that window.
RECONCILIATION_LOOKBACK_MINS = 60
# The oldest quote, by its ``ts_event``, that either mode acts on, and the most a quote's time
# may be ahead of the strategy's clock. With batch quotes the adapter stamps ``ts_event`` when
# the quote reaches it (crates/adapters/interactive_brokers/src/data/streams.rs:2184-2386,
# ``clock.get_time_ns()``), so this bound refuses a quote that was stamped long before it
# reached the strategy, or that carries no time (a zero ``ts_event``). It cannot tell how old
# delayed data is at the venue: a delayed quote stamped at receipt passes it.
MAX_QUOTE_AGE_SECONDS = 30
_NS_PER_SECOND = 1_000_000_000
MARKET_DATA_TYPE = ib.MarketDataType.DELAYED_FROZEN  # upstream's default and meridian's
CLIENT_CONNECTION_TIMEOUT_SECS = 10  # upstream's default for both clients
REQUEST_TIMEOUT_SECS = 30
NODE_NAME = "ASTROLABE-IBKR-PAPER"
TRADER_ID = "ASTROLABE-001"
# IB error 103, "duplicate order id": the order-id lockout of nautilus_trader issue 5277.
ORDER_ID_LOCKOUT_CODE = 103
_ERROR_CODE = re.compile(r"\b(?:error|code)\s*(?:code)?\s*[:#=]?\s*(\d{3,5})\b", re.IGNORECASE)


def ib_error_code(reason: str) -> int | None:
    """The IB error code named in a rejection reason, if the text names one.

    At v2.0.0rc9 the adapter's rejection reason is the IB notice's message text
    (crates/adapters/interactive_brokers/src/execution/updates.rs:716-722, read 2026-11-17),
    which need not carry the code. So a code is read where the text states one ("error 103",
    "code: 103"), and the text "Duplicate order id" is read as 103. That this is IB's message
    for 103 is an inference from IBKR's error-code list, not checked at the source for this
    port; a rejection fails the run whatever its code, so a wrong reading only mislabels it.
    """
    match = _ERROR_CODE.search(reason)
    if match:
        return int(match.group(1))
    if "duplicate order id" in reason.casefold():
        return ORDER_ID_LOCKOUT_CODE
    return None


# --- exact prices ---------------------------------------------------------------------------


def on_grid(value: Decimal, increment: Decimal, *, up: bool) -> Decimal:
    """``value`` rounded to a whole number of ``increment``: up (a BUY that must fill) or down
    (a resting BUY, a SELL). A value already on the grid is returned unchanged."""
    if increment <= 0:
        raise ValueError("the price increment must be positive")
    steps = (value / increment).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return (steps * increment).quantize(increment)


def exact_price(instrument: Any, value: Decimal, *, up: bool) -> Price:
    """A ``Price`` on the instrument's grid, built from Decimal without a float."""
    increment: Price = instrument.price_increment
    exact = on_grid(value, increment.as_decimal(), up=up)
    if exact <= 0:
        raise ValueError("no positive price on the instrument's grid")
    # Price.from_decimal_dp(decimal, precision): v2.0.0rc9 wheel model/__init__.pyi:6306;
    # crates/model/src/types/price.rs:495-530 rounds half to even only beyond ``precision``,
    # which ``exact`` never has.
    price = Price.from_decimal_dp(exact, increment.precision)
    if price.as_decimal() != exact:
        raise ValueError("the price changed on conversion")
    return price


def resting_buy_price(instrument: Any, bid: Price) -> Price:
    """Order mode: under the bid by ``LIMIT_UNDER_BID``, rounded down so it stays under."""
    if not bid.is_positive():
        raise ValueError("no usable bid to price the order from")
    return exact_price(instrument, bid.as_decimal() * (1 - LIMIT_UNDER_BID), up=False)


def marketable_buy_price(instrument: Any, bid: Price, ask: Price) -> Price:
    """Fill mode's BUY: above the ask by ``CROSS_BAND``, rounded up. Needs 0 < bid <= ask."""
    if not (bid.is_positive() and ask.is_positive() and bid.as_decimal() <= ask.as_decimal()):
        raise ValueError("no usable quote to price the buy from")
    return exact_price(instrument, ask.as_decimal() * (1 + CROSS_BAND), up=True)


def marketable_sell_price(instrument: Any, reference: Price) -> Price:
    """Fill mode's SELL: under ``reference`` by ``CROSS_BAND``, rounded down."""
    if not reference.is_positive():
        raise ValueError("no usable price to price the sell from")
    return exact_price(instrument, reference.as_decimal() * (1 - CROSS_BAND), up=False)


# --- the strategies --------------------------------------------------------------------------


class OneDayLimit(Strategy):
    """Order mode: one DAY LIMIT BUY that rests and is cancelled when accepted.
    Restart mode: the counts after reconciliation, and nothing sent."""

    strategy_id_value = "ASTROLABE-M1"

    def __init__(self) -> None:
        # No extra constructor arguments: ``create`` sets the record afterwards.
        super().__init__(StrategyConfig(strategy_id=StrategyId.from_str(self.strategy_id_value)))
        self.instrument_id = InstrumentId.from_str(INSTRUMENT_ID)
        self.instrument: Any = None
        self._record: RunRecord | None = None
        self._bid: Price | None = None
        self._bid_ts: int | None = None  # the ts_event of the quote the bid came from
        self._order_id: ClientOrderId | None = None
        self._sent = 0
        self._submitted = False  # the first usable quote has been acted on
        self._subscribed = False
        self._hold: str | None = None  # a reason this run must send nothing more
        self.stopped_at: float | None = None  # time.monotonic() of the first stop

    @classmethod
    def create(cls, record: RunRecord) -> OneDayLimit:
        strategy = cls()
        strategy._record = record
        return strategy

    @property
    def record(self) -> RunRecord:
        if self._record is None:
            raise RuntimeError("the strategy has no run record")
        return self._record

    def _mode(self) -> str:
        return str(self.record.data["mode"])

    def _send(self, order: Any) -> None:
        """The only way this lane sends an order. Refuses what the run's mode does not allow."""
        mode = self._mode()
        allowed = MAX_ORDERS.get(mode, 0)
        if self._hold is not None or self._sent >= allowed:
            why = self._hold or f"{mode} mode sends at most {allowed} order(s)"
            self.record.order_refused(why)
            print(f"{self.strategy_id}: an order was refused before it was sent: {why}", flush=True)
            return
        self._sent += 1
        self.submit_order(order, client_id=IB_CLIENT_ID)

    def _limit(self, side: OrderSide, quantity: Quantity, price: Price) -> Any:
        # OrderFactory.limit: v2.0.0rc9 wheel common/__init__.pyi:1411-1429. The time in force
        # is passed, never left to the factory's default.
        return self.order_factory.limit(
            self.instrument_id, side, quantity, price, time_in_force=TimeInForce.DAY
        )

    def _open_counts(self) -> dict[str, int]:
        cache = self.cache
        return {
            "instrument_open_orders": len(cache.orders_open(instrument_id=self.instrument_id)),
            "instrument_open_positions": len(
                cache.positions_open(instrument_id=self.instrument_id)
            ),
            "account_open_orders": len(cache.orders_open()),
            "account_open_positions": len(cache.positions_open()),
        }

    def _reconciled(self) -> dict[str, Any]:
        """What the cache holds for the instrument after startup reconciliation."""
        orders = self.cache.orders(instrument_id=self.instrument_id)
        by_status = Counter(order.status.name for order in orders)
        filled = [order for order in orders if order.status.name == "FILLED"]
        # A reconciled order the cache did not hold keeps the report's client order id
        # (v2.0.0rc9 crates/live/src/execution/manager.rs:4806-4808, ``handle_external_order``,
        # called from ``reconcile_execution_mass_status`` at :1037), which the adapter reads
        # back from IB's order reference (crates/adapters/interactive_brokers/src/execution/
        # parse.rs:633, ``normalized_order_ref``; the order reference is the client order id,
        # execution/commands.rs:182). The venue order id is ``PERM-<permId>`` or the bare IB
        # order id (parse.rs:440-446), so the fill run may have seen the other form.
        return {
            "instrument_orders_by_status": dict(sorted(by_status.items())),
            "filled_venue_order_ids": sorted(
                str(order.venue_order_id) for order in filled if order.venue_order_id is not None
            ),
            "filled_client_order_ids": sorted(str(order.client_order_id) for order in filled),
        }

    def on_start(self) -> None:
        # The node starts strategies only after startup reconciliation returned without error.
        counts = self._open_counts()
        self.record.trader_started(**counts)
        if self._mode() == "restart":
            self.record.reconciled(**self._reconciled())
            return  # no instrument request, no subscription, no order
        if counts["instrument_open_orders"] or counts["instrument_open_positions"]:
            self._hold = "the account had an open order or position in the instrument at start"
            print(
                f"{self.strategy_id}: the account already has an open order or position in "
                f"{self.instrument_id}; nothing will be submitted.",
                flush=True,
            )
            return
        cached = self.cache.instrument(self.instrument_id)
        if cached is not None:
            self.on_instrument(cached)
            return
        print(f"{self.strategy_id}: requesting {self.instrument_id}", flush=True)
        self.request_instrument(self.instrument_id, client_id=IB_CLIENT_ID)

    def on_instrument(self, instrument: Any) -> None:
        if instrument.id != self.instrument_id or self._subscribed:
            return
        self.instrument = instrument
        self._subscribed = True
        print(f"{self.strategy_id}: waiting for a quote for {self.instrument_id}", flush=True)
        self.subscribe_quotes(self.instrument_id, client_id=IB_CLIENT_ID)

    def _usable(self, quote: Any) -> bool:
        """Both modes act only on a two-sided, uncrossed quote of a known, recent time.

        The adapter builds a quote from separately cached sides
        (crates/adapters/interactive_brokers/src/data/cache.rs:326-362, ``try_build_quote``),
        so a crossed quote appears whenever one side has updated and the other has not: wait
        for the next. The age bound is ``MAX_QUOTE_AGE_SECONDS``; what it does and does not
        catch is said there.
        """
        bid, ask = quote.bid_price, quote.ask_price
        if not (bid.is_positive() and ask.is_positive() and bid.as_decimal() <= ask.as_decimal()):
            return False
        return self._recent(int(quote.ts_event))

    def _recent(self, ts_event: int | None) -> bool:
        """A known time (not None, not zero) within ``MAX_QUOTE_AGE_SECONDS`` of the
        strategy's clock now, in either direction. The one age rule for a quote on arrival and
        for a kept bid when it is used later."""
        if ts_event is None or ts_event <= 0:
            return False
        # Strategy.clock: v2.0.0rc9 wheel trading/__init__.pyi:518; Clock.timestamp_ns:
        # common/__init__.pyi:652.
        age_ns = self.clock.timestamp_ns() - ts_event
        return abs(age_ns) <= MAX_QUOTE_AGE_SECONDS * _NS_PER_SECOND

    def on_quote(self, quote: Any) -> None:
        if self._mode() not in ("order", "fill"):
            return  # restart mode acts on no quote, even if one arrives
        if quote.instrument_id != self.instrument_id or not self._usable(quote):
            return  # wait for a usable quote
        self._bid = quote.bid_price
        self._bid_ts = int(quote.ts_event)  # kept with the bid, so a later use can check it
        self._quote(quote)
        if self._submitted:
            return
        self._submitted = True
        print(f"{self.strategy_id}: submitting for {self.instrument_id}", flush=True)
        self.submit_first_order()

    def _quote(self, quote: Any) -> None:
        """Keep what else of the quote the subclass needs."""

    def submit_first_order(self) -> None:
        if self._bid is None or self.instrument is None:
            raise RuntimeError("no bid to price the order from")
        price = resting_buy_price(self.instrument, self._bid)
        order = self._limit(OrderSide.BUY, Quantity.from_str(ORDER_QTY), price)
        self._order_id = order.client_order_id
        self.record.order_built(**_described(order))
        self._send(order)

    # The engine calls the event's own handler (on_order_accepted, on_order_filled) and then
    # on_order_event, and only while the strategy runs (v2.0.0rc9
    # crates/trading/src/strategy/mod.rs:1379-1475, read 2026-11-17).
    def on_order_accepted(self, event: Any) -> None:
        if self._mode() != "order" or event.client_order_id != self._order_id:
            return
        print(f"{self.strategy_id}: cancelling accepted order {event.client_order_id}", flush=True)
        self.cancel_order(event.client_order_id, client_id=IB_CLIENT_ID)

    def on_order_event(self, event: Any) -> None:
        kind = type(event).__name__
        fields: dict[str, Any] = {"client_order_id": str(event.client_order_id)}
        venue_order_id = getattr(event, "venue_order_id", None)
        if venue_order_id is not None:
            fields["venue_order_id"] = str(venue_order_id)
            self.record.venue_order_id(str(event.client_order_id), str(venue_order_id))
        if kind == "OrderFilled":
            fields.update(
                last_qty=str(event.last_qty),
                last_px=str(event.last_px),
                order_side=event.order_side.name,
            )
        reason = getattr(event, "reason", None)
        if kind == "OrderRejected":
            fields["ib_error_code"] = ib_error_code(str(reason or ""))
        self.record.event(kind, event.ts_event, **fields)
        if reason:
            self.record.reason(kind, str(reason))

    def on_position_event(self, event: Any) -> None:
        self.record.event(type(event).__name__, event.ts_event)

    def _orders_at_stop(self) -> list[tuple[str, ClientOrderId | None]]:
        """(record key prefix, client order id) for each order this run placed."""
        return [("", self._order_id)]

    def _snapshot(self, *, venue_ids: bool = False) -> dict[str, Any]:
        """The counts in the instrument and the account, and each own order's status and filled
        quantity (and with ``venue_ids``, its venue order id). Statuses, counts, quantities and
        venue order ids only: no account identifier."""
        fields: dict[str, Any] = dict(self._open_counts())
        # Sent but not yet acknowledged orders are not "open" to the cache; count them too.
        fields["instrument_inflight_orders"] = len(
            self.cache.orders_inflight(instrument_id=self.instrument_id)
        )
        for prefix, order_id in self._orders_at_stop():
            order = None if order_id is None else self.cache.order(order_id)
            if order is not None:
                fields[f"{prefix}order_status"] = order.status.name
                fields[f"{prefix}filled_qty"] = str(order.filled_qty)
                if order.venue_order_id is not None:
                    self.record.venue_order_id(str(order_id), str(order.venue_order_id))
                    if venue_ids:
                        fields[f"{prefix}venue_order_id"] = str(order.venue_order_id)
        return fields

    def on_stop(self) -> None:
        if self.stopped_at is None:  # the first stop is the one that ended the run
            self.stopped_at = time.monotonic()
        # Captured before the stop-time cancel, so it shows what the run left.
        self.record.at_stop(**self._snapshot())
        if self._mode() == "restart":
            return  # restart mode sent nothing and subscribed to nothing
        # Sent unconditionally: an order sent but not yet acknowledged is not "open", and
        # cancel_all_orders(strategy_only=True) itself gathers this strategy's open, emulated,
        # in-flight and same-handler local orders in the instrument, and only logs that there
        # is nothing to cancel when all are empty (v2.0.0rc9
        # crates/trading/src/strategy/mod.rs:996-1112). Upstream's example cancels the same way,
        # with no check first (examples/live/interactive_brokers/ib_v2_order_strategies.py:659-667).
        self.cancel_all_orders(self.instrument_id, client_id=IB_CLIENT_ID, strategy_only=True)
        if self._subscribed:
            self.unsubscribe_quotes(self.instrument_id, client_id=IB_CLIENT_ID)

    def record_after_shutdown(self) -> None:
        """Called by ``run`` once ``node.run()`` has returned: the node is stopped but not
        disposed, so its cache is still readable (v2.0.0rc9 crates/live/src/python/node.rs:
        907-912, the ``cache`` getter). After ``on_stop`` the engine still processes events for
        ``delay_post_stop`` before it disconnects (crates/live/src/node/mod.rs:580-611), so
        this is where the stop-time cancel, or a late fill, shows."""
        self.record.after_shutdown(**self._snapshot(venue_ids=True))


class OneRoundTrip(OneDayLimit):
    """Fill mode: one DAY LIMIT BUY priced to fill, and when it fills, one DAY LIMIT SELL of the
    filled quantity priced to fill, so the run ends flat. Upstream's ``SpreadOrderStrategy``
    closes a filled position the same way (an opposite order of ``event.last_qty``); this lane
    uses limit orders priced past the quote instead of market orders, so the price is capped."""

    strategy_id_value = "ASTROLABE-M1-FILL"

    def __init__(self) -> None:
        super().__init__()
        self._ask: Price | None = None
        self._close_id: ClientOrderId | None = None

    def _quote(self, quote: Any) -> None:
        self._ask = quote.ask_price

    def submit_first_order(self) -> None:
        if self._bid is None or self._ask is None or self.instrument is None:
            raise RuntimeError("no quote to price the order from")
        price = marketable_buy_price(self.instrument, self._bid, self._ask)
        order = self._limit(OrderSide.BUY, Quantity.from_str(ORDER_QTY), price)
        self._order_id = order.client_order_id
        self.record.order_built(**_described(order))
        self._send(order)

    def on_order_filled(self, event: Any) -> None:
        if self._order_id is None or event.client_order_id != self._order_id:
            return
        if self._close_id is not None or self.instrument is None:
            return  # the closing order is placed once only
        # The fill price is live; the bid is delayed. Price from the lower of the two, so the
        # SELL still crosses if the delayed bid is under the fill. The bid counts only while
        # its quote is still within MAX_QUOTE_AGE_SECONDS of the clock now (``_recent``, the
        # rule ``_usable`` applies on arrival): an expired bid says nothing about the present,
        # and the reference is then the fill price alone.
        reference: Price = event.last_px
        bid = self._bid if self._recent(self._bid_ts) else None
        if bid is not None and bid.as_decimal() < reference.as_decimal():
            reference = bid
        price = marketable_sell_price(self.instrument, reference)
        close = self._limit(OrderSide.SELL, event.last_qty, price)
        self._close_id = close.client_order_id
        self.record.close_order_built(**_described(close))
        self._send(close)

    def _orders_at_stop(self) -> list[tuple[str, ClientOrderId | None]]:
        return [("", self._order_id), ("close_", self._close_id)]


def _described(order: Any) -> dict[str, str]:
    return {
        "client_order_id": str(order.client_order_id),
        "instrument_id": str(order.instrument_id),
        "side": order.side.name,
        "type": order.order_type.name,
        "time_in_force": order.time_in_force.name,
        "quantity": str(order.quantity),
        "price": str(order.price),
    }


# --- the node ----------------------------------------------------------------------------------


def _enum_name(value: object) -> str:
    """The member name of an adapter enum: its pyo3 enums print as ``Type.NAME`` and, unlike
    their stubs, have no ``.name`` at runtime (checked on the rc9 wheel, 2026-11-17)."""
    return str(value).rsplit(".", 1)[-1]


def instrument_provider_config() -> ib.InteractiveBrokersInstrumentProviderConfig:
    return ib.InteractiveBrokersInstrumentProviderConfig(
        symbology_method=ib.SymbologyMethod.SIMPLIFIED,
        load_ids={InstrumentId.from_str(INSTRUMENT_ID)},
        build_options_chain=False,
        build_futures_chain=False,
    )


def build(mode: str, account: str, record: RunRecord) -> tuple[LiveNode, OneDayLimit]:
    """Build the node and the mode's strategy. Connects to nothing."""
    if mode not in MAX_ORDERS:
        raise ValueError(f"unknown mode {mode!r}")
    provider = instrument_provider_config()
    record.data["engine"] = {"nautilus_trader": nautilus_trader.__version__}
    record.data["node"] = {
        "reconciliation_lookback_mins": RECONCILIATION_LOOKBACK_MINS,
        "max_quote_age_seconds": MAX_QUOTE_AGE_SECONDS,
        "client_ids": {
            "data": ib_ids.DATA_CLIENT_ID,
            "execution": ib_ids.EXECUTION_CLIENT_ID,
        },
        "market_data_type": _enum_name(MARKET_DATA_TYPE),
    }
    # LiveNode.builder and LiveNodeBuilder: v2.0.0rc9 wheel live/__init__.pyi:1138-1224;
    # with_reconciliation_lookback_mins: crates/live/src/node/builder.rs:267-272. The risk
    # engine's config is set explicitly, not bypassed (the default is also not bypassed:
    # crates/risk/src/engine/config.rs:48-50, LiveNodeConfig().risk_engine.bypass is False).
    builder = (
        LiveNode.builder(NODE_NAME, TraderId.from_str(TRADER_ID), Environment.LIVE)
        .with_risk_engine_config(LiveRiskEngineConfig(bypass=False))
        .with_timeout_connection(15)
        .with_timeout_reconciliation(5)
        .with_timeout_portfolio(5)
        .with_timeout_disconnection_secs(5)
        .with_delay_post_stop_secs(5)
        .with_reconciliation(True)
        .with_reconciliation_lookback_mins(RECONCILIATION_LOOKBACK_MINS)
        .add_data_client(
            None,
            ib.InteractiveBrokersDataClientFactory(),
            ib.InteractiveBrokersDataClientConfig(
                host=ib_ids.HOST,
                port=ib_ids.PAPER_PORT,
                client_id=ib_ids.DATA_CLIENT_ID,
                connection_timeout=CLIENT_CONNECTION_TIMEOUT_SECS,
                request_timeout=REQUEST_TIMEOUT_SECS,
                market_data_type=MARKET_DATA_TYPE,
                instrument_provider=provider,
                # After a drop, no reconnect to a Gateway the paper checks have not seen. The
                # bound limits only the reconnect() loop; the first connect does not use it
                # (rust-ibapi v5.0.0 src/connection/async.rs:153-158, read 2026-11-17 on
                # GitHub, wboayue/rust-ibapi).
                max_reconnect_attempts=0,
            ),
        )
        .add_exec_client(
            None,
            ib.InteractiveBrokersExecutionClientFactory(),
            ib.InteractiveBrokersExecutionClientConfig(
                host=ib_ids.HOST,
                port=ib_ids.PAPER_PORT,
                client_id=ib_ids.EXECUTION_CLIENT_ID,
                account_id=account,
                connection_timeout=CLIENT_CONNECTION_TIMEOUT_SECS,
                request_timeout=REQUEST_TIMEOUT_SECS,
                fetch_all_open_orders=False,
                instrument_provider=provider,
                # As for the data client: only the reconnect() loop is bounded (rust-ibapi
                # v5.0.0 src/connection/async.rs:153-158, read 2026-11-17 on GitHub,
                # wboayue/rust-ibapi).
                max_reconnect_attempts=0,
            ),
        )
    )
    node = builder.build()
    strategy = (OneRoundTrip if mode == "fill" else OneDayLimit).create(record)
    if str(strategy.instrument_id) != INSTRUMENT_ID:
        raise RuntimeError("the strategy is not on the lane's instrument")
    node.add_strategy(strategy)
    return node, strategy


@contextlib.contextmanager
def _output_to(path: Path) -> Iterator[None]:
    """Send everything written to stdout and stderr, by Python or by the engine's Rust core
    (file descriptors 1 and 2), to a local file with mode 0600 in a 0700 folder, and restore
    both afterwards."""
    boundary.private_dir(path.parent)
    sys.stdout.flush()
    sys.stderr.flush()
    saved = (os.dup(1), os.dup(2))
    log = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    os.fchmod(log, 0o600)
    stream = open(log, "w", buffering=1, encoding="utf-8", errors="replace", closefd=False)  # noqa: SIM115
    old = (sys.stdout, sys.stderr)
    try:
        os.dup2(log, 1)
        os.dup2(log, 2)
        sys.stdout = sys.stderr = stream
        yield
    finally:
        stream.flush()
        sys.stdout, sys.stderr = old
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        stream.close()
        for fd in (*saved, log):
            os.close(fd)


def run(mode: str, account: str, record: RunRecord, settings: Settings | None = None) -> None:
    """Build the node and run it until its own timer stops it.

    The engine's own output can name the account, so it goes to a log in ``settings.logs_dir``
    (outside the git tree) and the terminal shows only the lane's lines. A stop before the
    timer's due time (a Ctrl-C, or the node stopping on its own) is recorded as interrupted and
    fails the run.
    """
    settings = Settings.current() if settings is None else settings
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log = settings.logs_dir / f"M1-ibkr-paper-{stamp}-{mode}-engine.log"
    record.local["engine_log"] = str(log)
    print(f"Engine output goes to {log} (it can show the account id; it stays on this machine).")
    seconds = AUTO_STOP_SECONDS[mode]
    failure = None
    with _output_to(log):
        node: Any = None
        strategy: Any = None
        timer: threading.Timer | None = None
        try:
            node, strategy = build(mode, account, record)
            handle = node.handle()
            # The lane's own timer, cancelled below: nothing outlives the run to signal later.
            timer = threading.Timer(seconds, handle.stop)
            timer.daemon = True
            due = time.monotonic() + seconds
            timer.start()
            node.run()  # returns once the node has stopped, by this timer or by anything else
            # The node takes a few seconds to stop, and the timer can fire in them. So what
            # counts is when the node stopped the strategy: before the time was up, or never,
            # fails the run.
            if strategy.stopped_at is None or strategy.stopped_at < due:
                failure = "interrupted: the node stopped before its time"
        except KeyboardInterrupt:
            failure = "interrupted"
        except Exception as e:  # a failed connection or reconciliation aborts with an error
            failure = f"{type(e).__name__}: {e}"
            traceback.print_exc()  # into the log
        finally:
            if timer is not None:
                timer.cancel()
                if timer.is_alive():  # until its thread has ended it holds the node's handle
                    timer.join(5)
        # The last look, after node.run() has returned or raised and before the node is
        # dropped, while output still goes to the log. It never raises out of run: on any
        # error the record's after_shutdown stays None, which fails the run.
        if strategy is not None:
            try:
                strategy.record_after_shutdown()
            except Exception:
                traceback.print_exc()  # into the log
        # Drop the node, and all that holds it, while stdout and stderr still go to the log:
        # the engine's logger writes out what it still holds when the node is dropped.
        node = strategy = timer = handle = None
        gc.collect()
    if failure is not None:
        record.engine_error(failure)  # the text stays in runs/, never in evidence/
        kind = failure.split(":", 1)[0]
        print(f"The node stopped with an error ({kind}); details in {log}.", file=sys.stderr)
