"""Typed failures for tool execution.

Every way ToolRegistry.execute() can refuse or fail a call gets its own
class here, mirroring engine/src/errors.py's EngineError catalogue and
charter/errors.py's CharterError catalogue in the same repo: one base
class so a caller (the HTTP layer this module doesn't own yet, tests,
another world/ submodule) can catch every tool-registry failure with a
single `except ToolError`, plus specific subclasses so it can branch on
exactly what went wrong. DESIGN.md section 2 makes the tool registry the
one path through which world state changes; every reason a call can be
refused before or during that path is named here.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from typing import Any

NEAR_MISS_RATIO = 0.6


def nearest_name(wanted: str, available: "Sequence[str]") -> "str | None":
    """The one id in `available` that `wanted` was probably reaching for.

    Doc 01 (`docs/review/01-run-23-evaluation.md`) fix 5, "item names
    should fuzzy-match", is the last open half of run 23's largest refusal
    family. Every refusal in this module already prints the vocabulary;
    what it did not do was point at the near miss, so a resident that
    guessed `b_bakery_shop` had to scan twelve ids and work out which one
    it meant, on a turn it had already paid for.

    SUGGESTS, never resolves. TOOL-01's note in the findings register is
    the reason: "folding at the tool boundary is necessary and not
    sufficient ... the resolved id has to reach every one of them or the
    refusal moves one line down". A near miss that silently became a hit
    would move this failure somewhere harder to see. Naming it is free and
    cannot be wrong; acting on it stays the resident's call.

    Returns None rather than a bad guess. Silence beats a wrong steer.
    """
    if not wanted or not available:
        return None
    pool = [name for name in available if name and name != wanted]
    if not pool:
        return None
    matches = difflib.get_close_matches(wanted, pool, n=1, cutoff=NEAR_MISS_RATIO)
    return matches[0] if matches else None


class ToolError(Exception):
    """Base class for every tool-registry failure.

    `context` is anything the CALLER needs in order to get it right next
    time, carried alongside the message rather than crammed into it. The
    two places a ToolError becomes a wire response (admin_api's exception
    handler and run_genesis's /agents/{id}/tools route) include it when it
    is there, so it reaches the model as data rather than as prose it has
    to parse.

    The case it was added for: `skillify` refused six steps with "no tool
    named 'world_move'" and nothing else, so the author's second attempt
    was as blind as its first and cost another turn. A refusal that
    teaches is worth more than a refusal that is merely correct.

    Empty for almost every error, and that is right - a location gate has
    nothing to teach beyond where you are and where you must be, and both
    are already in the message.
    """

    context: "dict[str, Any] | None" = None


class ToolNotFoundError(ToolError):
    """No tool by this name, OR the tool exists but is invisible to this
    actor (a confidential government/business/organization tool the actor
    has no access to). DESIGN.md section 2: "Non-members cannot see a
    confidential tool exists" - deliberately the same error as a genuinely
    unknown name, never a distinguishable "forbidden," so a probing agent
    cannot use the error message to enumerate hidden tools.
    """

    def __init__(self, name: str, near: str | None = None) -> None:
        """`near` is a verb the CALLER CAN ALREADY SEE, or None.

        The confidentiality rule above is about what a non-member may
        learn, and a near miss drawn from the caller's own visible set
        teaches it nothing new - it already holds that access. What it
        does fix is the case the proof harness called indistinguishable:
        a seat-holder who types `set_tax_rates` gets the same sentence as
        somebody with no seat at all, and cannot tell a typo from a wall.

        Never pass a name the caller cannot see. That would turn this
        error into the tool-enumeration oracle the docstring forbids.
        """
        self.name = name
        self.near = near
        message = f"no tool registered under name {name!r}"
        if near:
            message += f" (did you mean {near!r}?)"
        super().__init__(message)


class ActorNotFoundError(ToolError):
    def __init__(self, actor_id: str) -> None:
        self.actor_id = actor_id
        super().__init__(f"no agent with id {actor_id!r}")


class ActorRequirementError(ToolError):
    """One or more actor requirements (age, license, role, alive,
    not-dormant) failed. Aggregates every violated requirement into one
    error, matching engine/src/config.py's ConfigError / charter's
    CharterValidationError pattern in this repo: a caller retrying blind
    should see everything wrong at once, not one rejection at a time.
    """

    def __init__(self, tool_name: str, reasons: list[str]) -> None:
        self.tool_name = tool_name
        self.reasons = list(reasons)
        joined = "; ".join(self.reasons)
        super().__init__(f"{tool_name}: actor requirement(s) not met: {joined}")


class LocationGateError(ToolError):
    """The wrong place - and, where the world can say so, the right one.

    `cook: requires location 'kitchen', actor is at 'pharmacy'` names a
    workshop and not a door. A kitchen is not somewhere a resident can
    `move` to by that name: it is a room a BUSINESS provides (TOOL-02), and
    which business provides it differs per town. So the refusal named a
    place that could not be walked to, and election30's residents tried to
    cook at the pharmacy and at the construction yard and baked 2 bread in
    two days while holding 78 flour.

    `where` is the premises that actually satisfy this gate, which only the
    world knows. Same rule as the recipe list and the storage list: a
    refusal teaches or it is noise.
    """

    def __init__(
        self, tool_name: str, required: Any, actual: Any, where: Sequence[str] = ()
    ) -> None:
        self.tool_name = tool_name
        self.required = required
        self.actual = actual
        self.where = list(where)
        message = (
            f"{tool_name}: requires location {required!r}, actor is at {actual!r}"
        )
        if self.where:
            message += " - go to " + " or ".join(self.where)
        super().__init__(message)


class ToolValidationError(ToolError):
    """params dict failed to validate against the tool's pydantic params model."""

    def __init__(
        self,
        tool_name: str,
        errors: list[str],
        context: "dict[str, Any] | None" = None,
    ) -> None:
        self.tool_name = tool_name
        self.errors = list(errors)
        self.context = context
        joined = "; ".join(self.errors)
        super().__init__(f"{tool_name}: invalid params: {joined}")


class NotFoundError(ToolError):
    """Generic entity-not-found for tools-owned state (a business, a
    resource zone, a storage, a plot, a recipe, a prayer request, a crime
    case). Named subclasses exist only where a caller needs to branch on
    the specific kind; most handlers raise this directly with `kind`.
    """

    def __init__(
        self,
        kind: str,
        entity_id: str,
        available: Sequence[str] = (),
        *,
        hint: str | None = None,
    ) -> None:
        """`hint`, when given, REPLACES the generated sentence.

        The tool-text addendum's rule 3: "refusals teach or they are
        noise", and it writes the exact wording for several of them. Run
        19's `sell` said "at its shop" and nothing about people, and the
        town spent a whole morning trying to register businesses. A
        handler that knows the sentence that would have helped should be
        able to say it, rather than have this class paraphrase it.
        """
        self.kind = kind
        self.entity_id = entity_id
        self.available = list(available)
        self.hint = hint
        if hint:
            self.available = list(available)
            if self.available:
                hint = f"{hint} Priced here: " + ", ".join(sorted(self.available)[:12]) + "."
            super().__init__(hint)
            return
        message = f"no {kind} with id {entity_id!r}"
        if self.available:
            shown = sorted(self.available)[:12]
            plural = f"{kind}es" if kind.endswith(("s", "x", "ch", "sh")) else f"{kind}s"
            message += f" - the {plural} here are: " + ", ".join(shown)
            near = nearest_name(entity_id, self.available)
            if near:
                message += f" (did you mean {near!r}?)"
        super().__init__(message)


class InsufficientInventoryError(ToolError):
    def __init__(self, owner_id: str, item: str, needed: int, available: int) -> None:
        self.owner_id = owner_id
        self.item = item
        self.needed = needed
        self.available = available
        super().__init__(
            f"{owner_id} needs {needed} {item}, has {available}"
        )


class RecipeNotFoundError(ToolError):
    """A recipe id this tool does not have - and the ones it does.

    "no recipe 'potato_soup'" is a dead end: it tells an agent that the id
    it guessed is wrong and gives it nothing better to guess next, so the
    same tool gets called again with another invention. Measured in one
    19-tick run: wood_chair, timber_beam, processed_timber, potato_soup and
    potato, five turns spent guessing at a list nobody had shown them.

    Listing what the tool DOES make turns the refusal into the answer, the
    same way `say` reports who was not there to hear it rather than only
    that nobody replied.
    """

    def __init__(self, recipe_id: str, available: Sequence[str] = ()) -> None:
        self.recipe_id = recipe_id
        self.available = list(available)
        message = f"no recipe {recipe_id!r}"
        if self.available:
            message += " - this tool makes: " + ", ".join(sorted(self.available))
            near = nearest_name(recipe_id, self.available)
            if near:
                message += f" (did you mean {near!r}?)"
        super().__init__(message)


class AlreadyExistsError(ToolError):
    def __init__(self, kind: str, entity_id: str) -> None:
        self.kind = kind
        self.entity_id = entity_id
        super().__init__(f"{kind} {entity_id!r} already exists")


class PrimitiveNotWhitelistedError(ToolError):
    """define_tool composed a step out of something other than a
    currently-registered primitive (DESIGN.md section 2: "compositions of
    existing primitives ONLY"). Carries the offending step's tool name so
    the inventor sees exactly which step is the problem.
    """

    def __init__(self, tool_name: str, citable: "frozenset[str] | None" = None) -> None:
        self.tool_name = tool_name
        self.citable = citable or frozenset()
        also = ""
        if self.citable:
            named = ", ".join(sorted(self.citable)[:6])
            also = f" You may also build on tools you can already call: {named}."
        super().__init__(
            f"{tool_name!r} is neither a primitive this town has nor a tool "
            "you can call. A step must be an action that already exists"
            f"{also} (DESIGN.md section 2: 'no new physics possible by "
            "construction' - every step, however deep, ends in a primitive)"
        )


class CompositionTooDeepError(ToolError):
    """A tool may stand on a tool, not on an arbitrarily tall tower.

    The ceiling is what keeps the promise cheap to check: flattening a
    composition to its primitives is what proves it adds no physics, and
    an unbounded tower is an unbounded validation and an unbounded bill.
    """

    def __init__(self, tool_name: str, path: "list[str]", max_depth: int) -> None:
        self.tool_name = tool_name
        self.path = path
        self.max_depth = max_depth
        super().__init__(
            f"{tool_name!r} would stand {len(path)} tools deep ("
            + " -> ".join(path)
            + f"); a tool may be built on at most {max_depth} layers of "
            "tools. Flatten the deepest ones into primitive steps."
        )


class CompositionCycleError(ToolError):
    """Two tools that stand on each other would never reach a primitive."""

    def __init__(self, tool_name: str, path: "list[str]") -> None:
        self.tool_name = tool_name
        self.path = path
        super().__init__(
            f"{tool_name!r} would end up calling itself ("
            + " -> ".join([*path, tool_name])
            + "); a composition has to end in primitives, so it cannot "
            "come back round to where it started."
        )


class UnknownStepPlaceholderError(ToolError):
    def __init__(self, placeholder: str, known: frozenset[str]) -> None:
        self.placeholder = placeholder
        self.known = known
        super().__init__(
            f"step parameter placeholder {placeholder!r} does not name a "
            f"declared param_schema field (known: {sorted(known)})"
        )


class PatentNotApprovedError(ToolError):
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"invented tool {tool_name!r} exists but its patent is not approved")
