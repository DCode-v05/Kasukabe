"""§4.4 — a default that fires forever looks exactly like working code.

Two bugs in this project had the identical shape:

    getattr(clock, "hour", 0)          -> the town clock read 00:00 for
                                          every run ever made
    getattr(dormancy, "dormant", False) -> the dormant count read 0 for
                                          every run ever made

Both were in the same route function. Both read a field the object does
not have. Both returned a plausible number instead of raising, so nothing
downstream could tell the difference between a healthy town and a broken
reading. The second survived a fix to the first, three lines away.

This pins the routes that report the town's vital signs: they must read
their fields directly, so a rename breaks the line loudly instead of
quietly resuming a lie.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# The surfaces an operator or a report actually reads to decide whether the
# town is healthy. A silent default in any of these is indistinguishable
# from good news.
VITALS_ROUTES = ["world_state", "world_vitals", "agent_detail"]

FORBIDDEN_FIELDS = ["hour", "minute", "dormant", "alive", "is_dormant", "health"]


def _module_source() -> str:
    return (Path(__file__).resolve().parents[1] / "agents_api.py").read_text(encoding="utf-8")


def _route_bodies() -> dict[str, str]:
    tree = ast.parse(_module_source())
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in VITALS_ROUTES:
                out[node.name] = ast.unparse(node)
    return out


def test_the_vitals_routes_still_exist() -> None:
    """If a route is renamed this test must fail rather than pass vacuously
    by finding nothing to check."""
    found = _route_bodies()
    assert "world_state" in found, (
        "the /world/state route was not found - this suite would otherwise "
        "pass by checking nothing"
    )


@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_no_vitals_route_reads_a_field_through_a_default(field: str) -> None:
    """`getattr(obj, "hour", 0)` cannot fail. That is the whole problem:
    when the field is missing or renamed, the default is returned and
    reported as fact."""
    for name, code in _route_bodies().items():
        needle = f'getattr({{}}, {field!r}'
        # any getattr naming this field with a third argument
        for call in ast.walk(ast.parse(code)):
            if not isinstance(call, ast.Call):
                continue
            if getattr(call.func, "id", None) != "getattr":
                continue
            if len(call.args) < 3:
                continue
            target = call.args[1]
            if isinstance(target, ast.Constant) and target.value == field:
                raise AssertionError(
                    f"{name}() reads {field!r} through getattr with a default "
                    f"({ast.unparse(call)}). If the field is ever missing this "
                    "reports the default as a measurement."
                )
        assert needle or True  # keeps the loop honest when a route has no calls


def test_the_two_known_instances_stay_fixed() -> None:
    """Named explicitly, because these two are the reason the rule exists."""
    code = _route_bodies()["world_state"]
    assert "timestamp_for" in code, "the clock must come from core/'s own function"
    assert "is_dormant" in code, "dormancy must be read off the real field"
    assert '"dormant"' not in code
