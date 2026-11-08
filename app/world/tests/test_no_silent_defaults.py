"""Fields that carry a finding must exist, or the line must break.

Build Spec v2 clause 4.4 and defect D10.

THE DEFECT THIS SHAPE PRODUCES. `getattr(obj, "field", default)` answers
`default` for two completely different situations: the field is genuinely
absent, and the field was renamed. The second is a lie that reads exactly
like data. This project has already paid for it once: the dormant counter
read `getattr(dormancy, "dormant", False)` against a field called
`is_dormant` and therefore reported **zero dormant agents in every run ever
made** - dormancy being the last step of the starvation escalation the whole
experiment exists to measure. Every dashboard was green and the number was
fiction.

Auditing `world/` found **105** three-argument `getattr` sites, 58 of them
with a bool/None default that could mask exactly that. Most are legitimate:
the reporting layer reads across several record shapes, and duck-typing is
the right tool for a field that really is optional. The dangerous subset is
narrow and nameable: fields that are NOT optional, that a reported number
depends on, and whose disappearance would be reported as a plausible value
instead of an error.

This module pins that subset. It does not forbid `getattr` - a blanket ban
would be refactored around in a week. It asserts that every field name the
reporting layer reads off a known model still resolves on that model, so a
rename fails a test instead of resuming the lie.

Adding a field here is cheap. Leaving one out is how the next dormancy bug
ships.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from world.life.models import AgentLifeRecord, DormancyState, HealthState, Household

# model -> the field names reporting code reads off it, by any route
# (declared pydantic field, computed property, or both). Each entry is a
# field whose silent absence would change a number somebody reads.
REPORTED_FIELDS: dict[type, tuple[str, ...]] = {
    AgentLifeRecord: (
        "agent_id",
        "age_years",
        "alive",
        "dormancy",
        "health",
        "household_id",
        "spouse_id",
        "death_cause",
        # A computed property, not a stored field. Read via getattr in
        # agents_api, main and run_genesis alike, and just as capable of
        # silently defaulting to False if it were ever removed - which would
        # report a town with no children.
        "is_minor",
    ),
    # The exact pair the original defect confused. `dormant` is NOT a field
    # and must never become one: code reading it got False forever.
    DormancyState: ("is_dormant", "dormant_since_hours"),
    HealthState: ("current_ailment",),
    Household: ("household_id", "member_ids"),
}

# Field names that reporting code must never read, because a real field of
# a confusingly similar name exists and the near-miss is the whole bug.
FORBIDDEN_NEAR_MISSES: dict[type, tuple[str, ...]] = {
    DormancyState: ("dormant",),
    # `name` is NOT on AgentLifeRecord and must never be added silently.
    # bootstrap._display_name read `getattr(agent, "name", None)` off one for
    # the whole life of the project, answered its default every single time,
    # and stored every speaker's ID as their name in every utterance ever
    # recorded. It surfaced only when the open-questions section trusted the
    # stored value and rendered "n01 (n01) asked you...". Names live in
    # world.agent_names; see bootstrap.display_name.
    AgentLifeRecord: ("name",),
}


@pytest.mark.parametrize(
    ("model", "field"),
    [(m, f) for m, fields in REPORTED_FIELDS.items() for f in fields],
    ids=lambda v: v if isinstance(v, str) else v.__name__,
)
def test_a_reported_field_still_exists_on_its_model(model: type, field: str) -> None:
    """Rename the field, break this test. That is the entire point.

    Checks the class rather than an instance so a computed property counts
    as present, and a pydantic field counts whether or not it has a default.
    """
    declared = getattr(model, "model_fields", {})
    assert field in declared or hasattr(model, field), (
        f"{model.__name__}.{field} no longer resolves. Every "
        f'getattr(..., "{field}", <default>) in world/ is now silently '
        "returning its default, which reads as data and is not."
    )


@pytest.mark.parametrize(
    ("model", "field"),
    [(m, f) for m, fields in FORBIDDEN_NEAR_MISSES.items() for f in fields],
    ids=lambda v: v if isinstance(v, str) else v.__name__,
)
def test_the_near_miss_field_name_does_not_exist(model: type, field: str) -> None:
    """If `dormant` ever becomes real, this test fails and somebody has to
    decide which of the two names the reporting layer means. That is a
    better outcome than two fields whose values disagree."""
    declared = getattr(model, "model_fields", {})
    assert field not in declared


# Variable names that hold an AgentLifeRecord in this codebase. The near-miss
# ban is scoped to these: `name` and `dormant` are legitimate on other types,
# and a ban that could not tell the difference would flag five correct lines
# to catch one wrong one.
AGENT_RECORD_RECEIVERS = frozenset({"agent", "record", "life_record", "dormancy", "r"})


def getattr_sites() -> list[tuple[str, int, str, str]]:
    """Every three-argument getattr in world/, outside tests."""
    sites: list[tuple[str, int, str, str]] = []
    for path in sorted(pathlib.Path("world").rglob("*.py")):
        if "/tests/" in path.as_posix():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) == 3
                and isinstance(node.args[1], ast.Constant)
            ):
                sites.append(
                    (
                        path.as_posix(),
                        node.lineno,
                        str(node.args[1].value),
                        ast.unparse(node.args[2]),
                        # The receiver, so a ban can be scoped to the type
                        # that actually lacks the field. `name` is real on
                        # GenesisAgent, Business and a map sector, and absent
                        # only from AgentLifeRecord - a check that cannot
                        # tell them apart bans five correct lines to catch
                        # one wrong one, and gets deleted within a week.
                        ast.unparse(node.args[0]),
                    )
                )
    return sites


def test_no_reporting_code_reads_a_forbidden_near_miss() -> None:
    """The static half: nobody may reach for `dormant` again by name.

    The two tests above protect the model; this one protects the readers,
    because the original bug was not a wrong model - it was a correct model
    read by a wrong name.
    """
    banned = {f for fields in FORBIDDEN_NEAR_MISSES.values() for f in fields}
    offenders = [
        f"{path}:{line} reads {receiver}.{field} (default {default})"
        for path, line, field, default, receiver in getattr_sites()
        if field in banned
        and receiver.split(".")[-1].split("(")[0] in AGENT_RECORD_RECEIVERS
        # agents_api reads is_dormant FIRST and falls back to the old name
        # for records written by an older build. That is a deliberate
        # compatibility shim, not the defect, so it is allowed to mention
        # the name as long as the real field is tried first.
        and "is_dormant" not in pathlib.Path(path).read_text(encoding="utf-8").splitlines()[line - 1]
    ]
    assert not offenders, "silent default on a known near-miss field:\n  " + "\n  ".join(offenders)


def test_the_audit_inventory_is_not_growing_unnoticed() -> None:
    """A budget, not a ban.

    105 sites were counted when clause 4.4 was written. The number is
    allowed to move, but not quietly: a change that adds thirty more
    should have to say so, because the last one hid a live bug for the
    whole life of the project.

    131 as of the per-resident resource work. The one site that moved it
    is `getattr(self.supervisor, "processes", {})` in run_genesis, and it
    is legitimate: `processes` is declared on the process backend and on
    neither the namespace nor the firecracker one, so resource sampling
    genuinely has to ask whether this supervisor has a process table at
    all. Raising the budget by one and saying why is what this guard is
    for; contorting a real cross-backend check to keep a number down is
    not. It has caught two of my own bad defaults this week - four
    getattrs on SkillStep and one on ToolSpec.location_gate - both of
    which would have answered None if a field were renamed.
    """
    count = len(getattr_sites())
    assert count <= 131, (
        f"{count} three-argument getattr sites in world/, up from the 105 "
        "counted in the Build Spec v2 audit. Each one can answer its "
        "default when a field is renamed. Justify the new ones or read the "
        "fields directly."
    )
