"""The lane's API client ids: distinct, never 0, none a multiple of 1000, no id or
order-id slot shared with the owner's other project, and named in one module only."""

import ast
from pathlib import Path

from astrolabe import ib_ids

PACKAGE = Path(ib_ids.__file__).resolve().parent
EXECUTION_IDS = (ib_ids.EXECUTION_CLIENT_ID, *ib_ids.SPARE_EXECUTION_CLIENT_IDS)
OUR_IDS = (
    ib_ids.DATA_CLIENT_ID,
    *EXECUTION_IDS,
    ib_ids.SANDBOX_DATA_CLIENT_ID,
    ib_ids.FEED_READER_CLIENT_ID,
    ib_ids.TIMER_PROBE_CLIENT_ID,
    ib_ids.CONTRACT_PROBE_CLIENT_ID,
    ib_ids.ACCOUNT_PROBE_CLIENT_ID,
)


def test_ids_are_distinct_and_never_zero():
    assert len(set(OUR_IDS)) == len(OUR_IDS)
    # Client id 0 binds manual orders to the client; no tool of this project may use it.
    assert all(client_id > 0 for client_id in OUR_IDS)


def test_no_id_is_a_multiple_of_1000():
    assert all(client_id % 1000 != 0 for client_id in OUR_IDS)


def test_order_id_slots_are_distinct_among_ourselves():
    slots = [client_id % 1000 for client_id in OUR_IDS]
    assert len(set(slots)) == len(slots)


def test_no_id_or_slot_is_shared_with_the_other_project():
    theirs = set(ib_ids.OTHER_PROJECT_CLIENT_IDS)
    assert not theirs & set(OUR_IDS)
    assert not {i % 1000 for i in theirs} & {i % 1000 for i in OUR_IDS}


def test_every_execution_slot_sits_above_the_other_projects_slots():
    highest_other = max(i % 1000 for i in ib_ids.OTHER_PROJECT_CLIENT_IDS)
    assert all(client_id % 1000 > highest_other for client_id in EXECUTION_IDS)


def test_the_target_is_loopback_on_the_paper_port():
    assert ib_ids.HOST == "127.0.0.1"
    assert ib_ids.PAPER_PORT == 4002


def test_the_port_and_the_client_ids_are_named_only_in_ib_ids():
    reserved = {ib_ids.PAPER_PORT, *OUR_IDS, *ib_ids.OTHER_PROJECT_CLIENT_IDS}
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "ib_ids.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Constant)
                and type(node.value) is int
                and node.value in reserved
            ):
                offenders.append(f"{path.relative_to(PACKAGE.parent)}:{node.lineno}")
    assert not offenders, f"import these from astrolabe.ib_ids instead: {offenders}"
