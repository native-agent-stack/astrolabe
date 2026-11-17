"""The guards every test runs under (tests/conftest.py): no Python socket to an IB API port, and
no reach into the lane's real state folder, neither in a test nor while a test module is
imported."""

import socket
from pathlib import Path

import conftest
import pytest

from astrolabe import ib_ids, settings

# Read while pytest collects this module, before any fixture has run.
SETTINGS_AT_IMPORT = settings.Settings.current()


def real_state_dir() -> Path:
    return conftest.REAL_USER_HOME() / ".local" / "state" / "astrolabe"


def test_the_blocked_ports_are_the_paper_ports_and_the_live_port_below_each():
    assert {ib_ids.PAPER_PORT, ib_ids.PAPER_PORT - 1, 7497, 7497 - 1} == conftest.BLOCKED_API_PORTS


@pytest.mark.parametrize("port", sorted(conftest.BLOCKED_API_PORTS))
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_a_python_socket_to_an_api_port_is_refused_before_it_connects(host, port):
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client,
        pytest.raises(RuntimeError, match="IB API port"),
    ):
        client.connect((host, port))


def test_create_connection_is_refused_too():
    with pytest.raises(RuntimeError, match="IB API port"):
        socket.create_connection((ib_ids.HOST, ib_ids.PAPER_PORT), timeout=1)


def test_a_socket_to_another_port_is_not_the_guards_business():
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client,
    ):
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client.connect(server.getsockname())


def test_the_guard_is_installed_when_conftest_is_imported_not_by_a_fixture():
    # A fixture runs after collection; an import of a test module must be covered as well.
    source = Path(conftest.__file__).read_text(encoding="utf-8")
    hook = "sys.addaudithook(_refuse_api_port_sockets)"
    assert f"\n{hook}\n" in source, "the hook must be installed at module level"


def test_the_real_home_lookup_is_kept_and_is_not_the_stand_in():
    assert conftest.REAL_USER_HOME() != conftest.SESSION_HOME
    assert real_state_dir() != conftest.SESSION_HOME / ".local" / "state" / "astrolabe"


def test_settings_read_while_a_module_is_imported_are_not_the_real_users():
    assert SETTINGS_AT_IMPORT.state_dir == conftest.SESSION_HOME / ".local" / "state" / "astrolabe"
    assert SETTINGS_AT_IMPORT.jts_dir == conftest.SESSION_HOME / "Jts"
    assert SETTINGS_AT_IMPORT.state_dir != real_state_dir()


def test_a_test_that_asks_for_nothing_still_gets_a_home_of_its_own():
    # No fixture is requested here: the per-test home must be automatic.
    home = settings._user_home()
    current = settings.Settings.current()

    assert home != conftest.SESSION_HOME
    assert home != conftest.REAL_USER_HOME()
    assert current.state_dir.is_relative_to(home)
    assert current.account_lock.is_relative_to(home)
    assert current.state_dir != real_state_dir()


def test_the_home_of_a_test_is_the_fixtures(_no_real_home):
    assert settings.Settings.current().jts_dir == _no_real_home / "Jts"
