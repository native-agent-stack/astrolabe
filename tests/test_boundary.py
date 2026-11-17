"""The lane refuses to start unless every paper check in astrolabe/boundary.py holds.

Ported from meridian@014f706d:tests/test_boundary.py. Changed: paths come from a ``Settings``
built on ``tmp_path`` in place of patched module constants; the port and the client ids come
from ``astrolabe.ib_ids``; fill mode's clock is ``ibkr_paper.fill_session`` (the calendar's own
cases are in tests/test_market_hours.py); new cases for check 5 (exclusive use) on synthetic
``/proc`` trees and for the reviewed-code line.
"""

import io
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_gateway import FakeGateway

from astrolabe import boundary, ib_ids, ibkr_paper, market_hours
from astrolabe import settings as settings_module
from astrolabe.settings import Settings

# Fake ids. They have letters between prefix and digits, so they never look like a real id
# to tests/test_public_tree.py.
PAPER = "DUFAKE0001"
OTHER = "UTEST00002"  # the form of a real-money account
NOT_LISTED = "DUFAKE0003"  # the paper form, but not on the allowlist
NOT_PAPER_FORM = [
    OTHER,
    "FTEST0003",
    "ITEST0004",
    "XTEST0005",
    "UDTEST0006",  # a D, but not first
    "DUFAKE0007X",  # the paper form, then a trailing letter
    "DTEST",
    "dufake0001",
    "D",
    "0001",
]
CLEAN = {
    "instrument_open_orders": 0,
    "instrument_open_positions": 0,
    "account_open_orders": 0,
    "account_open_positions": 0,
}
JTS_PAPER = "[IBGateway]\nApiOnly=true\n\n[Logon]\nLocale=en\ntradingMode=p\n"
# The Gateway's live value, kept out of the source text as a literal so that the paper-only scan
# (tests/test_paper_only.py), which fails on the live form anywhere, does not flag these tests.
LIVE = "l"
JTS_LIVE = f"[Logon]\ntradingMode={LIVE}\n"
LISTEN, ESTABLISHED, CLOSE_WAIT = "0A", "01", "08"
PORT = ib_ids.PAPER_PORT
WITH_AND_WITHOUT_A_CONTRACT = pytest.mark.parametrize(
    "listed", [None, (PAPER,)], ids=["no-contract", "contract"]
)


def contract_dict(**overrides: object) -> dict:
    contract: dict = {
        "schema": "astrolabe.boundary/1",
        "mode": "paper",
        "ibkr": {
            "gateway_host": "127.0.0.1",
            "gateway_port": PORT,
            "paper_accounts_sha256": [boundary.account_hash(PAPER)],
        },
    }
    for key, value in overrides.items():
        if key in contract["ibkr"]:
            contract["ibkr"][key] = value
        else:
            contract[key] = value
    return contract


def write_contract(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "boundary.contract.json"
    path.write_text(json.dumps(contract_dict(**overrides)), encoding="utf-8")
    return path


def bounds(tmp_path: Path) -> boundary.IbkrBoundary:
    return boundary.load_contract(write_contract(tmp_path))


def no_contract(tmp_path: Path) -> boundary.IbkrBoundary:
    return boundary.load_contract(tmp_path / "absent.json")


def assert_no_account_in(text: str, *accounts: str) -> None:
    for account in accounts:
        assert account not in text
        assert boundary.account_hash(account) not in text


# --- check 3: the account, and check 4: the optional allowlist ----------------------------------


def test_without_a_contract_a_paper_form_account_is_returned(tmp_path):
    assert boundary.allowed_account([PAPER], no_contract(tmp_path)) == PAPER


@pytest.mark.parametrize("account", NOT_PAPER_FORM)
def test_an_account_not_of_the_paper_form_is_refused_without_a_contract(tmp_path, account):
    with pytest.raises(boundary.BoundaryError, match="not of the paper form"):
        boundary.allowed_account([account], no_contract(tmp_path))


@pytest.mark.parametrize("account", NOT_PAPER_FORM)
def test_an_account_not_of_the_paper_form_is_refused_even_if_its_hash_is_listed(tmp_path, account):
    listed = boundary.load_contract(
        write_contract(tmp_path, paper_accounts_sha256=[boundary.account_hash(account)])
    )
    with pytest.raises(boundary.BoundaryError, match="not of the paper form"):
        boundary.allowed_account([account], listed)


def test_allowlisted_account_is_returned(tmp_path):
    assert boundary.allowed_account([PAPER], bounds(tmp_path)) == PAPER


def test_surrounding_whitespace_is_ignored_for_the_check_and_the_result(tmp_path):
    assert boundary.allowed_account([f" {PAPER}\n"], bounds(tmp_path)) == PAPER
    assert boundary.account_hash(f" {PAPER}\n") == boundary.account_hash(PAPER)


def test_with_a_contract_an_account_not_on_its_allowlist_is_refused(tmp_path):
    with pytest.raises(boundary.BoundaryError, match="not on the paper allowlist"):
        boundary.allowed_account([NOT_LISTED], bounds(tmp_path))


@pytest.mark.parametrize("reported", [[], [""], [" "], [PAPER, PAPER], [PAPER, NOT_LISTED]])
def test_anything_but_exactly_one_account_is_refused(tmp_path, reported):
    for b in (no_contract(tmp_path), bounds(tmp_path)):
        with pytest.raises(boundary.BoundaryError):
            boundary.allowed_account(reported, b)


def test_an_account_with_a_hyphen_is_refused_even_if_its_hash_is_listed(tmp_path):
    odd = f"IB-{PAPER}"  # the engine's adapter would read this as a different account
    listed = boundary.load_contract(
        write_contract(tmp_path, paper_accounts_sha256=[boundary.account_hash(odd)])
    )
    with pytest.raises(boundary.BoundaryError):
        boundary.allowed_account([odd], listed)


@pytest.mark.parametrize("account", [OTHER, NOT_LISTED])
def test_refusal_message_does_not_contain_the_account(tmp_path, account):
    with pytest.raises(boundary.BoundaryError) as refused:
        boundary.allowed_account([account], bounds(tmp_path))
    assert_no_account_in(str(refused.value), account)


# --- the optional contract -------------------------------------------------------------------


def test_no_contract_means_no_allowlist_and_the_fixed_target(tmp_path):
    b = no_contract(tmp_path)
    assert b.paper_accounts_sha256 is None
    assert (b.host, b.port) == ("127.0.0.1", PORT)


def test_a_dangling_contract_link_is_refused_not_ignored(tmp_path):
    path = tmp_path / "boundary.contract.json"
    os.symlink(tmp_path / "nowhere.json", path)
    with pytest.raises(boundary.BoundaryError):
        boundary.load_contract(path)


def test_a_contract_that_is_a_directory_is_refused(tmp_path):
    path = tmp_path / "boundary.contract.json"
    path.mkdir()
    with pytest.raises(boundary.BoundaryError):
        boundary.load_contract(path)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can search any folder")
def test_a_contract_folder_that_cannot_be_searched_is_refused_not_skipped(tmp_path):
    folder = tmp_path / "risk"
    folder.mkdir()
    write_contract(folder)
    folder.chmod(0)
    try:
        with pytest.raises(boundary.BoundaryError):
            boundary.load_contract(folder / "boundary.contract.json")
    finally:
        folder.chmod(0o700)


def test_a_file_where_the_contract_folder_should_be_is_refused(tmp_path):
    (tmp_path / "risk").write_text("not a folder", encoding="utf-8")
    with pytest.raises(boundary.BoundaryError):
        boundary.load_contract(tmp_path / "risk" / "boundary.contract.json")


def test_a_broken_link_where_the_contract_folder_should_be_is_refused(tmp_path):
    os.symlink(tmp_path / "missing-folder", tmp_path / "risk")
    with pytest.raises(boundary.BoundaryError, match="broken link"):
        boundary.load_contract(tmp_path / "risk" / "boundary.contract.json")


def test_a_link_to_a_real_folder_without_a_contract_means_no_allowlist(tmp_path):
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "risk")
    b = boundary.load_contract(tmp_path / "risk" / "boundary.contract.json")
    assert b.paper_accounts_sha256 is None


def test_no_contract_folder_at_all_means_no_allowlist(tmp_path):
    b = boundary.load_contract(tmp_path / "risk" / "boundary.contract.json")
    assert b.paper_accounts_sha256 is None


def test_malformed_contract_is_refused(tmp_path):
    path = tmp_path / "boundary.contract.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(boundary.BoundaryError):
        boundary.load_contract(path)


def test_a_contract_that_states_a_key_twice_is_refused(tmp_path):
    text = json.dumps(contract_dict())
    # A second "mode" after the first: a last-one-wins parser would read "paper".
    doubled = text.replace('"mode": "paper"', '"mode": "live", "mode": "paper"')
    assert doubled != text
    path = tmp_path / "boundary.contract.json"
    path.write_text(doubled, encoding="utf-8")
    with pytest.raises(boundary.BoundaryError):
        boundary.load_contract(path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "something-else"},
        {"mode": "live"},
        {"mode": "PAPER"},
        {"ibkr": None},
        {"gateway_host": "192.168.1.10"},
        {"gateway_host": "0.0.0.0"},
        {"gateway_host": "localhost"},
        {"gateway_host": ["127.0.0.1"]},
        {"gateway_host": {"127.0.0.1": 1}},
        {"gateway_port": PORT + 1},
        {"gateway_port": str(PORT)},
        {"gateway_port": float(PORT)},
        {"gateway_port": True},
        {"paper_accounts_sha256": []},
        {"paper_accounts_sha256": [PAPER]},
        {"paper_accounts_sha256": "*"},
        {"paper_accounts_sha256": {boundary.account_hash(PAPER): True}},
        {"paper_accounts_sha256": [boundary.account_hash(PAPER).upper()]},
    ],
)
def test_contract_outside_the_paper_boundary_is_refused(tmp_path, overrides):
    with pytest.raises(boundary.BoundaryError):
        boundary.load_contract(write_contract(tmp_path, **overrides))


def test_the_fixed_target_and_the_gateway_paths(monkeypatch, tmp_path):
    assert frozenset({ib_ids.PAPER_PORT}) == boundary.PAPER_PORTS
    assert boundary.PAPER_PORT == ib_ids.PAPER_PORT
    assert boundary.GATEWAY_HOST == ib_ids.HOST == "127.0.0.1"
    assert boundary.CONTRACT_PROBE_CLIENT_ID == ib_ids.CONTRACT_PROBE_CLIENT_ID
    assert boundary.PAPER_TRADING_MODE == "p"
    assert boundary.SCHEMA == "astrolabe.boundary/1"
    # No path is a module constant: every one comes from Settings.
    for name in ("REPO_ROOT", "CONTRACT_PATH", "JTS_INI", "GATEWAY_DIR", "PROC"):
        assert not hasattr(boundary, name)
    monkeypatch.setattr(settings_module, "_user_home", lambda: tmp_path)
    current = Settings.current()
    assert current.jts_ini == tmp_path / "Jts" / "jts.ini"
    assert current.gateway_dir == tmp_path / "Jts" / "ibgateway"
    # The allowlist is read from exactly this place, which git ignores (tests/test_public_tree.py).
    assert current.contract_path == current.repo_root / "risk" / "boundary.contract.json"


def test_checks_read_the_settings_they_are_given(tmp_path, gw):
    gateway_dir, java = gw
    settings = Settings(
        repo_root=tmp_path / "repo",
        jts_dir=gateway_dir.parent,
        state_dir=tmp_path / "state",
        proc=fake_proc(tmp_path, [(java, [11])], tcp=[(PORT, LISTEN, 11)]),
    )
    settings.jts_dir.mkdir(parents=True)
    write_jts(settings.jts_dir, JTS_PAPER)
    bounds = boundary.local_paper_checks(settings)
    assert bounds.paper_accounts_sha256 is None
    assert bounds.gateway_bind == "all interfaces"
    settings.contract_path.parent.mkdir(parents=True)
    settings.contract_path.write_text(json.dumps(contract_dict()), encoding="utf-8")
    assert boundary.local_paper_checks(settings).paper_accounts_sha256 == {
        boundary.account_hash(PAPER)
    }


# --- check 1: the Gateway's jts.ini says Paper Trading ---------------------------------------


def write_jts(tmp_path: Path, text: str | bytes) -> Path:
    path = tmp_path / "jts.ini"
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "text",
    [
        JTS_PAPER,
        "[Logon]\ntradingMode =  p  \n",  # spaces around the value
        "[DEFAULT]\nLocale=en\n[Logon]\ntradingMode=p\n",  # a [DEFAULT] header is harmless
    ],
)
def test_a_paper_login_passes(tmp_path, text):
    boundary.require_paper_login(write_jts(tmp_path, text))


@pytest.mark.parametrize(
    "text",
    [
        JTS_LIVE,
        "[Logon]\ntradingMode=P\n",
        "[Logon]\ntradingMode=\n",
        "[Logon]\ntradingMode=paper\n",
        "[Logon]\ntradingMode=p ; live\n",
        "[Logon]\nLocale=en\n",  # no key
        "[IBGateway]\ntradingMode=p\n",  # the key, but in another section
        "[DEFAULT]\ntradingMode=p\n[Logon]\nLocale=en\n",  # the key only where it is inherited
        "[logon]\ntradingMode=p\n",  # another section name
        f"[Logon]\ntradingMode=p\nTradingMode={LIVE}\n",  # the key twice, paper first
        f"[Logon]\ntradingMode={LIVE}\ntradingMode=p\n",  # the key twice, paper last
        "[Logon]\ntradingMode=p\n[Logon]\nLocale=en\n",  # the section twice
        f"[Logon]\ntradingMode=p\n[LOGON]\ntradingMode={LIVE}\n",  # the section twice, other case
        "[Logon]\ntradingMode=p\n[LOGON]\nLocale=en\n",  # a look-alike section, no key in it
        "[ Logon ]\ntradingMode=p\n",  # a look-alike section name
        f"[Logon]\ntradingMode=p\n[IBGateway]\ntradingMode={LIVE}\n",  # the key elsewhere too
        "tradingMode=p\n",  # no section
        "",
        "not a settings file\n",
    ],
)
def test_anything_but_a_clear_paper_login_is_refused(tmp_path, text):
    with pytest.raises(boundary.BoundaryError):
        boundary.require_paper_login(write_jts(tmp_path, text))


def test_a_settings_file_that_is_not_utf8_is_refused(tmp_path):
    with pytest.raises(boundary.BoundaryError):
        boundary.require_paper_login(write_jts(tmp_path, b"[Logon]\ntradingMode=p\n\xff\xfe\n"))


def test_no_settings_file_is_refused(tmp_path):
    with pytest.raises(boundary.BoundaryError):
        boundary.require_paper_login(tmp_path / "absent.ini")


# --- check 2: the Gateway listens on the paper port only (a stand-in /proc) -------------------

TCP_HEAD = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  "
    "timeout inode\n"
)


def tcp_row(
    n: int, port: int, state: str, inode: int, local: str | None = None, v6: bool = False
) -> str:
    """One /proc/net/tcp or tcp6 row; ``local`` is the local address in the kernel's hex form
    (default: every interface)."""
    zero = "0" * (32 if v6 else 8)
    return (
        f"{n:4d}: {local or zero}:{port:04X} {zero}:0000 {state} 00000000:00000000 00:00000000 "
        f"00000000  1000        0 {inode} 1 0000000000000000 100 0 0 10 0\n"
    )


def fake_proc(root: Path, processes, tcp=(), tcp6=None, start: int = 1000) -> Path:
    """processes: [(exe, [socket inodes])]; tcp, tcp6: [(port, state, inode)], None = absent.

    Process numbers start at 100, in the order given; each process started at ``start`` plus
    its number (clock ticks, field 22 of /proc/<pid>/stat).
    """
    proc = root / "proc"
    (proc / "net").mkdir(parents=True)
    for name, rows, v6 in (("tcp", tcp, False), ("tcp6", tcp6, True)):
        if rows is not None:
            body = "".join(tcp_row(i, *row, v6=v6) for i, row in enumerate(rows))
            (proc / "net" / name).write_text(TCP_HEAD + body, encoding="ascii")
    (proc / "self").mkdir()  # not a process number: skipped
    for pid, (exe, inodes) in enumerate(processes, start=100):
        fd = proc / str(pid) / "fd"
        fd.mkdir(parents=True)
        os.symlink(exe, proc / str(pid) / "exe")
        # The name holds a space and a ")" on purpose: the parser must split after the last ")".
        stat_fields = ["S"] + ["0"] * 18 + [str(start + pid), "0", "0"]
        (proc / str(pid) / "stat").write_text(f"{pid} (java) x) {' '.join(stat_fields)}\n")
        for n, inode in enumerate(inodes):
            os.symlink(f"socket:[{inode}]", fd / str(n))
        os.symlink("/dev/null", fd / str(len(inodes)))  # an open file that is not a socket
    return proc


@pytest.fixture
def gw(tmp_path):
    gateway_dir = (tmp_path / "Jts" / "ibgateway").resolve()
    return gateway_dir, str(gateway_dir / "1050" / "jre" / "bin" / "java")


def test_the_gateway_identity_names_its_process_start_and_socket(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(tmp_path, [(java, [11])], tcp=[(PORT, LISTEN, 11)], start=5000)
    seen = boundary.gateway_listening(proc, gateway_dir)
    assert seen.ports == {PORT}
    assert seen.identity == {
        "process 100 started 5100",
        f"socket 11 port {PORT} bound to all interfaces",
    }
    assert seen.bind == {"all interfaces"}


def test_a_restarted_gateway_has_another_identity(tmp_path, gw):
    gateway_dir, java = gw
    first = boundary.gateway_listening(
        fake_proc(tmp_path / "a", [(java, [11])], tcp=[(PORT, LISTEN, 11)], start=5000), gateway_dir
    )
    later = boundary.gateway_listening(
        fake_proc(tmp_path / "b", [(java, [12])], tcp=[(PORT, LISTEN, 12)], start=9000), gateway_dir
    )
    assert first.ports == later.ports == {PORT}
    assert first.identity != later.identity


@pytest.mark.parametrize(
    ("address", "scope"),
    [
        ("00000000", "all interfaces"),  # 0.0.0.0
        ("0100007F", "loopback"),  # 127.0.0.1, written little-endian
        ("0A01A8C0", "a specific address"),  # 192.168.1.10
        ("0" * 32, "all interfaces"),  # ::
        ("00000000000000000000000001000000", "loopback"),  # ::1
        ("0000000000000000FFFF00000100007F", "loopback"),  # ::ffff:127.0.0.1
        ("0000000000000000FFFF00000A01A8C0", "a specific address"),  # ::ffff:192.168.1.10
        ("0000000000000000FFFF000000000000", "all interfaces"),  # ::ffff:0.0.0.0
        ("000080FE00000000000000000000000A", "a specific address"),  # a link-local address
    ],
)
def test_the_bind_scope_of_a_kernel_address(address, scope):
    assert boundary.bind_scope(address) == scope
    assert boundary.bind_scope(address.lower()) == scope


@pytest.mark.parametrize(
    "address", ["", "0", "0000", "0" * 16, "0" * 31, "0100007", "XYZ0007F", "01000" * 5]
)
def test_an_address_of_another_shape_is_refused(address):
    with pytest.raises(ValueError):
        boundary.bind_scope(address)


def test_the_bind_scope_is_seen_and_kept_in_the_identity(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(
        tmp_path,
        [(java, [11, 12])],
        tcp=[(PORT, LISTEN, 11, "0100007F")],
        tcp6=[(PORT, LISTEN, 12)],
    )
    seen = boundary.gateway_listening(proc, gateway_dir)
    assert seen.bind == {"loopback", "all interfaces"}
    assert f"socket 11 port {PORT} bound to loopback" in seen.identity
    assert not any("7F" in part or "127" in part for part in seen.identity)  # no address kept


@pytest.mark.parametrize("bind", ["all interfaces", "a specific address", "", "all, loopback"])
def test_the_bind_line_asks_the_owner_to_check_the_box_unless_the_port_is_loopback_only(bind):
    note = ibkr_paper.bind_note(bind)
    assert '"Allow connections from localhost only"' in note
    assert "cannot read it" in note


def test_a_loopback_only_bind_needs_no_box():
    assert ibkr_paper.bind_note("loopback") == "The port is bound to loopback only."


def test_a_gateway_process_whose_start_cannot_be_read_is_refused(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(tmp_path, [(java, [11])], tcp=[(PORT, LISTEN, 11)])
    (proc / "100" / "stat").unlink()
    with pytest.raises(boundary.BoundaryError, match="start time"):
        boundary.gateway_listening(proc, gateway_dir)


def test_the_gateway_on_the_paper_port_is_seen(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(tmp_path, [(java, [11])], tcp=[(PORT, LISTEN, 11)])
    assert boundary.gateway_listen_ports(proc, gateway_dir) == {PORT}


def test_an_ipv6_listener_counts_too(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(
        tmp_path, [(java, [11, 12])], tcp=[], tcp6=[(PORT, LISTEN, 11), (5000, LISTEN, 12)]
    )
    assert boundary.gateway_listen_ports(proc, gateway_dir) == {PORT, 5000}


def test_a_connection_that_is_not_listening_is_not_a_port(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(tmp_path, [(java, [11])], tcp=[(PORT, ESTABLISHED, 11)])
    assert boundary.gateway_listen_ports(proc, gateway_dir) == frozenset()


def test_another_program_on_the_paper_port_is_not_the_gateway(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(tmp_path, [(java, []), ("/usr/bin/python3", [11])], tcp=[(PORT, LISTEN, 11)])
    assert boundary.gateway_listen_ports(proc, gateway_dir) == frozenset()


def test_ports_of_every_gateway_process_are_counted(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(
        tmp_path, [(java, [11]), (java, [12])], tcp=[(PORT, LISTEN, 11), (5000, LISTEN, 12)]
    )
    assert boundary.gateway_listen_ports(proc, gateway_dir) == {PORT, 5000}


def test_a_gateway_process_whose_sockets_cannot_be_read_is_refused(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(
        tmp_path, [(java, [11]), (java, [12])], tcp=[(PORT, LISTEN, 11), (5000, LISTEN, 12)]
    )
    shutil.rmtree(proc / "101" / "fd")
    with pytest.raises(boundary.BoundaryError, match="open sockets"):
        boundary.gateway_listen_ports(proc, gateway_dir)


@pytest.mark.parametrize("exe", ["/usr/bin/python3", "{dir}-old/1050/jre/bin/java", "{dir}"])
def test_no_gateway_process_is_refused(tmp_path, gw, exe):
    gateway_dir, _ = gw
    proc = fake_proc(tmp_path, [(exe.format(dir=gateway_dir), [11])], tcp=[(PORT, LISTEN, 11)])
    with pytest.raises(boundary.BoundaryError, match="not running"):
        boundary.gateway_listen_ports(proc, gateway_dir)


def test_an_unreadable_socket_table_is_refused(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(tmp_path, [(java, [11])], tcp=None)
    with pytest.raises(boundary.BoundaryError):
        boundary.gateway_listen_ports(proc, gateway_dir)


def test_a_malformed_socket_table_is_refused(tmp_path, gw):
    gateway_dir, java = gw
    proc = fake_proc(tmp_path, [(java, [11])], tcp=[(PORT, LISTEN, 11)])
    (proc / "net" / "tcp").write_text(TCP_HEAD + "   0: garbage\n", encoding="ascii")
    with pytest.raises(boundary.BoundaryError):
        boundary.gateway_listen_ports(proc, gateway_dir)


@pytest.mark.parametrize(
    ("listening", "passes"),
    [({PORT}, True), ({PORT, 5000}, False), ({5000}, False), (set(), False)],
)
def test_the_lane_needs_the_gateway_on_the_paper_port_only(
    tmp_path, gw, monkeypatch, listening, passes
):
    gateway_dir, java = gw
    ports = sorted(listening)
    inodes = list(range(20, 20 + len(ports)))
    rows = [(port, LISTEN, inode) for port, inode in zip(ports, inodes, strict=True)]
    settings = Settings(
        repo_root=tmp_path / "repo",
        jts_dir=gateway_dir.parent,
        state_dir=tmp_path / "state",
        proc=fake_proc(tmp_path, [(java, inodes)], tcp=rows),
    )
    if passes:
        boundary.require_gateway_on_paper_port_only(settings)
    else:
        with pytest.raises(boundary.BoundaryError):
            boundary.require_gateway_on_paper_port_only(settings)


# --- the lane, end to end against a stand-in Gateway -------------------------------------


def lane_settings(tmp_path: Path) -> Settings:
    """The lane's paths under ``tmp_path``: the checkout, ~/Jts, the state folder, and a
    synthetic /proc with empty socket tables and no process."""
    return Settings(
        repo_root=tmp_path / "repo",
        jts_dir=tmp_path / "Jts",
        state_dir=tmp_path / "state",
        proc=fake_proc(tmp_path / "lane", [], tcp=[], tcp6=[]),
    )


def evidence_dir(tmp_path: Path) -> Path:
    return tmp_path / "state" / "ibkr-paper" / "evidence"


@pytest.fixture
def paper_state(tmp_path, monkeypatch):
    """The Gateway's local state on a paper login, with no contract. Tests change parts:

    ``state.jts`` (the settings file), ``state.extra_ports`` (more listening ports) and
    ``state.running`` (False: the Gateway process is gone). ``state.use(gateway)`` points the
    checks at a stand-in Gateway. ``state.settings`` holds every path the lane reads.
    """
    settings = lane_settings(tmp_path)
    settings.jts_dir.mkdir()
    state = SimpleNamespace(
        settings=settings,
        jts=write_jts(settings.jts_dir, JTS_PAPER),
        extra_ports=set(),
        running=True,
        identity={"process 100 started 5100"},  # change it to stand in for a Gateway restart
        use=None,
    )

    def use(gateway: FakeGateway) -> None:
        # The stand-in listens on a free port; make that the paper port for this test only.
        monkeypatch.setattr(boundary, "PAPER_PORT", gateway.port)
        monkeypatch.setattr(boundary, "PAPER_PORTS", frozenset({gateway.port}))

        def listening(proc=None, gateway_dir=None, settings=None) -> boundary.Listening:
            if not state.running:
                raise boundary.BoundaryError("the IB Gateway installed in (test) is not running")
            return boundary.Listening(
                ports=frozenset({gateway.port, *state.extra_ports}),
                identity=frozenset(state.identity),
                bind=frozenset({"all interfaces"}),  # the Gateway's normal bind
            )

        monkeypatch.setattr(boundary, "gateway_listening", listening)

    state.use = use
    return state


def allowlist(settings: Settings, gateway: FakeGateway, accounts) -> None:
    """Write the owner's contract where the lane reads it, for these accounts."""
    settings.contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract = contract_dict(
        gateway_port=gateway.port,
        paper_accounts_sha256=[boundary.account_hash(a) for a in accounts],
    )
    settings.contract_path.write_text(json.dumps(contract), encoding="utf-8")


SOURCE_COMMIT = "f" * 40
SESSION = market_hours.Session(
    open_utc=datetime(2026, 11, 17, 14, 30, tzinfo=UTC),
    close_utc=datetime(2026, 11, 17, 21, 0, tzinfo=UTC),
)


@pytest.fixture
def lane(tmp_path, monkeypatch, paper_state):
    """Start the lane against a stand-in Gateway; record whether the engine side was reached."""
    reached: list[str] = []

    def fake_run_node(mode, bounds, account, settings, **kwargs):
        reached.append(account)
        return 0

    monkeypatch.setattr(ibkr_paper, "_run_node", fake_run_node)
    # The reviewed-code check runs against a scratch repository in tests/test_run_record.py.
    monkeypatch.setattr(ibkr_paper, "reviewed_code", lambda repo_root: SOURCE_COMMIT)
    # Fill mode checks the clock; these tests run as if in a session unless they say not.
    monkeypatch.setattr(ibkr_paper, "fill_session", lambda now=None: SESSION)

    def start(gateway: FakeGateway, mode: str = "order", confirm: bool = True, listed=None) -> int:
        paper_state.use(gateway)
        if listed is not None:
            allowlist(paper_state.settings, gateway, listed)
        if confirm:  # stand in for the owner typing the word; the tests below leave it real
            monkeypatch.setattr(ibkr_paper, "owner_confirms", lambda mode="order": True)
        return ibkr_paper.main([mode], paper_state.settings)

    yield start, reached
    ibkr_paper._release_held()


def test_lane_starts_without_a_contract_when_every_paper_check_holds(lane, capsys):
    start, reached = lane
    with FakeGateway(PAPER) as gateway:
        assert start(gateway) == 0
    assert reached == [PAPER]
    out = capsys.readouterr()
    assert "Paper checks passed" in out.out
    assert_no_account_in(out.out + out.err, PAPER)


def test_lane_refuses_a_real_money_form_account(lane, capsys):
    start, reached = lane
    with FakeGateway(OTHER) as gateway:
        assert start(gateway) == 2
    assert reached == []  # the engine side was never entered
    out = capsys.readouterr()
    assert "REFUSED TO START" in out.err and "not of the paper form" in out.err
    assert_no_account_in(out.out + out.err, OTHER)


def test_lane_refuses_a_real_money_form_account_even_if_the_contract_lists_it(lane, capsys):
    start, reached = lane
    with FakeGateway(OTHER) as gateway:
        assert start(gateway, listed=(PAPER, OTHER)) == 2
    assert reached == []
    assert "not of the paper form" in capsys.readouterr().err


def test_lane_refuses_to_start_against_a_non_allowlisted_account(lane, capsys):
    start, reached = lane
    with FakeGateway(NOT_LISTED) as gateway:
        assert start(gateway, listed=(PAPER,)) == 2
    assert reached == []
    out = capsys.readouterr()
    assert "REFUSED TO START" in out.err and "not on the paper allowlist" in out.err
    assert_no_account_in(out.out + out.err, NOT_LISTED)


def test_lane_starts_with_an_allowlisted_account(lane, capsys):
    start, reached = lane
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, listed=(PAPER,)) == 0
    assert reached == [PAPER]
    out = capsys.readouterr()
    assert "on the owner's allowlist" in out.out
    assert_no_account_in(out.out + out.err, PAPER)


@WITH_AND_WITHOUT_A_CONTRACT
@pytest.mark.parametrize("jts_text", [JTS_LIVE, None], ids=["live", "no-file"])
def test_lane_refuses_unless_jts_says_paper_and_never_contacts_the_gateway(
    lane, paper_state, capsys, jts_text, listed
):
    start, reached = lane
    if jts_text is None:
        paper_state.jts.unlink()
    else:
        paper_state.jts.write_text(jts_text, encoding="utf-8")
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, listed=listed) == 2
        assert gateway.sessions == 0
    assert reached == []
    assert "REFUSED TO START" in capsys.readouterr().err


@WITH_AND_WITHOUT_A_CONTRACT
def test_lane_refuses_when_the_gateway_also_listens_on_another_port(lane, paper_state, listed):
    start, reached = lane
    paper_state.extra_ports.add(5000)
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, listed=listed) == 2
        assert gateway.sessions == 0
    assert reached == []


@WITH_AND_WITHOUT_A_CONTRACT
def test_lane_refuses_when_the_gateway_process_is_not_running(lane, paper_state, capsys, listed):
    start, reached = lane
    paper_state.running = False
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, listed=listed) == 2
        assert gateway.sessions == 0
    assert reached == []
    assert "not running" in capsys.readouterr().err


def test_lane_refuses_with_a_malformed_contract(lane, paper_state):
    start, reached = lane
    bad = paper_state.settings.contract_path
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")  # present but malformed: refuse
    with FakeGateway(PAPER) as gateway:
        assert start(gateway) == 2
        assert gateway.sessions == 0
    assert reached == []


@pytest.mark.parametrize("accounts", [f"{PAPER},{NOT_LISTED}", ""])
def test_lane_refuses_unless_the_gateway_reports_exactly_one_account(lane, capsys, accounts):
    start, reached = lane
    with FakeGateway(accounts) as gateway:
        assert start(gateway) == 2
    assert reached == []
    out = capsys.readouterr()
    assert_no_account_in(out.out + out.err, PAPER, NOT_LISTED)


def test_lane_refuses_when_no_gateway_is_listening(lane):
    start, reached = lane
    with FakeGateway(PAPER) as gateway:
        pass  # closed again: nothing listens on that port now
    assert start(gateway) == 2
    assert reached == []


# --- the owner's optional contract command ------------------------------------------------------


@pytest.fixture
def contract_cmd(monkeypatch, paper_state):
    """Run ``python -m astrolabe.boundary contract`` against a stand-in Gateway."""
    asked: list[bool] = []

    def run(gateway: FakeGateway, typed: str = "PAPER", at_terminal: bool = True) -> int:
        paper_state.use(gateway)
        monkeypatch.setattr(boundary, "_owner_at_terminal", lambda: at_terminal)

        def answer() -> str:
            asked.append(True)
            return typed + "\n"

        monkeypatch.setattr(boundary, "_read_answer", answer)
        return boundary._main(["contract"], paper_state.settings)

    return run, asked


def test_contract_command_prints_a_contract_that_lets_only_that_account_through(
    contract_cmd, capsys, tmp_path
):
    run, asked = contract_cmd
    with FakeGateway(PAPER) as gateway:
        assert run(gateway) == 0
    out = capsys.readouterr().out
    assert PAPER not in out  # only its hash goes into the file
    path = tmp_path / "printed.contract.json"
    path.write_text(out, encoding="utf-8")
    loaded = boundary.load_contract(path)
    assert loaded.paper_accounts_sha256 == frozenset({boundary.account_hash(PAPER)})
    assert loaded.port == gateway.port and loaded.host == "127.0.0.1"
    assert boundary.allowed_account([PAPER], loaded) == PAPER
    with pytest.raises(boundary.BoundaryError):
        boundary.allowed_account([NOT_LISTED], loaded)


def test_the_contract_text_names_the_real_paper_port_and_loopback(tmp_path):
    path = tmp_path / "boundary.contract.json"
    path.write_text(boundary.contract_text(PAPER), encoding="utf-8")
    loaded = boundary.load_contract(path)
    assert (loaded.host, loaded.port) == ("127.0.0.1", PORT)
    assert PAPER not in path.read_text(encoding="utf-8")


class Stream(io.StringIO):
    """A text stream that says whether it is a terminal."""

    def __init__(self, tty: bool, text: str = "") -> None:
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.mark.parametrize(
    ("stdin_tty", "stderr_tty"), [(False, False), (True, False), (False, True), (True, True)]
)
def test_the_contract_command_needs_a_terminal_on_stdin_and_stderr(
    monkeypatch, paper_state, stdin_tty, stderr_tty
):
    # The real guard and the real answer reader run here; only the streams are stand-ins.
    out, err = Stream(False), Stream(stderr_tty)  # stdout redirected, as documented
    monkeypatch.setattr(sys, "stdin", Stream(stdin_tty, "PAPER\n"))
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    with FakeGateway(PAPER) as gateway:
        paper_state.use(gateway)
        code = boundary._main(["contract"], paper_state.settings)
        sessions = gateway.sessions
    if stdin_tty and stderr_tty:
        assert code == 0 and sessions == 1
        assert boundary.account_hash(PAPER) in out.getvalue()
    else:
        assert code == 2 and sessions == 0
        assert out.getvalue() == ""
        assert PAPER not in err.getvalue()


def test_contract_command_refuses_without_a_terminal_and_does_not_ask_the_gateway(contract_cmd):
    run, asked = contract_cmd
    with FakeGateway(PAPER) as gateway:
        assert run(gateway, at_terminal=False) == 2
        assert gateway.sessions == 0
    assert asked == []


@pytest.mark.parametrize("change", ["live", "extra-port", "not-running"])
def test_contract_command_refuses_unless_the_local_paper_checks_hold(
    contract_cmd, paper_state, capsys, change
):
    run, asked = contract_cmd
    if change == "live":
        paper_state.jts.write_text(JTS_LIVE, encoding="utf-8")
    elif change == "extra-port":
        paper_state.extra_ports.add(5000)
    else:
        paper_state.running = False
    with FakeGateway(PAPER) as gateway:
        assert run(gateway) == 2
        assert gateway.sessions == 0
    assert asked == [] and capsys.readouterr().out == ""


@pytest.mark.parametrize("typed", ["yes", "paper", ""])
def test_contract_command_prints_nothing_unless_the_owner_types_the_word(
    contract_cmd, capsys, typed
):
    run, asked = contract_cmd
    with FakeGateway(PAPER) as gateway:
        assert run(gateway, typed=typed) == 2
    assert capsys.readouterr().out == ""
    assert asked == [True]


@pytest.mark.parametrize("accounts", [OTHER, f"{PAPER},{NOT_LISTED}", ""])
def test_contract_command_refuses_before_asking_when_the_gateway_report_is_wrong(
    contract_cmd, capsys, accounts
):
    run, asked = contract_cmd
    with FakeGateway(accounts) as gateway:
        assert run(gateway) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert asked == []
    assert_no_account_in(captured.err, OTHER, NOT_LISTED)


def test_contract_command_refuses_when_no_gateway_is_listening(contract_cmd, capsys):
    run, asked = contract_cmd
    with FakeGateway(PAPER) as gateway:
        pass  # closed again: nothing listens on that port now
    assert run(gateway) == 2
    assert capsys.readouterr().out == ""
    assert asked == []


# --- order and fill mode need the owner at a terminal -----------------------------------------

CONFIRMED = pytest.mark.parametrize("mode", ["order", "fill"])


@CONFIRMED
def test_a_placing_mode_refuses_without_a_terminal(lane, capsys, mode):
    start, reached = lane
    with FakeGateway(PAPER) as gateway:
        # The real check runs: under pytest neither stdin nor stdout is a terminal.
        assert start(gateway, mode, confirm=False) == 2
    assert reached == []
    out = capsys.readouterr()
    assert "needs the owner at a terminal" in out.err
    assert_no_account_in(out.out + out.err, PAPER)


@CONFIRMED
@pytest.mark.parametrize(("typed", "exit_code"), [("yes", 2), ("paper", 2), ("", 2), ("PAPER", 0)])
def test_a_placing_mode_goes_on_only_when_the_owner_types_the_word(
    lane, monkeypatch, typed, exit_code, mode
):
    start, reached = lane
    prompts: list[str] = []
    monkeypatch.setattr(ibkr_paper, "_at_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or typed)
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, mode, confirm=False) == exit_code
    assert reached == ([PAPER] if exit_code == 0 else [])
    (prompt,) = prompts  # the owner is told what this mode places
    assert ("SELL" in prompt) == (mode == "fill") and "PAPER account" in prompt


def confirm_after(change):
    """An owner_confirms stand-in: something changes while the prompt waits, then PAPER."""

    def confirms(mode: str = "order") -> bool:
        change()
        return True

    return confirms


@CONFIRMED
@pytest.mark.parametrize("change", ["live", "extra-port", "restarted", "other-account"])
def test_a_placing_mode_checks_everything_again_after_the_confirmation(
    lane, paper_state, monkeypatch, capsys, change, mode
):
    start, reached = lane
    later = NOT_LISTED if change == "other-account" else None
    with FakeGateway(PAPER, later_accounts=later) as gateway:
        changes = {
            "live": lambda: paper_state.jts.write_text(JTS_LIVE, encoding="utf-8"),
            "extra-port": lambda: paper_state.extra_ports.add(5000),
            "restarted": lambda: paper_state.identity.symmetric_difference_update(
                {"process 100 started 5100", "process 200 started 9200"}
            ),
            "other-account": lambda: None,  # the Gateway reports another account the 2nd time
        }
        monkeypatch.setattr(ibkr_paper, "owner_confirms", confirm_after(changes[change]))
        assert start(gateway, mode, confirm=False) == 2
    assert reached == []  # the engine side was never entered
    assert "REFUSED TO START" in capsys.readouterr().err


@CONFIRMED
def test_a_placing_mode_goes_on_when_nothing_changed_during_the_confirmation(
    lane, monkeypatch, mode
):
    start, reached = lane
    monkeypatch.setattr(ibkr_paper, "owner_confirms", confirm_after(lambda: None))
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, mode, confirm=False) == 0
        assert gateway.sessions == 2  # asked before the prompt and again after it
    assert reached == [PAPER]


# --- fill mode runs only inside an XNYS session ----------------------------------------------


def test_fill_mode_refuses_outside_a_session_before_asking_the_gateway(lane, monkeypatch, capsys):
    start, reached = lane
    monkeypatch.setattr(ibkr_paper, "fill_session", lambda now=None: None)
    monkeypatch.setattr(ibkr_paper, "owner_confirms", lambda mode="order": pytest.fail("asked"))
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, "fill", confirm=False) == 2
        assert gateway.sessions == 0
    assert reached == []
    assert "regular XNYS session" in capsys.readouterr().err


def test_fill_mode_refuses_when_the_calendar_cannot_place_the_moment(lane, monkeypatch, capsys):
    start, reached = lane

    def uncovered(now=None):
        raise market_hours.MarketHoursError("the XNYS calendar covers 2006-11-17 to 2027-11-17")

    monkeypatch.setattr(ibkr_paper, "fill_session", uncovered)
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, "fill", confirm=False) == 2
        assert gateway.sessions == 0
    assert reached == []
    assert "calendar cannot place this moment" in capsys.readouterr().err


def test_fill_mode_refuses_when_the_session_ends_while_the_prompt_waits(lane, monkeypatch, capsys):
    start, reached = lane
    confirmed: list[bool] = []
    # In the session until the owner has answered the prompt, out of it after.
    monkeypatch.setattr(ibkr_paper, "fill_session", lambda now=None: None if confirmed else SESSION)
    monkeypatch.setattr(
        ibkr_paper, "owner_confirms", lambda mode="order": confirmed.append(True) or True
    )
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, "fill", confirm=False) == 2
        assert gateway.sessions == 1  # asked before the prompt only
    assert reached == [] and confirmed == [True]
    assert "after the confirmation" in capsys.readouterr().err


def test_order_and_restart_mode_do_not_look_at_the_clock(lane, monkeypatch):
    start, reached = lane
    monkeypatch.setattr(ibkr_paper, "fill_session", lambda now=None: pytest.fail("clock"))
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, "order") == 0
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, "restart") == 0
    assert reached == [PAPER, PAPER]


def test_the_lanes_clock_is_the_xnys_calendar(monkeypatch):
    asked: list[datetime] = []
    monkeypatch.setattr(market_hours, "fill_session", lambda now: asked.append(now) or SESSION)
    assert ibkr_paper.fill_session() is SESSION
    (now,) = asked
    assert now.tzinfo is not None  # the lane asks with an aware UTC moment
    moment = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
    assert ibkr_paper.fill_session(moment) is SESSION and asked[-1] == moment


def test_the_fill_run_records_its_session(lane, monkeypatch):
    start, _ = lane
    seen: dict = {}
    monkeypatch.setattr(
        ibkr_paper, "_run_node", lambda mode, bounds, account, settings, **kw: seen.update(kw) or 0
    )
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, "fill") == 0
    assert seen["session"] is SESSION
    assert seen["source_commit"] == SOURCE_COMMIT
    assert seen["exclusive_checks"] == 2


@pytest.mark.parametrize("listed", [None, (PAPER,)], ids=["no-contract", "contract"])
def test_every_start_says_whether_an_allowlist_is_used(lane, capsys, listed):
    start, _ = lane
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, "restart", listed=listed) == 0
    out = capsys.readouterr().out
    if listed is None:
        assert "Allowlist: none (no file at" in out
    else:
        assert "Allowlist: " in out and "none" not in out.split("Allowlist: ", 1)[1].split("\n")[0]


def test_restart_mode_needs_no_confirmation(lane, monkeypatch, capsys):
    start, reached = lane
    monkeypatch.setattr(
        ibkr_paper, "owner_confirms", lambda mode="order": pytest.fail("asked in restart")
    )
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, "restart", confirm=False) == 0
    assert reached == [PAPER]
    out = capsys.readouterr()
    assert_no_account_in(out.out + out.err, PAPER)


# --- the hand-off to the engine, and the checks after the run ---------------------------------


@pytest.fixture
def engine_stub(tmp_path, monkeypatch, paper_state):
    """Run the real _run_node with the engine replaced by a clean restart."""
    from astrolabe import ibkr_node

    seen: dict = {}
    during: list = []  # callables run in the middle of the stand-in run

    def fake_run(mode, account, record, settings=None):
        seen.update(mode=mode, account=account, port=record.data["gateway"]["port"])
        record.trader_started(**CLEAN)
        record.reconciled(instrument_orders_by_status={}, filled_venue_order_ids=[])
        for change in during:
            change()
        record.at_stop(**CLEAN)
        record.after_shutdown(**CLEAN, instrument_inflight_orders=0)

    monkeypatch.setattr(ibkr_node, "run", fake_run)
    monkeypatch.setattr(ibkr_paper, "reviewed_code", lambda repo_root: SOURCE_COMMIT)

    def start(gateway: FakeGateway, listed=None) -> int:
        paper_state.use(gateway)
        if listed is not None:
            allowlist(paper_state.settings, gateway, listed)
        return ibkr_paper.main(["restart"], paper_state.settings)

    yield start, seen, during
    ibkr_paper._release_held()


def local_records(tmp_path: Path) -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (tmp_path / "state" / "ibkr-paper" / "runs").iterdir()
    ]


def test_the_engine_gets_the_gated_mode_port_and_account(engine_stub, tmp_path, capsys):
    start, seen, _ = engine_stub
    with FakeGateway(PAPER) as gateway:
        assert start(gateway) == 0
        assert seen == {"mode": "restart", "port": gateway.port, "account": PAPER}
        assert gateway.sessions == 2  # asked before the run and again after it
    (evidence,) = evidence_dir(tmp_path).iterdir()
    text = evidence.read_text(encoding="utf-8")
    assert_no_account_in(text, PAPER)
    assert json.loads(text)["boundary"] == {
        "paper_checks_passed_before_run": True,
        "paper_checks_passed_after_run": True,
        "allowlist_contract_present": False,
    }
    # How the port was bound is in the record and on the terminal; no address is.
    assert json.loads(text)["gateway"] == {"port": gateway.port, "bind": "all interfaces"}
    out = capsys.readouterr()
    assert "PASS" in out.out and "bound to all interfaces" in out.out
    assert_no_account_in(out.out + out.err, PAPER)


def test_the_record_says_when_an_allowlist_was_used(engine_stub, tmp_path):
    start, _, _ = engine_stub
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, listed=(PAPER,)) == 0
    (evidence,) = evidence_dir(tmp_path).iterdir()
    assert json.loads(evidence.read_text(encoding="utf-8"))["boundary"][
        "allowlist_contract_present"
    ]


def test_no_evidence_when_the_gateway_reports_another_account_after_the_run(
    engine_stub, tmp_path, capsys
):
    start, seen, _ = engine_stub
    with FakeGateway(PAPER, later_accounts=NOT_LISTED) as gateway:
        assert start(gateway) == 1
    assert seen["account"] == PAPER
    assert not evidence_dir(tmp_path).exists()
    assert not (tmp_path / "evidence").exists()
    out = capsys.readouterr()
    assert "FAIL" in out.err
    assert_no_account_in(out.out + out.err, PAPER, NOT_LISTED)


def test_no_evidence_when_the_gateway_reports_no_account_after_the_run(engine_stub, tmp_path):
    start, _, _ = engine_stub
    with FakeGateway(PAPER, later_accounts="") as gateway:
        assert start(gateway) == 1
    assert not evidence_dir(tmp_path).exists()
    assert not (tmp_path / "evidence").exists()


def test_no_evidence_but_a_local_record_when_the_gateway_is_gone_after_the_run(
    engine_stub, tmp_path
):
    start, _, during = engine_stub
    holder: dict = {}
    during.append(lambda: holder["gateway"].__exit__(None, None, None))  # it goes away mid-run
    with FakeGateway(PAPER) as gateway:
        holder["gateway"] = gateway
        assert start(gateway) == 1
    assert not evidence_dir(tmp_path).exists()
    assert not (tmp_path / "evidence").exists()
    (record,) = local_records(tmp_path)  # the owner still has the local record of the run
    assert record["boundary"]["paper_checks_passed_after_run"] is False


@WITH_AND_WITHOUT_A_CONTRACT
def test_no_evidence_when_jts_stops_saying_paper_during_the_run(
    engine_stub, paper_state, tmp_path, listed
):
    start, _, during = engine_stub
    during.append(lambda: paper_state.jts.write_text(JTS_LIVE, encoding="utf-8"))
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, listed=listed) == 1
    assert not evidence_dir(tmp_path).exists()
    assert not (tmp_path / "evidence").exists()
    (record,) = local_records(tmp_path)
    assert record["boundary"]["paper_checks_passed_after_run"] is False


@WITH_AND_WITHOUT_A_CONTRACT
def test_no_evidence_when_the_gateway_opens_another_port_during_the_run(
    engine_stub, paper_state, tmp_path, listed
):
    start, _, during = engine_stub
    during.append(lambda: paper_state.extra_ports.add(5000))
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, listed=listed) == 1
    assert not evidence_dir(tmp_path).exists()
    assert not (tmp_path / "evidence").exists()


def test_no_evidence_when_the_gateway_restarts_during_the_run(engine_stub, paper_state, tmp_path):
    start, _, during = engine_stub
    during.append(
        lambda: paper_state.identity.symmetric_difference_update(
            {"process 100 started 5100", "process 200 started 9200"}
        )
    )
    with FakeGateway(PAPER) as gateway:
        assert start(gateway) == 1
    assert not evidence_dir(tmp_path).exists()
    assert not (tmp_path / "evidence").exists()
    (record,) = local_records(tmp_path)
    assert record["boundary"]["paper_checks_passed_after_run"] is False


def test_no_evidence_when_a_contract_appears_during_the_run(engine_stub, paper_state, tmp_path):
    start, _, during = engine_stub
    holder: dict = {}
    during.append(lambda: allowlist(paper_state.settings, holder["gateway"], (PAPER,)))
    with FakeGateway(PAPER) as gateway:
        holder["gateway"] = gateway
        assert start(gateway) == 1
    assert not evidence_dir(tmp_path).exists()
    assert not (tmp_path / "evidence").exists()


# --- check 5: exclusive use (a synthetic /proc) ----------------------------------------------


def exclusive_settings(tmp_path: Path, processes=(), tcp=(), tcp6=None) -> Settings:
    """Settings whose /proc is synthetic: ``processes`` as fake_proc takes them, then
    ``cmdline`` files written by ``with_cmdline``."""
    return Settings(
        repo_root=tmp_path / "repo",
        jts_dir=tmp_path / "Jts",
        state_dir=tmp_path / "state",
        proc=fake_proc(tmp_path, list(processes), tcp=list(tcp), tcp6=tcp6),
    )


def with_cmdline(settings: Settings, pid: int, *args: str) -> None:
    """The NUL-separated command line of process ``pid``, as the kernel shows it."""
    (settings.proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")


def test_check_5_takes_the_lock_in_private_files_and_passes_on_a_quiet_gateway(tmp_path):
    settings = exclusive_settings(
        tmp_path, [("/usr/bin/java", [11])], tcp=[(PORT, LISTEN, 11)], tcp6=[(PORT, LISTEN, 12)]
    )
    with_cmdline(settings, 100, "/usr/bin/java", "-jar", "ibgateway.jar")
    held = boundary.require_exclusive_use(settings)
    try:
        assert held.lock_path == settings.account_lock
        assert held.holds_lock_file()
        assert os.stat(settings.lane_dir).st_mode & 0o777 == 0o700
        assert os.stat(settings.account_lock).st_mode & 0o777 == 0o600
    finally:
        held.lock.close()


def test_check_5_refuses_while_another_run_holds_the_lock(tmp_path):
    settings = exclusive_settings(tmp_path)
    first = boundary.require_exclusive_use(settings)
    try:
        with pytest.raises(boundary.BoundaryError, match="holds the account lock"):
            boundary.require_exclusive_use(settings)
    finally:
        first.lock.close()
    boundary.require_exclusive_use(settings).lock.close()  # free again once released


def test_check_5_again_keeps_the_lock_it_holds(tmp_path):
    settings = exclusive_settings(tmp_path)
    held = boundary.require_exclusive_use(settings)
    try:
        assert boundary.require_exclusive_use(settings, held=held) is held
        settings.account_lock.unlink()  # a lock file replaced under the run is not this lock
        settings.account_lock.touch()
        with pytest.raises(boundary.BoundaryError, match="no longer held"):
            boundary.require_exclusive_use(settings, held=held)
    finally:
        held.lock.close()


@pytest.mark.parametrize(
    ("table", "state"),
    [("tcp", ESTABLISHED), ("tcp", CLOSE_WAIT), ("tcp6", ESTABLISHED), ("tcp6", CLOSE_WAIT)],
)
def test_check_5_refuses_a_connection_on_the_paper_port(tmp_path, table, state):
    rows = [(PORT, LISTEN, 11), (PORT, state, 12)]
    tcp, tcp6 = (rows, []) if table == "tcp" else ([(PORT, LISTEN, 11)], rows[1:])
    settings = exclusive_settings(tmp_path, tcp=tcp, tcp6=tcp6)
    shown = "ESTABLISHED" if state == ESTABLISHED else "CLOSE_WAIT"
    with pytest.raises(boundary.BoundaryError, match=shown):
        boundary.require_exclusive_use(settings)
    # The refusal released the lock it took.
    quiet = Settings(
        repo_root=settings.repo_root,
        jts_dir=settings.jts_dir,
        state_dir=settings.state_dir,
        proc=fake_proc(tmp_path / "quiet", [], tcp=[]),
    )
    boundary.require_exclusive_use(quiet).lock.close()


def test_check_5_names_what_it_saw(tmp_path):
    settings = exclusive_settings(tmp_path, tcp=[(PORT, ESTABLISHED, 12), (PORT, CLOSE_WAIT, 13)])
    with pytest.raises(boundary.BoundaryError) as refused:
        boundary.require_exclusive_use(settings)
    assert "2 connection(s)" in str(refused.value)
    assert "CLOSE_WAIT, ESTABLISHED" in str(refused.value)


@pytest.mark.parametrize(
    "rows",
    [
        [(PORT, LISTEN, 11)],  # the Gateway listening, nobody attached
        [(PORT, LISTEN, 11), (PORT + 1000, ESTABLISHED, 12)],  # a connection on another port
        [(PORT, LISTEN, 11), (PORT, "06", 13)],  # TIME_WAIT: a closed connection
    ],
    ids=["listen-only", "other-port", "time-wait"],
)
def test_check_5_passes_without_an_attached_client(tmp_path, rows):
    settings = exclusive_settings(tmp_path, tcp=rows)
    boundary.require_exclusive_use(settings).lock.close()


def test_check_5_looks_at_the_paper_port_the_lane_uses(tmp_path, monkeypatch):
    settings = exclusive_settings(tmp_path, tcp=[(50000, ESTABLISHED, 12)])
    boundary.require_exclusive_use(settings).lock.close()
    monkeypatch.setattr(boundary, "PAPER_PORT", 50000)
    with pytest.raises(boundary.BoundaryError, match="another API client"):
        boundary.require_exclusive_use(settings)


LANE_RUNS = [
    # -m with its value in the next argument, attached, or at the end of a cluster
    ("python", "-m", "meridian.ibkr_paper", "order"),
    ("/usr/bin/python3", "-mmeridian.ibkr_paper", "restart"),
    ("python", "-um", "meridian.ibkr_paper"),  # the reviewer's third case
    ("python3", "-uIm", "meridian.sandbox_lane"),
    ("python", "-OOmmeridian.ibkr_paper"),
    # options with a value before the entry point
    ("python", "-W", "ignore", "-X", "dev", "-m", "meridian.ibkr_paper"),
    ("python", "-uW", "ignore", "-m", "meridian.ibkr_paper"),
    ("python", "-Wignore", "-Xdev", "meridian/ibkr_paper.py"),
    ("python", "--check-hash-based-pycs", "never", "-m", "meridian.sandbox_lane"),
    ("python", "-u-check-hash-based-pycs", "always", "meridian/ibkr_paper.py"),
    # the module's file as the script, also after "--" or a "-" that ends a cluster
    ("python", "meridian/ibkr_paper.py", "fill"),
    ("python", "./meridian/ibkr_paper.py"),
    ("/usr/bin/python3.14", "/home/example/src/meridian/meridian/ibkr_paper.py"),
    ("python3", "-u", "meridian/sandbox_lane.py"),
    ("python", "--", "meridian/ibkr_paper.py", "fill"),
    ("python", "-u-", "meridian/ibkr_paper.py"),
    # the interpreter process a launcher starts (the launcher's own line is not counted)
    ("/home/example/src/meridian/.venv/bin/python", "-m", "meridian.ibkr_paper", "fill"),
    ("/home/example/src/meridian/.venv/bin/python3", "meridian/ibkr_paper.py"),  # uv run
    ("python3", "-m", "meridian.ibkr_paper"),  # env python3 -m, sudo -u example python3 -m
    ("python3", "-um", "meridian.sandbox_lane"),  # nice -n 10 python3 -um
    ("python", "meridian/sandbox_lane.py"),  # timeout 10 python
    ("/usr/bin/python3.14", "-m", "meridian.sandbox_lane"),  # systemd-run ... python3.14
    # a runner that runs the lane in the same process
    ("python", "-m", "pdb", "-c", "continue", "-m", "meridian.ibkr_paper"),  # the reviewer's
    ("python", "-m", "cProfile", "-o", "out.prof", "-m", "meridian.sandbox_lane"),
    ("python", "-m", "trace", "--trace", "meridian/ibkr_paper.py"),
    ("python", "-m", "coverage", "run", "-m", "meridian.ibkr_paper"),
    ("python3", "-mprofile", "-mmeridian.ibkr_paper"),
    ("python", "-m", "pdb", "/home/example/src/meridian/meridian/sandbox_lane.py"),
    (
        "python",
        "-m",
        "debugpy",
        "--listen",
        "localhost:5678",
        "--module",
        "meridian.ibkr_paper",
    ),
    ("python", "-m", "runpy", "--module=meridian.ibkr_paper"),
    # a runner's console script: the kernel runs its interpreter with the script's path
    (
        "/home/example/.venv/bin/python",
        "/home/example/.venv/bin/coverage",
        "run",
        "-m",
        "meridian.ibkr_paper",
    ),
    ("/usr/bin/python3", "/usr/bin/pdb3", "meridian/ibkr_paper.py"),
    # -c with the dotted module, or a from-import of it
    ("python3", "-c", "import meridian.ibkr_paper; meridian.ibkr_paper.main()"),
    ("python", "-I", "-c", "from meridian.sandbox_lane import main; main()"),
    ("python", "-Ic", "from meridian import ibkr_paper"),
    ("python", "-Icfrom meridian import ibkr_paper"),
    ("python", "-c", "from meridian import ibkr_paper; ibkr_paper.main()"),
    ("python", "-c", "from meridian import alpaca_lane, sandbox_lane as s; s.main()"),
    ("python", "-c", "from meridian import (\n    alpaca_lane,\n    ibkr_paper,\n)"),
    ("python", "-W", "ignore", "-cfrom meridian import ibkr_paper"),
    ("/home/example/src/meridian/.venv/bin/python", "-c", "from meridian import ibkr_paper"),
]


@pytest.mark.parametrize("cmdline", LANE_RUNS)
def test_check_5_refuses_while_the_other_projects_lane_runs(tmp_path, cmdline):
    settings = exclusive_settings(tmp_path, [("/usr/bin/python3", [])])
    with_cmdline(settings, 100, *cmdline)
    with pytest.raises(boundary.BoundaryError, match="a process runs a lane of the owner's other"):
        boundary.require_exclusive_use(settings)


LANE_DOES_NOT_RUN = [
    ("python", "-m", "astrolabe.ibkr_paper", "order"),
    ("python", "-m", "meridian.alpaca_lane"),
    ("python", "-m", "meridian.ibkr_paper_notes"),
    # a bare mention as an argument of a program that is not an interpreter
    ("rg", "meridian.ibkr_paper", "."),
    ("rg", "--", "-m meridian.ibkr_paper", "."),  # the reviewer's first case
    ("rg", "-n", "from meridian import ibkr_paper", "."),
    ("vim", "/home/example/src/meridian/meridian/ibkr_paper.py"),
    ("git", "grep", "-n", "meridian.sandbox_lane"),
    ("cat", "meridian/sandbox_lane.py"),
    ("bash", "-lc", "cd ~/src/meridian && rg -n meridian.ibkr_paper meridian/sandbox_lane.py"),
    ("bash", "-lc", "grep -rn meridian/ibkr_paper.py . && cat meridian/ibkr_paper.py"),
    # "python" as an argument of another program is not an interpreter (the reviewer's case)
    ("rg", "python", "meridian/ibkr_paper.py"),
    ("grep", "python", "meridian/sandbox_lane.py"),
    ("rg", "-l", "python", "-m", "meridian.ibkr_paper"),
    # a launcher's own line is not parsed: the interpreter it starts has its own /proc entry
    ("uv", "run", "python", "-m", "meridian.ibkr_paper", "fill"),
    ("uv", "run", "python", "meridian/ibkr_paper.py"),
    ("/usr/bin/env", "python3", "-m", "meridian.ibkr_paper"),
    ("nice", "-n", "10", "python3", "-um", "meridian.sandbox_lane"),
    ("timeout", "10", "python", "meridian/ibkr_paper.py"),
    ("sudo", "-u", "example", "python3", "-m", "meridian.ibkr_paper"),
    ("systemd-run", "--user", "--wait", "/usr/bin/python3.14", "-m", "meridian.sandbox_lane"),
    ("uv", "run", "--no-sync", "python", "-c", "from meridian import ibkr_paper"),
    # not a runner, or a runner that runs something else
    ("python", "-m", "pdb", "tool.py"),
    ("python", "-m", "pytest", "-m", "meridian.ibkr_paper"),  # pytest's -m selects markers
    ("python", "-m", "timeit", "-m", "meridian.ibkr_paper"),
    ("python", "-m", "cProfile", "-m", "meridian.ibkr_paper_notes"),
    ("python", "/home/example/.venv/bin/coverage", "report"),
    # a shell wrapper is not parsed: the Python process it starts has its own /proc entry
    ("bash", "-lc", "cd ~/src/meridian && uv run python -m meridian.sandbox_lane"),
    ("bash", "-lc", "cd ~/src/meridian && uv run python -mmeridian.sandbox_lane"),
    ("bash", "-lc", "cd ~/src/meridian && .venv/bin/python3 meridian/sandbox_lane.py"),
    ("bash", "-lc", "uv run python -c 'from meridian import ibkr_paper; ibkr_paper.main()'"),
    # the module only as an argument after the entry point (the reviewer's second case)
    ("python", "-c", "import time; time.sleep(100)", "meridian/ibkr_paper.py"),
    ("python", "tool.py", "meridian/ibkr_paper.py"),
    ("python", "tool.py", "-m", "meridian.ibkr_paper"),
    ("python", "-m", "pytest", "meridian/ibkr_paper.py"),
    ("python", "-um", "pytest", "-m", "meridian.ibkr_paper"),
    ("uv", "run", "python", "tool.py", "--", "meridian/sandbox_lane.py"),
    ("python", "-c", "print(1)", "meridian.ibkr_paper"),
    # a -W, -X or --check-hash-based-pycs value that looks like the module, or like -m
    ("python", "-W", "meridian.ibkr_paper", "tool.py"),
    ("python", "-X", "meridian.ibkr_paper", "tool.py"),
    ("python", "-Wmeridian.ibkr_paper", "-c", "pass"),
    ("python", "-W", "-m", "meridian.ibkr_paper"),  # the script is "meridian.ibkr_paper"
    ("python", "-uX", "-m", "tool.py", "meridian/ibkr_paper.py"),
    ("python", "--check-hash-based-pycs", "meridian/ibkr_paper.py", "tool.py"),
    # -m takes the rest of its cluster: this runs a module "eridian.ibkr_paper"
    ("python", "-umeridian.ibkr_paper"),
    # after "--" everything is the script and its arguments; "-" is standard input
    ("python", "--", "-m", "meridian.ibkr_paper"),
    ("python", "-", "meridian/ibkr_paper.py"),
    # an interpreter that exits or refuses before it runs anything
    ("python", "-V", "-m", "meridian.ibkr_paper"),
    ("python", "-h", "-m", "meridian.ibkr_paper"),
    ("python", "--version", "-m", "meridian.ibkr_paper"),
    ("python", "--help-env", "-m", "meridian.ibkr_paper"),
    ("python", "-J", "-m", "meridian.ibkr_paper"),  # an unknown option
    ("python", "--check-hash-based-pycs=never", "-m", "meridian.ibkr_paper"),
    ("python",),
    # a script whose path only shares the end of the module's file name
    ("python", "notmeridian/ibkr_paper.py"),
    # a -c program that names neither lane
    ("python", "-c", "from meridian import ibkr_paper_notes"),
    ("python", "-c", "import xmeridian.ibkr_paper"),
    ("python3", "-c"),
]


@pytest.mark.parametrize("cmdline", LANE_DOES_NOT_RUN)
def test_check_5_lets_other_processes_be(tmp_path, cmdline):
    settings = exclusive_settings(tmp_path, [("/usr/bin/python3", [])])
    with_cmdline(settings, 100, *cmdline)
    boundary.require_exclusive_use(settings).lock.close()


def test_check_5_refuses_without_a_socket_table(tmp_path):
    settings = exclusive_settings(tmp_path)
    (settings.proc / "net" / "tcp").unlink()
    with pytest.raises(boundary.BoundaryError, match="cannot read"):
        boundary.require_exclusive_use(settings)


def test_the_lane_refuses_before_the_probe_when_another_client_is_attached(
    lane, paper_state, capsys
):
    start, reached = lane
    with FakeGateway(PAPER) as gateway:
        (paper_state.settings.proc / "net" / "tcp").write_text(
            TCP_HEAD + tcp_row(0, gateway.port, ESTABLISHED, 12), encoding="ascii"
        )
        assert start(gateway, "restart") == 2
        assert gateway.sessions == 0  # the Gateway was never asked
    assert reached == []
    assert "another API client" in capsys.readouterr().err


def test_the_lane_refuses_while_another_run_holds_the_lock(lane, paper_state):
    start, reached = lane
    other = boundary.require_exclusive_use(paper_state.settings)
    try:
        with FakeGateway(PAPER) as gateway:
            assert start(gateway, "restart") == 2
            assert gateway.sessions == 0
    finally:
        other.lock.close()
    assert reached == []


@CONFIRMED
@pytest.mark.parametrize("change", ["client", "other-lane"])
def test_check_5_runs_again_after_the_confirmation(
    lane, paper_state, monkeypatch, capsys, change, mode
):
    start, reached = lane
    with FakeGateway(PAPER) as gateway:

        def attach() -> None:
            proc = paper_state.settings.proc
            if change == "client":
                (proc / "net" / "tcp").write_text(
                    TCP_HEAD + tcp_row(0, gateway.port, CLOSE_WAIT, 12), encoding="ascii"
                )
            else:
                (proc / "300").mkdir()
                (proc / "300" / "cmdline").write_bytes(b"python\0-m\0meridian.ibkr_paper\0")

        monkeypatch.setattr(ibkr_paper, "owner_confirms", confirm_after(attach))
        assert start(gateway, mode, confirm=False) == 2
        assert gateway.sessions == 1  # the second probe never ran
    assert reached == []
    assert "after the confirmation" in capsys.readouterr().err


def test_the_lane_holds_the_lock_after_it_starts(lane, paper_state):
    start, reached = lane
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, "restart") == 0
    assert reached == [PAPER]
    with pytest.raises(boundary.BoundaryError, match="holds the account lock"):
        boundary.require_exclusive_use(paper_state.settings)
    ibkr_paper._release_held()


def test_every_start_names_the_reviewed_code(lane, capsys):
    start, _ = lane
    with FakeGateway(PAPER) as gateway:
        assert start(gateway, "restart") == 0
    assert f"Code: main at {SOURCE_COMMIT}, clean, equal to origin/main" in capsys.readouterr().out


INTERPRETERS = ["python3", "/usr/bin/python3.14", "/home/example/src/meridian/.venv/bin/python"]
# Options that change nothing about what the interpreter runs.
NEUTRAL_FLAGS = [(), ("-u",), ("-B", "-I"), ("-W", "ignore"), ("-X", "dev"), ("-uB",)]


def scanned(cmdline) -> bool:
    return any(boundary._runs_module(list(cmdline), lane) for lane in boundary.OTHER_PROJECT_LANES)


@pytest.mark.parametrize("flags", NEUTRAL_FLAGS)
@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_the_scan_answers_the_same_whatever_the_interpreters_path_and_neutral_options(
    interpreter, flags
):
    # One case per finding never closed this scan: every case is tried again under each
    # interpreter path and each option that changes nothing about what runs.
    changed = []
    for expected, cases in ((True, LANE_RUNS), (False, LANE_DOES_NOT_RUN)):
        for cmdline in cases:
            if not boundary._is_python(cmdline[0]):
                continue  # not an interpreter process: nothing to vary
            variant = (interpreter, *flags, *cmdline[1:])
            if scanned(cmdline) is expected and scanned(variant) is not expected:
                changed.append(variant)
    assert changed == []


def test_every_listed_case_is_what_its_list_says():
    assert [c for c in LANE_RUNS if not scanned(c)] == []
    assert [c for c in LANE_DOES_NOT_RUN if scanned(c)] == []
