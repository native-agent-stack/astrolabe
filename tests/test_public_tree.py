"""
This repository is public. The build fails if an IBKR-shaped account id, a copy of the owner's
allowlist, a home path with a user name, an e-mail address or the name of the user running the
tests appears in any file git can see, or if CI gains something a fork's pull request could
abuse.

Account ids, raw order or fill history and tuned parameters belong in the private
``astrolabe-evidence`` repository. Like the paper-only scan, this is a tripwire: it catches a
pasted value, not a determined one. Tests that need such a value build it from parts, so the
literal never appears.

Ported from meridian@014f706d:tests/test_public_tree.py; the Alpaca account shapes, the
public-report exception and the landscape workflow recipe are dropped. The home-path and e-mail
scans are the two rules of ``.gitleaks.toml`` written in Python; the user-name scan is new.
"""

import getpass
import json
import re
import subprocess
from pathlib import Path

import pytest
from test_paper_only import REPO_ROOT, git_files, is_link_to_listed_files, scan_bytes

# Nothing is skipped, this file included: its examples are built from parts.
NOT_SCANNED: set[str] = set()

# A capital D, U, F or I, at most two more capital letters, then 5 to 9 digits, standing alone.
# Covers the shapes in IBKR's and NautilusTrader's documentation examples: U, F and I for
# individual, advisor and institutional accounts; DU, DF and DUD for paper ones; 5 to 7 digits.
ACCOUNT_ID = re.compile(r"(?<![A-Za-z0-9])[DUFI][A-Z]{0,2}[0-9]{5,9}(?![A-Za-z0-9])")

WORKFLOW_HAZARDS = {
    "CI context token": re.compile(
        r"github\s*[.\[]\s*[\'\"]?token|tojson\(\s*github", re.IGNORECASE
    ),
    "pull_request_target trigger (runs fork code with this repo's token)": re.compile(
        r"pull_request_target"
    ),
    "self-hosted runner": re.compile(r"self-hosted", re.IGNORECASE),
    "CI secret": re.compile(
        r"secrets\s*[.\[]|tojson\(\s*secrets|secrets\s*:\s*inherit", re.IGNORECASE
    ),
}

# A literal list of hashes under the allowlist's key, as in a copy of the owner's contract.
# The key is built from parts so that this file does not hold it.
ALLOWLIST_KEY = "paper_accounts" + "_sha256"
ALLOWLIST_LITERAL = re.compile(
    re.escape(ALLOWLIST_KEY) + r"""["']?\s*[:=]\s*\[[^\]]*?[0-9a-fA-F]{64}"""
)

# .gitleaks.toml, rule public-home-path: a Linux, Windows or WSL home with a name after it.
# Python's re has no \p{L}\p{N}; [^\W_] is a Unicode letter or digit.
HOME_PATH = re.compile(
    r"(?:^|[^\w/\\])(?:/+|[a-z]:\\+)(?:home/|Users[/\\]+|mnt/[a-z]/Users/)"
    r"([^\W_][^/\\\s\"'<>|:]*)",
    re.IGNORECASE | re.MULTILINE,
)
# Not in .gitleaks.toml: a WSL home reached from Windows (\\wsl.localhost\<distro>\home\<name>).
WSL_UNC_HOME = re.compile(
    r"\\\\wsl(?:\.localhost|\$)\\[^\\\s]+\\home\\([^\W_][^\\\s\"'<>|:]*)", re.IGNORECASE
)
# The generic account, synthetic homes and the CI runner; never a person's login.
HOME_NAMES_ALLOWED = {"owner", "example", "runner"}

# .gitleaks.toml, rule public-email, and its allowlist.
EMAIL = re.compile(
    r"[a-zA-Z0-9_][a-zA-Z0-9._%+!#$&'*?^`{|}~-]*@[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z][a-zA-Z0-9-]*"
)
EMAIL_ALLOWED = (
    # RFC 2606 example domains, subdomains included.
    re.compile(r"(?i)^[^@]+@(?:[a-z0-9-]+\.)*(?:example\.(?:com|net|org)|example|invalid|test)$"),
    # The exact commit-trailer addresses of the agents.
    re.compile(r"^(?:noreply@(?:openai|anthropic)|bot@(?:openai|deepseek))\.com$"),
    # SSH clone remotes use the literal user git; systemd units are not mailboxes.
    re.compile(r"^git@[^@]+$"),
    re.compile(r"\.(?:service|timer|socket|target|mount|path)(?:\.d)?$"),
)

# Logins that name no person: CI's, a container's and the generic ones above.
GENERIC_LOGINS = HOME_NAMES_ALLOWED | {"root"}


def tracked_files() -> list[str]:
    """Every name git would track: committed, staged and untracked but not ignored."""
    deleted = set(git_files("--deleted"))
    listed = git_files("--cached", "--others", "--exclude-standard")
    seen = set(listed)
    names = [name for name in listed if name not in NOT_SCANNED and name not in deleted]
    # A link must point at files git lists; it is read as its own text, its target by name.
    for name in names:
        is_link_to_listed_files(name, seen)
    return names


def read_text(name: str, root: Path = REPO_ROOT) -> str:
    data = scan_bytes(name, root)
    assert b"\0" not in data, f"{name} is not plain text (UTF-16 or binary); cannot scan it"
    return data.decode("utf-8", errors="replace")


def account_id_lines(text: str) -> list[int]:
    """Line numbers holding an IBKR account-id shape."""
    return [n for n, line in enumerate(text.splitlines(), start=1) if ACCOUNT_ID.search(line)]


def account_id_hits(names: list[str], root: Path = REPO_ROOT) -> list[str]:
    """file:line for every line holding an account-id shape; the location only, never the value."""
    return [
        f"{name}:{line_no}" for name in names for line_no in account_id_lines(read_text(name, root))
    ]


def allowlist_copies(names: list[str], root: Path = REPO_ROOT) -> list[str]:
    """Files holding a literal list of hashes under the allowlist's key; names only."""
    return [name for name in names if ALLOWLIST_LITERAL.search(read_text(name, root))]


def home_path_lines(text: str) -> list[int]:
    """Line numbers holding a home path with a name that is not a generic one."""
    found = set()
    for match in [*HOME_PATH.finditer(text), *WSL_UNC_HOME.finditer(text)]:
        if match[1].lower() not in HOME_NAMES_ALLOWED:
            found.add(text.count("\n", 0, match.start(1)) + 1)
    return sorted(found)


def email_lines(text: str) -> list[int]:
    """Line numbers holding an e-mail address outside the allowlist."""
    found = set()
    for match in EMAIL.finditer(text):
        if not any(allowed.search(match[0]) for allowed in EMAIL_ALLOWED):
            found.add(text.count("\n", 0, match.start()) + 1)
    return sorted(found)


def personal_names() -> set[str]:
    """The login of the user running the tests and the owner of the origin remote, if any,
    unless the name is a generic one."""
    names = {getpass.getuser()}
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if remote.returncode == 0 and (match := re.search(r"github\.com[:/]([^/]+)/", remote.stdout)):
        names.add(match[1])
    return {name for name in names if name.lower() not in GENERIC_LOGINS and len(name) >= 3}


def name_lines(text: str, names: set[str]) -> list[int]:
    patterns = [re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.I) for name in names]
    return [
        n
        for n, line in enumerate(text.splitlines(), start=1)
        if any(pattern.search(line) for pattern in patterns)
    ]


def personal_hits(names: list[str], root: Path = REPO_ROOT) -> list[str]:
    """file:line and the kind for each home path, address or personal name; never the value."""
    people = personal_names()
    hits = []
    for name in names:
        text = read_text(name, root)
        hits += [f"{name}:{n}: home path" for n in home_path_lines(text)]
        hits += [f"{name}:{n}: e-mail address" for n in email_lines(text)]
        hits += [f"{name}:{n}: personal name" for n in name_lines(text, people)]
    return hits


def test_no_account_id_in_the_public_tree():
    files = tracked_files()
    assert files, "git reported no files; the scan would pass without looking at anything"
    assert account_id_hits(files) == []


def test_no_copy_of_the_allowlist_in_the_public_tree():
    assert allowlist_copies(tracked_files()) == []


def test_no_home_path_address_or_personal_name_in_the_public_tree():
    assert personal_hits(tracked_files()) == []


def test_both_scans_find_what_is_planted(tmp_path):
    # The scans themselves, on planted files outside the repository (values built at run time).
    (tmp_path / "clean.md").write_text("nothing here\n", encoding="utf-8")
    (tmp_path / "id.md").write_text("one\naccount " + "DU" + "1234567\n", encoding="utf-8")
    copy = json.dumps({"ibkr": {ALLOWLIST_KEY: ["ab" * 32]}})
    (tmp_path / "copy.json").write_text(copy, encoding="utf-8")
    names = ["clean.md", "id.md", "copy.json"]
    assert account_id_hits(names, tmp_path) == ["id.md:2"]
    assert allowlist_copies(names, tmp_path) == ["copy.json"]


def test_the_allowlist_check_catches_a_copy_of_a_contract():
    copy = json.dumps({"ibkr": {ALLOWLIST_KEY: ["ab" * 32]}}, indent=2)
    assert ALLOWLIST_LITERAL.search(copy)
    assert ALLOWLIST_LITERAL.search(f"{ALLOWLIST_KEY} = ['{'cd' * 32}']")
    assert not ALLOWLIST_LITERAL.search(f'"{ALLOWLIST_KEY}": [account_hash(account_id)]')


@pytest.mark.parametrize(
    "name",
    [
        "risk/boundary.contract.json",
        "risk/boundary.contract.json.bak",
        "risk/boundary.contract.json~",
        "risk/.boundary.contract.json.swp",
        "other.contract.json",
        ".env",
        "runs/one.json",
    ],
)
def test_the_allowlist_and_any_copy_of_it_are_ignored_by_git(name):
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", name], cwd=REPO_ROOT, check=False
    )
    assert ignored.returncode == 0, f"git would not ignore {name}"


@pytest.mark.parametrize(
    "parts",
    [
        ("U", "1234567"),
        ("U", "12345678"),
        ("DU", "1234567"),
        ("DF", "1234567"),
        ("F", "1234567"),
        ("I", "1234567"),
        ("DF", "12345"),
        ("DUD", "12345"),
    ],
)
def test_the_scan_catches_account_id_shapes(parts):
    candidate = "".join(parts)
    assert ACCOUNT_ID.search(f"account = '{candidate}'")
    assert ACCOUNT_ID.search(f"{candidate},{candidate}")


@pytest.mark.parametrize(
    "text",
    [
        "DUFAKE0001",  # the fake ids the boundary tests use
        "UTEST00002",
        "port = 4002",
        "sha256 = 'a9b1ea35ebee0b377255f7d069e45ed41c1fe718cb67208c64fffb2179f3a5be'",
        "ibkr-paper-20261112T213000Z-order.json",
        "ts = 1794002000000000000",
        "E501 UP035 SIM105",
        "U+1234",
    ],
)
def test_the_scan_leaves_other_text_alone(text):
    assert ACCOUNT_ID.search(text) is None


# Home paths, built from parts so that this file holds none.
SLASH_HOME = "/" + "home/"
WINDOWS_USERS = "C:" + "\\" + "Users" + "\\"
WSL_USERS = "/" + "mnt/c/" + "Users/"


@pytest.mark.parametrize(
    "text",
    [
        SLASH_HOME + "someone/src/astrolabe",
        "cd " + SLASH_HOME + "someone",
        "path = '" + SLASH_HOME + "someone/.cache'",
        WINDOWS_USERS + "someone" + "\\AppData",
        "c:" + "\\\\" + "Users" + "\\\\" + "someone",
        WSL_USERS + "someone/Downloads",
        "\\\\" + "wsl.localhost\\distro\\" + "home\\someone\\src",
        "\\\\" + "wsl$\\distro\\" + "home\\someone",
    ],
)
def test_the_home_path_scan_catches_a_named_home(text):
    assert home_path_lines(text) == [1]


@pytest.mark.parametrize(
    "text",
    [
        "~/src/astrolabe",
        "$HOME/src/astrolabe",
        "Path.home() / 'Jts'",
        SLASH_HOME + "owner/src",
        SLASH_HOME + "runner/work",
        SLASH_HOME + "example",
        "https://example.org" + SLASH_HOME + "someone",
        "docs" + SLASH_HOME + "someone",
    ],
)
def test_the_home_path_scan_leaves_generic_homes_alone(text):
    assert home_path_lines(text) == []


AT = "@"


@pytest.mark.parametrize(
    "text",
    ["someone" + AT + "gmail.com", "Name <first.last" + AT + "company.io>", "a" + AT + "b.co"],
)
def test_the_email_scan_catches_an_address(text):
    assert email_lines(text) == [1]


@pytest.mark.parametrize(
    "text",
    [
        "t" + AT + "example.invalid",
        "user" + AT + "mail.example.com",
        "Co-Authored-By: Claude <noreply" + AT + "anthropic.com>",
        "git" + AT + "github.com:org/repo.git",
        "meridian-wsl" + AT + "boot.service",
        "actions/checkout" + AT + "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "react" + AT + "18.3.1",
        "    " + AT + "pytest.mark.parametrize",
    ],
)
def test_the_email_scan_leaves_allowed_and_non_addresses_alone(text):
    assert email_lines(text) == []


def test_the_name_scan_matches_whole_words_only():
    name = "zq" + "xname"
    assert name_lines(f"by {name}\nnot {name}s or x{name}\n", {name}) == [1]
    assert name_lines(f"{name.upper()}", {name}) == [1]


def test_nothing_under_github_is_a_link():
    # The workflow check reads a link as its own text, so a workflow must be a real file.
    names = git_files("--cached", "--others", "--exclude-standard", "--", ".github")
    assert [name for name in names if (REPO_ROOT / name).is_symlink()] == []


def workflow_hits(name: str, text: str) -> list[str]:
    return [
        f"{name}: {label}" for label, pattern in WORKFLOW_HAZARDS.items() if pattern.search(text)
    ]


def test_ci_has_no_fork_reachable_token_secret_or_runner():
    workflows = [n for n in tracked_files() if n.startswith(".github/workflows/")]
    assert workflows, "no workflow files found; the check would pass without looking"
    hits = [hit for name in workflows for hit in workflow_hits(name, read_text(name))]
    assert hits == []


@pytest.mark.parametrize(
    "text",
    [
        "on:\n  pull_request_target:\n",
        "runs-on: [self-hosted, linux]",
        "token: ${{ secrets.SOME_TOKEN }}",
        "key: ${{ Secrets . KEY }}",
        "token: ${{ secrets['KEY'] }}",
        "env: ${{ toJSON(secrets) }}",
        "    secrets: inherit",
        "GITHUB_TOKEN: ${{ github.token }}",
        "env: ${{ toJSON(github) }}",
    ],
)
def test_the_workflow_check_catches_hazards(text):
    assert workflow_hits("x.yml", text)


def test_the_workflow_check_leaves_this_ci_alone():
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert workflow_hits(".github/workflows/ci.yml", text) == []
    assert ".github/workflows/ci.yml" in tracked_files()
