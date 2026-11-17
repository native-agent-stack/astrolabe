"""
Paper-only boundary for the IBKR lane. Own design (no upstream ships it).

Ported from meridian@014f706d:meridian/boundary.py. Changed: paths come from
``astrolabe.settings.Settings`` and the target and client ids from ``astrolabe.ib_ids``; the
schema is ``astrolabe.boundary/1``; check 5 ("exclusive use") is new. Checks 1 to 4 keep their
logic.

The lane may start only when every check below holds. Checks 1 to 4 run before the engine is
loaded and again after the run, and each one refuses on anything it does not recognise:

1. The IB Gateway's own settings file, ``~/Jts/jts.ini``, has ``tradingMode=p`` under
   ``[Logon]``. The Gateway writes it when it logs in. No file, no key, a duplicate, or any
   other value refuses.
2. The Gateway installed under ``~/Jts/ibgateway`` is running and listens on exactly one TCP
   port, the paper port (``ib_ids.PAPER_PORT``, read from ``/proc``). No Gateway, another
   port, or an extra port refuses.
3. The Gateway reports exactly one account, and its id has the paper form: a capital D,
   capital letters, then digits, the form of the paper accounts in the examples of
   NautilusTrader's IB integration guide. It is a convention, not a documented guarantee:
   IBKR's API documentation says the API makes no difference between live and paper trading.
4. Only if the owner has created ``risk/boundary.contract.json``: the account is on its
   allowlist. The file is optional. When present it can only make the check stricter, and a
   file that is present but malformed refuses.
5. Exclusive use: one order-placing client per Gateway session. It runs before the first
   account probe and again after the owner types the confirmation word, and is not part of
   the after-run comparison. It refuses when
   - another run of this lane holds the account lock (a non-blocking ``flock`` on
     ``Settings.account_lock``; the caller keeps the open handle for the life of the process);
   - ``/proc/net/tcp`` or ``tcp6`` shows a socket in state ESTABLISHED or CLOSE_WAIT whose
     local port is the paper port: the Gateway's end of a connection from another API client,
     or a half-closed one that is left over;
   - a process command line runs ``meridian.ibkr_paper`` or ``meridian.sandbox_lane``: the
     owner's other project uses the same paper account.

   Reading those sockets as "another API client is attached" is an inference, not something
   the Gateway documents: the Gateway's end of every API connection is a socket whose local
   port is its listening port, and in this distro the kernel lists it in ``/proc/net/tcp*``.
   Whether a client on Windows (through mirrored networking) shows up there too is not
   verified; the owner's first ``restart`` run confirms what the check sees. A process of
   another user, or one hidden by ``hidepid``, is not seen by the command-line check.

The lane connects to 127.0.0.1 on the paper port and nowhere else. The Gateway itself listens
on every interface. Only its own "Allow connections from localhost only" setting keeps other
hosts out, and that setting sits in the Gateway's encrypted settings, where no check here can
read it. Check 2 records how the port is bound (``gateway_bind``) but does not refuse an
every-interface bind, which is the Gateway's normal one.

The Gateway's paths follow the home folder of the user who runs the lane, read from the
password database and not from ``HOME`` (``Settings.current()``), and checks 1, 2 and 5 read
the Gateway's own files and ``/proc`` entries. That works for the owner, who runs the
Gateway. Under another user the checks refuse: that user cannot read the owner's files or
another user's ``/proc`` entries.

The allowlist holds SHA-256 hashes, which keeps the account id out of plain sight. It is
not a secret: an account id is short, so anyone holding the contract can recover it by
hashing every possible id. Treat the contract as if it held the id. No message raised
here contains an account id.

    uv run python -m astrolabe.boundary hash        # prompts for an account id, prints its hash
    uv run python -m astrolabe.boundary contract    # owner at a terminal: prints a contract
                                                    # for the account the Gateway reports
"""

from __future__ import annotations

import configparser
import dataclasses
import fcntl
import getpass
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from astrolabe import ib_ids
from astrolabe.settings import Settings

SCHEMA = "astrolabe.boundary/1"
# The lane reaches the Gateway, which runs inside this distro, over loopback.
GATEWAY_HOST = ib_ids.HOST
# IB Gateway's default socket port for a paper login. Nothing widens this set.
PAPER_PORT = ib_ids.PAPER_PORT
PAPER_PORTS = frozenset({PAPER_PORT})
PAPER_TRADING_MODE = "p"
# Client id for the probe of the ``contract`` command; distinct from the lane's own clients.
CONTRACT_PROBE_CLIENT_ID = ib_ids.CONTRACT_PROBE_CLIENT_ID
CONFIRM_WORD = "PAPER"
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_ACCOUNT_ID = re.compile(r"[A-Za-z0-9]+")
# The paper form of the guide's examples (check 3): a capital D, capital letters, digits.
_PAPER_FORM = re.compile(r"D[A-Z]*[0-9]+")
# TCP states as the kernel writes them in /proc/net/tcp, in hex: TCP_ESTABLISHED = 1,
# TCP_CLOSE_WAIT = 8, TCP_LISTEN = 10 (Linux include/net/tcp_states.h; not re-read at the source
# for this port, and LISTEN = 0A is the value check 2 has relied on since meridian).
_TCP_LISTEN = "0A"
_TCP_ESTABLISHED = "01"
_TCP_CLOSE_WAIT = "08"
_ATTACHED_STATES = {_TCP_ESTABLISHED: "ESTABLISHED", _TCP_CLOSE_WAIT: "CLOSE_WAIT"}
# The owner's other project's lanes on the same paper account (check 5).
OTHER_PROJECT_LANES = ("meridian.ibkr_paper", "meridian.sandbox_lane")


class BoundaryError(RuntimeError):
    """The lane must not start."""


@dataclass(frozen=True)
class IbkrBoundary:
    host: str
    port: int
    # None when the owner has no contract: checks 1 to 3 decide alone.
    paper_accounts_sha256: frozenset[str] | None
    # Which Gateway process and listening socket the checks saw. Compared before the engine
    # connects and after the run, so a Gateway restart or a new socket in between refuses.
    gateway_identity: frozenset[str] = frozenset()
    # How the paper port is bound: "all interfaces", "loopback" or "a specific address"
    # (never the address itself, which can be a private network address).
    gateway_bind: str = ""


def _settings(settings: Settings | None) -> Settings:
    return Settings.current() if settings is None else settings


def account_hash(account_id: str) -> str:
    """SHA-256 of the account id exactly as the Gateway reports it, without whitespace."""
    return hashlib.sha256(account_id.strip().encode("utf-8")).hexdigest()


def private_dir(path: Path) -> Path:
    """Create ``path`` and any missing parent with mode 0700, and make ``path`` itself 0700.

    Parents that already exist are left as they are. ``Path.mkdir(mode, parents=True)`` would
    create the parents with the default mode (CPython 3.14.10 Lib/pathlib/__init__.py:1006-1021,
    read 2026-11-17: the parents are made by ``self.parent.mkdir(parents=True, ...)``, without
    ``mode``), so each missing level is made here.
    """
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for folder in reversed(missing):
        folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


# --- check 4: the owner's optional contract ---------------------------------------------------


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _no_contract_file(path: Path) -> bool:
    """True only when the contract plainly does not exist. Any other doubt raises.

    A folder that cannot be searched, a file or a broken link where the folder should be, or a
    link loop is not "no contract": it refuses, so an allowlist the owner made is never skipped.
    """
    try:
        os.lstat(path)
        return False  # something is there: it must read as a valid contract
    except FileNotFoundError:
        pass
    except OSError as e:
        raise BoundaryError(f"cannot look up the boundary contract: {path}") from e
    # The file is plainly missing. (A file, or a link to one, where the folder should be makes
    # the lookup above fail with "not a directory", which already refused.) A broken link where
    # the folder should be also reads as missing, so look for that one case.
    folder = path.parent
    if os.path.islink(folder) and not os.path.exists(folder):
        raise BoundaryError(f"{folder} is a broken link")
    return True


def load_contract(path: Path | None = None, settings: Settings | None = None) -> IbkrBoundary:
    """The owner's contract if there is one, else no allowlist. Raises on any doubt."""
    path = _settings(settings).contract_path if path is None else path
    if _no_contract_file(path):
        return IbkrBoundary(host=GATEWAY_HOST, port=PAPER_PORT, paper_accounts_sha256=None)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, ValueError, RecursionError) as e:
        raise BoundaryError(f"boundary contract is unreadable: {path}") from e

    if not isinstance(raw, dict):
        raise BoundaryError("boundary contract is not a JSON object")
    if raw.get("schema") != SCHEMA:
        raise BoundaryError(f"boundary contract schema is not {SCHEMA!r}")
    if raw.get("mode") != "paper":
        raise BoundaryError('boundary contract mode is not "paper"')

    ibkr = raw.get("ibkr")
    if not isinstance(ibkr, dict):
        raise BoundaryError("boundary contract has no ibkr section")
    host = ibkr.get("gateway_host")
    port = ibkr.get("gateway_port")
    hashes = ibkr.get("paper_accounts_sha256")

    if not isinstance(host, str) or host != GATEWAY_HOST:
        raise BoundaryError(f"gateway_host is not {GATEWAY_HOST}")
    if type(port) is not int or port not in PAPER_PORTS:
        raise BoundaryError("gateway_port is not the IB Gateway paper port")
    if not isinstance(hashes, list) or not hashes:
        raise BoundaryError("paper_accounts_sha256 is missing or empty")
    for value in hashes:
        if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
            raise BoundaryError("paper_accounts_sha256 holds a value that is not a SHA-256 hash")

    return IbkrBoundary(host=host, port=port, paper_accounts_sha256=frozenset(hashes))


# --- check 1: the Gateway's own record of the login mode --------------------------------------


def gateway_trading_mode(path: Path | None = None, settings: Settings | None = None) -> str:
    """The value of tradingMode under [Logon] in the Gateway's jts.ini. Raises if unclear.

    Only that one value is returned; the file can hold other things (such as a user name),
    and nothing else from it is kept or shown.
    """
    path = _settings(settings).jts_ini if path is None else path
    # No key is inherited from another section: with this default_section, a literal
    # [DEFAULT] header in the file is an ordinary section, so only [Logon]'s own key counts.
    parser = configparser.ConfigParser(
        strict=True, interpolation=None, default_section="\0astrolabe: no default section\0"
    )
    try:
        parser.read_string(path.read_text(encoding="utf-8"), source=str(path))
    except (OSError, UnicodeError, configparser.Error) as e:
        raise BoundaryError(f"cannot read the IB Gateway's settings file {path}") from e
    # Section names are compared exactly by configparser, so [Logon] and [LOGON] would both
    # parse. Refuse anything but one plain [Logon], and a tradingMode anywhere else.
    if [s for s in parser.sections() if s.strip().casefold() == "logon"] != ["Logon"]:
        raise BoundaryError("the IB Gateway's settings file has no single plain [Logon] section")
    if any(s != "Logon" and parser.has_option(s, "tradingMode") for s in parser.sections()):
        raise BoundaryError("the IB Gateway's settings file states tradingMode outside [Logon]")
    value = parser.get("Logon", "tradingMode", fallback=None)
    if value is None:
        raise BoundaryError("the IB Gateway's settings file has no tradingMode under [Logon]")
    return value


def require_paper_login(path: Path | None = None, settings: Settings | None = None) -> None:
    if gateway_trading_mode(path, settings) != PAPER_TRADING_MODE:
        raise BoundaryError(
            "the IB Gateway is not logged in to Paper Trading (jts.ini tradingMode is not p)"
        )


# --- check 2: the ports the Gateway listens on --------------------------------------------------


def bind_scope(address_hex: str) -> str:
    """How a /proc/net/tcp or tcp6 local address (hex, as the kernel writes it) is bound:
    "all interfaces", "loopback" or "a specific address". The address itself is not kept."""
    address = address_hex.upper()
    if len(address) not in (8, 32) or any(c not in "0123456789ABCDEF" for c in address):
        raise ValueError("not an IPv4 or IPv6 address in the kernel's hex form")
    if set(address) == {"0"}:
        return "all interfaces"
    if len(address) == 8:  # IPv4, little-endian: the first octet is the last byte
        return "loopback" if int(address[6:8], 16) == 127 else "a specific address"
    # IPv6, four little-endian 32-bit words
    if address == "00000000000000000000000001000000":  # ::1
        return "loopback"
    if address.startswith("0000000000000000FFFF0000"):  # ::ffff:a.b.c.d
        if address[24:32] == "00000000":  # ::ffff:0.0.0.0 takes IPv4 on every interface
            return "all interfaces"
        return "loopback" if int(address[30:32], 16) == 127 else "a specific address"
    return "a specific address"


def _listening_ports_by_inode(proc: Path) -> dict[str, tuple[int, str]]:
    """Socket inode -> (port, bind scope), for every listening TCP socket (IPv4 and, if
    present, IPv6)."""
    found: dict[str, tuple[int, str]] = {}
    for name, required in (("tcp", True), ("tcp6", False)):
        table = proc / "net" / name
        try:
            lines = table.read_text(encoding="ascii").splitlines()
        except FileNotFoundError:
            if required:
                raise BoundaryError(f"cannot read {table}") from None
            continue
        except (OSError, UnicodeError) as e:
            raise BoundaryError(f"cannot read {table}") from e
        for line in lines[1:]:
            fields = line.split()
            try:
                local, state, inode = fields[1], fields[3], fields[9]
                address, port_hex = local.rsplit(":", 1)
                port = int(port_hex, 16)
                scope = bind_scope(address)
            except (IndexError, ValueError) as e:
                raise BoundaryError(f"unexpected line in {table}") from e
            if state == _TCP_LISTEN:
                found[inode] = (port, scope)
    return found


@dataclass(frozen=True)
class Listening:
    """What the installed Gateway listens on."""

    ports: frozenset[int]
    # Each Gateway process by number and start time, each listening socket by inode, port and
    # bind scope, so a restarted Gateway or a re-opened socket shows up as a change.
    identity: frozenset[str]
    # The bind scope of each listening socket.
    bind: frozenset[str]


def _process_start(entry: Path) -> str:
    """The process's start time in clock ticks since boot (field 22 of /proc/<pid>/stat)."""
    try:
        text = (entry / "stat").read_text(encoding="ascii")
        return text.rsplit(")", 1)[1].split()[19]  # the fields after "(name)" begin at field 3
    except (OSError, UnicodeError, IndexError) as e:
        raise BoundaryError("cannot read the IB Gateway's process start time") from e


def gateway_listening(
    proc: Path | None = None, gateway_dir: Path | None = None, settings: Settings | None = None
) -> Listening:
    """Ports the installed IB Gateway listens on, which process and sockets those are, and how
    they are bound. Raises if no Gateway process runs."""
    if proc is None or gateway_dir is None:
        current = _settings(settings)
        proc = current.proc if proc is None else proc
        gateway_dir = current.gateway_dir if gateway_dir is None else gateway_dir
    prefix = str(gateway_dir.resolve()) + os.sep
    listening = _listening_ports_by_inode(proc)
    try:
        entries = list(proc.iterdir())
    except OSError as e:
        raise BoundaryError(f"cannot list {proc}") from e
    gateway_running = False
    ports: set[int] = set()
    identity: set[str] = set()
    bind: set[str] = set()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            exe = os.readlink(entry / "exe")
        except OSError:
            continue  # the process has ended, or it belongs to another user
        if not exe.startswith(prefix):
            continue
        gateway_running = True
        identity.add(f"process {entry.name} started {_process_start(entry)}")
        try:
            fds = list((entry / "fd").iterdir())
        except OSError as e:
            raise BoundaryError("cannot read the IB Gateway's open sockets") from e
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue  # closed since the listing
            if target.startswith("socket:[") and target.endswith("]"):
                inode = target[len("socket:[") : -1]
                seen = listening.get(inode)
                if seen is not None:
                    port, scope = seen
                    ports.add(port)
                    bind.add(scope)
                    identity.add(f"socket {inode} port {port} bound to {scope}")
    if not gateway_running:
        raise BoundaryError(f"the IB Gateway installed in {gateway_dir} is not running")
    return Listening(frozenset(ports), frozenset(identity), frozenset(bind))


def gateway_listen_ports(
    proc: Path | None = None, gateway_dir: Path | None = None, settings: Settings | None = None
) -> frozenset[int]:
    """The TCP ports that processes of the installed IB Gateway listen on. Raises if none run."""
    return gateway_listening(proc, gateway_dir, settings).ports


def require_gateway_on_paper_port_only(settings: Settings | None = None) -> Listening:
    """Refuse unless the Gateway listens on the paper port only; return what was seen."""
    seen = gateway_listening(settings=_settings(settings))
    if seen.ports != PAPER_PORTS:
        shown = ", ".join(str(p) for p in sorted(seen.ports)) or "no port"
        raise BoundaryError(
            f"the IB Gateway listens on {shown}; the lane needs it to listen on the paper port only"
        )
    return seen


def local_paper_checks(settings: Settings | None = None) -> IbkrBoundary:
    """Checks 1, 2 and the optional contract. Reads local state only; no network."""
    settings = _settings(settings)
    bounds = load_contract(settings=settings)
    require_paper_login(settings=settings)
    seen = require_gateway_on_paper_port_only(settings)
    return dataclasses.replace(
        bounds, gateway_identity=seen.identity, gateway_bind=", ".join(sorted(seen.bind))
    )


# --- check 3: the account the Gateway reports -----------------------------------------------


def single_paper_form_account(reported: Iterable[str]) -> str:
    """The one account the Gateway reports, if it has the paper form; else BoundaryError."""
    accounts = [a.strip() for a in reported if a and a.strip()]
    if not accounts:
        raise BoundaryError("the Gateway reported no account")
    if len(accounts) != 1:
        raise BoundaryError(
            f"the Gateway reported {len(accounts)} accounts; the lane needs exactly one"
        )
    account = accounts[0]
    # The engine's adapter treats a hyphen in the account specially. Letters and digits only.
    if not _ACCOUNT_ID.fullmatch(account):
        raise BoundaryError("the Gateway reported an account id of an unexpected form")
    if not _PAPER_FORM.fullmatch(account):
        raise BoundaryError("the Gateway reported an account id that is not of the paper form")
    return account


def allowed_account(reported: Iterable[str], boundary: IbkrBoundary) -> str:
    """Return the one account the lane may use, or raise BoundaryError."""
    account = single_paper_form_account(reported)
    allowlist = boundary.paper_accounts_sha256
    if allowlist is not None and account_hash(account) not in allowlist:
        raise BoundaryError("the account the Gateway reports is not on the paper allowlist")
    return account


# --- check 5: exclusive use -------------------------------------------------------------------


@dataclass(frozen=True)
class ExclusiveUse:
    """The held account lock and what check 5 saw. Keep it for the life of the process: the
    lock is released when ``lock`` is closed."""

    lock: IO[bytes]
    lock_path: Path

    def holds_lock_file(self) -> bool:
        """The handle is still open on the file at ``lock_path`` (not a replaced one)."""
        try:
            held = os.fstat(self.lock.fileno())
            there = os.stat(self.lock_path)
        except OSError, ValueError:
            return False
        return (held.st_dev, held.st_ino) == (there.st_dev, there.st_ino)


def _take_account_lock(path: Path) -> IO[bytes]:
    """A non-blocking exclusive ``flock`` on ``path``, created with mode 0600 if missing.

    flock(2) locks belong to the open file description, so a second open of the same file,
    even in this process, does not get the lock while this one is held
    (flock(2), the man page installed in this distro, read 2026-11-17: "Locks created by
    flock() are associated with an open file description"; tests/test_boundary.py shows it).
    """
    private_dir(path.parent)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    except OSError as e:
        raise BoundaryError(f"cannot open the account lock {path}") from e
    handle = open(fd, "r+b", buffering=0)  # noqa: SIM115 - the caller keeps it open
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise BoundaryError(
            "another run of this lane holds the account lock: one order-placing client per "
            "Gateway session"
        ) from None
    except OSError as e:
        handle.close()
        raise BoundaryError(f"cannot lock the account lock {path}") from e
    return handle


def attached_api_sockets(proc: Path, port: int) -> list[str]:
    """The states of sockets in ``proc``'s TCP tables whose local port is ``port`` and whose
    state is ESTABLISHED or CLOSE_WAIT. A listening socket is not counted."""
    found: list[str] = []
    for name, required in (("tcp", True), ("tcp6", False)):
        table = proc / "net" / name
        try:
            lines = table.read_text(encoding="ascii").splitlines()
        except FileNotFoundError:
            if required:
                raise BoundaryError(f"cannot read {table}") from None
            continue
        except (OSError, UnicodeError) as e:
            raise BoundaryError(f"cannot read {table}") from e
        for line in lines[1:]:
            fields = line.split()
            try:
                local, state = fields[1], fields[3]
                local_port = int(local.rsplit(":", 1)[1], 16)
            except (IndexError, ValueError) as e:
                raise BoundaryError(f"unexpected line in {table}") from e
            if local_port == port and state in _ATTACHED_STATES:
                found.append(_ATTACHED_STATES[state])
    return found


_PYTHON_NAME = re.compile(r"python[0-9]*(\.[0-9]+)*")
# The interpreter's own command line, as CPython 3.14 parses it: the options of
# https://docs.python.org/3.14/using/cmdline.html and the parser itself, Python/getopt.c at
# https://github.com/python/cpython/blob/v3.14.0/Python/getopt.c (both read 2026-11-17), whose
# SHORT_OPTS are "bBc:dEhiIm:OPqRsStuvVW:xX:?". Checked against the wheel's Python 3.14.10.
_PYTHON_FLAGS = frozenset("bBdEiIOPqRsStuvx")  # no value; may be clustered (-uIB, -OO)
_PYTHON_FLAGS_WITH_VALUE = frozenset("WX")  # the rest of the cluster, or the next argument
_PYTHON_LONG_WITH_VALUE = frozenset({"check-hash-based-pycs"})  # always the next argument
# Anything else (-h, -?, -V, --help, --version, --help-all, --help-env, --help-xoptions, or an
# unknown option) makes the interpreter print and exit, or refuse the command line: no entry.
_OPTIONS_END = -1  # what _python_cluster returns for a "-" that ends a cluster


def _is_python(word: str) -> bool:
    """Whether ``word`` names a Python interpreter: a base name ``python``, with an optional
    version (``python3``, ``python3.14``)."""
    return _PYTHON_NAME.fullmatch(os.path.basename(word)) is not None


# Runners that run another program in their own process, named by later arguments: the
# standard library's pdb, cProfile, profile, trace and runpy, and coverage and debugpy. Not
# timeit (it takes statements) and not pytest (its -m selects markers).
_RUNNER_MODULES = frozenset({"pdb", "cProfile", "profile", "trace", "runpy", "coverage", "debugpy"})
# The same runners started as console scripts: the kernel runs ``python .../bin/coverage``.
_RUNNER_SCRIPT = re.compile(r"(coverage|pdb|debugpy)[0-9.-]*")

Entry = tuple[str, str, list[str]]  # (kind, value, the program's own arguments)


def _python_entry(args: list[str]) -> Entry | None:
    """The entry point of the Python interpreter that is the process, from its argument vector
    ``args``: ``("module", name, ...)``, ``("command", program, ...)``, ``("script", path, ...)``
    or ``("stdin", "-", ...)``, each with the arguments after it; ``None`` when the process is
    not an interpreter, or when it would run nothing.

    The interpreter must be the first argument: a base name ``python`` with an optional
    version. A launcher (``uv run``, ``env``, ``nice``, ``timeout``, ``sudo``, ``systemd-run``)
    either replaces itself with the interpreter or starts it as a child; either way there is a
    process whose first argument is the interpreter, with its own entry under ``/proc``, and
    that one is parsed. Its options are parsed as CPython does: ``-c`` and ``-m`` take the rest
    of their cluster or the next argument and end the options; ``--``, or a ``-`` that ends a
    cluster, ends them too; the first argument that is not an option is the script, and ``-``
    is standard input. Every argument after the entry point is the program's own.
    """
    if not args or not _is_python(args[0]):
        return None
    rest = args[1:]
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "-":
            return ("stdin", arg, rest[i + 1 :])
        if not arg.startswith("-"):
            return ("script", arg, rest[i + 1 :])
        i += 1
        if arg == "--":
            break
        parsed = _python_cluster(arg[1:], rest, i)
        if parsed == _OPTIONS_END:
            break
        if not isinstance(parsed, int):
            return parsed
        i = parsed
    else:
        return None  # no entry point: the interactive prompt
    if i >= len(rest):
        return None
    return ("stdin" if rest[i] == "-" else "script", rest[i], rest[i + 1 :])


def _python_cluster(cluster: str, rest: list[str], i: int) -> Entry | int | None:
    """One option argument without its leading ``-``; ``rest[i]`` is the argument after it.
    Returns the entry point it names, the index of the next argument to parse,
    ``_OPTIONS_END``, or ``None`` when the interpreter would exit or refuse the command line."""
    for k, flag in enumerate(cluster):
        attached = cluster[k + 1 :]
        if flag in "cm":
            if not attached and i >= len(rest):
                return None
            value, after = (attached, rest[i:]) if attached else (rest[i], rest[i + 1 :])
            return ("command" if flag == "c" else "module", value, after)
        if flag in _PYTHON_FLAGS_WITH_VALUE:
            if attached:
                return i
            return i + 1 if i < len(rest) else None
        if flag == "-":  # a long option, or a lone "-" that ends the options
            if not attached:
                return _OPTIONS_END
            if attached in _PYTHON_LONG_WITH_VALUE and i < len(rest):
                return i + 1
            return None
        if flag not in _PYTHON_FLAGS:
            return None
    return i


def _program_imports(program: str, module: str) -> bool:
    """Whether a ``-c`` program names the dotted ``module`` or imports it with
    ``from <package> import <name>``."""
    package, _, name = module.rpartition(".")
    if re.search(rf"(?<![\w.]){re.escape(module)}(?!\w)", program):
        return True
    imported = rf"(?<![\w.])from\s+{re.escape(package)}\s+import\s+(\([^)]*\)|[^;\n]*)"
    for names in re.findall(imported, program):
        for item in names.strip("()").split(","):
            if re.match(rf"\s*{re.escape(name)}(?!\w)", item):
                return True
    return False


def _runs_module(args: list[str], module: str) -> bool:
    """Whether the command line ``args`` runs ``module``; see ``other_project_lanes``."""
    entry = _python_entry(args)
    if entry is None:
        return False
    kind, value, after = entry
    module_path = module.replace(".", "/") + ".py"
    if kind == "module":
        return value == module or (
            value in _RUNNER_MODULES and _runner_runs(after, module, module_path)
        )
    if kind == "script":
        if _RUNNER_SCRIPT.fullmatch(os.path.basename(value)):
            return _runner_runs(after, module, module_path)
        return _is_module_file(value, module_path)
    if kind == "command":
        return _program_imports(value, module)
    return False  # standard input: nothing on the command line says what it runs


def _is_module_file(path: str, module_path: str) -> bool:
    return path == module_path or path.endswith("/" + module_path)


def _runner_runs(after: list[str], module: str, module_path: str) -> bool:
    """Whether a runner's arguments name ``module``: ``-m <module>``, ``--module <module>``,
    ``-m<module>``, ``--module=<module>``, or the module's file. A heuristic that over-counts
    in the safe direction: a runner whose own program merely receives such arguments counts."""
    for j, arg in enumerate(after):
        if arg in ("-m", "--module") and after[j + 1 : j + 2] == [module]:
            return True
        if arg in (f"-m{module}", f"--module={module}") or _is_module_file(arg, module_path):
            return True
    return False


def other_project_lanes(proc: Path) -> list[str]:
    """The modules of ``OTHER_PROJECT_LANES`` that a process under ``proc`` runs.

    A heuristic over command lines; the attached-socket check beside it
    (``attached_api_sockets``) is the hard evidence. Each process's real argument vector is
    read (``/proc/<pid>/cmdline``, split on NUL only, never on whitespace) and the Python
    interpreter's entry point is parsed from it (``_python_entry``). A process counts when that
    entry point is:

    - the module itself (``-m meridian.ibkr_paper``, ``-um meridian.ibkr_paper``);
    - a script whose path ends with the module's file path (``.../meridian/ibkr_paper.py``);
    - a ``-c`` program that names the dotted module or imports it with
      ``from <package> import <name>``;
    - a runner that runs another program in the same process (``-m pdb``, ``-m cProfile``,
      ``-m profile``, ``-m trace``, ``-m runpy``, ``-m coverage``, ``-m debugpy``, or the
      ``coverage``, ``pdb`` or ``debugpy`` console script as the script), when a later argument
      is ``-m <module>``, ``--module <module>``, ``-m<module>``, ``--module=<module>`` or the
      module's file. This part over-counts in the safe direction: a runner whose own program
      merely receives such arguments counts too.

    Otherwise arguments after the entry point belong to the program and never count. Only a
    process whose first argument is the interpreter is parsed: ``rg python x.py`` or
    ``vim .../ibkr_paper.py`` does not count. A launcher (``uv run``, ``env``, ``nice``,
    ``timeout``, ``sudo``, ``systemd-run``) or a shell wrapper (``bash -lc '...'``) is not
    parsed either: it replaces itself with the interpreter or starts it as a child, and that
    Python process has its own entry under ``/proc``, with the interpreter as its first
    argument, and is the one that runs the lane. A process that ended meanwhile, or whose
    command line cannot be read, is skipped.
    """
    try:
        entries = list(proc.iterdir())
    except OSError as e:
        raise BoundaryError(f"cannot list {proc}") from e
    found: set[str] = set()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        args = [os.fsdecode(arg) for arg in raw.split(b"\0")]
        if args and args[-1] == "":
            args.pop()  # the kernel ends the command line with a NUL
        found.update(module for module in OTHER_PROJECT_LANES if _runs_module(args, module))
    return sorted(found)


def require_exclusive_use(
    settings: Settings | None = None, held: ExclusiveUse | None = None
) -> ExclusiveUse:
    """Check 5. Without ``held``, take the account lock; with it, require that lock to be still
    held on the same file. Then refuse on another attached API client or another project's
    lane. Returns what the caller must keep for the life of the process."""
    settings = _settings(settings)
    if held is None:
        path = settings.account_lock
        exclusive = ExclusiveUse(lock=_take_account_lock(path), lock_path=path)
    else:
        if not held.holds_lock_file():
            raise BoundaryError("the account lock this run took is no longer held")
        exclusive = held
    try:
        attached = attached_api_sockets(settings.proc, PAPER_PORT)
        if attached:
            states = ", ".join(sorted(set(attached)))
            raise BoundaryError(
                f"{len(attached)} connection(s) to the paper port are open ({states}): another "
                "API client is attached to the Gateway, or a closed one is left over"
            )
        lanes = other_project_lanes(settings.proc)
        if lanes:
            raise BoundaryError(
                f"a process runs a lane of the owner's other project ({', '.join(lanes)}); "
                "that lane uses the same paper account"
            )
    except BoundaryError:
        if held is None:
            exclusive.lock.close()
        raise
    return exclusive


# --- the owner's commands ------------------------------------------------------------------------


def contract_text(account_id: str) -> str:
    """The contract that allowlists exactly this account, as the owner would write it."""
    contract = {
        "schema": SCHEMA,
        "mode": "paper",
        "ibkr": {
            "gateway_host": GATEWAY_HOST,
            "gateway_port": PAPER_PORT,
            "paper_accounts_sha256": [account_hash(account_id)],
        },
    }
    return json.dumps(contract, indent=2) + "\n"


def _contract(settings: Settings | None = None) -> int:
    """Print a contract for the account the Gateway reports, once the owner confirms it.

    The contract is optional. The owner's own shell writes the file
    (``> risk/boundary.contract.json``); this prints to stdout only. Prompts and the account
    id go to the terminal (stderr), never stdout. Without a person at a terminal it refuses,
    so a tool call cannot produce a contract.
    """
    if not _owner_at_terminal():
        print("REFUSED: the contract command needs the owner at a terminal", file=sys.stderr)
        return 2
    from astrolabe import ib_probe

    settings = _settings(settings)
    try:
        require_paper_login(settings=settings)
        require_gateway_on_paper_port_only(settings)
        reported = ib_probe.managed_accounts(GATEWAY_HOST, PAPER_PORT, CONTRACT_PROBE_CLIENT_ID)
        account = single_paper_form_account(reported)
    except (BoundaryError, ib_probe.ProbeError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(
        f"The Gateway on {GATEWAY_HOST}:{PAPER_PORT} reports one account: {account}\n"
        "Check that the Gateway window says Paper Trading and that this is your paper "
        "account.\n"
        f"Type {CONFIRM_WORD} to print the contract for it: ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    if _read_answer().strip() != CONFIRM_WORD:
        print("Not confirmed; nothing printed.", file=sys.stderr)
        return 2
    sys.stdout.write(contract_text(account))
    return 0


def _owner_at_terminal() -> bool:
    # stdout is redirected to the file, so the terminal is stdin and stderr.
    return sys.stdin.isatty() and sys.stderr.isatty()


def _read_answer() -> str:
    return sys.stdin.readline()


def _main(argv: list[str], settings: Settings | None = None) -> int:
    if argv == ["contract"]:
        return _contract(settings)
    if argv != ["hash"]:
        print("usage: python -m astrolabe.boundary hash|contract", file=sys.stderr)
        return 2
    account_id = getpass.getpass("Paper account id (not echoed): ")
    if not account_id.strip():
        print("no account id given", file=sys.stderr)
        return 2
    print(account_hash(account_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
