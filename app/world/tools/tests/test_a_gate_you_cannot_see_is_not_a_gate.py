"""A parameter the handler refuses without, and the schema never showed.

RUN 25, THE BAKERY. Marta Thorne and Tova Okonjo negotiated a complete
employment contract out loud, over fourteen ticks:

    t51  Marta: "I can offer you fifteen sim per day for the morning shift"
    t53  Tova:  "I'll take it, Marta. Fifteen a day, six days, paid weekly."
    t54  Marta: "I need to hear it plain: do you agree to work for..."
    t55  Tova:  "I agree. Fifteen a day, six days, paid weekly. I'll start now."

`hire_employee` refused all six attempts, because `employee_consent` was
False. Marta could not have set it. This is the schema she was sent:

    {"name": "world_hire_employee",
     "description": "... Needs: business_id, employee_agent_id.",
     "input_schema": {"properties": {"business_id": {...},
                                     "employee_agent_id": {...}},
                      "required": ["business_id", "employee_agent_id"]}}

`employee_consent` is not in it. Not in the properties, not in the
description, not in `Needs:`. The one parameter that decides whether the
call succeeds was invisible.

She worked it out anyway, and said so in the town:

    t56  Marta: "There's fifteen for today, Tova. Not a wage proper yet -
                 the system's being stubborn about your agreement - but
                 you'll have what's fair."

and paid the fifteen sim by hand with `transfer`. The relationship formed.
The tool would not record it, so the town shows zero employees, zero
wages, and S6 - "is a promise made a promise kept" - has nothing to
measure.

WHY IT WAS INVISIBLE. TRADE-02 fixed index rows that declared
`properties: {}` by carrying the REQUIRED parameter names. Pydantic's
"required" means "has no default" - and `employee_consent: bool =
Field(default=False)` has one. So a field that is optional to the PARSER
and mandatory to the HANDLER is dropped, and the tool becomes uncallable
from an index row for the rest of the run.

`teach_skill` has the identical consent flag and made it required, so it
survives. `hire_employee`'s own comment claims it "is the same consent
path `teach_skill` uses". It was not. `teach_tool` had the same divergence.

The sweep below is the guard. It does not enumerate the three tools: it
finds every handler that refuses on a params field and checks that field
is one the resident is actually shown.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from world.tools.registry import json_schema_for

TOOLS_ROOT = Path(__file__).resolve().parents[1]


def _handler_modules() -> list[Path]:
    return sorted(
        p
        for p in [*TOOLS_ROOT.glob("*.py"), *(TOOLS_ROOT / "primitives").glob("*.py")]
        if p.name != "__init__.py"
    )


def _fields_the_handlers_refuse_on() -> set[str]:
    """Every `params.X` a handler raises over when it is falsy.

    Read off the handlers rather than listed here, so a fourth consent
    flag is covered the day it is written - the failure this whole file
    is about is a hand-kept list that fell one row behind.
    """
    found: set[str] = set()
    for path in _handler_modules():
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if not (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)):
                continue
            operand = test.operand
            if (
                isinstance(operand, ast.Attribute)
                and isinstance(operand.value, ast.Name)
                and operand.value.id == "params"
                and any(isinstance(n, ast.Raise) for n in ast.walk(node))
            ):
                found.add(operand.attr)
    return found


def _params_models() -> dict[str, type]:
    """Every pydantic params model reachable from the tool modules."""
    import importlib

    from pydantic import BaseModel

    models: dict[str, type] = {}
    for path in _handler_modules():
        rel = path.relative_to(TOOLS_ROOT.parents[1]).with_suffix("")
        module = importlib.import_module(".".join(rel.parts))
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                models[f"{path.name}:{name}"] = obj
    return models


def test_the_sweep_finds_the_consent_flags_at_all():
    """A guard that matches nothing passes for the wrong reason."""
    refused = _fields_the_handlers_refuse_on()
    assert "employee_consent" in refused
    assert "student_consent" in refused


def test_every_field_a_handler_refuses_on_is_one_the_resident_is_shown():
    """The whole finding, as one assertion.

    An index row carries a tool's REQUIRED parameters. A field the handler
    will not proceed without, but which pydantic calls optional because it
    has a default, is therefore never shown - and the tool cannot be
    called correctly by anyone who was sent an index row.
    """
    refused = _fields_the_handlers_refuse_on()
    invisible: list[str] = []

    for where, model in _params_models().items():
        try:
            schema = json_schema_for(model)
        except Exception:  # noqa: BLE001 - not every model is a tool's
            continue
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or ())
        for field in refused & set(properties):
            if field not in required:
                invisible.append(f"  {where}.{field}")

    assert not invisible, (
        "These handlers refuse when the field is falsy, and the field has a "
        "default - so it is dropped from the index row the resident is sent, "
        "and the tool cannot be called successfully at all:\n"
        + "\n".join(sorted(invisible))
        + "\n\nMake it required, the way `teach_skill.student_consent` is. A "
        "default of False on a flag that gates the handler is a gate nobody "
        "can see."
    )


@pytest.mark.parametrize(
    "module_name,model_name,field",
    [
        ("world.tools.primitives.civic", "HireEmployeeParams", "employee_consent"),
        ("world.tools.primitives.social", "TeachSkillParams", "student_consent"),
        ("world.tools.primitives.conveyance", "TeachToolParams", "student_consent"),
    ],
)
def test_each_consent_flag_is_required(module_name, model_name, field):
    """Named individually as well, so a failure says which tool."""
    import importlib

    model = getattr(importlib.import_module(module_name), model_name)
    required = set(json_schema_for(model).get("required") or ())

    assert field in required, (
        f"{model_name}.{field} gates the handler and is optional to the "
        "parser, so the resident is never shown it"
    )


def test_the_flag_still_defaults_to_refusing():
    """Required does not mean 'assumed true'.

    Making the field required must not make consent the default - that
    would turn a gate nobody could satisfy into a gate nobody has to. The
    point is that the caller must SAY, not that saying is skipped.
    """
    from world.tools.primitives.civic import HireEmployeeParams

    with pytest.raises(Exception):
        HireEmployeeParams(business_id="b_bakery", employee_agent_id="n18")

    asked = HireEmployeeParams(
        business_id="b_bakery", employee_agent_id="n18", employee_consent=False
    )
    assert asked.employee_consent is False
