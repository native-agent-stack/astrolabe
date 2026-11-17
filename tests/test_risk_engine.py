"""The engine's risk engine is never bypassed: a source tripwire and an offline build of every
mode of every node builder under astrolabe/.

Ported from meridian@014f706d:tests/test_risk_bypass.py. Changed: only astrolabe's one node
builder (``ibkr_node.build``) is registered; the lane sets its risk engine config itself, so the
offline build checks the config it passes; a plain-text scan for ``bypass=True`` and
``IB_V2_BYPASS_RISK`` is new.

NautilusTrader v2.0.0rc9 (read 2026-11-17): crates/risk/src/engine/config.rs:48-50, "Whether
to bypass risk checks and order rate limits"; crates/live/src/node/builder.rs:372-375,
``with_risk_engine_config`` replaces the node's risk engine config. The static check does not
follow assignment aliases or construction through another callable such as
functools.partial; ``test_each_mode_builds_with_risk_checks_enabled`` is the behavioural
backstop.
"""

import ast
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nautilus_trader.live import LiveNodeConfig, LiveRiskEngineConfig

from astrolabe import ibkr_paper

PACKAGE = Path(ibkr_paper.__file__).resolve().parent
RISK_CONFIGS = {"RiskEngineConfig", "LiveRiskEngineConfig"}
BUILDER_MODES = {("ibkr_node", "build"): ("order", "fill", "restart")}


def python_sources(root: Path):
    paths = sorted(root.rglob("*.py"))
    assert paths, f"{root}: no Python files scanned"
    for path in paths:
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def imported_names(tree: ast.AST) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    # Keep every possibility: an unrelated scope must not hide a monitored import.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases.setdefault(alias.asname or alias.name, set()).add(alias.name)
    return aliases


def names_of(node: ast.AST, aliases: dict[str, set[str]]) -> set[str]:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, set()) | {node.id}
    return {node.attr} if isinstance(node, ast.Attribute) else set()


def literal_false(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def assert_risk_checks(root: Path) -> None:
    """Unknown expressions/spreads fail closed; this does not interpret dynamic Python."""
    hits = []
    for path, tree in python_sources(root):
        aliases = imported_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not names_of(node.func, aliases) & RISK_CONFIGS:
                continue
            # Both pinned Python constructors take bypass as their first positional argument.
            unsafe = bool(node.args) and not literal_false(node.args[0])
            unsafe |= any(isinstance(arg, ast.Starred) for arg in node.args)
            unsafe |= any(
                kw.arg is None or (kw.arg == "bypass" and not literal_false(kw.value))
                for kw in node.keywords
            )
            if unsafe:
                hits.append(f"{path.relative_to(root)}:{node.lineno}: risk bypass may be true")
    assert not hits, "\n".join(hits)


@pytest.mark.parametrize("config", ["RiskEngineConfig", "LiveRiskEngineConfig"])
@pytest.mark.parametrize(
    "arguments",
    ["bypass=True", "bypass=enabled", "**options", "**{'bypass': True}", "True", "*args"],
)
def test_source_guard_rejects_possible_bypass(tmp_path, config, arguments):
    path = tmp_path / "nested" / "lane.py"
    path.parent.mkdir()
    path.write_text(f"# planted\nconfig = {config}({arguments})\n")
    with pytest.raises(AssertionError, match=r"nested/lane\.py:2"):
        assert_risk_checks(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "from nautilus_trader.risk import RiskEngineConfig as C\nC(bypass=True)",
        "from nautilus_trader.live import LiveRiskEngineConfig as C\nC(**options)",
        "import nautilus_trader.live as c\nc.LiveRiskEngineConfig(bypass=enabled)",
    ],
)
def test_source_guard_rejects_aliased_configurations(tmp_path, source):
    (tmp_path / "alias.py").write_text(source)
    with pytest.raises(AssertionError, match=r"alias\.py:2"):
        assert_risk_checks(tmp_path)


def test_source_guard_keeps_aliases_reused_in_other_scopes(tmp_path):
    (tmp_path / "scopes.py").write_text(
        "def lane():\n"
        "    from nautilus_trader.live import LiveRiskEngineConfig as Config\n"
        "    return Config(bypass=True)\n"
        "def unrelated():\n"
        "    from nautilus_trader.live import LiveNodeConfig as Config\n"
        "    return Config()\n"
    )
    with pytest.raises(AssertionError, match=r"scopes\.py:3"):
        assert_risk_checks(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "RiskEngineConfig()",
        "LiveRiskEngineConfig(bypass=False)",
        "RiskEngineConfig(False)",
        "LoggerConfig(bypass_logging=True)",
        "SomethingElse(bypass=True)",
        "RiskEngineConfig(bypass_logging=True)",
        "not_bypass = True\n# RiskEngineConfig(bypass=True)",
        "description = 'LiveRiskEngineConfig(bypass=True)'",
    ],
)
def test_source_guard_ignores_safe_defaults_and_unrelated_names(tmp_path, source):
    (tmp_path / "safe.py").write_text(source)
    assert_risk_checks(tmp_path)


def test_no_astrolabe_risk_configuration_can_request_bypass():
    assert_risk_checks(PACKAGE)


def test_no_file_under_astrolabe_names_a_bypass():
    found = [
        f"{path.relative_to(PACKAGE.parent)}: {word}"
        for path in sorted(PACKAGE.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
        for word in ("bypass=True", "IB_V2_BYPASS_RISK")
        if word in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert not found, found


def node_builders(root: Path) -> dict[tuple[str, str], int]:
    """Discover construction sites and node-module build functions, including import aliases."""
    found = {}
    for path, tree in python_sources(root):
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        aliases = imported_names(tree)

        def visit(
            node: ast.AST, owner: str = "<module>", path=path, module=module, aliases=aliases
        ) -> None:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                owner = node.name if owner == "<module>" else f"{owner}.{node.name}"
                if path.stem.endswith("_node") and node.name == "build":
                    found[module, owner] = node.lineno
            if isinstance(node, ast.Call):
                names = names_of(node.func, aliases)
                receiver = (
                    names_of(node.func.value, aliases)
                    if isinstance(node.func, ast.Attribute)
                    else set()
                )
                if names & {"LiveNode", "TradingNode", "build_ib_live_node"} or (
                    receiver & {"LiveNode", "TradingNode", "LiveNodeBuilder"}
                    and names & {"build", "builder", "from_config"}
                ):
                    found[module, owner] = node.lineno
            for child in ast.iter_child_nodes(node):
                visit(child, owner)

        visit(tree)
    return found


def assert_builder_coverage(root: Path) -> None:
    discovered = node_builders(root)
    unknown = discovered.keys() - BUILDER_MODES.keys()
    assert not unknown, "untested node builders: " + ", ".join(
        f"{module.replace('.', '/')}.py:{discovered[module, name]} ({name})"
        for module, name in sorted(unknown)
    )
    assert discovered.keys() == BUILDER_MODES.keys(), "registered builder disappeared"


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("new_node.py", "def build():\n    return helper()"),
        ("nested/lane.py", "def assemble():\n    return LiveNode.builder('x', trader, env)"),
        ("lane.py", "from nautilus_trader.live import LiveNode as N\ndef make():\n    N.build()"),
        ("lane.py", "from _common import build_ib_live_node as make\ndef other():\n    make()"),
        ("lane.py", "def other():\n    return TradingNode(config=config)"),
        (
            "scopes.py",
            "def lane():\n"
            "    from nautilus_trader.live import LiveNode as Node\n"
            "    return Node.build()\n"
            "def unrelated():\n"
            "    from ast import AST as Node\n"
            "    return Node()\n",
        ),
    ],
)
def test_new_node_builder_requires_an_offline_case(tmp_path, filename, source):
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    with pytest.raises(AssertionError, match="untested node builders") as error:
        assert_builder_coverage(tmp_path)
    assert f"{filename}:" in str(error.value)


def test_every_discovered_node_builder_has_an_offline_case():
    assert_builder_coverage(PACKAGE)


class RiskRecordingBuilder:
    """Stands in for the engine's node builder: keeps the engine's default risk engine config
    (LiveNodeConfig's) unless the lane replaces it, as builder.rs:372-375 does."""

    def __init__(self) -> None:
        self.node = SimpleNamespace(
            risk_engine=LiveNodeConfig().risk_engine, strategies=[], add_strategy=None
        )
        self.node.add_strategy = self.node.strategies.append

    def with_risk_engine_config(self, config):
        self.node.risk_engine = config
        return self

    def build(self):
        return self.node

    def __getattr__(self, name: str):
        return lambda *args, **kwargs: self


def assert_not_bypassed(config, identity: str) -> None:
    assert config.bypass is False, f"{identity}: engine risk checks are bypassed"


@pytest.mark.parametrize("keyword", [False, True])
def test_offline_capture_rejects_a_bypassed_override(keyword):
    builder = RiskRecordingBuilder()
    config = LiveRiskEngineConfig(bypass=True)
    if keyword:
        builder.with_risk_engine_config(config=config)
    else:
        builder.with_risk_engine_config(config)
    with pytest.raises(AssertionError, match="engine risk checks are bypassed"):
        assert_not_bypassed(builder.build().risk_engine, "planted")


def test_the_engine_default_is_not_bypassed():
    assert_not_bypassed(LiveNodeConfig().risk_engine, "LiveNodeConfig default")
    assert_not_bypassed(LiveRiskEngineConfig(), "LiveRiskEngineConfig default")


@pytest.mark.parametrize(
    ("identity", "mode"),
    [
        (identity, mode)
        for identity in sorted(node_builders(PACKAGE))
        for mode in BUILDER_MODES.get(identity, ("unregistered",))
    ],
)
def test_each_mode_builds_with_risk_checks_enabled(monkeypatch, identity, mode):
    assert identity in BUILDER_MODES, f"untested node builder: {identity}"
    module = importlib.import_module(f"astrolabe.{identity[0]}")
    build = getattr(module, identity[1])
    builder: Any = RiskRecordingBuilder()
    # The upstream example's switch, set in the shell: the lane must not read it.
    monkeypatch.setenv("IB_V2_BYPASS_RISK", "1")
    monkeypatch.setattr(module, "LiveNode", SimpleNamespace(builder=lambda *a: builder))
    record = ibkr_paper.RunRecord(mode, 0)
    node, _ = build(mode, "DUFAKE0001", record)
    assert_not_bypassed(node.risk_engine, f"{identity}/{mode}")
