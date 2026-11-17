"""Settings: paths only, from the password database's home folder and no environment variable,
with records outside the git tree."""

import dataclasses
import os
import pwd
from pathlib import Path

import conftest
import pytest

from astrolabe import settings as settings_module
from astrolabe.settings import Settings

# Taken at import, before any fixture can replace it.
# conftest replaces the lookup when it is imported; it keeps the real one for this test.
REAL_USER_HOME = conftest.REAL_USER_HOME
CHECKOUT = Path(settings_module.__file__).resolve().parent.parent


def test_the_user_home_comes_from_the_password_database_not_from_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert REAL_USER_HOME() == Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert REAL_USER_HOME() != tmp_path


def test_current_follows_the_user_home(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_module, "_user_home", lambda: tmp_path)

    current = Settings.current()

    assert current.repo_root == CHECKOUT
    assert (current.repo_root / "astrolabe" / "settings.py").is_file()
    assert current.jts_dir == tmp_path / "Jts"
    assert current.state_dir == tmp_path / ".local" / "state" / "astrolabe"


def test_home_and_xdg_state_home_change_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_module, "_user_home", lambda: tmp_path / "user")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    without = Settings.current()

    monkeypatch.setenv("HOME", str(tmp_path / "other-home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "other-state"))
    assert Settings.current() == without
    monkeypatch.setenv("XDG_STATE_HOME", str(CHECKOUT))  # XDG_STATE_HOME=$PWD in the checkout
    assert Settings.current() == without
    assert without.state_dir == tmp_path / "user" / ".local" / "state" / "astrolabe"
    assert without.jts_dir == tmp_path / "user" / "Jts"


def test_a_state_folder_inside_the_checkout_is_refused(monkeypatch):
    # A home folder at the checkout puts <home>/.local/state/astrolabe inside it. Nothing is
    # created: the check compares paths only.
    monkeypatch.setattr(settings_module, "_user_home", lambda: CHECKOUT)
    with pytest.raises(RuntimeError, match="state folder .* is inside the checkout"):
        Settings.current()


def test_a_checkout_inside_the_state_folder_is_refused(monkeypatch, tmp_path):
    # The checkout is where settings.py sits, two folders up: place the module's file inside
    # the state folder of a temporary home.
    state = tmp_path / ".local" / "state" / "astrolabe"
    monkeypatch.setattr(settings_module, "_user_home", lambda: tmp_path)
    monkeypatch.setattr(
        settings_module, "__file__", str(state / "checkout" / "astrolabe" / "settings.py")
    )
    with pytest.raises(RuntimeError, match="checkout .* is inside the lane's state folder"):
        Settings.current()


def test_a_state_folder_beside_the_checkout_is_not_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_module, "_user_home", lambda: tmp_path)
    monkeypatch.setattr(
        settings_module, "__file__", str(tmp_path / "src" / "astrolabe" / "astrolabe" / "x.py")
    )
    current = Settings.current()
    assert current.repo_root == (tmp_path / "src" / "astrolabe").resolve()
    assert not current.state_dir.resolve().is_relative_to(current.repo_root)
    assert not current.repo_root.is_relative_to(current.state_dir.resolve())


def test_derived_paths():
    settings = Settings(repo_root=Path("/r"), jts_dir=Path("/h/Jts"), state_dir=Path("/s"))

    assert settings.contract_path == Path("/r/risk/boundary.contract.json")
    assert settings.jts_ini == Path("/h/Jts/jts.ini")
    assert settings.gateway_dir == Path("/h/Jts/ibgateway")
    assert settings.lane_dir == Path("/s/ibkr-paper")
    assert settings.runs_dir == Path("/s/ibkr-paper/runs")
    assert settings.evidence_dir == Path("/s/ibkr-paper/evidence")
    assert settings.logs_dir == Path("/s/ibkr-paper/logs")
    assert settings.account_lock == Path("/s/ibkr-paper/account.lock")
    assert settings.proc == Path("/proc")


def test_settings_are_frozen():
    settings = Settings(repo_root=Path("/r"), jts_dir=Path("/j"), state_dir=Path("/s"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.state_dir = Path("/elsewhere")  # pyright: ignore[reportAttributeAccessIssue]


def test_settings_hold_no_host_port_or_client_id():
    names = {field.name for field in dataclasses.fields(Settings)}
    assert names == {"repo_root", "jts_dir", "state_dir", "proc"}
