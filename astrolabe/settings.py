"""Where the lane finds things on this machine. Own design, on the standard library.

Paths only. The Gateway target and the API client ids are constants in ``ib_ids`` and are never
settings. ``Settings.current()`` reads no environment variable: the home folder comes from the
password database entry of the user who runs the process (``_user_home``), not from ``HOME``,
and the state folder is always ``<home>/.local/state/astrolabe``, whatever ``XDG_STATE_HOME``
says. So every shell of one user locks the same account lock for the same Gateway, and run
records and engine logs, which can show the account id, land in the one place the agent guard
protects. ``current()`` refuses a state folder inside the checkout, or a checkout inside the
state folder.
"""

from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from pathlib import Path


def _user_home() -> Path:
    """The home folder of the user who runs the process, from the password database."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@dataclass(frozen=True)
class Settings:
    # The checkout the lane runs from. The owner's contract, if any, is below it.
    repo_root: Path
    # The IB Gateway's folder of the user who runs the lane: its settings file and its install.
    jts_dir: Path
    # This project's state: run records, evidence, engine logs, the account lock.
    state_dir: Path
    proc: Path = Path("/proc")

    @classmethod
    def current(cls) -> Settings:
        """The settings of the user who runs the lane. Reads no environment variable.

        Raises ``RuntimeError`` when the state folder is inside the checkout or the checkout is
        inside the state folder.
        """
        home = _user_home()
        repo_root = Path(__file__).resolve().parent.parent
        state_dir = home / ".local" / "state" / "astrolabe"
        state = state_dir.resolve()
        if state.is_relative_to(repo_root):
            raise RuntimeError(
                f"the lane's state folder {state} is inside the checkout {repo_root}: run "
                "records and engine logs must stay outside the git tree"
            )
        if repo_root.is_relative_to(state):
            raise RuntimeError(
                f"the checkout {repo_root} is inside the lane's state folder {state}: run "
                "records and engine logs must stay outside the git tree"
            )
        return cls(repo_root=repo_root, jts_dir=home / "Jts", state_dir=state_dir)

    @property
    def contract_path(self) -> Path:
        return self.repo_root / "risk" / "boundary.contract.json"

    @property
    def jts_ini(self) -> Path:
        return self.jts_dir / "jts.ini"

    @property
    def gateway_dir(self) -> Path:
        return self.jts_dir / "ibgateway"

    @property
    def lane_dir(self) -> Path:
        return self.state_dir / "ibkr-paper"

    @property
    def runs_dir(self) -> Path:
        """Full local run records. They can hold the account id and never leave the machine."""
        return self.lane_dir / "runs"

    @property
    def evidence_dir(self) -> Path:
        """Scrubbed records of passed runs: the only records that go to the evidence repository."""
        return self.lane_dir / "evidence"

    @property
    def logs_dir(self) -> Path:
        """Engine logs. They can show the account id and never leave the machine."""
        return self.lane_dir / "logs"

    @property
    def account_lock(self) -> Path:
        """Held for a whole run: one order-placing client per Gateway session."""
        return self.lane_dir / "account.lock"
