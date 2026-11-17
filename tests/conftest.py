"""Every test owns the nodes it builds: one fixture disposes of each and releases its run, also
when the test fails or times out, so one slow node test costs one failure and never poisons
its worker (`node_lifetime.py`). No test reaches a real `gh`, no test opens a Python socket to
an IB API port, and no test reaches the lane's real state folder. Ported from
meridian@014f706d:tests/conftest.py; the port guard and the home folder guard are new."""

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from node_lifetime import live_nodes as live_nodes

from astrolabe import ib_ids
from astrolabe import settings as _settings

# The lane's own home lookup, kept for the test of it, and a stand-in for everything else. The
# stand-in is put in place when pytest imports this file, before any test module is collected,
# so a module that reads `Settings.current()` while it is imported gets it as well.
REAL_USER_HOME = _settings._user_home
SESSION_HOME = Path(tempfile.mkdtemp(prefix="astrolabe-tests-home-"))
atexit.register(shutil.rmtree, SESSION_HOME, ignore_errors=True)
_settings._user_home = lambda: SESSION_HOME

# The Gateway's and TWS's paper ports, and the live port that sits one below each.
_TWS_PAPER_PORT = 7497
BLOCKED_API_PORTS = frozenset(
    {ib_ids.PAPER_PORT, ib_ids.PAPER_PORT - 1, _TWS_PAPER_PORT, _TWS_PAPER_PORT - 1}
)


def _refuse_api_port_sockets(event, args):
    """A Python socket to an IB API port fails whatever opens it, before it connects.

    The audit hook sees Python sockets only
    (https://docs.python.org/3.14/library/audit_events.html, `socket.connect`). The engine's
    own transport is native code, which is why no test runs a node against the adapter.
    """
    if event != "socket.connect":
        return
    address = args[1]
    if isinstance(address, tuple) and len(address) >= 2 and address[1] in BLOCKED_API_PORTS:
        raise RuntimeError("a test tried to connect to an IB API port")


# Installed when pytest imports this file: before any test module is collected, in the main
# process and in every xdist worker. An audit hook lives in one process, so a Python process
# that a test starts is not covered; tests/test_paper_only.py and tests/test_client_ids.py keep
# the ports out of the tree's own code instead.
sys.addaudithook(_refuse_api_port_sockets)


@pytest.fixture(autouse=True)
def every_node_is_owned(live_nodes):
    """`live_nodes` for every test, including one that builds a node without asking for it."""
    yield


@pytest.fixture(autouse=True)
def _no_real_home(tmp_path_factory, monkeypatch):
    """`Settings.current()` resolves below a temporary folder of its own in every test, so no
    test can take the real account lock or write beside the owner's run records, and no test
    sees another test's state. Outside a test the session's stand-in above applies."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(_settings, "_user_home", lambda: home)
    return home


@pytest.fixture(scope="session", autouse=True)
def _gh_audit_policy():
    """Audit below subprocess helpers and cached Popen aliases.

    https://docs.python.org/3.14/library/audit_events.html#subprocess.Popen
    """
    import os
    import shutil
    import sys
    from pathlib import Path

    allowed: set[Path] = set()

    def audit(event, args):
        if event not in {"subprocess.Popen", "os.exec", "os.posix_spawn"}:
            return
        executable = os.fsdecode(args[0])
        if Path(executable).name not in {"gh", "gh.exe"}:
            return
        env = args[3] if event == "subprocess.Popen" else args[2]
        search = env.get("PATH") if env is not None else None
        resolved = shutil.which(executable, path=search)
        if resolved is None or Path(resolved).resolve() not in allowed:
            pytest.fail("real gh blocked: use an explicit fake fixture", pytrace=False)

    sys.addaudithook(audit)
    return allowed


@pytest.fixture(autouse=True)
def no_real_gh(_gh_audit_policy, tmp_path_factory, monkeypatch):
    """Direct calls fail before spawn; ordinary child-shell calls meet an offline sentinel."""
    import os
    import shlex

    folder = tmp_path_factory.mktemp("gh-guard")
    attempted = folder / "attempted"
    executable = folder / "gh"
    executable.write_text(
        f"#!/bin/sh\nprintf 'blocked\\n' >> {shlex.quote(str(attempted))}\nexit 97\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", f"{folder}:{os.environ.get('PATH', os.defpath)}")
    _gh_audit_policy.clear()
    try:
        yield
    finally:
        assert not attempted.exists(), "real gh blocked in a child: use an explicit fake fixture"


@pytest.fixture
def fake_gh_executable(_gh_audit_policy, tmp_path):
    """Explicitly admit only a regular fake executable created in this test's own directory."""

    def admit(path):
        assert not path.is_symlink() and path.resolve().is_relative_to(tmp_path.resolve())
        assert path.is_file()
        _gh_audit_policy.add(path.resolve())
        return path

    return admit
