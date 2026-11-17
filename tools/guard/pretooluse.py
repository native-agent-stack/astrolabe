#!/usr/bin/env python3
"""Guard for agent sessions in this repository: it refuses tool calls that cross a boundary.

Own design, on Claude Code's PreToolUse hooks (https://code.claude.com/docs/en/hooks, read
2026-11-17): the hook receives the tool call as JSON on stdin (``tool_name``, ``tool_input``)
and blocks it by exiting with code 2, with the reason on stderr. ``.claude/settings.json``
registers it for the shell and file tools. It runs under the system Python with the standard
library only.

What it refuses (AGENTS.md, "Boundaries"):

- a shell command that names an IB API port as a whole token, unless every part of it only
  searches, shows or records text (``rg``, ``grep``, a local ``git`` subcommand, a ``gh``
  command that talks to GitHub, a text filter such as ``head``);
- starting the lane, the probe or the contract command, in any mode, and an interpreter
  one-liner that loads one of the lane's connecting modules, also when it would only inspect
  it: read the source instead;
- changing the state of an astrolabe unit with ``systemctl``, and removing a hold or a marker;
- reading the Gateway's files, the lane's local run records and engine logs, key stores and
  environment files;
- changing ``risk/``, a contract file or anything under ``upstream/``, by a file tool or by a
  shell command, and removing or staging git-ignored files (the owner's contract is one).

How a shell command is read. The port, lane, unit, hold and protected-folder rules look at the
command's text. The rules about which file a command reads and what it can change look at its
words: the command is split into simple commands with the standard library's ``shlex``, so
quotes are respected and a commit message is not taken for a path. A word is a file unless it
stands where a search program or git takes a pattern or a message. The text of a
here-document is data unless it feeds a shell or an interpreter, and a substitution inside it
is a command; the argument of ``sh -c`` and of ``eval``, and the inside of ``$(...)`` and of
backticks, are read as commands too. A command that cannot be split (an open quote) is judged
by its text alone, strictly.

A guardrail, not a security boundary. It stops accidental and casual attempts. A process that
deliberately writes its own socket code, builds a name at run time (a variable, a glob, a
brace expansion), hides a file name inside a program's own syntax or reads a protected folder
through one of its parents is not stopped here, which is why the Gateway accepts orders only
while the owner attends (docs/RUNBOOK.md).
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import PurePosixPath
from typing import Any

# The Gateway's and TWS's default socket ports for a paper login, and the live port that sits
# one below each. tests/test_guard.py holds GATEWAY_PAPER_PORT equal to astrolabe.ib_ids.
GATEWAY_PAPER_PORT = 4002
TWS_PAPER_PORT = 7497
API_PORTS = (
    GATEWAY_PAPER_PORT,
    GATEWAY_PAPER_PORT - 1,
    TWS_PAPER_PORT,
    TWS_PAPER_PORT - 1,
)
_PORT_TOKEN = re.compile(r"(?<![\w.])(?:" + "|".join(str(port) for port in API_PORTS) + r")(?!\w)")

# --- rules on the command's text -----------------------------------------------------------

# Substitutions, a URL and bash's own socket paths: a command with any of these is never taken
# for one that only searches, shows or records text.
_NOT_PLAIN = re.compile(r"\$\(|`|<\(|>\(|://|/dev/(?:tcp|udp)/")

# The lane's modules that connect to the Gateway. ``gateway_probe`` is the timer's probe, which
# arrives with the automation track; it is named here so that it is refused from its first day.
_LANE_MODULES = r"(?:ibkr_paper|ibkr_node|ib_probe|gateway_probe)"
_LANE_ENTRY = re.compile(
    rf"-m\s*[\"']?astrolabe\.{_LANE_MODULES}\b"
    r"|-m\s*[\"']?astrolabe\.boundary[\"']?\s+contract\b"
    rf"|\bpython[\d.]*\s+\S*astrolabe/{_LANE_MODULES}\.py\b"
)
# A program text that loads one of those modules, or calls the contract command's function.
_LANE_LOAD = re.compile(
    rf"\bastrolabe\.{_LANE_MODULES}\b"
    rf"|\bimport\b[^;\n]*\b{_LANE_MODULES}\b"
    rf"|\bfrom\s+[\w.]*\b{_LANE_MODULES}\s+import\b"
    r"|\bboundary\b[\s\S]*(?:\b_?contract|\b_main)\s*\("
)
_INTERPRETER = re.compile(r"\bpython[\d.]*\b")
_UNIT_CHANGE = re.compile(
    r"\bsystemctl\b[^;&|\n]*\b(?:start|stop|restart|try-restart|reload|enable|disable|mask"
    r"|unmask|kill|edit|revert|set-property|reset-failed)\b[^;&|\n]*\bastrolabe"
)
_HOLD_REMOVAL = re.compile(
    r"\b(?:rm|unlink|mv|truncate)\b[^;&|\n]*astrolabe[^;&|\n]*\b(?:hold|marker)\b"
)

# Folders no agent reads, as they appear in a command or in a file tool's path. Each ends at a
# name boundary, so "~/Jts", "~/Jts/" and "~/Jts/jts.ini" all match and "~/Jtsx" does not. A
# folder named without a trailing slash is read whole by grep -r, tar or cp -r.
_END = r"(?![\w.-])"
_PROTECTED_PATHS = tuple(
    (re.compile(pattern), what)
    for pattern, what in (
        (rf"/Jts{_END}", "the IB Gateway's own files"),
        # The lane's folder holds runs/ and logs/, which can show the account id, and the
        # account lock. Only its evidence/ folder (scrubbed records) may be read.
        (
            rf"\.local/state/astrolabe/ibkr-paper(?!/evidence{_END}){_END}",
            "the lane's local run records and engine logs, which can show the account id",
        ),
        # The state folder itself, named with nothing below it: a recursive read reaches the
        # lane's folder.
        (
            rf"\.local/state/astrolabe{_END}(?!/[\w.-])",
            "this project's state folder as a whole, which holds the lane's local records",
        ),
        (r"/etc/credstore", "the credential store"),
        (rf"/\.ssh{_END}", "the SSH keys"),
        (rf"/\.gnupg{_END}", "the GnuPG keys"),
        (r"noesis-restic\.key", "a backup key"),
        (r"\.sota-runtime/secrets", "the runtime secrets"),
    )
)

# Files that are protected by their name alone, wherever they are.
_GATEWAY_SETTINGS = "jts.ini"
_SETTINGS_FILE = "the IB Gateway's settings file"
_ENV_FILE = "an environment file, which holds keys"
_GATEWAY_SETTINGS_IN_TEXT = re.compile(r"(?<![\w.-])jts\.ini(?![\w-])")
_ENV_FILE_IN_TEXT = re.compile(r"(?:^|[\s/=\"'(])\.env(?:\.[\w.-]+)?(?=$|[\s\"';|&)])")

# The owner's places and the verbatim place, as a program text or an unreadable command names
# them (with the slash), and what counts there as a way to change a file.
_OWNERS = "it can change risk/ or a contract file, which are the owner's"
_VERBATIM = (
    "it can change upstream/, which is verbatim: it changes only through its own install command"
)
_IGNORED = "it removes or stages git-ignored files, and the owner's contract file is one of them"
_OWNER_IN_TEXT = re.compile(r"(?:^|[\s/=\"'(])risk/|\.contract\.json")
_VERBATIM_IN_TEXT = re.compile(r"(?:^|[\s/=\"'(])upstream/")
_WRITER_NAMES = (
    "rm|rmdir|unlink|shred|mv|cp|rsync|touch|truncate|chmod|chown|chgrp|ln|install|dd|tee"
    "|mkdir|patch|sort|uniq|split|csplit"
)
_INTERPRETER_NAMES = r"python[\d.]*|perl|ruby|node|php|lua|g?awk"
_SHELL_NAMES = "sh|bash|zsh|dash|ksh"
_WRITE_IN_TEXT = re.compile(
    # An output redirection, unless it goes to a file descriptor or to /dev/null.
    r"(?<!>)>++\|?\s*+(?!&[\d-]|/dev/null(?![\w./-]))"
    r"|\bsed\b"
    rf"|(?<![\w.-])(?:{_WRITER_NAMES})(?=\s)"
    r"|\bgit\s+(?:checkout|restore|rm|mv|apply|am|stash|clean)\b"
    rf"|(?<![\w.-])(?:{_INTERPRETER_NAMES}|{_SHELL_NAMES})(?![\w-])"
)
_IGNORED_IN_TEXT = re.compile(
    r"\bgit\b[^;&|\n]*\bclean\b[^;&|\n]*\s-[A-Za-z]*[xX]"
    r"|\bgit\b[^;&|\n]*\bstash\b[^;&|\n]*\s(?:-[A-Za-z]*a[A-Za-z]*\b|--all\b)"
    r"|\bgit\b[^;&|\n]*\badd\b[^;&|\n]*\s(?:-[A-Za-z]*f[A-Za-z]*\b|--force\b)"
)

# --- how a command is split into simple commands ---------------------------------------------

_PUNCTUATION = frozenset("();<>|&")
_OPERATOR = re.compile(r"&&|\|\||\|&|;;|&>>|&>|>>|>&|>\||<<<|<<|<&|<>|[;&|()<>]")
_SEPARATORS = frozenset({"&&", "||", "|&", ";;", ";", "&", "|", "(", ")"})
_OUTPUT = frozenset({"&>>", "&>", ">>", ">&", ">|", ">", "<>"})
_HEREDOC_TAG = re.compile(r"<<-?[ \t]*(?P<quote>[\"']?)(?P<tag>[A-Za-z_][\w-]*)(?P=quote)")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_SHELLS = frozenset(_SHELL_NAMES.split("|"))
_SHELL_COMMAND_OPTION = re.compile(r"-[A-Za-z]*c")
_SHELL_WORD = re.compile(rf"(?<![\w.-])(?:{_SHELL_NAMES})(?![\w.-])")
_INTERPRETER_WORD = re.compile(rf"(?<![\w.-])(?:{_INTERPRETER_NAMES})(?![\w-])")
_INTERPRETER_NAME = re.compile(_INTERPRETER_NAMES)
_MAX_DEPTH = 4


class _Simple:
    """One simple command: its words in order, the targets of its output redirections, the
    sources of its input redirections, and the texts fed to it (a here-string, a
    here-document's closing word).

    A plain class: the tests load this file by its path, outside ``sys.modules``, where a
    dataclass cannot resolve its annotations."""

    def __init__(self) -> None:
        self.words: list[str] = []
        self.writes: list[str] = []
        self.reads: list[str] = []
        self.data: list[str] = []

    @property
    def names(self) -> list[str]:
        return [PurePosixPath(word).name for word in self.words]

    @property
    def runs_an_interpreter(self) -> bool:
        return any(_INTERPRETER_NAME.fullmatch(name) for name in self.names)

    @property
    def carries_a_program(self) -> bool:
        """An interpreter or sed is among its words, so a quoted word can be a program text
        that names a file in its own syntax."""
        return self.runs_an_interpreter or "sed" in self.names


def _without_heredocs(command: str) -> tuple[str, list[tuple[str, str]]]:
    """The command without the texts of its here-documents and with `` ; `` for each newline
    that ends a command, and each here-document's text with the line that owns it. Quotes are
    followed: ``<<`` inside a quoted word starts nothing, and a newline inside one stays, so
    the argument of ``sh -c`` keeps its own lines and its own here-documents."""
    kept: list[str] = []
    documents: list[tuple[str, str]] = []
    pending: list[str] = []  # the closing words opened on the current line
    line_start = 0
    quote = ""
    i, end = 0, len(command)
    while i < end:
        char = command[i]
        if quote:
            kept.append(char)
            if char == "\\" and quote == '"' and i + 1 < end:
                kept.append(command[i + 1])
                i += 1
            elif char == quote:
                quote = ""
            i += 1
        elif char == "\\" and i + 1 < end:
            kept += [char, command[i + 1]]
            i += 2
        elif char in "'\"":
            quote = char
            kept.append(char)
            i += 1
        elif char == "<" and command.startswith("<<", i) and command[i - 1 : i] != "<":
            match = None if command.startswith("<<<", i) else _HEREDOC_TAG.match(command, i)
            taken = match.group(0) if match else "<<<" if command.startswith("<<<", i) else "<<"
            if match:
                pending.append(match.group("tag"))
            kept.append(taken)
            i += len(taken)
        elif char == "\n":
            line = "".join(kept[line_start:])
            kept.append(" ; ")
            i += 1
            for tag in pending:
                body: list[str] = []
                while i < end:
                    stop = command.find("\n", i)
                    stop = end if stop == -1 else stop
                    text_line = command[i:stop]
                    i = min(stop + 1, end)
                    if text_line.strip() == tag:
                        break
                    body.append(text_line)
                documents.append((line, "\n".join(body)))
            pending = []
            line_start = len(kept)
        else:
            kept.append(char)
            i += 1
    return "".join(kept), documents


def _read(command: str, depth: int = 0) -> tuple[list[_Simple], list[str]] | None:
    """The simple commands in a command and the program texts it feeds to an interpreter, or
    None when it cannot be split.

    A quoted word made only of operator characters (``grep ">" file``) reads as an operator.
    That errs on the strict side: the next word then counts as a redirection target or as a
    command of its own.
    """
    text, documents = _without_heredocs(command)
    # A backslash before a newline joins two lines; a backtick starts or ends a command.
    text = text.replace("\\\n", " ").replace("`", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # shlex would end a word at any #, which a shell does not
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    commands = [_Simple()]
    place = ""  # where the next word goes: "" for a word, or writes, reads or data
    for token in tokens:
        if token and all(char in _PUNCTUATION for char in token):
            for operator in _OPERATOR.findall(token):
                if operator in _SEPARATORS:
                    commands.append(_Simple())
                    place = ""
                elif operator in _OUTPUT:
                    place = "writes"
                elif operator == "<":
                    place = "reads"
                else:  # << and <<< take a closing word or a text, <& a descriptor
                    place = "data"
        elif place:
            getattr(commands[-1], place).append(token)
            place = ""
        else:
            commands[-1].words.append(token)
    commands = [s for s in commands if s.words or s.writes or s.reads or s.data]

    programs: list[str] = []
    nested: list[str] = []
    for simple in commands:
        names = simple.names
        for i, name in enumerate(names):
            if name in _SHELLS:
                for j in range(i + 1, len(names) - 1):
                    if _SHELL_COMMAND_OPTION.fullmatch(simple.words[j]):
                        nested.append(simple.words[j + 1])
                        break
            elif name == "eval":
                nested.append(" ".join(simple.words[i + 1 :]))
        for word in (*simple.words, *simple.writes, *simple.reads, *simple.data):
            if "$(" in word:  # a substitution inside double quotes
                nested.append(word.replace("$(", " ; ").replace(")", " ; "))
    for line, body in documents:
        # bash expands a substitution in a here-document whose closing word is not quoted; it
        # is read as a command whatever the quoting, which errs on the strict side.
        nested += [first or second for first, second in _SUBSTITUTION.findall(body)]
        if _SHELL_WORD.search(line):
            nested.append(body)
        elif _INTERPRETER_WORD.search(line):
            programs.append(body)
    if nested and depth >= _MAX_DEPTH:
        return None
    for inner_text in nested:
        inner = _read(inner_text, depth + 1)
        if inner is None:
            return None
        commands += inner[0]
        programs += inner[1]
    return commands, programs


# --- what a simple command is ------------------------------------------------------------------

_SEARCHERS = frozenset({"rg", "grep", "egrep", "fgrep"})
# With the search programs, what a search is piped into: none of these opens a socket.
_FILTERS = frozenset(
    {"head", "tail", "wc", "sort", "uniq", "cut", "tr", "cat", "less", "more", "nl", "tac"}
)
# What stands around a search without reaching anything: a folder change, a line of output.
_HARMLESS = frozenset({"cd", "pwd", "echo", "printf", "true", "false", "ls", "test", "date"})
# Of those, the ones with no way to write a file of their own (sort -o and uniq's second
# operand write one).
_READ_ONLY = frozenset({"head", "tail", "wc", "cut", "tr", "cat", "less", "more", "nl", "tac"})
# git subcommands that work on the local repository only, and options that run a program or
# name a transport.
_GIT_LOCAL = frozenset(
    {
        "grep", "log", "show", "diff", "status", "blame", "add", "commit", "tag", "notes",
        "branch", "describe", "rev-parse", "rev-list", "ls-files", "ls-tree", "cat-file",
        "shortlog", "reflog", "merge-base", "show-ref", "for-each-ref", "switch", "checkout",
        "restore", "reset", "rm", "mv", "stash", "merge", "cherry-pick", "revert", "worktree",
        "apply", "clean", "check-ignore", "hash-object", "version",
    }
)  # fmt: skip
_GIT_RUNS = (
    "--open-files-in-pager",
    "-O",
    "--exec",
    "--ext-diff",
    "--upload-pack",
    "--receive-pack",
)
_GIT_WRITES = frozenset({"checkout", "restore", "rm", "mv", "apply", "am", "stash", "clean"})
# Where git takes a pattern or a message, as an option's next word or attached to it.
_GIT_TEXT_OPTIONS = frozenset({"-S", "-G", "--grep", "--author", "--committer", "-m", "--message"})
_GIT_TEXT_ATTACHED = ("-S", "-G", "--grep=", "--author=", "--committer=", "--message=")
# gh command groups that talk to GitHub only. ``gh api`` takes any endpoint and is not one.
_GH_GITHUB = frozenset({"pr", "issue", "run", "repo", "release", "workflow", "label", "search"})
_WRITERS = frozenset(_WRITER_NAMES.split("|"))
# Options of the search programs that take the next word as their value, so that the pattern
# can be told from the paths (grep(1) and rg(1), the manual pages installed in this distro,
# read 2026-11-17). An option written --name=value is one word and needs no entry.
_GREP_VALUES = frozenset(
    {
        "-e", "-f", "-m", "-A", "-B", "-C", "-d", "-D", "--regexp", "--file", "--max-count",
        "--after-context", "--before-context", "--context", "--include", "--exclude",
        "--exclude-from", "--exclude-dir", "--label", "--binary-files", "--devices",
        "--directories",
    }
)  # fmt: skip
_RG_VALUES = frozenset(
    {
        "-e", "-f", "-g", "-t", "-T", "-A", "-B", "-C", "-m", "-j", "-M", "-E", "-d", "-r",
        "--regexp", "--file", "--glob", "--iglob", "--type", "--type-not", "--type-add",
        "--type-clear", "--after-context", "--before-context", "--context", "--max-count",
        "--threads", "--max-columns", "--encoding", "--max-depth", "--replace",
        "--max-filesize", "--sort", "--sortr", "--color", "--colors", "--engine", "--pre",
        "--pre-glob", "--ignore-file", "--context-separator", "--field-context-separator",
        "--field-match-separator", "--path-separator", "--dfa-size-limit",
        "--regex-size-limit", "--hostname-bin", "--hyperlink-format",
    }
)  # fmt: skip
# A search program's -f with its file attached (-f.env).
_ATTACHED_PATTERN_FILE = re.compile(r"-[A-Za-z]*f(.+)")
# What marks a word as a program text rather than a path.
_PROGRAM_TEXT = re.compile(r"[\s;(){}'\"$]")


def _git_words(words: list[str]) -> list[str] | None:
    """The words of a git call from ``git`` on, wherever git stands (``/usr/bin/git``,
    ``env git``, ``sudo git``); None when no word is git."""
    for i, word in enumerate(words):
        if PurePosixPath(word).name == "git":
            return words[i:]
    return None


def _git_subcommand(words: list[str], *, strict: bool) -> str | None:
    """The subcommand of a ``git`` call. With ``strict``, None unless the only options before
    it are ``--no-pager``, ``-P`` and ``-C DIR``: any other can run a program or point git
    elsewhere."""
    i = 1
    while i < len(words):
        word = words[i]
        if word in ("-C", "-c") and (word == "-C" or not strict):
            i += 2
        elif word in ("--no-pager", "-P") or (not strict and word.startswith("-")):
            i += 1
        else:
            break
    if i < len(words) and not words[i].startswith("-"):
        return words[i]
    return None


def _opens_no_socket(simple: _Simple) -> bool:
    """Whether a simple command only searches, shows or records text, so that a port number
    or a module name among its words is a pattern or a message and not a target."""
    words = simple.words
    if not words:
        return True  # a redirection alone
    program, rest = words[0], words[1:]
    if program in _SEARCHERS:
        return not any(word.startswith(("--pre", "--hostname-bin")) for word in rest)
    if program in _FILTERS or program in _HARMLESS:
        return True
    if program == "git":
        subcommand = _git_subcommand(words, strict=True)
        return subcommand in _GIT_LOCAL and not any(word.startswith(_GIT_RUNS) for word in rest)
    if program == "gh":
        return (
            bool(rest)
            and rest[0] in _GH_GITHUB
            and not any(word.startswith("--hostname") for word in rest)
        )
    return False


def _can_write(simple: _Simple) -> bool:
    """Whether a simple command can change the files its words name."""
    words = simple.words
    if not words:
        return False
    program = words[0]
    if program in _SEARCHERS or program in _READ_ONLY or program == "gh":
        return False  # they read: a word that looks like a tool's name is a pattern or a file
    names = simple.names
    if any(name in _WRITERS or name in _SHELLS for name in names) or simple.runs_an_interpreter:
        return True
    git = _git_words(words)
    if git is not None:
        subcommand = _git_subcommand(git, strict=False)
        return subcommand is None or subcommand in _GIT_WRITES
    if "sed" in names and any(re.fullmatch(r"-[A-Za-z]*i.*|--in-place.*", word) for word in words):
        return True
    return "find" in names and any(
        word == "-delete" or word.startswith(("-exec", "-ok", "-fprint", "-fls")) for word in words
    )


def _pattern_positions(words: list[str]) -> set[int]:
    """Where a search program's patterns stand among its words: the value of each ``-e``, and
    without ``-e`` or ``-f`` the first positional word. The value of ``-f`` is a file."""
    values = _RG_VALUES if words[0] == "rg" else _GREP_VALUES
    patterns: set[int] = set()
    explicit = False
    first: int | None = None
    i = 1
    while i < len(words):
        word = words[i]
        if word == "--":
            if first is None and i + 1 < len(words):
                first = i + 1
            break
        if word.startswith("--"):
            name, equals, _ = word.partition("=")
            explicit = explicit or name in ("--regexp", "--file")
            if name == "--regexp":
                patterns.add(i if equals else i + 1)
            if not equals and name in values:
                i += 1  # its value is the next word
        elif word.startswith("-") and len(word) > 1:
            for k, letter in enumerate(word[1:], start=1):
                if f"-{letter}" in values:
                    explicit = explicit or letter in "ef"
                    if k == len(word) - 1:
                        if letter == "e":
                            patterns.add(i + 1)
                        i += 1  # its value is the next word
                    break
        elif first is None:
            first = i
        i += 1
    if not explicit and first is not None:
        patterns.add(first)
    return patterns


def _git_text_positions(words: list[str]) -> set[int]:
    """Where a git call takes a pattern or a message and not a file: the values of ``-S``,
    ``-G``, ``--grep``, ``--author`` and ``-m``, and ``git grep``'s pattern."""
    texts: set[int] = set()
    for i, word in enumerate(words):
        if word in _GIT_TEXT_OPTIONS:
            texts.add(i + 1)
        elif word.startswith(_GIT_TEXT_ATTACHED):
            texts.add(i)
    if _git_subcommand(words, strict=False) == "grep":
        after = words.index("grep") + 1
        explicit = False
        for i in range(after, len(words)):
            if words[i] in ("-e", "-f"):
                explicit = True
                if words[i] == "-e":
                    texts.add(i + 1)
        for i in range(after, len(words)):
            word = words[i]
            if explicit or word == "--":
                break
            if not word.startswith("-") and not word.isdigit():
                texts.add(i)
                break
    return texts


def _named_file(word: str, *, in_text: bool) -> str | None:
    """Which file protected by its name a word names: as a path, with or without spaces, or,
    with ``in_text``, inside a program text."""
    for piece in re.split(r"[=:,]", word):
        name = PurePosixPath(piece).name
        if name == _GATEWAY_SETTINGS:
            return _SETTINGS_FILE
        if name == ".env" or name.startswith(".env."):
            return _ENV_FILE
    if in_text and _GATEWAY_SETTINGS_IN_TEXT.search(word):
        return _SETTINGS_FILE
    if in_text and _ENV_FILE_IN_TEXT.search(word):
        return _ENV_FILE
    return None


def _protected_file(simple: _Simple) -> str | None:
    """Which file protected by its name a simple command reads or writes. A word that stands
    where a search program or git takes a pattern or a message is not a file."""
    words = simple.words
    searching = bool(words) and words[0] in _SEARCHERS
    texts: set[int] = set()
    if searching:
        texts = _pattern_positions(words)
    else:
        git = _git_words(words)
        if git is not None:
            offset = len(words) - len(git)
            texts = {offset + i for i in _git_text_positions(git)}
    # A search program behind a launcher (sudo grep, env grep) gets no pattern exemption, and
    # its -f with the file attached is still unpacked.
    any_search = any(name in _SEARCHERS for name in simple.names)
    for i, word in enumerate(words):
        if i in texts:
            continue
        attached = _ATTACHED_PATTERN_FILE.fullmatch(word) if any_search else None
        for candidate in (word, attached.group(1)) if attached else (word,):
            what = _named_file(candidate, in_text=simple.carries_a_program)
            if what:
                return what
    for path in (*simple.writes, *simple.reads):
        what = _named_file(path, in_text=False)
        if what:
            return what
    return None


def _owned_place(word: str) -> str | None:
    """Why a path, with or without spaces, may not be changed: it is in risk/, is a contract
    file, or is in upstream/."""
    for piece in re.split(r"[=:,]", word):
        if ".contract.json" in piece:
            return _OWNERS
        parts = PurePosixPath(piece.replace("\\", "/")).parts
        if "risk" in parts:
            return _OWNERS
        if "upstream" in parts:
            return _VERBATIM
    return None


def _ignored_files(git: list[str]) -> bool:
    """Whether a git call removes, stashes or stages git-ignored files."""
    subcommand = _git_subcommand(git, strict=False)
    options = [word for word in git[1:] if word.startswith("-")]
    clusters = [word for word in options if not word.startswith("--")]
    if subcommand == "clean":
        return any("x" in word or "X" in word for word in clusters)
    if subcommand == "stash":
        return "--all" in options or any("a" in word for word in clusters)
    if subcommand == "add":
        return (
            "--force" in options
            or any("f" in word for word in clusters)
            or any(".contract.json" in word for word in git)
        )
    return False


def _owner_write(simple: _Simple) -> str | None:
    """Why a simple command is refused for what it can change, or None."""
    for target in simple.writes:
        why = _owned_place(target)
        if why:
            return why
    words = simple.words
    git = _git_words(words)
    if git is not None and _ignored_files(git):
        return _IGNORED
    if _can_write(simple):
        for word in words:
            why = _owned_place(word)
            if why:
                return why
    if simple.carries_a_program:
        for word in words:
            why = _text_write(word) if _PROGRAM_TEXT.search(word) else None
            if why:
                return why
    return None


def _text_write(text: str) -> str | None:
    """Why a program text is refused for the places it names, or None."""
    if _OWNER_IN_TEXT.search(text):
        return _OWNERS
    if _VERBATIM_IN_TEXT.search(text):
        return _VERBATIM
    return None


def _first_match(rules: tuple[tuple[re.Pattern[str], str], ...], text: str) -> str | None:
    for pattern, what in rules:
        if pattern.search(text):
            return what
    return None


def _unread_refusal(command: str) -> str | None:
    """A command that could not be split, judged by its text alone."""
    if _GATEWAY_SETTINGS_IN_TEXT.search(command):
        return f"it reads {_SETTINGS_FILE}"
    if _ENV_FILE_IN_TEXT.search(command):
        return f"it reads {_ENV_FILE}"
    if _IGNORED_IN_TEXT.search(command):
        return _IGNORED
    if _WRITE_IN_TEXT.search(command):
        return _text_write(command)
    return None


def shell_refusal(command: str) -> str | None:
    """Why this shell command is refused, or None."""
    reading = _read(command)
    commands, programs = reading if reading is not None else ([], [])
    plain = (
        bool(commands)
        and not programs
        and not _NOT_PLAIN.search(command)
        and all(_opens_no_socket(simple) for simple in commands)
    )
    if not plain:
        if _PORT_TOKEN.search(command):
            return "it names an IB API port; no agent connects to the Gateway"
        if _LANE_ENTRY.search(command):
            return "it starts the lane, the probe or the contract command; only the owner does"
        if _INTERPRETER.search(command) and _LANE_LOAD.search(command):
            return (
                "it loads one of the lane's connecting modules in an interpreter; only the "
                "owner runs them (to inspect one, read its source)"
            )
    if _UNIT_CHANGE.search(command):
        return "it changes the state of an astrolabe unit; only the owner does"
    if _HOLD_REMOVAL.search(command):
        return "it removes a hold or a run marker; only the owner does"
    what = _first_match(_PROTECTED_PATHS, command)
    if what:
        return f"it reads {what}"
    if reading is None:
        return _unread_refusal(command)
    for simple in commands:
        what = _protected_file(simple)
        if what:
            return f"it reads {what}"
        why = _owner_write(simple)
        if why:
            return why
    for program in programs:
        if _GATEWAY_SETTINGS_IN_TEXT.search(program):
            return f"it reads {_SETTINGS_FILE}"
        if _ENV_FILE_IN_TEXT.search(program):
            return f"it reads {_ENV_FILE}"
        why = _text_write(program)
        if why:
            return why
    return None


def file_refusal(tool: str, path: str) -> str | None:
    """Why this file tool call is refused, or None. Claude Code passes an absolute path."""
    normal = path.replace("\\", "/")
    pure = PurePosixPath(normal)
    what = _first_match(_PROTECTED_PATHS, normal)
    if what is None and pure.name == _GATEWAY_SETTINGS:
        what = _SETTINGS_FILE
    if what is None and (pure.name == ".env" or pure.name.startswith(".env.")):
        what = _ENV_FILE
    if what:
        return f"it reads {what}"
    if tool in _WRITE_TOOLS:
        folders = pure.parts[:-1]
        if "risk" in folders or ".contract.json" in pure.name:
            return "risk/ and contract files are the owner's: read, never edit"
        if "upstream" in folders:
            return "upstream/ is verbatim: it changes only through its own install command"
    return None


_READ_TOOLS = frozenset({"Read", "Grep", "Glob"})
_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def refusal(call: dict[str, Any]) -> str | None:
    tool = str(call.get("tool_name", ""))
    tool_input = call.get("tool_input") or {}
    if tool == "Bash":
        return shell_refusal(str(tool_input.get("command", "")))
    if tool in _READ_TOOLS or tool in _WRITE_TOOLS:
        path = (
            tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
        )
        if path:
            return file_refusal(tool, str(path))
    return None


def main() -> int:
    try:
        reason = refusal(json.load(sys.stdin))
    except Exception as error:  # a broken guard must be seen, and must not block every call
        print(f"astrolabe guard could not read the tool call: {error}", file=sys.stderr)
        return 1
    if reason:
        print(
            f"astrolabe guard: refused, because {reason}. This is one of the owner's "
            "boundaries (AGENTS.md). Do not work around it; report it instead.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
