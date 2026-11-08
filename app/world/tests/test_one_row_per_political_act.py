"""One political act, one row.

`GOV-04`. `run_genesis.py` never called `rewire_charter_event_sink`, so
every event emitted inside a charter sub-engine went to `noop_sink`. The
tool handlers emitted their own copies of the acts they wrap, which
covered the loss on the runner and made DOUBLE rows on `main.py`.

Earlier work wired the sink and stopped there. That fixed the loss and
turned the duplication into a live defect on BOTH hosts: the finding's
own acceptance - "one `politics.tax_rate.set` row per `set_tax_rate`
call in the trace on both hosts" - failed on `run_genesis` afterwards
where before it had merely lost the engine-only events. The fix made
this half worse before this commit finished it.

Six handlers emitted an event their own charter call already emits:
`vote.cast`, `petition.filed`, `organization.registered`,
`license.issued`, `tax_rate.set`, `candidacy.declared`.

DELETING THEM WOULD HAVE LOST FIELDS, which is why this is not a
one-line change:

  - the handlers added a generic `agent_id`; the charter payloads name
    the agent under the ROLE it played (`voter_agent_id`,
    `filed_by_agent_id`, `founder_agent_id`, `holder_agent_id`,
    `set_by_agent_id`). Readers scope on the generic key
    (`admin_api.py:1044, 1305, 1343, 1454`). So `build_event` now adds
    it, in the one place every charter event passes through, and only
    when the payload names exactly one agent - an event about two people
    has no single "the agent" and guessing is worse than omitting.
  - `license.issued` and `candidacy.declared` carried
    `involved_agent_ids`, which is GOV-20's INTERRUPT list:
    `core/perception.py:52` wakes exactly those residents, and that
    field was once `sorted(world.agents)`, which woke the entire town
    per candidacy at about $0.006 a turn. Both moved onto the engine's
    payload before the handler's copy was removed.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from world.charter.events import build_event

PRIMITIVES = Path(__file__).resolve().parents[1] / "tools" / "primitives"
CHARTER = Path(__file__).resolve().parents[1] / "charter"


def _charter_event_types() -> set[str]:
    text = (CHARTER / "events.py").read_text(encoding="utf-8")
    import re

    return set(re.findall(r'"(politics\.[a-z_.]+)"', text))


def _handler_event_types() -> dict[str, str]:
    import re

    found: dict[str, str] = {}
    for path in PRIMITIVES.glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.search(r'type="(politics\.[a-z_.]+)"', line)
            if match:
                found[match.group(1)] = path.name
    return found


def test_no_handler_emits_an_event_its_engine_already_emits() -> None:
    """The finding, as an invariant. Every name in both places is a
    double row on every host now that the sink is live."""
    both = set(_handler_event_types()) & _charter_event_types()
    assert not both, (
        "GOV-04: these events are emitted by the charter engine AND by a "
        f"tool handler, so each act writes two rows: {sorted(both)}"
    )


def test_the_handlers_that_still_emit_have_no_engine_behind_them() -> None:
    """The control. A test that only forbade overlap could be satisfied
    by deleting every handler event, including the ones that are the only
    record of their act."""
    remaining = _handler_event_types()
    assert remaining, (
        "GOV-04: every political event now comes from the charter engine. "
        "If that is deliberate, delete this test; if a handler's own event "
        "was removed by accident, its act is now unrecorded."
    )
    for event_type in remaining:
        assert event_type not in _charter_event_types()


def test_a_charter_event_names_the_agent_generically() -> None:
    """Readers scope on `agent_id`; the charter payloads name the role."""
    row = build_event(
        "politics.tax_rate.set", {"tax_rate_bps": 300, "set_by_agent_id": "a1"}
    )
    assert row["payload"]["agent_id"] == "a1", (
        "GOV-04: the handler's copy carried the generic agent_id and has "
        "been deleted, so any reader that scopes an event to a resident "
        "now sees none"
    )
    assert row["payload"]["set_by_agent_id"] == "a1", "the role key stays"


def test_an_event_about_two_people_gets_no_guess() -> None:
    """A membership change names the org and the member. There is no
    single 'the agent', and picking one would attribute the act to
    whichever key sorted first."""
    row = build_event(
        "politics.election.tallied",
        {"pm_agent_id": "a1", "runner_up_agent_id": "a2"},
    )
    assert "agent_id" not in row["payload"]


def test_an_explicit_agent_id_is_not_overwritten() -> None:
    row = build_event(
        "politics.candidacy.declared", {"agent_id": "a1", "voter_agent_id": "a2"}
    )
    assert row["payload"]["agent_id"] == "a1"


@pytest.mark.parametrize(
    "module,function,event",
    [
        ("government", "issue_license", "involved_agent_ids"),
        ("elections", "declare_candidacy", "involved_agent_ids"),
    ],
)
def test_the_interrupt_list_survived_the_deletion(module, function, event) -> None:
    """GOV-20's list. `core/perception.py:52` treats `involved_agent_ids`
    as who to WAKE, so losing it either wakes nobody or - if a later
    reader defaults it - wakes the whole town, which is the $0.006-a-turn
    mistake GOV-20 was filed for."""
    import importlib

    engine = importlib.import_module(f"world.charter.{module}")
    holder = next(
        obj for name, obj in vars(engine).items()
        if inspect.isclass(obj) and hasattr(obj, function)
    )
    source = inspect.getsource(getattr(holder, function))
    assert event in source, (
        f"GOV-04: {module}.{function} does not carry {event}, and the tool "
        "handler that used to has been deleted as a duplicate"
    )


def test_the_petition_event_kept_its_richer_field() -> None:
    """The charter's copy carried a `title` the handler's did not, so
    deleting the handler's lost nothing here - this pins that."""
    import inspect

    from world.charter import land_petitions

    source = inspect.getsource(land_petitions.LandPetitionQueue.file_petition)
    for field in ('"petition_id"', '"filed_by_agent_id"', '"title"',
                  '"tile_count_requested"'):
        assert field in source
