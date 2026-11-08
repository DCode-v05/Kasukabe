"""Would the calls that worked still be accepted today?

520 recorded session files hold every tool call a real model made across
the project's history, with the arguments it chose and the answer the
world gave. That corpus was paid for at four to ten dollars a run. It is
the only body of *real model output* this project owns, and replaying it
costs nothing.

WHAT IS REPLAYED, AND WHY NOT MORE. Not the whole act. Those runs had
their own towns - other residents, other inventories, other places - so
re-executing `move` or `buy` against a fresh genesis would fail for
reasons that are about world state and not about regression. A replay
that reported those as breakages would cry wolf until nobody read it.

What IS world-state-independent, and is exactly the thing that keeps
breaking here, is the **contract**: does the verb still exist, and does it
still accept the arguments a model actually sent it?

That is the TRADE-01 shape. `post_price` took `on_behalf_of_business`,
every resident wrote `business_id`, pydantic dropped the unknown field,
and a whole run settled nothing across 6,424 calls - "the silent drop is
the defect. It returns SUCCESS for an act nobody asked for." A rename, a
tightened type or a removed parameter does the same thing, and no unit
test catches it, because every unit test is written against the CURRENT
spelling.

So: every call the corpus records as having SUCCEEDED is pushed through
the registry's real `validate_params` - the same function the live
dispatcher calls, including the synonym folding and the stray-parameter
refusal. A recorded success that no longer validates is a contract that
moved under the model's feet.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

from proof.corpus import recorded_turns
from proof.deliberate import declared_reason
from proof.engine_tools import resolve
from proof.verdicts import FAILED, PROVEN, UNPROVEN, Findings, Verdict

AREA = "replay"

SUCCEEDED = {"success", "ok", "succeeded"}


@dataclass
class Shape:
    """One distinct way the corpus called one verb."""

    tool: str
    keys: frozenset[str]
    example: dict
    times: int = 0
    series: set[str] = field(default_factory=set)

    @property
    def signature(self) -> str:
        return f"{self.tool}({', '.join(sorted(self.keys)) or 'no arguments'})"


def shapes_that_worked() -> dict[tuple[str, frozenset], Shape]:
    """Every (verb, argument-set) the corpus records as having succeeded.

    Deduplicated by argument NAMES rather than values: the contract is
    about which parameters exist, and 2,912 successful `move` calls make
    the same claim about the contract as one does.
    """
    found: dict[tuple[str, frozenset], Shape] = {}
    for turn in recorded_turns():
        for action in turn.actions:
            if str(action.get("status", "")).lower() not in SUCCEEDED:
                continue
            tool = action.get("tool")
            params = action.get("params")
            if not isinstance(tool, str) or not isinstance(params, dict):
                continue
            from proof.corpus import bare_tool_name

            name = bare_tool_name(tool)
            key = (name, frozenset(params))
            shape = found.get(key)
            if shape is None:
                shape = found[key] = Shape(
                    tool=name, keys=frozenset(params), example=dict(params)
                )
            shape.times += 1
            shape.series.add(turn.series)
    return found


async def audit(findings: Findings) -> None:
    from world.tools.bootstrap import build_world

    world, registry = await build_world(genesis_tick=0, seed=1)
    shapes = shapes_that_worked()

    if not shapes:
        findings.add(
            Verdict(
                AREA,
                "the recorded corpus",
                "no successful calls found to replay",
                UNPROVEN,
                "docs/runs/series/*/sessions/*.jsonl",
            )
        )
        return

    by_verdict: dict[str, list[Shape]] = defaultdict(list)
    rejected: dict[str, str] = {}

    for shape in shapes.values():
        spec = registry.tools.get(shape.tool)
        if spec is None:
            by_verdict["gone"].append(shape)
            continue
        try:
            registry.validate_params(spec, dict(shape.example), world, None)
            by_verdict["ok"].append(shape)
        except Exception as exc:  # noqa: BLE001 - the refusal IS the finding
            by_verdict["refused"].append(shape)
            rejected[shape.signature] = f"{type(exc).__name__}: {exc}"

    on_purpose = [
        shape for shape in by_verdict["refused"]
        if declared_reason(shape.signature, rejected[shape.signature]) is not None
    ]
    unexplained = len(by_verdict["refused"]) - len(on_purpose)
    findings.add(
        Verdict(
            AREA,
            "the recorded corpus still validates",
            f"{len(by_verdict['ok'])} of {len(shapes)} distinct call shapes that the corpus "
            f"records as successful are still accepted by the registry's own validator; "
            f"{len(on_purpose)} more are refused on purpose and declared as such; "
            f"{unexplained} are refused and nobody has said why",
            PROVEN if unexplained == 0 else FAILED,
            f"{sum(s.times for s in shapes.values())} recorded successful "
            "calls, deduplicated by argument names",
        )
    )

    for shape in sorted(by_verdict["refused"], key=lambda s: -s.times):
        intended = declared_reason(shape.signature, rejected[shape.signature])
        if intended is not None:
            findings.add(
                Verdict(
                    AREA,
                    shape.signature,
                    f"succeeded {shape.times} times in the corpus and is refused today ON "
                    f"PURPOSE: {intended}",
                    PROVEN,
                    f"seen in {', '.join(sorted(shape.series)[:3])}; declared in "
                    "proof/deliberate.py and matched against the refusal the town gives",
                )
            )
            continue
        findings.add(
            Verdict(
                AREA,
                shape.signature,
                f"succeeded {shape.times} times in the corpus and is refused "
                f"today: {rejected[shape.signature][:160]}",
                FAILED,
                f"seen in {', '.join(sorted(shape.series)[:3])}; a spelling a "
                "real model used - candidate for param_repair.SYNONYMS, "
                "unless the tightening was deliberate",
            )
        )

    for shape in sorted(by_verdict["gone"], key=lambda s: -s.times):
        seen = f"seen in {', '.join(sorted(shape.series)[:3])}"
        engine_side = resolve(shape.tool, sorted(shape.keys))
        if engine_side is not None:
            hint, why = engine_side
            if hint == "failed":
                intended = declared_reason(shape.signature, why)
                if intended is not None:
                    hint, why = "proven", f"refused on purpose: {intended}"
            findings.add(
                Verdict(
                    AREA,
                    shape.signature,
                    f"succeeded {shape.times} times in the corpus; {why}",
                    {"proven": PROVEN, "failed": FAILED}.get(hint, UNPROVEN),
                    seen,
                )
            )
            continue
        findings.add(
            Verdict(
                AREA,
                shape.signature,
                f"succeeded {shape.times} times in the corpus; the registry "
                "no longer serves this verb",
                UNPROVEN,
                f"{seen} - a rename or a withdrawal, not necessarily a defect",
            )
        )


def main() -> int:
    findings = Findings()
    asyncio.run(audit(findings))
    print(findings.matrix())
    print(findings.summary())
    return findings.exit_code()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
