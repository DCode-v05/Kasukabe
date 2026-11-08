"""Did anybody ever reach it?

This is the audit the project has most needed and never had. Every other
instrument here answers "does the mechanism work?"; this one answers
"has the mechanism ever been used?", and in this repository that is the
question that keeps coming back with a bad answer:

  - eighteen GOVERNMENT verbs, served in every seated winner's schema,
    with not one refusal row and not one call (GOV-3)
  - `toolify`, `do_in_order`: served in full, called zero times across
    five consecutive runs
  - nine verbs found dead across the series, "each at the cost of a four
    to ten dollar run"

None of those were failures of logic. Every one of them passed its unit
tests on the day it shipped. What was missing was a reader that counted
zero and said so.

WHY A CALL COUNT IS NOT A PASS. A verb with calls and no successes is a
worse result than a verb with no calls, not a better one - the town spent
paid turns reaching for it and got nothing. `buy` in the 50-resident run
was attempted 178 times and refused 140 of them, while the price board
appeared in a resident's perception exactly 0 times. That is FAILED, not
PROVEN, and counting attempts would have called it healthy.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

from proof.corpus import (
    SERIES,
    bare_tool_name,
    real_run_databases,
    recorded_turns,
)
from proof.engine_tools import served_engine_side
from proof.verdicts import FAILED, PROVEN, UNPROVEN, Findings, Verdict

AREA = "verb-reach"
ORPHANS = "verbs-not-in-the-registry"
COVERAGE = "corpus-coverage"

# Statuses the session log uses for a call that actually changed the world.
SUCCEEDED = {"success", "ok", "succeeded"}


@dataclass
class Reach:
    """What the corpus knows about one verb."""

    calls: int = 0
    successes: int = 0
    #: Calls whose row carries no outcome at all. Not failures. The
    #: platform seam writes `{"tool": ..., "params": ...}` with no
    #: `status` field for the verbs the engine serves directly, so a
    #: silent row means the corpus never recorded what happened - which
    #: is a different fact from "it was refused", and the instrument is
    #: not allowed to spend one as the other.
    unknown: int = 0
    seen_in: set[str] = field(default_factory=set)

    def note(self, status: str, where: str) -> None:
        self.calls += 1
        if status in SUCCEEDED:
            self.successes += 1
        elif not status:
            self.unknown += 1
        self.seen_in.add(where)


async def registered_tools() -> dict[str, str]:
    """`name -> namespace` for every verb the town serves.

    Built from the real registry, not from a scan of the source. A list
    of tool names parsed out of `ToolSpec(` literals would be a claim
    about the text; booting the registry is a claim about the town.
    """
    from world.tools.bootstrap import build_world

    _world, registry = await build_world(genesis_tick=0, seed=1)
    tools = getattr(registry, "tools", None)
    if tools is None:  # pragma: no cover - shape guard, not a fallback
        raise RuntimeError(
            "the registry no longer exposes `.tools`; this audit must be "
            "pointed at the new accessor rather than guess at one"
        )
    specs = tools.values() if hasattr(tools, "values") else tools
    return {s.name: getattr(getattr(s, "namespace", None), "value", "public") for s in specs}


def reach_from_sessions() -> dict[str, Reach]:
    """Verb reach as recorded in the 520 session files."""
    found: dict[str, Reach] = defaultdict(Reach)
    for turn in recorded_turns():
        for tool, status in turn.called():
            found[tool].note(status, f"series/{turn.series}")
    return found


def reach_from_databases() -> dict[str, Reach]:
    """Verb reach as recorded in `tool_invocations`.

    A database without that table is not counted as silence - it is not
    consulted at all, and `looked_in` says so.
    """
    found: dict[str, Reach] = defaultdict(Reach)
    for db in real_run_databases():
        if not db.has("tool_invocations"):
            continue
        for (tool, n) in db.rows(
            "select tool, count(*) from tool_invocations group by tool"
        ):
            if not tool:
                continue
            r = found[bare_tool_name(str(tool))]
            # The table records dispatches, which only exist after the
            # charge and the handler; it has no failure column, so these
            # count as calls AND successes.
            r.calls += int(n)
            r.successes += int(n)
            r.seen_in.add(db.name)
    return found


def merge(*sources: dict[str, Reach]) -> dict[str, Reach]:
    out: dict[str, Reach] = defaultdict(Reach)
    for src in sources:
        for name, r in src.items():
            m = out[name]
            m.calls += r.calls
            m.successes += r.successes
            m.unknown += r.unknown
            m.seen_in |= r.seen_in
    return out


def reach_sentence(r: Reach) -> str:
    """What to say about a verb that was called and never succeeded.

    Two different facts wear the same zero. `pay_tax` has ten calls and ten
    recorded `error` statuses: the town answered, and the answer was no.
    `search_memory` has thirteen calls and thirteen rows with no status
    field: nobody wrote down what happened. The first earns the accusation
    - paid calls that changed nothing - and the second must not, because
    the evidence for it does not exist.
    """
    refused = r.calls - r.unknown
    if refused <= 0:
        return (
            f"called {r.calls} times, every one with no outcome recorded - the "
            "corpus does not say whether the town answered, so neither success "
            "nor refusal is evidenced here"
        )
    tail = (
        f"; {r.unknown} with no outcome recorded and not counted either way"
        if r.unknown
        else ""
    )
    return (
        f"reached for {refused} times and never once succeeded - every one a "
        "paid call that changed nothing; the refusals may be correct, which is "
        f"why this is not scored{tail}"
    )


def judge(
    inventory: dict[str, str], found: dict[str, Reach], corpus: str
) -> list[Verdict]:
    rows: list[Verdict] = []
    for name in sorted(inventory):
        ns = inventory[name]
        r = found.get(name)
        label = f"{name} ({ns})" if ns != "public" else name
        if r is None or r.calls == 0:
            rows.append(
                Verdict(
                    AREA,
                    label,
                    "served to residents; never once called",
                    UNPROVEN,
                    f"searched {corpus}",
                )
            )
        elif r.successes == 0:
            # UNPROVEN, not FAILED, and the distinction cost a false
            # finding to learn. `pay_tax` reads 10 calls / 0 successes,
            # which looked like a broken verb. It is not: five of the ten
            # were answered "you owe no head tax" - the town refusing
            # correctly - and four more were called with NO arguments at
            # all. A verb that refuses correctly has held its contract.
            #
            # What the row does prove is that the town spent paid calls
            # and got nothing back, every time it tried. That is worth
            # surfacing loudly and is not, by itself, evidence of a defect.
            # Deciding which it is needs a human reading the refusals, so
            # the instrument declines to score it.
            rows.append(
                Verdict(
                    AREA,
                    label,
                    reach_sentence(r),
                    UNPROVEN,
                    f"in {', '.join(sorted(r.seen_in)[:3])}",
                )
            )
        else:
            rows.append(
                Verdict(
                    AREA,
                    label,
                    f"{r.successes} successful of {r.calls} calls",
                    PROVEN,
                    f"in {', '.join(sorted(r.seen_in)[:3])}",
                )
            )

    # A verb the corpus used that the registry does not serve is a
    # different finding and belongs in a different column: either a rename
    # that orphaned the recorded history, or a tool the resident reaches
    # through some other surface (the seam registers `search_memory`,
    # `recall_memories` and `recall_session_log` straight onto the thread).
    # Filing these as "never called" would be exactly backwards.
    for name in sorted(set(found) - set(inventory)):
        r = found[name]
        seen = f"in {', '.join(sorted(r.seen_in)[:3])}"
        # Ask the engine's registry before recording an absence. These three
        # are not orphans: `pinned_boot` builds and registers them onto the
        # same registry the world:* verbs reach the model through, so a
        # resident calling one is calling a verb that exists and answers.
        engine_side = served_engine_side(name)
        if engine_side is not None:
            served, why = engine_side
            if not served:
                rows.append(Verdict(ORPHANS, name, f"called {r.calls} times; {why}", FAILED, seen))
            elif r.successes:
                # Judged by the same rule as any other verb: it ran, and the
                # world answered. The only difference is which registry
                # served it.
                rows.append(
                    Verdict(
                        ORPHANS, name,
                        f"{r.successes} successful of {r.calls} calls; {why}",
                        PROVEN, seen,
                    )
                )
            else:
                rows.append(
                    Verdict(ORPHANS, name, f"{reach_sentence(r)}; {why}", UNPROVEN, seen)
                )
            continue
        rows.append(
            Verdict(
                ORPHANS,
                name,
                f"called {r.calls} times but the world registry does not "
                "serve it",
                UNPROVEN,
                seen,
            )
        )
    return rows


def corpus_coverage() -> list[Verdict]:
    """What this instrument cannot see, said out loud.

    The same rule that governs the verbs governs the audit itself. The two
    most recent and most interesting runs - `election8` (the run that
    finally seated a government) and `teambox50` - left a report, a log and
    a video in `docs/runs/series/`, and no per-turn session files at all.
    Their traces (`app/data/election-at-dawn-8.db`) are gitignored and are
    not in this checkout.

    So every UNPROVEN row above means "not in the corpus this instrument
    can read", not "never happened in the history of the town". Stating
    that is the difference between an instrument and a claim.
    """
    rows: list[Verdict] = []
    for series in sorted(p.name for p in SERIES.iterdir() if p.is_dir()):
        files = len(list((SERIES / series / "sessions").glob("*.jsonl")))
        rows.append(
            Verdict(
                COVERAGE,
                series,
                f"{files} per-turn session files recorded"
                if files
                else "no per-turn session files; this run cannot be re-audited",
                PROVEN if files else UNPROVEN,
                f"docs/runs/series/{series}/sessions/",
            )
        )
    return rows


async def audit(findings: Findings) -> None:
    inventory = await registered_tools()
    sessions = reach_from_sessions()
    databases = reach_from_databases()
    dbs = [d.name for d in real_run_databases() if d.has("tool_invocations")]
    files = len(list(SERIES.glob("*/sessions/*.jsonl")))
    corpus = f"{files} session files and {len(dbs)} trace databases"
    findings.add(*judge(inventory, merge(sessions, databases), corpus))
    findings.add(*corpus_coverage())


def main() -> int:
    findings = Findings()
    asyncio.run(audit(findings))
    print(findings.matrix())
    print(findings.summary())
    return findings.exit_code()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
