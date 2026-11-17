"""A synthetic upstream map, the gate's other inputs for it, and `render`, which writes it.

Ported from meridian@014f706d:tests/upstream_map_fixture.py; the synthetic paths are
astrolabe's code roots.
"""

import re
from typing import Any

FIELD_NAMES = ("upstream", "how", "feature", "why")
AREAS = {
    "tools/alpha.py": {
        "upstream": "NautilusTrader",
        "how": "build-on",
        "feature": "alpha entries",
        "why": "the alpha path needs a venue",
    },
    "tools/beta.py": {
        "upstream": "NautilusTrader",
        "how": "build-on",
        "feature": "beta exits",
        "why": "the beta path needs a fill",
    },
    "astrolabe/gamma.py": {
        "upstream": "NautilusTrader",
        "how": "integrate",
        "feature": "gamma rows",
        "why": "the gamma study needs sessions",
    },
    "astrolabe/delta.py": {
        "upstream": "NautilusTrader",
        "how": "build-on",
        "feature": "delta policy",
        "why": "the delta lane needs a gate",
    },
}
DEPENDENCIES = {
    "nautilus-trader": "NautilusTrader",
    "pyright": "Pyright (with a Node runtime)",
}
ROWS = {"NautilusTrader": ("2.0", "upstream"), "Pyright (with a Node runtime)": ("1.1", "upstream")}
PROJECT = {"project": {"dependencies": ["nautilus-trader==2.0", "pyright==1.1"]}}
LOCK = {
    "package": [
        {"name": "nautilus-trader", "version": "2.0"},
        {"name": "pyright", "version": "1.1"},
    ]
}


class FixtureError(ValueError):
    """An input the fixture refuses."""


# The escapes of a TOML 1.0 basic string ("String"); other control characters are \uXXXX.
ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
LAYOUTS = ("multiline", "multiline_literal", "line_backslash", "split_array", "dotted", "subtable")
WRITES_WHY = ("multiline", "multiline_literal", "line_backslash", "split_array")


def controls(text: str, allowed: str = "\t") -> list[str]:
    """The control characters in `text` that are not in `allowed`, as U+XXXX."""
    return [f"U+{ord(c):04X}" for c in text if (c < " " or c == "\x7f") and c not in allowed]


def quote(text: str) -> str:
    """`text` as a TOML basic string; refuses a lone surrogate."""
    if surrogates := [f"U+{ord(c):04X}" for c in text if "\ud800" <= c <= "\udfff"]:
        raise FixtureError(f"quote: {surrogates[0]} is not a Unicode scalar value")
    return (
        '"'
        + "".join(ESCAPES.get(c, f"\\u{ord(c):04X}" if controls(c, "") else c) for c in text)
        + '"'
    )


def literal(option: str, text: str) -> str:
    """`text` as a TOML literal string; refuses, naming `option`, what one cannot hold."""
    found = ["'"] if "'" in text else controls(text)
    if found:
        raise FixtureError(f"{option}: a literal string cannot hold {found[0]}: {text[:40]!r}")
    return f"'{text}'"


def multiline_string(option: str, why: str) -> str:
    """`why` as the multi-line string `option` names; refuses, naming it, what one cannot hold."""
    delimiter, forbidden = ('"""', ['"""', "\\"]) if option == "multiline" else ("'''", ["'''"])
    if "\n" not in why or why.startswith("\n") or controls(why, "\t\n"):
        raise FixtureError(
            f"{option}: the why must hold a line break, not start with one, and hold no control"
            f" character but tab and line feed: {why[:40]!r}"
        )
    if found := [text for text in forbidden if text in why]:
        raise FixtureError(f"{option}: the why cannot hold {found[0]}: {why[:40]!r}")
    return f"{delimiter}{why}{delimiter}"


def render(
    areas: dict[str, dict[str, Any]] | None = None,
    dependencies: dict[str, Any] | None = None,
    *,
    header_comment: str | None = None,
    tight: bool = False,
    literal_key: str | None = None,
    literal_why: str | None = None,
    backslash_comment: bool = False,
    crlf: bool = False,
    dependencies_first: bool = False,
    multiline: str | None = None,
    multiline_literal: str | None = None,
    line_backslash: str | None = None,
    split_array: str | None = None,
    dotted: str | None = None,
    subtable: str | None = None,
) -> str:
    """The map file for `areas` and `dependencies` (by default `AREAS` and `DEPENDENCIES`).

    Spelling options keep the parse; each layout option (`LAYOUTS`) writes the entry it names so
    that the map check rejects the file, `line_backslash` and `split_array` with a fixed `why`.
    Each refusal raises FixtureError naming the option.
    """
    areas = AREAS if areas is None else areas
    dependencies = DEPENDENCIES if dependencies is None else dependencies
    for key, entry in areas.items():
        if set(entry) != set(FIELD_NAMES) or not all(isinstance(v, str) for v in entry.values()):
            raise FixtureError(f"render: [areas] {key!r} needs the string fields {FIELD_NAMES}")
    if not all(isinstance(value, str) for value in dependencies.values()):
        raise FixtureError("render: every [dependencies] value must be a string")
    named = {
        "literal_key": literal_key,
        "literal_why": literal_why,
        "multiline": multiline,
        "multiline_literal": multiline_literal,
        "line_backslash": line_backslash,
        "split_array": split_array,
        "dotted": dotted,
        "subtable": subtable,
    }
    for option, key in named.items():
        if key is not None and key not in areas:
            raise FixtureError(f"{option}: names no entry: {key!r}")
    chosen = [
        (option, key) for option, key in named.items() if option in LAYOUTS and key is not None
    ]
    layout = {key: option for option, key in chosen}
    if len(layout) < len(chosen):
        raise FixtureError("render: two layout options name one entry")
    if literal_why is not None and layout.get(literal_why) in WRITES_WHY:
        raise FixtureError(f"literal_why: {layout[literal_why]} writes the why of {literal_why!r}")
    if header_comment is not None and controls(header_comment):
        raise FixtureError(f"header_comment: a comment cannot hold {controls(header_comment)[0]}")
    space = "" if tight else " "

    def key_text(key: str) -> str:
        return literal("literal_key", key) if key == literal_key else quote(key)

    def field_text(key: str, field: str) -> str:
        value = areas[key][field]
        written = literal("literal_why", value) if (key, field) == (literal_why, "why") else None
        return f"{field}{space}={space}{written or quote(value)}"

    def entry_lines(key: str) -> list[str]:
        option = layout.get(key)
        fields = [field_text(key, field) for field in FIELD_NAMES[:-1]]
        why = field_text(key, "why")
        if option in ("multiline", "multiline_literal"):
            why = f"why{space}={space}{multiline_string(option, areas[key]['why'])}"
        elif option == "line_backslash":
            why = f'why{space}={space}"""one\\\ntwo"""'
        elif option == "split_array":
            why = f'why{space}={space}[\n  "one",\n  "two",\n]'
        if option == "dotted":
            return [f"{key_text(key)}.{field}" for field in [*fields, why]]
        if option == "subtable":
            return [f"[areas.{key_text(key)}]", *fields, why]
        return [f"{key_text(key)}{space}={space}{{ {', '.join([*fields, why])} }}"]

    area_lines = ["# Synthetic upstream map for the merge tests."]
    if backslash_comment:
        area_lines.append("# a provenance comment \\")
    area_lines.append("[areas]" if header_comment is None else f"[areas] # {header_comment}")
    for key in sorted(areas, key=lambda key: layout.get(key) == "subtable"):
        area_lines.extend(entry_lines(key))
    dependency_lines = ["# Only differently named rows need aliases.", "[dependencies]"]
    for key, value in dependencies.items():
        name = key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else quote(key)
        dependency_lines.append(f"{name}{space}={space}{quote(value)}")
    blocks = (
        [dependency_lines, area_lines] if dependencies_first else [area_lines, dependency_lines]
    )
    text = "\n\n".join("\n".join(block) for block in blocks) + "\n"
    return text.replace("\n", "\r\n") if crlf else text
