"""A composite tool with control flow, and the ceilings that make it safe.

`PROG-02`. Kasukabe's economy is *thinking costs money*. A resident
spends one model call per turn and gets one decision out of it. In run
`deni-a6f9eb4-5h` that bought 400 acting turns out of 3,741, at $27.78.
If a resident could write a small program - walk to the mill, take 5
flour, walk to the shop, stock the shelf, post 30 - that is one model
call for six acts instead of six calls, and the ratio this project
measures changes shape.

Half of it already existed. `meta.py`'s composite handler runs N steps in
one dispatch, each charged as itself, each gated as itself, each traced as
itself. What it lacked was control flow: the steps were a fixed list with
one level of `$placeholder` substitution, deliberately, because the
alternative on offer was an expression language and an expression language
inside the one path through which world state changes is a new physics.

This module is the third option. It adds three things and no more:

  * a CONDITION on a step (`when`), drawn from a closed set of
    comparisons named in `world/tools/models.py` as `CONDITION_OPS`;
  * a bounded REPEAT (`repeat`), capped by a world rule rather than by
    the author's good intentions;
  * a BINDING (`bind`), so one step can read what the step before it
    found out.

WHAT IT DELIBERATELY IS NOT. There is no arithmetic, no boolean algebra,
no nested conditions, no while, no jump, no user-defined name that is not
a step binding, and no way to read world state from inside a condition.
That last one is the load-bearing restriction and it is worth being exact
about: **a condition can only see what a step already paid to find out.**
A resident that wants to branch on whether the shelf is empty puts a real
`look_around` in the program and reads its binding. Giving conditions
their own window onto the world would be a second way to read the town
that nothing charges for and no gate covers, and the entire reason a
composite is safe is that it adds no path the registry does not already
own. Every step still goes through `ToolRegistry.dispatch`, so every step
is still location-gated, priced, validated, refusable and logged.

WHY THE CAPS ARE WORLD RULES. `docs/runs/WIRING-AND-TOOL-CALLING-AUDIT.md`
section 7 says it in one line: *"an unbounded loop is a resident that
spends its whole wallet in one turn, so the cap must be a world rule, not
a convention."* A convention is a number the author is trusted to respect.
A world rule is a number the author cannot argue past, because the
interpreter stops whether the author agrees or not. There are four, and
they answer four different questions:

  * `PROGRAM_MAX_STEPS`  - how long a program may be FILED. Checked at
    authoring time, so a bad program is refused before it can be sold,
    taught or licensed to anyone else.
  * `PROGRAM_MAX_REPEAT` - how many times ONE step may repeat.
  * `PROGRAM_MAX_CALLS`  - how many dispatches the whole program may make
    once loops are expanded. Two steps each repeating twenty times is
    forty acts, and the per-step cap alone would have allowed it.
  * `PROGRAM_WALLET_SHARE` - what share of the wallet the caller held
    when the program STARTED it may spend before the interpreter stops.
    This is the one the audit actually names. The other three bound the
    shape of the program; only this one bounds the damage.

Stopping is not failing. A program that hits a ceiling returns what it
did, says which ceiling it hit and at which step, and leaves the world
exactly as its completed steps left it. It does not raise, because a
raise would roll back acts that really happened and really were charged
(`ToolRegistry.execute` restores tools-owned state on any exception), and
"you harvested nine and then we un-harvested them" is a worse answer than
"you harvested nine and stopped." A step that REFUSES is different and
still raises, because a refusal means the world would not let that act
happen, and TOOL-06's refund path is exactly what gives back what the
earlier steps paid.

WHAT MUST BE DECIDED BY THE OPERATOR, NOT HERE. The audit is explicit
that this is not an engineering call: *"'Thinking costs money' IS the
experiment. If six acts cost one call, a resident that writes good
programs is structurally richer than one that does not - which is either
the most interesting result this project could produce, or a confound
that makes every earlier run incomparable."* This module does not decide
that. It gives the operator the lever - `PROGRAM_MAX_CALLS`, settable per
run - and it makes the effect measurable, which is PROG-01's parent link
in the invocation log. Setting `KASUKABE_PROGRAM_MAX_CALLS=1` reduces a
program to a single act and makes a run comparable with every run before
this one, at the cost of the feature. That is a run configuration, and
`docs/decisions/PROG-COST-MODEL.md` is where the choice gets written down.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from world.tools.errors import ToolError
from world.tools.models import CONDITION_OPS, StepCondition, ToolStep

if TYPE_CHECKING:  # pragma: no cover - typing only
    from world.life.models import AgentLifeRecord
    from world.tools.registry import ToolRegistry
    from world.tools.state import WorldState


def _cap(name: str, default: int) -> int:
    """An operator-set ceiling, or the default.

    Read at import rather than per call: a ceiling that can move while a
    program is halfway through it is not a ceiling. A run that wants a
    different number sets the environment variable and boots.

    RAISES on a value it cannot read, rather than falling back. These are
    safety ceilings, and a fallback would answer a typo with the
    PERMISSIVE number - `KASUKABE_PROGRAM_WALLET_SHARE_PERCENT=5O` (with a
    letter O) becoming 50% is an operator who thinks they tightened
    something and did not. That is the silent-default shape this
    repository polices in its own code (`test_no_silent_defaults`), and
    it is worse in a knob than in a field because nobody re-reads a knob.

    At import, so it fails at boot with the variable named, not on the
    first program somebody writes three hours into a run.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"{name}={raw!r} is not a whole number. It is a ceiling on what "
            "one program may do, and guessing at it would set the ceiling to "
            f"{default} while you believed you had set it to something else."
        ) from None
    if value < 0:
        raise ValueError(
            f"{name}={value} is negative. A ceiling of less than nothing has "
            "no meaning; 0 is the strictest setting and it is allowed."
        )
    return value


PROGRAM_MAX_STEPS = _cap("KASUKABE_PROGRAM_MAX_STEPS", 12)

PROGRAM_MAX_REPEAT = _cap("KASUKABE_PROGRAM_MAX_REPEAT", 20)

PROGRAM_MAX_CALLS = _cap("KASUKABE_PROGRAM_MAX_CALLS", 40)

PROGRAM_WALLET_SHARE_PERCENT = _cap("KASUKABE_PROGRAM_WALLET_SHARE_PERCENT", 50)


PROGRAM_HELP_CONTEXT: dict[str, Any] = {
    "conditions_available": sorted(CONDITION_OPS),
    "max_steps": PROGRAM_MAX_STEPS,
    "max_repeat_per_step": PROGRAM_MAX_REPEAT,
    "max_calls_per_program": PROGRAM_MAX_CALLS,
    "how_to_read_a_result": (
        "bind a step with \"bind\": \"shelf\", then read it later as "
        "\"@shelf\" or \"@shelf.quantity\". A condition can only read what "
        "an earlier step already paid to find out."
    ),
    "how_to_guard_a_step": (
        "\"when\": {\"left\": \"@shelf.quantity\", \"op\": \"<\", \"right\": 1} "
        "runs the step only when the shelf is empty. A false condition "
        "SKIPS the step; it does not fail the program."
    ),
}


class UnknownStepReferenceError(ToolError):
    """A step read `@name` and no earlier step bound that name.

    Separate from `UnknownStepPlaceholderError` (which is about `$name`,
    the call's own parameters) because the two are fixed differently: a
    missing `$` is a caller that did not pass an argument, a missing `@`
    is an author who read a binding that does not exist yet. Telling them
    apart is the difference between "pass it" and "bind it".
    """

    def __init__(self, ref: str, bound: frozenset[str]) -> None:
        self.ref = ref
        self.bound = bound
        known = ", ".join(sorted(bound)) if bound else "nothing yet"
        super().__init__(
            f"{ref!r} reads a step result nothing has bound. Bound so far: "
            f"{known}. Add \"bind\" to an earlier step to name its result."
        )
        self.context = {"bound_so_far": sorted(bound)}


MAX_TOOL_DEPTH = _cap("KASUKABE_MAX_TOOL_DEPTH", 3)


@dataclass
class ProgramBudget:
    """One ceiling for a whole nest of programs.

    Every `run_program` used to read the dispatch ceiling and the wallet
    share for itself. Once an invented tool could be a STEP of another
    invented tool that became a hole: a tool calling a tool calling a tool
    got three times forty dispatches and three times half the wallet, and
    the resident had made one call. The outermost run builds this, every
    nested one inherits it off `world.program_budget`, and the ceilings
    are enforced against the totals here rather than against per-run
    counters.

    `charges_floor` is where this nest's spending starts in
    `world.charges_this_call`, so "half of what you were holding" means
    half of what the whole nest may spend, not half again per level.
    """

    charges_floor: int = 0
    wallet_ceiling: int | None = None
    dispatches: int = 0
    depth: int = 0


STOP_REASONS = {
    "calls": (
        "You stopped after {calls} acts: one program may make at most "
        "{limit}. Everything up to there happened and was paid for."
    ),
    "wallet": (
        "You stopped after {calls} acts: this program had spent {spent} "
        "sim-currency, which is as much of what you were holding as one "
        "program may spend. Everything up to there happened."
    ),
    "depth": (
        "You stopped: this tool stands on tools that stand on tools, more "
        "than {limit} layers deep. Everything up to there happened. Flatten "
        "the deepest ones into primitive steps."
    ),
}


@dataclass
class ProgramRun:
    """What one run of a program did, as the wrapper's result.

    Returned rather than logged, because the resident is the reader that
    matters here: the whole point of a program is that the resident does
    not get a turn in the middle of it, so the account of what happened
    has to arrive whole at the end or not at all.
    """

    program_id: str
    steps_attempted: int = 0
    steps_run: int = 0
    steps_skipped: int = 0
    calls_made: int = 0
    repeats_folded: int = 0
    dispatches: int = 0
    sim_spent: int = 0
    stopped_early: str | None = None
    stopped_at_step: int | None = None
    bindings: dict[str, Any] = field(default_factory=dict)
    answer: dict[str, Any] = field(default_factory=dict)
    last_result: dict[str, Any] = field(default_factory=dict)

    def as_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "program_id": self.program_id,
            "steps_executed": self.steps_run,
            "steps_skipped": self.steps_skipped,
            "calls_made": self.calls_made,
            "sim_spent": self.sim_spent,
            "last_step_result": self.last_result,
        }
        if self.answer:
            result["answer"] = self.answer
        if self.repeats_folded:
            result["repeats_folded"] = self.repeats_folded
        if self.stopped_early:
            result["stopped_early"] = self.stopped_early
            result["stopped_at_step"] = self.stopped_at_step
            result["note"] = STOP_REASONS[self.stopped_early].format(
                calls=self.dispatches or self.calls_made,
                limit=MAX_TOOL_DEPTH if self.stopped_early == "depth" else PROGRAM_MAX_CALLS,
                spent=self.sim_spent,
            )
        return result


def has_control_flow(steps: "list[ToolStep]") -> bool:
    """Is this a program, or is it the fixed list composites always were?

    NOT a switch between two execution paths, and it is worth saying why
    not. Every composite runs through `run_program`, including one filed
    before PROG-02 existed, because a plain composite is simply a program
    whose every step is unconditional, unrepeated and unbound - and
    because the measurement PROG-01 exists for applies to it just as
    much: five hand-written steps bought with one model call is the same
    finding as five programmed ones. Two execution paths would have meant
    the older, commoner shape was the one nothing measured.

    What this is for is DESCRIPTION - telling a reader, a report and
    `describe_primitives` which of the town's invented tools actually
    took up control flow, which is the adoption number the cost-model
    decision turns on.
    """
    return any(
        step.when is not None or step.repeat is not None or step.bind is not None
        for step in steps
    )


def _read_field(value: Any, field_name: str) -> Any:
    """One level into a step result, and one level only.

    A step result is a dict the handler wrote. `@shelf.quantity` reads
    `quantity` out of it. `@shelf.a.b` is not supported and is refused at
    authoring time rather than silently returning None here, because a
    reference that quietly evaluates to nothing is how a condition comes
    to be false for a reason nobody can see.
    """
    if isinstance(value, dict):
        return value.get(field_name)
    return getattr(value, field_name, None)


def resolve_reference(
    value: Any, call_params: dict[str, Any], bindings: dict[str, Any]
) -> Any:
    """`$param`, `@binding`, `@binding.field`, or a literal.

    The one resolver both `params` and `when` go through, so a program
    cannot read one way in a condition and a different way in an
    argument. `$` keeps meaning exactly what it has always meant in a
    composite's steps; `@` is new and reads only bindings an earlier step
    in this same run produced.
    """
    if not isinstance(value, str):
        return value
    if value.startswith("$"):
        placeholder = value[1:]
        if placeholder not in call_params:
            from world.tools.errors import UnknownStepPlaceholderError

            raise UnknownStepPlaceholderError(value, frozenset(call_params.keys()))
        return call_params[placeholder]
    if value.startswith("@"):
        ref = value[1:]
        name, _, field_name = ref.partition(".")
        if name not in bindings:
            raise UnknownStepReferenceError(value, frozenset(bindings.keys()))
        bound = bindings[name]
        return _read_field(bound, field_name) if field_name else bound
    return value


def resolve_params(
    step_params: dict[str, Any],
    call_params: dict[str, Any],
    bindings: dict[str, Any],
) -> dict[str, Any]:
    """Every value in one step's params, resolved.

    Deliberately not recursive. A nested dict passes through whole, the
    way a composite's steps have always passed one through, because a
    placeholder buried three levels inside a JSON object is a program
    nobody reading the filing can follow.
    """
    return {
        key: resolve_reference(value, call_params, bindings)
        for key, value in step_params.items()
    }


def _compare(left: Any, op: str, right: Any) -> bool:
    """The closed set, and nothing outside it.

    Ordered comparisons on values that cannot be ordered return False
    rather than raising. A condition is a question about the world the
    program found, and "is this string less than that number" has an
    answer - no - which is more useful mid-program than an exception that
    unmakes nine acts that already happened.
    """
    if op == "truthy":
        return bool(left)
    if op == "falsy":
        return not bool(left)
    if op == "==":
        return bool(left == right)
    if op == "!=":
        return bool(left != right)
    if op == "in":
        try:
            return left in right  # type: ignore[operator]
        except TypeError:
            return False
    if op == "not_in":
        try:
            return left not in right  # type: ignore[operator]
        except TypeError:
            return False
    if op == "contains":
        try:
            return right in left  # type: ignore[operator]
        except TypeError:
            return False
    if op == "not_contains":
        try:
            return right not in left  # type: ignore[operator]
        except TypeError:
            return False
    try:
        if op == "<":
            return bool(left < right)
        if op == "<=":
            return bool(left <= right)
        if op == ">":
            return bool(left > right)
        if op == ">=":
            return bool(left >= right)
    except TypeError:
        return False
    return False


TAKES = frozenset({"all", "first", "count"})


def returns_problems(
    returns: "dict[str, Any] | None",
    param_schema: "dict[str, Any]",
    steps: "list[ToolStep]",
) -> list[str]:
    """Everything wrong with a `returns` block, at filing time.

    Checked here rather than at run time because the whole point of a
    projection is that the resident reads the answer instead of the log:
    a `returns` that silently resolves to nothing is the quiet failure
    that would teach a resident its tool does not work without ever
    saying so.
    """
    if not returns:
        return []
    problems: list[str] = []
    bound = {step.bind for step in steps if step.bind}

    def check_reference(where: str, value: Any) -> None:
        if not isinstance(value, str) or value[:1] not in ("@", "$"):
            problems.append(
                f"{where} is {value!r}; an answer names what a step found "
                "(\"@name\" or \"@name.field\", after `bind`) or one of this "
                "tool\'s own parameters (\"$name\")"
            )
            return
        if value.startswith("$"):
            if value[1:] not in param_schema:
                problems.append(
                    f"{where} reads {value}, and this tool has no parameter "
                    f"called {value[1:]!r} (it has: {sorted(param_schema) or 'none'})"
                )
            return
        name = value[1:].partition(".")[0]
        if name not in bound:
            problems.append(
                f"{where} reads @{name}, and no step binds a result under "
                f"{name!r}. Add \"bind\": \"{name}\" to the step whose result "
                f"you mean (bound so far: {sorted(bound) or 'nothing'})"
            )

    for key, value in returns.items():
        if isinstance(value, dict):
            unknown = set(value) - {"from", "where", "take"}
            if unknown:
                problems.append(
                    f"returns.{key} has no use for {sorted(unknown)}; a selector "
                    "is {\"from\": ..., \"where\": {...}, \"take\": ...}"
                )
            if "from" not in value:
                problems.append(f"returns.{key} needs a `from` saying what to read")
            else:
                check_reference(f"returns.{key}.from", value["from"])
            clause = value.get("where")
            if clause is not None:
                if not isinstance(clause, dict):
                    problems.append(
                        f"returns.{key}.where is {clause!r}; it is "
                        "{\"field\": optional, \"op\": ..., \"value\": ...}"
                    )
                else:
                    op = clause.get("op")
                    if op not in CONDITION_OPS:
                        problems.append(
                            f"returns.{key}.where.op is {op!r}; the comparisons are "
                            f"{', '.join(sorted(CONDITION_OPS))}"
                        )
            take = value.get("take", "all")
            if take not in TAKES:
                problems.append(
                    f"returns.{key}.take is {take!r}; it is one of "
                    f"{', '.join(sorted(TAKES))}"
                )
        else:
            check_reference(f"returns.{key}", value)
    return problems


def _rows_of(source: Any) -> list[Any]:
    """The entries a selector filters, out of whatever a step returned.

    The town does not answer in tidy flat lists and a projection that only
    understood those would be useless exactly where a resident needs one:
    `look_around`\'s `prices_here` is {shop: [row, row]}, `people_here` is a
    list of dicts, `location_id` is a string. So a dict is read as its
    values, one level of nesting is flattened, and anything else is a list
    of one - which is the reading that makes "the bread rows" and "the
    people here" and "where I am" all expressible the same way.
    """
    if isinstance(source, dict):
        items: list[Any] = list(source.values())
    elif isinstance(source, list):
        items = list(source)
    else:
        return [source]
    rows: list[Any] = []
    for item in items:
        if isinstance(item, list):
            rows.extend(item)
        else:
            rows.append(item)
    return rows


def resolve_answer(
    returns: "dict[str, Any] | None",
    call_params: dict[str, Any],
    bindings: dict[str, Any],
) -> dict[str, Any]:
    """The answer one run hands back, built from what its steps found.

    Nothing here reads the world. Every value comes from a binding a step
    already paid for or from the call\'s own parameters, which is the same
    rule `StepCondition` keeps and for the same reason: a projection that
    could read the town would be a second, unpriced way to look at it.
    """
    if not returns:
        return {}
    answer: dict[str, Any] = {}
    for key, value in returns.items():
        if not isinstance(value, dict):
            answer[key] = resolve_reference(value, call_params, bindings)
            continue
        source = resolve_reference(value["from"], call_params, bindings)
        rows = _rows_of(source)
        clause = value.get("where")
        if isinstance(clause, dict):
            field_name = clause.get("field")
            rows = [
                row for row in rows
                if _compare(
                    _read_field(row, field_name) if field_name else row,
                    str(clause.get("op")),
                    clause.get("value"),
                )
            ]
        take = value.get("take", "all")
        if take == "count":
            answer[key] = len(rows)
        elif take == "first":
            answer[key] = rows[0] if rows else None
        else:
            answer[key] = rows
    return answer


def evaluate_condition(
    condition: StepCondition,
    call_params: dict[str, Any],
    bindings: dict[str, Any],
) -> bool:
    left = resolve_reference(condition.left, call_params, bindings)
    right = resolve_reference(condition.right, call_params, bindings)
    return _compare(left, condition.op, right)


def resolve_repeat(
    repeat: int | str | None, call_params: dict[str, Any], bindings: dict[str, Any]
) -> int:
    """How many times this step runs, after the ceiling.

    Clamped, never refused. A resident that asks for a hundred harvests
    gets twenty and is told so by the wrapper's own result; refusing the
    whole program because one number was ambitious would throw away
    eleven good steps over one bad one.

    A repeat that resolves to something which is not a whole number at
    all - a string from a binding, None, a dict - is one, not zero. One
    is what the step would have done with no `repeat` at all, and that is
    the reading least likely to surprise: a program whose loop count came
    back as nonsense still does the work once, visibly, instead of
    silently doing nothing and looking like a step that was skipped.
    """
    if repeat is None:
        return 1
    value = resolve_reference(repeat, call_params, bindings)
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(str(value))
        except (TypeError, ValueError):
            return 1
    if value < 1:
        return 1
    return min(value, PROGRAM_MAX_REPEAT)


def program_step_problems(
    steps: "list[ToolStep]", param_schema: dict[str, Any]
) -> list[str]:
    """Everything wrong with a program's CONTROL FLOW, as reasons.

    Returns rather than raises, so `meta.py`'s `validate_steps` can fold
    these into the one aggregated `ToolValidationError` every authoring
    verb already produces. An author who gets the whitelist complaint on
    one turn and the binding complaint on the next has paid twice for one
    filing, which is the exact thing that module's aggregate-errors idiom
    exists to stop.

    Authoring time, not run time, and that ordering is the point: an
    invented tool can be taught, licensed and sold, so a program with a
    reference nothing binds must never become somebody else's problem on
    a turn they paid for.

    SCOPE, deliberately narrow. Whether a step's TOOL exists and is a
    whitelisted primitive stays with `validate_steps`, which owns that
    question for every composite and must keep owning it: two validators
    that each half-check a whitelist are how something gets through both.
    `$` references inside step PARAMS also stay there, already checked;
    this adds `$` inside `when` and `repeat`, which that function does not
    look at, and `@` references everywhere, which are new.
    """
    reasons: list[str] = []

    if len(steps) > PROGRAM_MAX_STEPS:
        reasons.append(
            f"{len(steps)} steps, and one program may have at most "
            f"{PROGRAM_MAX_STEPS}. Split it, or file the tail as a second tool."
        )

    bound: set[str] = set()
    known_params = set(param_schema or {})

    def check_reference(ref: Any, where: str) -> None:
        if not isinstance(ref, str):
            return
        if ref.startswith("$"):
            if ref[1:] not in known_params:
                reasons.append(
                    f"{where} reads {ref!r}, and this tool takes no parameter "
                    f"named {ref[1:]!r}. It takes: "
                    f"{', '.join(sorted(known_params)) or 'nothing'}."
                )
            return
        if ref.startswith("@"):
            name, _, field_name = ref[1:].partition(".")
            if "." in field_name:
                reasons.append(
                    f"{where} reads {ref!r}, which goes two levels into a step "
                    "result. One level is the most a program may read: bind the "
                    "inner value with its own step instead."
                )
            if name not in bound:
                reasons.append(
                    f"{where} reads {ref!r} before any step binds {name!r}. "
                    "A step may only read results from steps ABOVE it."
                )

    for index, step in enumerate(steps, start=1):
        where = f"step {index} ({step.tool_name})"
        if step.when is not None:
            check_reference(step.when.left, f"{where} `when.left`")
            check_reference(step.when.right, f"{where} `when.right`")
        for key, value in (step.params or {}).items():
            if isinstance(value, str) and value.startswith("@"):
                check_reference(value, f"{where} param {key!r}")
        if isinstance(step.repeat, str):
            check_reference(step.repeat, f"{where} `repeat`")
        elif isinstance(step.repeat, int) and step.repeat < 1:
            reasons.append(
                f"{where} repeats {step.repeat} times. A step that never runs "
                "is a step nobody meant to file; remove it, or guard it with "
                "`when`, which is how a program says \"only sometimes\"."
            )
        if step.bind is not None:
            if not step.bind.isidentifier():
                reasons.append(
                    f"{where} binds {step.bind!r}, which is not a plain name. "
                    "A binding is read back as \"@name\", so it must be a word."
                )
            elif step.bind in bound:
                reasons.append(
                    f"{where} binds {step.bind!r}, and an earlier step already "
                    "did. Two steps under one name means a later reader cannot "
                    "tell which result it got; give this one its own name."
                )
            else:
                bound.add(step.bind)

    floor = 0
    for step in steps:
        if isinstance(step.repeat, int) and step.repeat > 0:
            floor += min(step.repeat, PROGRAM_MAX_REPEAT)
        else:
            floor += 1
    if floor > PROGRAM_MAX_CALLS:
        reasons.append(
            f"even with nothing skipped this program makes at least {floor} "
            f"calls, and one program may make at most {PROGRAM_MAX_CALLS}. It "
            "would stop partway every time it ran."
        )

    return reasons


async def run_program(
    registry: "ToolRegistry",
    world: "WorldState",
    caller: "AgentLifeRecord",
    tool_name: str,
    steps: "list[ToolStep]",
    call_params: dict[str, Any],
    returns: "dict[str, Any] | None" = None,
) -> tuple[ProgramRun, list[Any]]:
    """Walk the steps. Every act goes through `dispatch` and nothing else.

    Returns the account of the run and the events its steps produced, for
    the caller to fold into its own `ToolOutcome`. Does not publish: the
    registry publishes, after the whole call has proven it succeeded, and
    a program that published as it went would leave events behind for
    steps a later refusal rolled back.

    The wallet is read ONCE, at the start. Reading it per step would let
    a program that earns money mid-run raise its own ceiling, which is
    the loop the ceiling exists to stop: sell something, afford more,
    sell something else, and the "at most half of what you were holding"
    rule means nothing by the twentieth act.
    """
    program_id = f"program-{uuid.uuid4().hex[:12]}"
    run = ProgramRun(program_id=program_id)
    events: list[Any] = []
    bindings: dict[str, Any] = {}

    budget: ProgramBudget | None = getattr(world, "program_budget", None)
    if budget is None:
        wallet_ceiling: int | None
        try:
            opening_sim = int(world.ledger.sim_balance(caller.agent_id))
            wallet_ceiling = (opening_sim * PROGRAM_WALLET_SHARE_PERCENT) // 100
        except Exception:  # noqa: BLE001 - a wallet we cannot read is not a reason to refuse
            wallet_ceiling = None
        budget = ProgramBudget(
            charges_floor=len(world.charges_this_call), wallet_ceiling=wallet_ceiling
        )
        world.program_budget = budget
    charges_before = budget.charges_floor
    wallet_ceiling = budget.wallet_ceiling
    dispatches = 0

    budget.depth += 1
    if budget.depth > MAX_TOOL_DEPTH:
        budget.depth -= 1
        run.stopped_early = "depth"
        run.stopped_at_step = 1
        return run, events

    def spent_so_far() -> int:
        return sum(
            amount
            for step_tool, payer, amount in world.charges_this_call[charges_before:]
            if payer == caller.agent_id
        )

    for index, step in enumerate(steps, start=1):
        run.steps_attempted += 1

        if step.when is not None and not evaluate_condition(step.when, call_params, bindings):
            run.steps_skipped += 1
            continue

        times = resolve_repeat(step.repeat, call_params, bindings)
        for _ in range(times):
            if budget.dispatches >= PROGRAM_MAX_CALLS:
                run.stopped_early = "calls"
                run.stopped_at_step = index
                break
            if wallet_ceiling is not None and spent_so_far() >= wallet_ceiling:
                run.stopped_early = "wallet"
                run.stopped_at_step = index
                break

            resolved = resolve_params(step.params, call_params, bindings)
            world.program_stack.append((program_id, index))
            try:
                outcome = await registry.dispatch(
                    world, caller.agent_id, step.tool_name, resolved
                )
            finally:
                world.program_stack.pop()
                dispatches += 1
                budget.dispatches += 1

            events.extend(outcome.events)
            run.last_result = outcome.result
            folded = bool(
                isinstance(outcome.result, dict)
                and outcome.result.get("repeat_of_this_turn")
            )
            if folded:
                run.repeats_folded += 1
            else:
                run.calls_made += 1
            run.steps_run += 1
            if step.bind:
                bindings[step.bind] = outcome.result

        if run.stopped_early:
            break

    run.sim_spent = spent_so_far()
    run.dispatches = dispatches
    run.bindings = bindings
    run.answer = resolve_answer(returns, call_params, bindings)
    budget.depth -= 1
    return run, events
