"""Who can reach the order path: order calls only in astrolabe/ibkr_node.py, no order change and
no IOC anywhere, the gate's modules load no engine, and ruff keeps the engine's live, trading
and IB adapter packages out of every other module. Own design, on the standard library's
``ast``.

The scan sees names as the source writes them: an attribute (``self.submit_order``), a bare
name, or a string constant equal to the name (``getattr(x, "submit_order")``). Code that builds
the name at run time, or opens its own socket to the Gateway, escapes it.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "astrolabe"
ENGINE_MODULE = PACKAGE / "ibkr_node.py"
# NautilusTrader's order calls in the v2.0.0rc9 wheel's trading/__init__.pyi: Strategy's
# (:565-639), its exit routine market_exit (:539) and cancel_gtd_expiry (:604, which cancels the
# timer that would cancel a GTD order, so it changes when an order ends), the Controller's exit
# routines (:101-102), and ExecutionAlgorithm's (:455-476, the same names) with its child-order
# calls spawn_market, spawn_limit and spawn_market_to_limit (:419-453). Then the TWS API's own
# (placeOrder, cancelOrder, reqGlobalCancel).
ORDER_CALLS = {
    "submit_order",
    "submit_order_list",
    "cancel_order",
    "cancel_orders",
    "cancel_all_orders",
    "cancel_gtd_expiry",
    "close_position",
    "close_all_positions",
    "market_exit",
    "market_exit_strategy",
    "market_exit_strategy_from_id",
    "spawn_market",
    "spawn_limit",
    "spawn_market_to_limit",
    "placeOrder",
    "cancelOrder",
    "reqGlobalCancel",
}
# Never used: an order change, modify_order (nautilus_trader issue 5463) and Strategy's
# modify_orders (:588-597) or ExecutionAlgorithm's modify_order_in_place (:469), and an IOC time
# in force (issue 5598).
BANNED_EVERYWHERE = {"modify_order", "modify_orders", "modify_order_in_place"}
GATE_MODULES = (
    "astrolabe.boundary",
    "astrolabe.ib_probe",
    "astrolabe.ibkr_paper",
    "astrolabe.settings",
    "astrolabe.market_hours",
)


def mentions(tree: ast.AST, names: set[str]) -> list[tuple[str, int]]:
    """(name, line) for each attribute, bare name or string constant in ``names``."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in names:
            found.append((node.attr, node.lineno))
        elif isinstance(node, ast.Name) and node.id in names:
            found.append((node.id, node.lineno))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names:
            found.append((node.value, node.lineno))
        elif isinstance(node, ast.alias):  # an import of the name, under any alias
            for name in (node.name.rsplit(".", 1)[-1], node.asname):
                if name in names:
                    found.append((name, node.lineno))
    return found


def ioc_uses(tree: ast.AST) -> list[int]:
    """Lines that name the IOC time in force: ``TimeInForce.IOC``, any ``.IOC``, or "IOC"."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == "IOC")
        or (isinstance(node, ast.Name) and node.id == "IOC")
        or (isinstance(node, ast.Constant) and node.value == "IOC")
    ]


def sources(root: Path):
    paths = sorted(root.rglob("*.py"))
    assert paths, f"{root}: nothing scanned"
    for path in paths:
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def order_call_sites(root: Path, engine_module: Path) -> list[str]:
    return [
        f"{path.relative_to(root)}:{line}: {name}"
        for path, tree in sources(root)
        if path != engine_module
        for name, line in mentions(tree, ORDER_CALLS)
    ]


def test_order_calls_appear_only_in_the_engine_module():
    assert order_call_sites(PACKAGE, ENGINE_MODULE) == []


def test_the_engine_module_has_the_order_calls_the_scan_looks_for():
    # A renamed engine module would leave the scan above looking at nothing.
    tree = ast.parse(ENGINE_MODULE.read_text(encoding="utf-8"))
    assert {"submit_order", "cancel_order", "cancel_all_orders"} <= {
        name for name, _ in mentions(tree, ORDER_CALLS)
    }


@pytest.mark.parametrize(
    "source",
    [
        "self.submit_order(order)",
        "strategy.cancel_all_orders(instrument_id)",
        "client.placeOrder(1, contract, order)",
        "getattr(self, 'close_all_positions')()",
        "self.market_exit()",
        "controller.market_exit_strategy(strategy_id)",
        "controller.market_exit_strategy_from_id(strategy_id)",
        "self.cancel_gtd_expiry(order)",
        "from somewhere import reqGlobalCancel",
        "reqGlobalCancel()",
    ],
)
def test_the_scan_finds_a_planted_order_call(tmp_path, source):
    (tmp_path / "planted.py").write_text(f"# planted\n{source}\n")
    (tmp_path / "ibkr_node.py").write_text("")
    assert order_call_sites(tmp_path, tmp_path / "ibkr_node.py")


def test_the_scan_ignores_comments_and_longer_names(tmp_path):
    (tmp_path / "quiet.py").write_text(
        "# self.submit_order(order)\nsubmit_order_count = 0\ntext = 'cancel_order is banned'\n"
    )
    assert order_call_sites(tmp_path, tmp_path / "ibkr_node.py") == []


def test_no_order_change_and_no_ioc_anywhere_under_astrolabe():
    found = [
        f"{path.relative_to(REPO)}:{line}: {what}"
        for path, tree in sources(PACKAGE)
        for what, line in [
            *mentions(tree, BANNED_EVERYWHERE),
            *(("IOC", line) for line in ioc_uses(tree)),
        ]
    ]
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "self.modify_order(order_id, price=price)",
        "self.modify_order_in_place(order, price=price)",
        "tif = TimeInForce.IOC",
        "from nautilus_trader.model import TimeInForce as T\nT.IOC",
        "getattr(TimeInForce, 'IOC')",
    ],
)
def test_the_scan_finds_a_planted_modify_or_ioc(source):
    tree = ast.parse(source)
    assert mentions(tree, BANNED_EVERYWHERE) or ioc_uses(tree)


def test_the_gate_modules_load_no_engine_module():
    code = (
        "import sys\n"
        f"import {', '.join(GATE_MODULES)}\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'nautilus_trader'))\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(REPO)!r})\n{code}"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert done.stdout.strip() == "[]"


def ruff_tid251(stdin_filename: str, source: str) -> subprocess.CompletedProcess[str]:
    """Ruff with the repository's configuration on ``source``, as if it were the file at
    ``stdin_filename``; nothing is written to the repository."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--config",
            str(REPO / "pyproject.toml"),
            "--select",
            "TID251",
            "--output-format",
            "concise",
            "--stdin-filename",
            stdin_filename,
            "-",
        ],
        cwd=REPO,
        input=source,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    "source",
    [
        "from nautilus_trader.live import LiveNode\n",
        "import nautilus_trader.live\n",
        "from nautilus_trader.trading import Strategy\n",
        "from nautilus_trader.adapters import interactive_brokers\n",
        "from nautilus_trader.adapters.interactive_brokers import MarketDataType\n",
    ],
)
def test_ruff_refuses_the_engines_order_packages_outside_the_engine_module(source):
    refused = ruff_tid251("astrolabe/elsewhere.py", source)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "TID251" in refused.stdout


def test_ruff_admits_them_in_the_engine_module():
    admitted = ruff_tid251("astrolabe/ibkr_node.py", "from nautilus_trader.live import LiveNode\n")
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr


def test_ruff_admits_the_engines_model_anywhere():
    admitted = ruff_tid251("astrolabe/elsewhere.py", "from nautilus_trader.model import Price\n")
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr
