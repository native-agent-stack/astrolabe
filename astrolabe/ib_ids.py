"""The one place for the Gateway target and the API client ids. Own design.

Host and port are constants, never settings: nothing widens this set. The lane reaches the
IB Gateway over loopback on the Gateway's default socket port for a paper login, and on no
other port. No other file in this package names the port or a client id
(tests/test_client_ids.py).

Client ids. NautilusTrader's IB adapter partitions order ids by ``client_id % 1000`` and
refuses an execution client id that is a multiple of 1000 (nautilus_trader v2.0.0rc9,
crates/adapters/interactive_brokers/src/execution/core.rs:127, 452, 700-722, read 2026-11-16).
When a client lists all open orders, IB can raise that client's next valid order id above
another client's working orders; the client with the lower slot is then refused with error 103
(nautilus_trader issue 5277, open on 2026-11-16). Slot 712 has never been used on the owner's
paper account and sits above the slot of the owner's other project, so this lane is not the
exposed one.
"""

HOST = "127.0.0.1"
PAPER_PORT = 4002

DATA_CLIENT_ID = 1711
EXECUTION_CLIENT_ID = 1712
# Used in turn after an order-id lockout (IB error 103), never reused. Moving the execution
# client to the next one is a reviewed change to this file.
SPARE_EXECUTION_CLIENT_IDS = (1713, 1714, 1715, 1716, 1717, 1718, 1719)
SANDBOX_DATA_CLIENT_ID = 1721
FEED_READER_CLIENT_ID = 1722
TIMER_PROBE_CLIENT_ID = 1797
CONTRACT_PROBE_CLIENT_ID = 1798
ACCOUNT_PROBE_CLIENT_ID = 1799

# The API client ids of the owner's other project on the same paper account. This lane shares
# none of them and none of their order-id slots.
OTHER_PROJECT_CLIENT_IDS = (1511, 1512, 1521, 1598, 1599)
