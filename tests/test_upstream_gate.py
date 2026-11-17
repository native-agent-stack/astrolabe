"""The offline upstream gate: every code file has one entry in ``upstream-map.toml`` that names
an ``UPSTREAM.md`` row, every dependency has a row whose version is the locked one, and the
7-day release cooldown holds with its one named engine exemption.

Ported from meridian@014f706d:tests/test_upstream_gate.py (lines 137-861); the ADR, manifest
and index coupling is dropped, the code roots are astrolabe's, and the cooldown also checks
``no-build``.
"""

import fnmatch
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from test_paper_only import REPO_ROOT
from test_public_tree import tracked_files
from upstream_map_fixture import (
    AREAS,
    DEPENDENCIES,
    FIELD_NAMES,
    render,
)

# Code that needs an entry: the package, the tools and every skill's scripts.
CODE_ROOTS = ("astrolabe/", "tools/")
SKILL_SCRIPTS = ".agents/skills/*/scripts/*"


def register_rows(text: str) -> dict[str, tuple[str, str]]:
    """Keep exact component labels, versions and origins from register tables."""
    rows = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]]
        if len(cells) >= 6 and cells[-1] == "upstream":
            assert cells[0] not in rows, f"duplicate upstream row: {cells[0]}"
            rows[cells[0]] = (cells[1], cells[-1])
    return rows


def area_problems(
    names: list[str], areas: dict[str, dict[str, str]], rows: dict[str, tuple[str, str]]
) -> list[str]:
    files = [
        name
        for name in names
        if name.startswith(CODE_ROOTS) or fnmatch.fnmatchcase(name, SKILL_SCRIPTS)
    ]
    found = []
    for name in files:
        matches = [pattern for pattern in areas if fnmatch.fnmatchcase(name, pattern)]
        if len(matches) != 1:
            found.append(f"{name}: expected one upstream entry, found {len(matches)}")
    for pattern, area in areas.items():
        if not any(fnmatch.fnmatchcase(name, pattern) for name in files):
            found.append(f"{pattern}: matches no code file")
        if area.get("upstream") not in rows:
            found.append(f"{pattern}: no upstream row: {area.get('upstream')}")
        if area.get("how") not in {"install", "integrate", "build-on"}:
            found.append(f"{pattern}: how must be install, integrate or build-on")
        for field in ("feature", "why"):
            if not isinstance(area.get(field), str) or not area[field].strip():
                found.append(f"{pattern}: missing {field}")
    return found


def package_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement.strip())
    assert match is not None, f"invalid dependency: {requirement}"
    return re.sub(r"[-_.]+", "-", match[0]).lower()


def dependency_problems(
    project: dict[str, Any],
    lock: dict[str, Any],
    aliases: dict[str, str],
    rows: dict[str, tuple[str, str]],
) -> list[str]:
    metadata = project["project"]
    requirements = list(metadata.get("dependencies", []))
    for group in metadata.get("optional-dependencies", {}).values():
        requirements.extend(group)
    groups = project.get("dependency-groups", {})
    for group in groups.values():
        for requirement in group:
            if isinstance(requirement, str):
                requirements.append(requirement)
            else:
                # Every group is scanned, including groups included by another group.
                assert set(requirement) == {"include-group"}
                assert requirement["include-group"] in groups, "unknown dependency group"
    dependencies = {package_name(requirement) for requirement in requirements}
    found = [
        f"{name}: alias has no declared dependency"
        for name in sorted(aliases.keys() - dependencies)
    ]
    named_rows = {}
    for label, row in rows.items():
        # Normalize whole package labels, never the first word of a prose label.
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", label):
            name = package_name(label)
            assert name not in named_rows, f"ambiguous normalized upstream row: {name}"
            named_rows[name] = row
    for name in sorted(dependencies):
        row = named_rows.get(name, rows.get(aliases.get(name, "")))
        if row is None:
            found.append(f"{name}: no upstream dependency row")
            continue
        versions = {
            package["version"]
            for package in lock["package"]
            if package_name(package["name"]) == name and "version" in package
        }
        # The register's version cell starts with the version, followed by prose.
        version = re.match(r"v?(\d[^\s,;]*)", row[0])
        if version is None or versions != {version[1]}:
            found.append(
                f"{name}: register version {row[0]!r} disagrees with lock {sorted(versions)}"
            )
    return found


ENGINE = "nautilus-trader"
# The engine's exemption from the 7-day release cooldown (pyproject.toml's
# exclude-newer-package) was granted for this one release, uploaded 2026-11-10, so that the
# repository could be bootstrapped before its 7 days ended at 2026-11-17T21:00Z. The first
# change after that time removes both the exclude-newer-package line and this exemption
# (ENGINE_COOLDOWN_EXEMPT = None, and the tests below that need it go with it). The engine is
# pinned exactly; if the pin moves, the exemption does not follow: a later release waits seven
# days like every other package. A new pin changes this constant on purpose, in the same
# commit as the register row.
ENGINE_COOLDOWN_EXEMPT = "2.0.0rc9"


def cooldown_problems(project: dict[str, Any], lock: dict[str, Any]) -> list[str]:
    """The 7-day release cooldown and its one exemption, bounded to ENGINE_COOLDOWN_EXEMPT,
    and wheels only."""
    uv = project.get("tool", {}).get("uv", {})
    found = []
    if uv.get("exclude-newer") != "7 days":
        found.append(f"exclude-newer is {uv.get('exclude-newer')!r}; new releases wait 7 days")
    if uv.get("no-build") is not True:
        found.append(f"no-build is {uv.get('no-build')!r}; only wheels are installed")
    exempt = uv.get("exclude-newer-package", {})
    if not isinstance(exempt, dict):
        return found + [f"exclude-newer-package is {exempt!r}, not a table"]
    for name, value in exempt.items():
        if name != ENGINE:
            found.append(f"exclude-newer-package exempts {name}; only {ENGINE} is named")
        elif value is not False:
            found.append(f"{ENGINE}'s exemption is {value!r}; it exempts entirely or not at all")
    requirements = [
        requirement
        for requirement in project["project"].get("dependencies", [])
        if package_name(requirement) == ENGINE
    ]
    if len(requirements) != 1:
        return found + [f"{ENGINE}: expected one pinned dependency, found {len(requirements)}"]
    pin = re.fullmatch(r"[A-Za-z0-9._-]+==([^\s;,]+)", requirements[0])
    if pin is None:
        return found + [f"{ENGINE}: {requirements[0]!r} is not an exact pin"]
    pinned = pin[1]
    versions = {
        package["version"]
        for package in lock["package"]
        if package_name(package["name"]) == ENGINE and "version" in package
    }
    if versions != {pinned}:
        found.append(f"{ENGINE} {pinned}: the lock holds {sorted(versions)}")
    if exempt.get(ENGINE) is False and pinned != ENGINE_COOLDOWN_EXEMPT:
        found.append(
            f"{ENGINE} {pinned}: the exemption covers {ENGINE_COOLDOWN_EXEMPT} only;"
            " a later release waits 7 days like every other"
        )
    return found


def load_toml(name: str, root: Path = REPO_ROOT) -> dict[str, Any]:
    return tomllib.loads((root / name).read_text(encoding="utf-8"))


def read_map_text(path: Path) -> str:
    """The map's exact text: its bytes decoded as UTF-8, with no newline translation."""
    return path.read_bytes().decode("utf-8")


def parse(text: str) -> dict[str, Any] | Exception:
    """`tomllib.loads(text)`, or the Exception it raised (tomllib raises more than its own)."""
    try:
        return tomllib.loads(text)
    except Exception as error:
        return error


def why(error: Exception) -> str:
    if isinstance(error, tomllib.TOMLDecodeError):
        return str(error)
    return f"{type(error).__name__}: {error}"


OPENS_A_TABLE = (
    "opens a table; the map has only [areas] and [dependencies], and each area is one line"
    ' under [areas]: "path" = { upstream = "...", how = "...", feature = "...", why = "..." }'
)


def map_text_problems(text: str) -> list[str]:
    """The map text's problems; what tomllib raises on a line or the whole file is reported."""
    found = []
    tables: dict[str, dict[str, Any]] = {"areas": {}, "dependencies": {}}
    current = None
    in_other_table = False
    for number, crlf_line in enumerate(text.split("\n"), 1):
        line = crlf_line.removesuffix("\r")
        where = f"upstream-map.toml:{number}"
        document = parse(line)
        if isinstance(document, tomllib.TOMLDecodeError):
            found.append(f"{where}: not a complete TOML document")
            continue
        if isinstance(document, Exception):
            found.append(f"{where}: does not load: {why(document)}")
            continue
        if not document:
            continue
        key, value = next(iter(document.items()))
        if document in ({"areas": {}}, {"dependencies": {}}):
            current, in_other_table = key, False
            continue
        if current is None:
            found.append(f"{where}: {key!r} is outside [areas] and [dependencies]")
            continue
        if parse(f"[{current}]\n{line}") != {current: document}:
            found.append(f"{where}: {line.strip()} {OPENS_A_TABLE}")
            in_other_table = True
        elif in_other_table:
            continue
        elif key in tables[current]:
            found.append(f"{where}: {key} is already given in [{current}]")
        else:
            tables[current][key] = value
    if found:
        return found
    # test_a_header_given_twice_fails_the_whole_file_load
    whole = parse(text)
    if isinstance(whole, Exception):
        return [f"upstream-map.toml: the whole file does not load: {why(whole)}"]
    if tables != whole:
        return ["upstream-map.toml: the per-line reading does not rebuild the whole file"]
    return [
        *(
            f"upstream-map.toml: [areas] {key!r} must be a table of strings"
            for key, value in tables["areas"].items()
            if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values())
        ),
        *(
            f"upstream-map.toml: [dependencies] {key!r} must be a string"
            for key, value in tables["dependencies"].items()
            if not isinstance(value, str)
        ),
    ]


def gate_problems(
    text: str,
    files: list[str],
    rows: dict[str, tuple[str, str]],
    project: dict[str, Any],
    lock: dict[str, Any],
) -> list[str]:
    """The upstream gate on one map text; a problem in the text is reported alone."""
    problems = map_text_problems(text)
    if problems:
        return problems
    mapping = tomllib.loads(text)
    return [
        *area_problems(files, mapping["areas"], rows),
        *dependency_problems(project, lock, mapping["dependencies"], rows),
    ]


def upstream_gate_problems() -> list[str]:
    """The upstream gate on this repository: its map, tracked files, register, project and lock."""
    try:
        text = read_map_text(REPO_ROOT / "upstream-map.toml")
    except UnicodeDecodeError as error:
        return [f"upstream-map.toml: not UTF-8: {error}"]
    problems = map_text_problems(text)
    if problems:
        return problems  # before the repository is read
    return gate_problems(
        text,
        tracked_files(),
        register_rows((REPO_ROOT / "UPSTREAM.md").read_text(encoding="utf-8")),
        load_toml("pyproject.toml"),
        load_toml("uv.lock"),
    )


def test_every_code_area_and_dependency_has_its_upstream():
    assert upstream_gate_problems() == []


def test_the_synthetic_fields_match_the_tracked_map():
    tracked = load_toml("upstream-map.toml")
    assert {field for area in tracked["areas"].values() for field in area} == set(FIELD_NAMES)
    assert all(set(area) == set(FIELD_NAMES) for area in AREAS.values())


def assert_accepted(text: str, expected: dict[str, Any]) -> None:
    """`text` parses to `expected` and the map check accepts it."""
    assert tomllib.loads(text) == expected
    assert map_text_problems(text) == []


def assert_rejected_layout(text: str, expected: dict[str, Any], problems: list[str]) -> None:
    """`text` parses to `expected` and the map check reports exactly `problems`."""
    assert tomllib.loads(text) == expected
    assert map_text_problems(text) == problems


FIRST = next(iter(AREAS))
NON_BMP = "astrolabe/😀.py"
WITH_NON_BMP = {**AREAS, NON_BMP: {**AREAS[FIRST], "why": "emoji key"}}


def areas_with_why(why: Any) -> dict[str, Any]:
    return {**AREAS, FIRST: {**AREAS[FIRST], "why": why}}


@pytest.mark.parametrize(
    ("areas", "options"),
    [
        (AREAS, {}),
        (AREAS, {"header_comment": "Code provenance"}),
        (AREAS, {"header_comment": "Code\tprovenance"}),
        (AREAS, {"tight": True}),
        (AREAS, {"literal_key": FIRST}),
        (AREAS, {"literal_why": FIRST}),
        (AREAS, {"backslash_comment": True}),
        (AREAS, {"dependencies_first": True}),
        (areas_with_why("one\u0085two"), {}),
        (areas_with_why("one two"), {}),
        (WITH_NON_BMP, {}),
        (AREAS, {"crlf": True}),
    ],
)
def test_accepted_layouts_pass_the_map_check(areas: dict[str, Any], options: dict[str, Any]):
    assert_accepted(
        render(areas, DEPENDENCIES, **options), {"areas": areas, "dependencies": DEPENDENCIES}
    )


def not_complete(*numbers: int) -> list[str]:
    return [f"upstream-map.toml:{number}: not a complete TOML document" for number in numbers]


def given_twice(key: str, *numbers: int) -> list[str]:
    return [f"upstream-map.toml:{number}: {key} is already given in [areas]" for number in numbers]


def opens_table(header: str, *numbers: int) -> list[str]:
    return [f"upstream-map.toml:{number}: {header} {OPENS_A_TABLE}" for number in numbers]


MULTILINE = areas_with_why("first\nmiddle\nlast")


@pytest.mark.parametrize(
    ("options", "areas", "parsed", "problems"),
    [
        ({"multiline": FIRST}, MULTILINE, MULTILINE, not_complete(3, 4, 5)),
        ({"multiline_literal": FIRST}, MULTILINE, MULTILINE, not_complete(3, 4, 5)),
        ({"line_backslash": FIRST}, AREAS, areas_with_why("onetwo"), not_complete(3, 4)),
        ({"split_array": FIRST}, AREAS, areas_with_why(["one", "two"]), not_complete(3, 4, 5, 6)),
        ({"dotted": FIRST}, AREAS, AREAS, given_twice(FIRST, 4, 5, 6)),
        ({"dotted": NON_BMP}, WITH_NON_BMP, WITH_NON_BMP, given_twice(NON_BMP, 8, 9, 10)),
        ({"subtable": FIRST}, AREAS, AREAS, opens_table(f'[areas."{FIRST}"]', 6)),
    ],
)
def test_rejected_layouts_fail_the_map_check(
    options: dict[str, Any], areas: dict[str, Any], parsed: dict[str, Any], problems: list[str]
):
    expected = {"areas": parsed, "dependencies": DEPENDENCIES}
    assert_rejected_layout(render(areas, DEPENDENCIES, **options), expected, problems)


REBUILD = ["upstream-map.toml: the per-line reading does not rebuild the whole file"]


def outside(key: str) -> list[str]:
    return [f"upstream-map.toml:1: {key!r} is outside [areas] and [dependencies]"]


ENTRY = 'upstream = "X", how = "build-on", feature = "f", why = "w"'


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", REBUILD),
        ("# only\n", REBUILD),
        ("[areas]\n", REBUILD),
        ("[areas]\ndependencies = {}\n", REBUILD),
        ('key = "v"\n[areas]\n[dependencies]\n', outside("key")),
        ("[[areas]]\n", outside("areas")),
        ("[areas]\n[[areas]]\n", opens_table("[[areas]]", 2)),
        ("[areas]\r[dependencies]\r", not_complete(1)),
        ("[areas]\n[dependencies]\n[notes]\n", opens_table("[notes]", 3)),
        (
            '[areas]\n[dependencies]\n[areas."a"]\nupstream = "X"\n[areas."b"]\nupstream = "X"\n',
            [*opens_table('[areas."a"]', 3), *opens_table('[areas."b"]', 5)],
        ),
        (
            f'[areas]\n"a" = {{ {ENTRY} }}\n"a" = {{ {ENTRY} }}\n[dependencies]\n',
            ["upstream-map.toml:3: a is already given in [areas]"],
        ),
        (
            '[areas]\n"a" = 1\n[dependencies]\n',
            ["upstream-map.toml: [areas] 'a' must be a table of strings"],
        ),
        (
            "[areas]\n[dependencies]\nnautilus-trader = 1\n",
            ["upstream-map.toml: [dependencies] 'nautilus-trader' must be a string"],
        ),
    ],
)
def test_malformed_maps_are_reported_not_raised(text: str, expected: list[str]):
    assert map_text_problems(text) == expected


def test_a_header_given_twice_fails_the_whole_file_load():
    text = "[areas]\n[dependencies]\n[areas]\n"
    with pytest.raises(tomllib.TOMLDecodeError) as caught:
        tomllib.loads(text)
    assert map_text_problems(text) == [
        f"upstream-map.toml: the whole file does not load: {caught.value}"
    ]


def raised(text: str) -> BaseException:
    """What `tomllib.loads` raises for `text`."""
    with pytest.raises(Exception) as caught:
        tomllib.loads(text)
    return caught.value


def gate_on(monkeypatch: pytest.MonkeyPatch, text: str) -> list[str]:
    """The real gate on a planted map text; it must not read the repository for a bad map."""

    def unread() -> list[str]:
        raise AssertionError("the repository is read for a bad map")

    monkeypatch.setattr("test_upstream_gate.read_map_text", lambda path: text)
    monkeypatch.setattr("test_upstream_gate.tracked_files", unread)
    return upstream_gate_problems()


@pytest.fixture
def default_limits():
    """Python's default integer-string and recursion limits, for one test."""
    digits, depth = sys.get_int_max_str_digits(), sys.getrecursionlimit()
    sys.set_int_max_str_digits(4300)
    sys.setrecursionlimit(1000)
    yield
    sys.set_int_max_str_digits(digits)
    sys.setrecursionlimit(depth)


@pytest.mark.parametrize(
    ("entry", "kind"),
    [(f"x = {'9' * 4301}", "ValueError"), (f"x = {'[' * 5000}{']' * 5000}", "RecursionError")],
    ids=["integer-over-the-digit-limit", "array-deeper-than-the-recursion-limit"],
)
@pytest.mark.usefixtures("default_limits")
def test_the_gate_reports_what_tomllib_raises_besides_a_decode_error(
    monkeypatch: pytest.MonkeyPatch, entry: str, kind: str
):
    error = raised(entry)
    assert type(error).__name__ == kind
    text = f"[areas]\n[dependencies]\n{entry}\n"
    assert gate_on(monkeypatch, text) == [f"upstream-map.toml:3: does not load: {kind}: {error}"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '[areas]\n"a" = { upstream = { a = 1 }, how = "build-on", feature = "f", why = "w" }\n'
            "[dependencies]\n",
            ["upstream-map.toml: [areas] 'a' must be a table of strings"],
        ),
        (
            '[areas]\n"a" = { upstream = "X", how = ["build-on"], feature = "f", why = "w" }\n'
            "[dependencies]\n",
            ["upstream-map.toml: [areas] 'a' must be a table of strings"],
        ),
        (
            "[areas]\n[dependencies]\nx = { a = 1 }\n",
            ["upstream-map.toml: [dependencies] 'x' must be a string"],
        ),
    ],
)
def test_the_gate_reports_a_bad_map(
    monkeypatch: pytest.MonkeyPatch, text: str, expected: list[str]
):
    assert gate_on(monkeypatch, text) == expected


def test_the_gate_reports_a_non_utf8_map(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("test_upstream_gate.read_map_text", lambda path: b"\xff".decode("utf-8"))
    assert upstream_gate_problems() == [
        "upstream-map.toml: not UTF-8: 'utf-8' codec can't decode byte 0xff in position 0:"
        " invalid start byte"
    ]


def test_read_map_text_keeps_every_carriage_return(tmp_path: Path):
    path = tmp_path / "map.toml"
    path.write_bytes(b"[areas]\r[dependencies]\r\n")
    assert read_map_text(path) == "[areas]\r[dependencies]\r\n"


def test_the_helpers_refuse_bad_fixtures():
    with pytest.raises(tomllib.TOMLDecodeError):
        assert_rejected_layout("not TOML", {}, [])
    with pytest.raises(tomllib.TOMLDecodeError):
        assert_accepted("not TOML", {})
    with pytest.raises(AssertionError):
        assert_rejected_layout(render(), {}, [])
    with pytest.raises(AssertionError):
        assert_accepted(render(), {})
    with pytest.raises(AssertionError):
        assert_accepted("[areas]\n", {"areas": {}})


@pytest.mark.parametrize(
    ("pinned", "exempt", "expected"),
    [
        (ENGINE_COOLDOWN_EXEMPT, True, []),
        # Without the exemption a later engine release waits like every other package.
        ("2.0.0rc10", False, []),
        (
            "2.0.0rc10",
            True,
            [
                f"{ENGINE} 2.0.0rc10: the exemption covers {ENGINE_COOLDOWN_EXEMPT} only;"
                " a later release waits 7 days like every other"
            ],
        ),
    ],
)
def test_a_later_engine_release_waits_or_fails_the_bound(pinned, exempt, expected):
    lock = {"package": [{"name": ENGINE, "version": pinned}]}
    assert cooldown_problems(engine_project(pinned, exempt=exempt), lock) == expected


def test_the_engine_cooldown_exemption_is_bounded_to_the_pinned_release():
    project = load_toml("pyproject.toml")
    lock = load_toml("uv.lock")
    assert cooldown_problems(project, lock) == []
    # The bound is the release the register names, which is the release the lock resolved.
    rows = register_rows((REPO_ROOT / "UPSTREAM.md").read_text(encoding="utf-8"))
    assert rows["NautilusTrader"][0].startswith(ENGINE_COOLDOWN_EXEMPT)


def engine_project(pinned: str = ENGINE_COOLDOWN_EXEMPT, *, exempt: bool = True) -> dict[str, Any]:
    project: dict[str, Any] = {
        "project": {"dependencies": [f"nautilus_trader=={pinned}"]},
        "tool": {"uv": {"exclude-newer": "7 days", "no-build": True}},
    }
    if exempt:
        project["tool"]["uv"]["exclude-newer-package"] = {ENGINE: False}
    return project


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            {"exclude-newer": "1 day"},
            ["exclude-newer is '1 day'; new releases wait 7 days"],
        ),
        (
            {"no-build": False},
            ["no-build is False; only wheels are installed"],
        ),
        (
            {"exclude-newer-package": {"some-package": False}},
            ["exclude-newer-package exempts some-package; only nautilus-trader is named"],
        ),
        (
            {"exclude-newer-package": {ENGINE: "2026-11-01"}},
            ["nautilus-trader's exemption is '2026-11-01'; it exempts entirely or not at all"],
        ),
    ],
)
def test_a_changed_cooldown_setting_is_reported(change, expected):
    project = engine_project()
    project["tool"]["uv"].update(change)
    lock = {"package": [{"name": ENGINE, "version": ENGINE_COOLDOWN_EXEMPT}]}
    assert cooldown_problems(project, lock) == expected


def test_a_lock_that_did_not_resolve_the_engine_pin_is_reported():
    lock = {"package": [{"name": ENGINE, "version": "2.0.0rc8"}]}
    assert cooldown_problems(engine_project(), lock) == [
        "nautilus-trader 2.0.0rc9: the lock holds ['2.0.0rc8']"
    ]


def test_a_loose_engine_pin_is_reported():
    project = engine_project(exempt=False)
    project["project"]["dependencies"] = ["nautilus_trader>=2.0.0rc9"]
    lock = {"package": [{"name": ENGINE, "version": "2.0.0rc9"}]}
    assert cooldown_problems(project, lock) == [
        "nautilus-trader: 'nautilus_trader>=2.0.0rc9' is not an exact pin"
    ]


@pytest.fixture
def area():
    return {"upstream": "Example", "how": "build-on", "feature": "hooks", "why": "local policy"}


@pytest.mark.parametrize("root", ["astrolabe", "tools"])
def test_planted_module_without_entry_fails(area, root):
    found = area_problems(
        [f"{root}/known.py", f"{root}/planted.py"],
        {f"{root}/known.py": area},
        {"Example": ("1.0", "upstream")},
    )
    assert found == [f"{root}/planted.py: expected one upstream entry, found 0"]


@pytest.mark.parametrize(
    "name",
    [
        ".agents/skills/lead/scripts/dispatch.py",
        ".agents/skills/lead/scripts/learnings.py",
        ".agents/skills/other/scripts/helper.sh",
    ],
)
def test_skill_scripts_require_upstream_provenance(area, name):
    rows = {"Example": ("1.0", "upstream")}
    assert area_problems([name], {}, rows) == [f"{name}: expected one upstream entry, found 0"]
    assert area_problems([name], {name: area}, rows) == []


def test_entry_without_upstream_row_fails(area):
    assert area_problems(["tools/example.sh"], {"tools/*": area}, {}) == [
        "tools/*: no upstream row: Example"
    ]


def test_overlap_and_unused_entries_fail(area):
    found = area_problems(
        ["astrolabe/known.py"],
        {"astrolabe/*": area, "astrolabe/known.py": area, "tools/*": area},
        {"Example": ("1.0", "upstream")},
    )
    assert found == [
        "astrolabe/known.py: expected one upstream entry, found 2",
        "tools/*: matches no code file",
    ]


@pytest.mark.parametrize("field", ["how", "feature", "why"])
def test_missing_provenance_fails(area, field):
    del area[field]
    assert area_problems(
        ["astrolabe/known.py"], {"astrolabe/known.py": area}, {"Example": ("1.0", "upstream")}
    )


@pytest.mark.parametrize("location", ["project", "optional", "group"])
def test_dependency_without_row_fails(location):
    project = {"project": {}}
    if location == "project":
        project["project"]["dependencies"] = ["planted>=1"]
    elif location == "optional":
        project["project"]["optional-dependencies"] = {"extra": ["planted>=1"]}
    else:
        project["dependency-groups"] = {"dev": ["planted>=1"]}
    assert dependency_problems(project, {"package": []}, {}, {}) == [
        "planted: no upstream dependency row"
    ]


@pytest.mark.parametrize("version", ["1.0", "1.0.1", None])
@pytest.mark.parametrize("use_alias", [False, True])
def test_dependency_version_must_match_lock(version, use_alias):
    project = {
        "project": {"dependencies": ["Example_Package[extra]>=1; python_version >= '3.14'"]},
        "dependency-groups": {"dev": [{"include-group": "checks"}], "checks": []},
    }
    packages = [] if version is None else [{"name": "example-package", "version": version}]
    found = dependency_problems(
        project,
        {"package": packages},
        {"example-package": "Example"} if use_alias else {},
        {"Example" if use_alias else "Example.Package": ("1.0 from PyPI", "upstream")},
    )
    assert bool(found) == (version != "1.0")


def test_new_dependency_group_needs_no_aliases():
    names = [f"example-{index}" for index in range(11)]
    assert (
        dependency_problems(
            {"project": {}, "dependency-groups": {"research": [f"{name}>=1" for name in names]}},
            {"package": [{"name": name, "version": "1.0"} for name in names]},
            {},
            {name: ("1.0", "upstream") for name in names},
        )
        == []
    )


def test_exact_package_row_takes_priority_over_alias():
    assert (
        dependency_problems(
            {"project": {"dependencies": ["example==1.0"]}},
            {"package": [{"name": "example", "version": "1.0"}]},
            {"example": "Old label"},
            {"example": ("1.0", "upstream"), "Old label": ("0.9", "upstream")},
        )
        == []
    )


def test_undeclared_alias_still_fails():
    assert dependency_problems({"project": {}}, {"package": []}, {"unused": "Example"}, {}) == [
        "unused: alias has no declared dependency"
    ]


def test_prose_row_needs_an_explicit_alias():
    assert dependency_problems(
        {"project": {"dependencies": ["example>=1"]}},
        {"package": [{"name": "example", "version": "1.0"}]},
        {},
        {"example (with extra)": ("1.0", "upstream")},
    ) == ["example: no upstream dependency row"]


def test_register_uses_only_upstream_rows_and_exact_labels():
    text = (
        "| Component | Version or commit | Source | Date | Used for | Origin |\n"
        "| Example (with extra) | 1.0 | url | date | testing | upstream |\n"
        "| Local | this repo | n/a | date | hooks | own design |\n"
    )
    assert register_rows(text) == {"Example (with extra)": ("1.0", "upstream")}


def test_dependency_alias_comment_heads_the_dependencies():
    text = (REPO_ROOT / "upstream-map.toml").read_text()
    following = text.split(
        "# Only differently named rows need aliases; package labels are normalized automatically.",
        1,
    )[1].lstrip()
    assert following.startswith("[dependencies]\n")


def test_files_outside_the_code_roots_need_no_entry(area):
    rows = {"Example": ("1.0", "upstream")}
    names = ["tests/test_x.py", "docs/RUNBOOK.md", ".agents/skills/lead/SKILL.md", "README.md"]
    assert area_problems(names, {}, rows) == []
