"""What `charter/` says about the map is what the map does.

`GROW-30`. Four docstrings claimed the opposite of the code. Two were
still stale when this was written:

  - `charter/land_petitions.py` and `charter/models.py`'s `LandPetition`
    both said the Tiled-JSON mutation belonged to a land/map module that
    did not exist yet, and would be triggered by the
    `politics.petition.approved` event. `world/map/service.py` is built,
    `MapService.expand_land` does the work, and `admin_api.py:684` calls
    it DIRECTLY. Nothing anywhere subscribes to that event.

The other two the finding names (`admin_api.py`'s "documented scope
boundary" line about `map_service`, and `wiring.py`'s claim that
utterances/memories/skills are unpopulated) had already been corrected by
earlier work - checked, not assumed.

Checking the same paragraph then turned up three more of the same kind,
about the agent registry, the ledger and the tool registry "not being
built yet". All three are built. The boundary those paragraphs describe
is still correct; only its stated REASON was wrong, and a docstring that
gives a false reason is how a deliberate design decision gets undone by
someone who thinks they are finishing it.

WHY THIS IS TESTED AT ALL. A stale comment is not a defect a test
normally catches, and a test that only greps for a phrase is worth
little - I would just be pinning today's wording. So the assertions below
are on the MECHANISM each docstring describes. If someone wires a
subscriber to `politics.petition.approved`, or stops calling
`expand_land` directly, these fail and the docstrings need rewriting
again - which is the actual thing GROW-30 asks for: "correct the
docstrings when the wiring changes".
"""
from __future__ import annotations

import inspect
from pathlib import Path

WORLD = Path(__file__).resolve().parents[1]
CHARTER = WORLD / "charter"


def _charter_sources() -> dict[str, str]:
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in CHARTER.glob("*.py")
    }


def test_the_approval_event_is_recorded_and_not_acted_on() -> None:
    """What the corrected docstrings actually claim.

    The first draft of this asserted that NOTHING consumes the event, and
    it failed - which is exactly why it was written against the mechanism
    rather than the wording. `wiring.py`'s `record_petition_if_new` does
    consume it, to write the `petitions` trace row (GROW-19/INFRA-17). My
    correction to the two docstrings had said "nothing anywhere
    subscribes", and that was simply wrong. Both now say the true thing:
    the one consumer RECORDS, and no consumer GROWS.

    So this is the real invariant. The recorder may listen; map growth
    must stay a direct call. If somebody wires expansion to this event,
    these docstrings need rewriting again - which is precisely what
    GROW-30 asks for.
    """
    consumers = []
    for path in WORLD.rglob("*.py"):
        if "tests" in path.parts or path.parent.name == "charter":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "politics.petition.approved" in text and "EVENT_PETITION" not in text:
            consumers.append(path.name)

    assert consumers == ["wiring.py"], (
        "GROW-30: the consumers of politics.petition.approved changed - "
        f"found {consumers}, expected only wiring.py's trace recorder. If "
        "one of them grows the map, the LandPetition docstrings saying "
        "expansion is a direct call are stale."
    )

    recorder = (WORLD / "wiring.py").read_text(encoding="utf-8")
    start = recorder.index("politics.petition.approved")
    assert "expand_land" not in recorder[max(0, start - 2000):start + 2000], (
        "GROW-30: the trace recorder now grows the map. That is the "
        "event-driven design the docstrings say was never built."
    )


def test_the_operator_route_grows_the_map_directly() -> None:
    """The other half. Approval calls `expand_land` in the handler; it
    does not emit and hope."""
    admin = (WORLD / "admin_api.py").read_text(encoding="utf-8")
    assert "map_service.expand_land(" in admin, (
        "GROW-30: admin_api no longer calls expand_land directly, so the "
        "LandPetition docstrings describing a direct call are stale"
    )


def test_the_land_map_module_exists() -> None:
    """Both docstrings used to say it did not."""
    from world.map.service import MapService

    assert hasattr(MapService, "expand_land")
    assert hasattr(MapService, "stamp_building")


def test_the_registries_the_charter_says_it_avoids_all_exist() -> None:
    """The three found by checking rather than by the finding. The
    boundary is deliberate; the old reason ("not built yet") was false."""
    assert (WORLD / "tools" / "registry.py").exists()
    assert (WORLD / "ledger").is_dir()
    assert (WORLD / "agents_api.py").exists()


def test_no_charter_docstring_still_calls_a_built_thing_unbuilt() -> None:
    """Narrow on purpose: this checks one phrase, in one package, and
    only because every use of it in `charter/` was found to be false.
    It is not a general prohibition - `world/` uses it correctly
    elsewhere about things that really are unbuilt."""
    stale = "not-" + "yet-built"
    offenders = [
        name for name, text in _charter_sources().items() if stale in text
    ]
    assert not offenders, (
        f"GROW-30: {offenders} describe something as unbuilt. Every use of "
        "that phrase in charter/ was checked and all were false - the "
        "registry, ledger, tool registry and land/map module are all "
        "built. Check before adding another."
    )


def test_the_charter_still_imports_nothing_from_those_registries() -> None:
    """The design the docstrings describe, which the correction must not
    have quietly changed while rewording. charter/ is imported BY the
    tool layer; importing back would be a cycle."""
    for name, text in _charter_sources().items():
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith(("import ", "from ")):
                continue
            assert "world.tools.registry" not in line, (
                f"GROW-30/{name}: charter now imports the tool registry - "
                "that is the cycle the boundary exists to prevent"
            )
            assert "world.agents_api" not in line, (
                f"GROW-30/{name}: charter now imports the agent registry"
            )


def test_the_land_petition_docstring_names_the_finding() -> None:
    """So the next reader can find out why the wording changed rather
    than assuming it drifted."""
    from world.charter.models import LandPetition

    assert "GROW-30" in (inspect.getdoc(LandPetition) or "")
