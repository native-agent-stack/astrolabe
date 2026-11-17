"""
Paper only: tripwires for recognizable live ports, live modes and live key names, and for any
Alpaca trading host or Alpaca order call, in files git can see. Computed values and indirect
calls can escape a source scan.

"Live" here means real-money trading. NautilusTrader's own names such as ``LiveNode`` and
``Environment.LIVE`` mean "connected to a venue, not a backtest" and are not matched.

Astrolabe may read Alpaca market data later; it never has an Alpaca order path, so the paper
trading host is refused as well as the live one, and so are alpaca-py's trading client and
order requests.

This scan is a tripwire, not the boundary. The boundary is ``astrolabe/boundary.py``. Literal
folding never executes code. The live ports are computed here, never written: the Gateway's
live port sits one below its paper port in ``astrolabe/ib_ids.py``, and TWS's one below its
paper port (NautilusTrader IB integration guide, v2.0.0rc9).

Ported from meridian@014f706d:tests/test_paper_only.py; the Alpaca lane's patterns are
dropped, and the Alpaca trading-host and order-call patterns are new.
"""

import ast
import io
import os
import re
import subprocess
import tokenize
from collections.abc import Iterator
from pathlib import Path

import pytest

from astrolabe import ib_ids

REPO_ROOT = Path(__file__).resolve().parent.parent
# This file states the patterns. The lock file holds only package names and hashes.
NOT_SCANNED = {"tests/test_paper_only.py", "uv.lock"}

TWS_PAPER_PORT = 7497
GATEWAY_LIVE_PORT = ib_ids.PAPER_PORT - 1
TWS_LIVE_PORT = TWS_PAPER_PORT - 1
LIVE_PORTS = (GATEWAY_LIVE_PORT, TWS_LIVE_PORT)

LIVE_PATTERNS = {
    "live port (IB Gateway live or TWS live)": re.compile(
        r"(?<![\w.])(?:" + "|".join(str(port) for port in LIVE_PORTS) + r")(?!\w|\.\d)"
    ),
    "live trading mode (IB adapter)": re.compile(
        r"TradingMode\s*(?:\.|::)\s*LIVE|trading_mode[\"']?\s*[:=]\s*[\"']?live", re.IGNORECASE
    ),
    # The Gateway's own form in jts.ini: tradingMode=l is a live login (p is paper). Also the
    # ways Python writes it (a configparser subscript, ConfigParser.set). Horizontal space only,
    # so a key on one line never joins a word on the next.
    "live login mode (IB Gateway jts.ini)": re.compile(
        r"tradingMode[\"']?[^\S\r\n]*(?:[:=]|\][^\S\r\n]*=|,)[^\S\r\n]*[\"']?l(?:ive)?\b",
        re.IGNORECASE,
    ),
    # Alpaca's trading and broker hosts, paper and sandbox included: api.alpaca.markets,
    # paper-api.alpaca.markets, broker-api.alpaca.markets, broker-api.sandbox.alpaca.markets.
    # The market-data hosts (data.alpaca.markets, stream.data.alpaca.markets) and the
    # documentation host are not matched.
    "Alpaca trading host": re.compile(r"api\.(?:sandbox\.)?alpaca\.markets", re.IGNORECASE),
    # alpaca-py's trading package, its trading client and stream, its order requests, and the
    # Trading API's order route. NautilusTrader's own submit_order and OrderFactory are not
    # matched.
    "Alpaca order call": re.compile(
        r"\balpaca\.trading\b|\bTrading(?:Client|Stream)\b"
        r"|\b(?:Market|Limit|Stop|StopLimit|TrailingStop|Replace)OrderRequest\b"
        r"|/v2/orders\b"
    ),
}
_NAME = re.compile(r"[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)*")
_NAME_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")
_SECRET_WORDS = {"key", "secret", "token", "password"}
_SECRET_PART = re.compile(r"(?:api)?(?:key|secret|token|password)s?")
_COMPOUND_SECRET = re.compile(
    r"(?:live(?:api)?(?:key|secret|token|password)s?|"
    r"(?:api)?(?:key|secret|token|password)s?live)"
)


def live_key_name(text: str) -> bool:
    """A name with LIVE as one of its parts and a secret word as another."""
    for name in _NAME.findall(text):
        parts = {part.lower() for part in _NAME_PART.findall(name)}
        if "live" in parts and parts & _SECRET_WORDS:
            return True
        if "live" in parts and any(_SECRET_PART.fullmatch(part) for part in parts):
            return True
        if any(_COMPOUND_SECRET.fullmatch(part) for part in parts):
            return True
    return False


def text_findings(text: str) -> list[str]:
    found = [label for label, pattern in LIVE_PATTERNS.items() if pattern.search(text)]
    if live_key_name(text):
        found.append("live key name")
    return found


def python_tree(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except SyntaxError, ValueError:
        return None  # Non-Python files still receive every text rule.


def literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal_text(node.left), literal_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def folded_strings(text: str) -> Iterator[str]:
    """Maximal literal additions and adjacent Python string literals, without eval.

    Variables, formatting and function calls are not resolved. Non-Python text is not folded.
    """

    def visit(node: ast.AST) -> Iterator[str]:
        if isinstance(node, ast.JoinedStr):
            return  # Formatting is outside literal folding, including its partial constants.
        value = literal_text(node)
        if value is not None:
            segment = ast.get_source_segment(text, node) or ""
            tokens = tokenize.generate_tokens(io.StringIO("(" + segment + ")").readline)
            if sum(token.type == tokenize.STRING for token in tokens) > 1:
                yield value
            return
        for child in ast.iter_child_nodes(node):
            yield from visit(child)

    tree = python_tree(text)
    if tree is not None:
        yield from visit(tree)


def findings(text: str) -> list[str]:
    found = text_findings(text)
    for value in folded_strings(text):
        found.extend(text_findings(value))
    return found


def git_files(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", *args], cwd=REPO_ROOT, check=True, capture_output=True
    ).stdout
    return [os.fsdecode(name) for name in out.split(b"\0") if name]


def is_link_to_listed_files(name: str, listed: set[str], root: Path = REPO_ROOT) -> bool:
    """True for a symbolic link that only shows again what git already lists.

    Such a link (``.claude/skills/<name>`` pointing at ``.agents/skills/<name>``) is scanned as
    its own text; the files behind it are scanned under their own names. It must be relative,
    not broken, and resolve inside the repository: to a listed file, or to a folder in which
    git lists every file (an ignored file there would reach an agent unscanned). Any other link
    fails the scan, because it could show an agent text that git never lists.
    """
    path = root / name
    if not path.is_symlink():
        return False
    assert not os.path.isabs(os.readlink(path)), f"{name} is an absolute symbolic link"
    assert path.exists(), f"{name} is a broken symbolic link"
    resolved = path.resolve(strict=True)
    assert resolved.is_relative_to(root), f"{name} links outside the repository"
    target = resolved.relative_to(root).as_posix()
    if resolved.is_dir():
        inside = [p.relative_to(root).as_posix() for p in resolved.rglob("*") if not p.is_dir()]
        assert inside and set(inside) <= listed, (
            f"{name} links to {target}, a folder with files git does not list"
        )
    else:
        assert target in listed, f"{name} links to {target}, which git does not list"
    return True


def files_to_scan() -> list[str]:
    """Every name git can see, less NOT_SCANNED and deleted files. Links are checked first."""
    deleted = set(git_files("--deleted"))
    listed = git_files("--cached", "--others", "--exclude-standard")
    seen = set(listed)
    names = [name for name in listed if name not in NOT_SCANNED and name not in deleted]
    for name in names:
        is_link_to_listed_files(name, seen)
    return names


def scan_bytes(name: str, root: Path = REPO_ROOT) -> bytes:
    """What a scan reads: a file's bytes, or a symbolic link's own text (git stores a link as
    the path it points to). The files behind a link are scanned under their own names."""
    path = root / name
    if path.is_symlink():
        return os.fsencode(os.readlink(path))
    # Nothing is skipped quietly: a nested repository or an odd entry fails the scan.
    assert path.is_file(), f"{name} is listed by git but is not a regular file"
    return path.read_bytes()


def test_no_live_port_mode_key_name_or_alpaca_order_path_in_the_repo():
    files = files_to_scan()
    assert files, "git reported no files; the scan would pass without looking at anything"
    hits = []
    for name in files:
        data = scan_bytes(name)
        assert b"\0" not in data, f"{name} is not plain text (UTF-16 or binary); cannot scan it"
        hits += [f"{name}: {label}" for label in findings(data.decode("utf-8", errors="replace"))]
    assert hits == []


def test_the_live_ports_are_computed_from_the_paper_ports():
    assert ib_ids.PAPER_PORT not in LIVE_PORTS
    assert TWS_PAPER_PORT not in LIVE_PORTS
    assert len(set(LIVE_PORTS)) == 2


G, T = GATEWAY_LIVE_PORT, TWS_LIVE_PORT


@pytest.mark.parametrize(
    "text",
    [
        f"port = {G}",
        f"IB_PORT={T}",
        f"connect('127.0.0.1', {G})",
        f"the Gateway live port is {G}.",
        "DockerizedIBGatewayConfig(trading_mode=TradingMode.LIVE)",
        "TradingMode::Live",
        "TRADING_MODE=live",
        'trading_mode: "live"',
        "[Logon]\ntradingMode=l\n",
        "TradingMode = l",
        "tradingMode='l'",
        '"tradingMode": "live"',
        "[Logon]\r\ntradingMode=l\r\n",
        "tradingMode=l ; set by hand",
        "tradingMode=L",
        'parser["Logon"]["tradingMode"] = "l"',
        'parser.set("Logon", "tradingMode", "l")',
        "IBKR_LIVE_API_KEY",
        "ibkr_live_password",
        "SECRET_KEY_LIVE",
        "liveToken",
        "live-api-key",
        "live.api.key",
    ],
)
def test_the_scan_catches_live_markers(text):
    assert findings(text)


@pytest.mark.parametrize(
    "text",
    [
        "https://api.alpaca.markets/v2/orders",
        "https://paper-api.alpaca.markets/v2/account",
        "wss://paper-api.alpaca.markets/stream",
        "https://broker-api.alpaca.markets",
        "https://broker-api.sandbox.alpaca.markets/v1/accounts",
        "PAPER-API.ALPACA.MARKETS",
        "from alpaca.trading.client import TradingClient",
        "import alpaca.trading.requests",
        "client = TradingClient(key, secret, paper=True)",
        "stream = TradingStream(key, secret, paper=True)",
        "client.submit_order(order_data=MarketOrderRequest(symbol=s, qty=1))",
        "LimitOrderRequest(symbol=s, limit_price=p)",
        "StopLimitOrderRequest(symbol=s)",
        "TrailingStopOrderRequest(symbol=s)",
        "ReplaceOrderRequest(qty=2)",
        "POST /v2/orders",
    ],
)
def test_the_scan_catches_an_alpaca_trading_host_or_order_call(text):
    assert findings(text)


@pytest.mark.parametrize(
    "text",
    [
        f"port = {ib_ids.PAPER_PORT}",
        f"DEFAULT_TWS_PORT = {TWS_PAPER_PORT}",
        "LiveNode.builder(name, trader, Environment.LIVE)",
        "LiveRiskEngineConfig(bypass=True)",
        "TradingMode.PAPER",
        "TRADING_MODE=paper",
        "[Logon]\ntradingMode=p\n",
        "tradingMode=lane",
        "jts.ini tradingMode is not p",
        "tradingMode=lively",
        "tradingMode:\n  live: false",
        'parser.get("Logon", "tradingMode", fallback=None)',
        f"ts = 179{G}000000000000",
        f"sha256 = 'a{G}{T}b'",
        f"version 1.{G}.2",
        f'"price": "{G}.00"',
        "IBKR_PAPER_API_KEY",
        "delivery_token",
        "alive_key",
        "a live Gateway would be refused; the key is the allowlist",
        # Alpaca market data and documentation stay readable.
        "https://data.alpaca.markets/v2/stocks/quotes",
        "wss://stream.data.alpaca.markets/v2/iex",
        "https://docs.alpaca.markets/docs/about-market-data-api",
        "from alpaca.data.historical import StockHistoricalDataClient",
        "StockLatestQuoteRequest(symbol_or_symbols='AAPL')",
        # The engine's own order calls belong to the IB lane.
        "self.submit_order(order)",
        "self.order_factory.limit(instrument_id, OrderSide.BUY, quantity, price)",
        "SubmitOrder(trader_id, strategy_id, order)",
        "CancelOrder(trader_id, strategy_id, instrument_id, client_order_id)",
    ],
)
def test_the_scan_leaves_paper_data_and_engine_names_alone(text):
    assert findings(text) == []


@pytest.mark.parametrize(
    "name",
    [
        "LIVE_KEYS",
        "IBKR_LIVE_APIKEY",
        "livekey",
        "livekeys",
        "liveapikey",
        "liveSecrets",
        "LIVE_TOKENS",
        "LIVE_PASSWORDS",
        "KEYSLIVE",
    ],
)
def test_plural_and_compound_secret_names_are_refused(name):
    assert live_key_name(name)


@pytest.mark.parametrize(
    "source",
    [
        'HOST = "api." + "alpaca.markets"',
        'HOST = "paper-api." "alpaca.markets"',
        'HOST = ("paper-api."\n "alpaca.markets")',
        'HOST = ("api." + ("alpaca." + "markets"))',
        'HOST = "api\\x2e" + "alpaca.markets"',
        'HOST = "https://paper-api." + "alpaca.markets/v2/orders"',
        'MODULE = "alpaca." + "trading"',
        f'PORT = "{str(G)[:2]}" + "{str(G)[2:]}"',
    ],
)
def test_literal_concatenations_are_refused(source):
    assert findings(source)


def test_a_planted_file_fails_the_repository_scan(monkeypatch):
    module = __import__(__name__)
    monkeypatch.setattr(module, "files_to_scan", lambda: ["astrolabe/planted.py"])
    for source in (
        'HOST = "paper-api." + "alpaca.markets"',
        "from alpaca.trading.client import TradingClient",
        f"PORT = {G}",
    ):
        monkeypatch.setattr(module, "scan_bytes", lambda _, source=source: source.encode())
        with pytest.raises(AssertionError):
            test_no_live_port_mode_key_name_or_alpaca_order_path_in_the_repo()
    monkeypatch.setattr(module, "scan_bytes", lambda _: b"PORT = ib_ids.PAPER_PORT\n")
    test_no_live_port_mode_key_name_or_alpaca_order_path_in_the_repo()


@pytest.mark.parametrize("name", ["tests/conftest.py", "tools/guard/pretooluse.py"])
def test_the_files_that_compute_live_ports_pass_the_scan(name):
    # They compute the live ports from the paper ports instead of naming them. The guard is
    # written by another task; the test waits for it.
    path = REPO_ROOT / name
    if not path.is_file():
        pytest.fail(f"{name} is not in the tree yet")
    assert findings(path.read_text(encoding="utf-8")) == []


LISTED = {"a/skill/SKILL.md", "a/mixed/SKILL.md", "b/link"}


@pytest.fixture
def tree(tmp_path):
    root = (tmp_path / "repo").resolve()
    (root / "a" / "skill").mkdir(parents=True)
    (root / "a" / "skill" / "SKILL.md").write_text("text", encoding="utf-8")
    (root / "a" / "mixed").mkdir()
    (root / "a" / "mixed" / "SKILL.md").write_text("text", encoding="utf-8")
    (root / "a" / "mixed" / "ignored.txt").write_text("text", encoding="utf-8")
    (root / "a" / "unlisted").mkdir()
    (root / "b").mkdir()
    (tmp_path / "outside").mkdir()
    return root


@pytest.mark.parametrize("target", ["../a/skill", "../a/skill/SKILL.md"])
def test_a_link_to_listed_files_is_left_to_its_target(tree, target):
    (tree / "b" / "link").symlink_to(target)
    assert is_link_to_listed_files("b/link", LISTED, tree)
    assert scan_bytes("b/link", tree) == target.encode()
    assert not is_link_to_listed_files("a/skill/SKILL.md", LISTED, tree)


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("../../outside", "outside the repository"),
        ("/etc", "absolute"),
        ("IN-TREE", "absolute"),
        ("../a/unlisted", "git does not list"),
        ("../a/mixed", "git does not list"),
        ("..", "git does not list"),
        ("../a/missing", "broken"),
        ("link", "broken"),
        ("../missing/../a/skill", "broken"),
    ],
)
def test_any_other_link_fails_the_scan(tree, target, reason):
    # IN-TREE stands for an absolute link to a listed folder, which is refused all the same.
    (tree / "b" / "link").symlink_to(tree / "a" / "skill" if target == "IN-TREE" else target)
    with pytest.raises(AssertionError, match=reason):
        is_link_to_listed_files("b/link", LISTED, tree)
