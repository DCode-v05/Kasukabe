"""What the engine makes of a stream a different model would send.

This is the model-agility evaluation. It sends a known pair of tool calls
down the real chain - broker, translate, kimi markup recovery, real HTTP,
herald's transport, herald's frozen parser - once cleanly, then once per
hazard, and compares what came out the far end with what went in.

THE BAR, and it is deliberately low:

    The town may lose the turn. It may not lose the truth.

Losing a turn to a malformed stream is survivable; a resident wastes a
tick. Three things are not survivable, and each gets its own verdict:

  SILENT LOSS   calls went in, nothing came out, nothing raised, and the
                turn ended with `end_turn` as though the resident had
                simply chosen to do nothing. This is the worst outcome in
                the set, because every instrument downstream reads it as
                a resident that thought and declined to act.
  CORRUPTION    a call arrived carrying arguments that were never sent.
                Worse than a dropped call: the town does something
                nobody asked for. TRADE-01 cost 6,424 calls to this shape.
  MISREPORTED   the turn ended claiming a stop reason that is not what
                happened.

A hazard that raises loudly is PROVEN. Nothing was hidden.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from proof.brain import ScriptedBrain, always
from proof.hazards import ALL, drop_tool_block_start, tool_call_as_prose
from proof.harness import cognition
from proof.verdicts import FAILED, PROVEN, Findings, Verdict

AREA = "model-agility"

SENT = [
    ("world_look_around", {"detail": "full"}),
    ("world_move", {"destination_location_id": "Kasukabe Bakery"}),
]

TOOLS = [
    {
        "name": "world_look_around",
        "description": "Look around.",
        "input_schema": {
            "type": "object",
            "properties": {"detail": {"type": "string"}},
        },
    },
    {
        "name": "world_move",
        "description": "Walk somewhere.",
        "input_schema": {
            "type": "object",
            "properties": {"destination_location_id": {"type": "string"}},
        },
    },
]

PERCEPTION = (
    "It is game-day 0, 06:10 (day). You are Liora Thorne, the town's student. "
    "You are at home."
)


class Outcome:
    """What one pass produced."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.stop: str = ""
        self.raised: str = ""
        self.traced: int = 0
        self.noticed: bool = False

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def describe(self) -> str:
        if self.raised:
            return f"raised {self.raised}"
        return (
            f"{len(self.calls)} call(s) {self.names}, stop={self.stop or 'none'}"
        )


async def run_once(mutator: Any) -> Outcome:
    out = Outcome()
    tmp = Path(tempfile.mkdtemp(prefix="proof-agility-")) / "trace.db"
    brain = ScriptedBrain(plan=always(SENT), mutate=mutator)
    try:
        async with cognition(brain, tmp, seam=True) as town:
            try:
                events = await asyncio.wait_for(
                    town.think(PERCEPTION, tools=TOOLS, system=""), timeout=60
                )
            except Exception as exc:  # noqa: BLE001 - the verdict IS the class
                out.raised = type(exc).__name__
                events = []
            for e in events:
                if getattr(e, "tool_call", None) is not None:
                    out.calls.append((e.tool_call.name, dict(e.tool_call.input or {})))
                if getattr(e, "stop_reason", None) is not None:
                    out.stop = str(e.stop_reason)
            out.traced = len(town.traced_calls())
            w = town.wrapper
            out.noticed = bool(
                getattr(w, "turns_claiming_tools_that_sent_none", 0)
                or getattr(w, "turns_that_wrote_a_call_as_text", 0)
            )
    except Exception as exc:  # noqa: BLE001 - boot failures are results too
        out.raised = out.raised or f"boot:{type(exc).__name__}"
    return out


def judge(name: str, clean: Outcome, got: Outcome) -> Verdict:
    sent_args = {n: a for n, a in SENT}

    if got.raised:
        return Verdict(
            AREA,
            name,
            f"the turn failed loudly ({got.raised}); nothing was hidden",
            PROVEN,
            "a lost turn that raises is survivable",
        )

    for got_name, got_args in got.calls:
        if got_name not in sent_args:
            return Verdict(
                AREA, name,
                f"CORRUPTION: a call to {got_name!r} arrived and was never sent",
                FAILED, "the town would act on a verb nobody chose",
            )
        others = [a for n, a in SENT if n != got_name]
        if got_args and got_args in others:
            return Verdict(
                AREA, name,
                f"CORRUPTION: {got_name} arrived wearing another call's "
                f"arguments {got_args!r}",
                FAILED, "the town would act on arguments nobody wrote for it",
            )

    if clean.calls and not got.calls:
        if got.noticed:
            return Verdict(
                AREA, name,
                f"lost all {len(clean.calls)} calls and recorded the loss; "
                f"the turn ends {got.stop} and the seam counted it",
                PROVEN,
                "the call is not recoverable - the tool's name never crossed "
                "the wire - but the turn is no longer indistinguishable from "
                "a resident that chose not to act",
            )
        contradiction = got.stop == "StopReason.TOOL_USE"
        return Verdict(
            AREA, name,
            f"SILENT LOSS: {len(clean.calls)} calls went in, none came out, "
            + (
                "nothing raised, and the turn still reported TOOL_USE - it "
                "claims to have used tools it did not deliver"
                if contradiction
                else f"nothing raised, turn ended "
                f"{got.stop or 'with no stop reason'}"
            ),
            FAILED,
            "nothing in the chain wrote down that this turn tried to act",
        )

    if len(got.calls) < len(clean.calls):
        return Verdict(
            AREA,
            name,
            f"lost {len(clean.calls) - len(got.calls)} of {len(clean.calls)} "
            f"calls, and said so (stop={got.stop})",
            PROVEN,
            got.describe(),
        )

    return Verdict(
        AREA, name, f"survived intact: {got.describe()}", PROVEN, ""
    )


async def _counters_under(mutator: Any) -> tuple[int, int]:
    """Run one hazard through the seam-wrapped chain; return its counters."""
    tmp = Path(tempfile.mkdtemp(prefix="proof-seam-")) / "trace.db"
    brain = ScriptedBrain(plan=always(SENT), mutate=mutator)
    async with cognition(brain, tmp, seam=True) as town:
        try:
            await asyncio.wait_for(
                town.think(PERCEPTION, tools=TOOLS, system=""), timeout=60
            )
        except Exception:  # noqa: BLE001 - a raise is loud and not our case
            pass
        w = town.wrapper
        return (
            int(getattr(w, "turns_claiming_tools_that_sent_none", 0)),
            int(getattr(w, "turns_that_wrote_a_call_as_text", 0)),
        )


async def the_seam_notices_what_the_parser_lost(findings: Findings) -> None:
    """The two silent losses, run again through the wrapper chain.

    `sim/a_turn_that_tried_to_act.py` cannot recover either call - the
    tool's name never crossed the wire in one case, and guessing at an
    unknown markup in the other is how you call the wrong verb. It counts
    them, which is the whole difference between a turn that tried and
    failed and a resident that chose not to act.

    The clean control is not decoration. A counter that fires on every
    turn would 'catch' both hazards and mean nothing, which is exactly the
    gate-that-cannot-fail shape this package exists to delete.
    """
    clean_claim, clean_text = await _counters_under(None)
    findings.add(
        Verdict(
            AREA,
            "the seam stays quiet on a healthy turn",
            f"a well-formed stream tripped neither counter "
            f"({clean_claim} claimed-tools, {clean_text} call-shaped-text)",
            PROVEN if (clean_claim, clean_text) == (0, 0) else FAILED,
            "without this the two rows below would prove nothing",
        )
    )

    claimed, _ = await _counters_under(drop_tool_block_start)
    findings.add(
        Verdict(
            AREA,
            "a turn claiming tools it never sent is counted",
            f"the seam counted {claimed} such turn(s) where the parser "
            "reported TOOL_USE and delivered nothing",
            PROVEN if claimed else FAILED,
            "sim/a_turn_that_tried_to_act.py",
        )
    )

    _, as_text = await _counters_under(tool_call_as_prose)
    findings.add(
        Verdict(
            AREA,
            "a call written in an unknown markup is counted",
            f"the seam counted {as_text} turn(s) that wrote something "
            "call-shaped and called nothing",
            PROVEN if as_text else FAILED,
            "INFRA-07 in a spelling the broker does not recover",
        )
    )


async def stop_reason_fidelity(findings: Findings) -> None:
    """Does the reason a turn ended survive the journey?

    `herald/provider.py:573-577` maps three stop reasons and sends every
    other one to `END_TURN`. `StopReason.REFUSAL` is declared in
    `types.py:274` and produced by neither map. The broker DOES translate
    a guardrail stop to `"refusal"` (`translate.py:52-53`), so the signal
    exists right up until the parser flattens it.

    It does not matter much on Kimi, which rarely refuses. It matters a
    great deal on a model that refuses often: every refused turn is billed,
    produces nothing, and is recorded as an ordinary completed turn. The
    town cannot tell "the model declined" from "the resident had nothing
    to do", and neither can anybody reading the trace afterwards.
    """
    from proof.hazards import unknown_stop_reason

    for wire, meaning in (
        ("guardrail_intervened", "a guardrail stopped the model"),
        ("content_filtered", "the content was filtered"),
        ("max_tokens", "the model ran out of room"),
    ):
        got = await run_once(unknown_stop_reason(wire))
        arrived = got.stop or "nothing"
        distinct = arrived not in ("StopReason.END_TURN", "nothing")
        findings.add(
            Verdict(
                AREA,
                f"stop reason {wire!r} reaches the agent loop",
                f"{meaning}; the loop was told {arrived}"
                + ("" if distinct else " - indistinguishable from a normal turn"),
                PROVEN if distinct else FAILED,
                "herald/provider.py:573-577 maps three reasons and flattens "
                "the rest to END_TURN",
            )
        )


async def audit(findings: Findings) -> None:
    clean = await run_once(None)
    findings.add(
        Verdict(
            AREA,
            "a well-formed stream",
            f"the control: {clean.describe()}",
            PROVEN if len(clean.calls) == len(SENT) else FAILED,
            "if this is not intact, no hazard below means anything",
        )
    )
    for name, mutator in ALL.items():
        findings.add(judge(name, clean, await run_once(mutator)))
    await stop_reason_fidelity(findings)
    await the_seam_notices_what_the_parser_lost(findings)


def main() -> int:
    findings = Findings()
    asyncio.run(audit(findings))
    print(findings.matrix())
    print(findings.summary())
    return findings.exit_code()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
