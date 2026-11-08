"""TOOL-08, the half that was still open: the fix was applied by hand,
tool by tool, and three tools were missed.

`place_aliases.place_for_charter` is the fix. It was landed at seven call
sites - `vote`, `register_org`, `petition`, and four in `meta.py` - and
`test_the_charter_folds_places_like_the_registry.py` covers those seven by
name. Enumerating the fixed sites is not a guard against the unfixed ones,
and three handlers went on passing `world.location_of(...)` straight into a
charter engine that compares it with `!=`:

    world/tools/primitives/institutions.py
      declare_candidacy_handler     -> elections.py:194
      set_tax_rate_handler          -> government.py:84  (via set_tax_rate)
      issue_builder_license_handler -> government.py:84  (via require_sitting)

Run 25 proved the first one live. A resident walked to the government
complex - the place `list_places` names, the place the map draws - and
`declare_candidacy` refused with "must be at town_hall, was at Kasukabe
Government Complex". So the town could `vote` and could not STAND: every
ballot in every run has been empty by construction, and the seat-gated
explanation (GOV-01) was never the whole story.

The behavioural tests below cover the three. The sweep after them is the
part that matters: it reads the handler modules and fails on ANY handler
that takes a location from the world and hands it to `world.charter`,
whether or not anybody thought to write a test for that tool. The next
person to add a civic tool does not have to know this finding exists.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from world.charter.errors import NotAtRequiredLocation
from world.tools import registry as registry_module
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL
from world.tools.place_aliases import place_for_charter

from .conftest import spawn_agent

# What the map draws, what `move` walks you to, and what `look_around`
# reports - none of which is the string the charter compares against.
COMPLEX_SECTOR = "Kasukabe Government Complex"


# ── the three that were missed, one test each ─────────────────────────


@pytest.mark.asyncio
async def test_a_resident_at_the_complex_can_stand_for_election(world, registry):
    """The one run 25 caught. `vote` was fixed; this was not."""
    await spawn_agent(world, "n01", COMPLEX_SECTOR, age_years=40.0, household_id="h1")

    # Scheduled at this tick, so candidacy is open at this tick.
    election = await world.charter.elections.schedule_election(world.tick())
    await world.charter.elections.advance_phase(election.election_id, world.tick())

    outcome = await registry.execute(
        world,
        "n01",
        "declare_candidacy",
        {"election_id": election.election_id, "platform": "Plain accounts, and debts settled in the open."},
    )

    assert "n01" in outcome.result["candidates"]
    assert world.tool_refusals == []


@pytest.mark.asyncio
async def test_setting_a_tax_rate_is_refused_for_the_seat_not_the_place(
    world, registry
):
    """`set_tax_rate` is seat-gated, so this proves the PLACE half only.

    Same shape as the existing `petition` test: a resident with no seat is
    refused for the seat. What must NOT happen is a refusal naming the
    place they are correctly standing in.
    """
    await spawn_agent(world, "n02", COMPLEX_SECTOR, age_years=40.0, household_id="h1")

    with pytest.raises(Exception) as raised:
        await registry.execute(world, "n02", "set_tax_rate", {"rate_percent": 10})

    assert not isinstance(raised.value, NotAtRequiredLocation)
    assert "town_hall" not in str(raised.value)
    assert "government_complex" not in str(raised.value)


@pytest.mark.asyncio
async def test_issuing_a_builder_licence_is_refused_for_the_seat_not_the_place(
    world, registry
):
    await spawn_agent(world, "n03", COMPLEX_SECTOR, age_years=40.0, household_id="h1")
    await spawn_agent(world, "n04", COMPLEX_SECTOR, age_years=40.0, household_id="h1")

    with pytest.raises(Exception) as raised:
        await registry.execute(
            world, "n03", "issue_builder_license", {"holder_agent_id": "n04"}
        )

    assert not isinstance(raised.value, NotAtRequiredLocation)
    assert "government_complex" not in str(raised.value)


# ── the sweep, which is the actual guard ──────────────────────────────


# Every module that defines tool handlers. Derived from the registry's own
# import list rather than a hand-kept tuple, for the same reason this file
# exists: a hand-kept list of places to look is what let three sites hide.
def _handler_modules() -> list[Path]:
    root = Path(registry_module.__file__).parent
    return sorted(
        p
        for p in [*root.glob("*.py"), *(root / "primitives").glob("*.py")]
        if p.name != "__init__.py"
    )


class _RawPlaceIntoCharter(ast.NodeVisitor):
    """Finds `x = world.location_of(...)` flowing into a `world.charter.*`
    call in the same function.

    Deliberately crude: it does not try to prove the argument reaches a
    location parameter, because a handler that reads its own location and
    calls the charter in the same breath is either passing it or has a
    name that says otherwise. False positives here cost one call to
    `place_for_charter`, which is correct anyway. A false NEGATIVE costs a
    civic tool nobody can call for twenty-three runs.
    """

    def __init__(self) -> None:
        self.offenders: list[tuple[str, int]] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check(node)
        self.generic_visit(node)

    def _check(self, node: ast.AST) -> None:
        raw_names: set[str] = set()
        charter_args: list[tuple[str, int]] = []

        for sub in ast.walk(node):
            # `here = world.location_of(agent.agent_id) or ""`
            if isinstance(sub, ast.Assign):
                for call in ast.walk(sub.value):
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "location_of"
                    ):
                        for target in sub.targets:
                            if isinstance(target, ast.Name):
                                raw_names.add(target.id)

            # `await world.charter.elections.declare_candidacy(..., here)`
            if isinstance(sub, ast.Call) and _is_charter_call(sub.func):
                for arg in [*sub.args, *(kw.value for kw in sub.keywords)]:
                    if isinstance(arg, ast.Name):
                        charter_args.append((arg.id, sub.lineno))

        name = getattr(node, "name", "?")
        for arg_name, lineno in charter_args:
            if arg_name in raw_names:
                self.offenders.append((f"{name} (arg `{arg_name}`)", lineno))


def _is_charter_call(func: ast.expr) -> bool:
    """`world.charter.<engine>.<method>` or `world.charter.<method>`."""
    node = func
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    parts.reverse()
    return len(parts) >= 3 and parts[0] == "world" and parts[1] == "charter"


def test_no_handler_hands_a_raw_location_to_the_charter():
    """The sweep. Enumerating fixed sites is what let three hide."""
    findings: list[str] = []
    for path in _handler_modules():
        visitor = _RawPlaceIntoCharter()
        visitor.visit(ast.parse(path.read_text()))
        for where, lineno in visitor.offenders:
            findings.append(f"  {path.name}:{lineno}  {where}")

    assert not findings, (
        "These handlers hand the charter a location the world gave them, "
        "which the charter compares with `!=` against its own constant. "
        "The map's spelling never equals the charter's, so the tool passes "
        "its own gate and the engine behind it refuses - invisibly, because "
        "CharterError is not a ToolError.\n\n"
        + "\n".join(findings)
        + "\n\nUse `place_for_charter(world, agent_id, LOCATION_*)`."
    )


def test_the_sweep_can_actually_see_an_offender():
    """A guard that cannot fail is not a guard.

    The three real offenders are fixed, so the sweep above now passes on
    the real tree. This proves it passes because the tree is clean and not
    because the visitor never matches anything.
    """
    offending = inspect.cleandoc(
        """
        async def bad_handler(world, agent, params):
            here = world.location_of(agent.agent_id) or ""
            return await world.charter.elections.declare_candidacy(
                params.election_id, agent.agent_id, here
            )
        """
    )
    visitor = _RawPlaceIntoCharter()
    visitor.visit(ast.parse(offending))
    assert [w for w, _ in visitor.offenders] == ["bad_handler (arg `here`)"]

    fixed = inspect.cleandoc(
        """
        async def good_handler(world, agent, params):
            here = place_for_charter(world, agent.agent_id, LOCATION_TOWN_HALL)
            return await world.charter.elections.declare_candidacy(
                params.election_id, agent.agent_id, here
            )
        """
    )
    visitor = _RawPlaceIntoCharter()
    visitor.visit(ast.parse(fixed))
    assert visitor.offenders == []
