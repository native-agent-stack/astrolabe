"""Test ownership for rc9 nodes, including references retained by pytest tracebacks.

Ported from meridian@014f706d:tests/node_lifetime.py, unchanged.

rc9 crates/live/src/node/mod.rs requires dropping even a disposed LiveNode.
A traceback can retain this proxy after teardown, but cannot retain its node.

Nor its run. At the pin (v2.0.0rc9, crates/live/src/python/node.rs) a node's thread guard is
a field of the LiveNode value (`_thread_owner`, crates/live/src/node/mod.rs:213, taken at
:346-357), so only dropping the node frees the thread. `run_async` moves the node into a
`PyNodeRun` that also keeps an `Rc` on the node's slot (`owner`, python/node.rs:502) and
returns the engine's driver coroutine `async def drive(run): return await run` (:794-805). A
cancelled or failed run finishes its shutdown and gives the node back to the slot
(`restore_node`, :573-592), but the exception it raises carries the driver's frame, whose
`run` still holds the slot, and `dispose` cannot drop a node it only borrows (:1204-1224). A
test that fails while its node runs (a wait that times out) keeps that exception in its
traceback, which pytest keeps past teardown, and the next node on the thread is refused:
"A LiveNode already exists or is being built on this thread" (node/mod.rs:351).
"""

import gc
import traceback
from typing import Any
from weakref import WeakSet

import pytest
from nautilus_trader.live import LiveNode, LiveNodeBuilder


class OwnedNode:
    def __init__(self, node: LiveNode):
        self._node: LiveNode | None = node

    def __getattr__(self, name: str) -> Any:
        if self._node is None:
            raise RuntimeError("test node already released")
        return getattr(self._node, name)

    async def run_async(self) -> None:
        """The engine's hosted run; whatever it raises leaves without the frames that hold it."""
        if self._node is None:
            raise RuntimeError("test node already released")
        try:
            await self._node.run_async()
        except BaseException as exc:
            release_frames(exc)
            raise

    def release(self) -> None:
        if self._node is not None:
            try:
                self._node.dispose()
            finally:
                self._node = None


def release_frames(exc: BaseException) -> None:
    """Clear the locals of every finished frame in `exc`'s tracebacks, its causes and contexts
    included; `traceback.clear_frames` leaves a frame that is still executing as it is."""
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        pending += [current.__cause__, current.__context__]
        pending += list(getattr(current, "exceptions", ()))


@pytest.fixture
def live_nodes(monkeypatch):
    # Do not retain successful nodes: a test may build and drop several in turn.
    nodes: WeakSet[OwnedNode] = WeakSet()

    def own(build):
        def managed(*args, **kwargs):
            node = OwnedNode(build(*args, **kwargs))
            nodes.add(node)
            return node

        return managed

    monkeypatch.setattr(LiveNode, "build", own(LiveNode.build))
    monkeypatch.setattr(LiveNodeBuilder, "build", own(LiveNodeBuilder.build))
    yield
    try:
        for node in list(nodes):
            node.release()
    finally:
        nodes.clear()
        gc.collect()
