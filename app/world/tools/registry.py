"""ToolRegistry: DESIGN.md section 2's "law of physics."

"World state mutates ONLY through tool calls." Every primitive in
primitives/ and the meta-tool in meta.py is registered here as a
`ToolSpec`: a name, a pydantic params schema, a location gate, actor
requirements, and an `execute()` coroutine that mutates `WorldState` and
returns the `WorldEvent`s it caused. `ToolRegistry.execute()` is the one
and only call path DESIGN.md's sentence describes - nothing in this
package mutates `WorldState` any other way, and app/CONVENTIONS.md's
module boundary ("world/ owns ALL world state ... through world's async
API") is this method.

Dispatch order, every call, no exceptions: actor exists -> tool exists
and is visible to this actor (namespace ACL) -> actor requirements (age,
license, role, alive, not dormant) -> params validate against the tool's
pydantic model -> location gate -> handler runs -> its events publish.
Every one of THESE stages raises a specific `ToolError` subclass
(errors.py), so a caller can tell a gate refusal from a deeper failure
just by the exception type, and no stage after a failed one ever runs (a
bad-params call never gets charged, an out-of-position call never even
validates params it didn't need to). A handler itself may also raise
whatever its sibling systems raise once it starts running (a
`world.ledger.errors.InsufficientFundsError`, a
`world.charter.errors.CharterError`, a
`world.life.household.MarriageIneligibleError`) - those are not
`ToolError` subclasses and are not meant to be; they are already
well-typed failures from the module that actually detected them, and
wrapping them again here would only hide which system said no.

Atomicity: see state.py's module docstring for the split between
sibling-system state (ledger/charter, which guarantee their own
operations are all-or-nothing) and tools-owned state (this module snapshots
and rolls back). The discipline every handler in primitives/ follows to
make that split safe: validate everything that can be checked up front,
call any fallible external system (ledger, charter) before mutating
anything in tools-owned state that assumes that call succeeded, and only
do tools-owned mutations that cannot themselves fail (dict/inventory
bookkeeping) after that point. A handler that raises partway through
therefore never leaves tools-owned state assuming an external side effect
happened when it did not, and this module's snapshot/restore never needs
to know anything about ledger or charter internals to stay correct.
"""

from __future__ import annotations

import os

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from world.ledger import action_costs
from world.charter.errors import (
    CharterError,
    NotAtRequiredLocation,
    NotEligibleToVote,
    NotSittingGovernment,
)
from world.charter.models import LicenseStatus
from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord, DormancyState
from world.ledger.action_costs import ActionUnaffordableError
from world.tools import wear
from world.tools.errors import (
    ActorNotFoundError,
    ActorRequirementError,
    LocationGateError,
    ToolNotFoundError,
    nearest_name,
    ToolValidationError,
)
from world.tools.models import ToolInvocationRecord, ToolRefusalRecord
from world.tools.place_aliases import (
    display_place,
    places_that_satisfy,
    satisfies_gate,
    satisfies_gate_in,
)
from world.tools.state import (
    MAX_TOOL_INVOCATIONS_KEPT,
    MAX_TOOL_REFUSALS_KEPT,
    WorldState,
)


class ToolNamespace(str, Enum):
    """DESIGN.md section 2's four tool namespaces."""

    PUBLIC = "public"
    GOVERNMENT = "government"
    BUSINESS = "business"
    ORGANIZATION = "organization"


@dataclass(frozen=True)
class ActorRequirements:
    """Every gate ToolRegistry checks about the calling agent before a
    handler ever runs. All-optional and additive - a tool with no
    requirements (the default) is callable by any living, non-dormant
    agent, which is the correct default for a primitive like `move`.
    """

    min_age: float | None = None
    max_age: float | None = None
    require_alive: bool = True
    require_not_dormant: bool = True
    require_government_seat: bool = False
    required_license_types: frozenset[str] = frozenset()
    # Part D. Set on `move` and nothing else: an arrest that leaves the
    # accused free to walk out is a word, not an arrest. Everything else a
    # confined resident can still do - above all `say`, because pleading,
    # arguing and being argued with is the whole of what a cell is for.
    require_not_confined: bool = False


# A location gate is either a fixed location_id, `None` (no gate - callable
# from anywhere), or a resolver that computes the required location_id
# from the tool's own validated params (e.g. craft's required arena
# depends on which recipe was requested; buy's depends on which business).
LocationResolver = Callable[[WorldState, BaseModel], "str | None"]
LocationGate = str | LocationResolver | None


@dataclass
class ToolOutcome:
    """What a handler hands back to the registry: the JSON-able result a
    caller sees, and every WorldEvent the mutation caused. A handler
    never publishes events itself - returning them here is what lets
    ToolRegistry.execute() commit tools-owned state and publish in the
    same place, after the handler has already proven it succeeded.
    """

    result: dict[str, Any]
    events: list[WorldEvent] = field(default_factory=list)


ToolHandler = Callable[[WorldState, AgentLifeRecord, BaseModel], Awaitable[ToolOutcome]]


@dataclass(frozen=True)
class ToolSpec:
    """One registered tool: DESIGN.md section 2's "name, params schema,
    location gate, actor requirements, execute()", plus the namespace/ACL
    metadata section 2's confidential-tool rules need.
    """

    name: str
    description: str
    params_model: type[BaseModel]
    handler: ToolHandler
    namespace: ToolNamespace = ToolNamespace.PUBLIC
    location_gate: LocationGate = None
    actor_requirements: ActorRequirements = field(default_factory=ActorRequirements)
    # For BUSINESS tools: the business_id whose owner/employees may call
    # it. For ORGANIZATION tools: the org_id whose members may call it.
    # Unused (None) for PUBLIC/GOVERNMENT.
    owner_id: str | None = None
    # True for the ~20 built-in tools bootstrap.py registers before
    # define_tool exists; False for anything define_tool later registers.
    # This is exactly the whitelist meta.py's define_tool validates
    # composition steps against (DESIGN.md: "compositions of existing
    # primitives ONLY") - see ToolRegistry.primitive_names.
    is_primitive: bool = True


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    actor_id: str
    result: dict[str, Any]
    events: list[WorldEvent]


# CORE-22. One JSON schema per params model, for the life of the process.
#
# `tool_registry_rows` called `spec.params_model.model_json_schema()` for
# every tool on every request. With KASUKABE_LOCAL_TOOL_SCHEMAS on (the
# default) each engine re-GETs the registry every tick with `force=True`,
# so at 250 residents on a 0.3 cadence that is ~83 requests a tick, each
# rebuilding ~82 pydantic schemas: about 6,800 schema builds per tick, on
# the world event loop, to produce bytes that are identical every time.
#
# A params model is a class defined at import; its JSON schema cannot
# change while the process runs. Keyed on the class itself so an invented
# tool (`toolify` builds a fresh model per definition) gets its own entry
# and is not confused with another tool of the same name.
_SCHEMA_CACHE: "dict[type, dict[str, Any]]" = {}


def _without_class_docstrings(schema: "dict[str, Any]") -> "dict[str, Any]":
    """The schema a model is served, minus the prose written for engineers.

    Kimi K2.5 on Bedrock has no prompt caching, so every token of the tool
    payload is paid for on every call - and run 28's input was 95% of its
    bill. `model_json_schema()` copies each pydantic model's DOCSTRING into
    its schema as `description`: for a params model at the root, and for
    every nested model in `$defs`. `ToolStep` and `StepCondition` carry the
    PROG-02 rationale that way, so `toolify` and `do_in_order` were served
    at 5,172 and 4,412 characters.

    A field's own `description=` stays: that is what a resident needs to
    form the call. Only the model-level docstrings go.
    """
    schema.pop("description", None)
    for definition in (schema.get("$defs") or {}).values():
        if isinstance(definition, dict):
            definition.pop("description", None)
    return schema


def json_schema_for(params_model: type) -> "dict[str, Any]":
    """The cached JSON schema for one params model (CORE-22)."""
    cached = _SCHEMA_CACHE.get(params_model)
    if cached is None:
        cached = _without_class_docstrings(params_model.model_json_schema())
        _SCHEMA_CACHE[params_model] = cached
    # Copied on the way out: callers hand these dicts to FastAPI and to
    # the engine, and one mutation of a shared dict would poison every
    # later reader. The copy is shallow because a JSON schema is a tree of
    # plain data that nothing here edits in place.
    return dict(cached)


# Tools a resident is OFFERED only while it is in a conversation. The
# operator's rule, 2026-09-21: "the buy tool will only be enabled after the
# participants of the buy process exist in a conversation - single or group
# conversations are equally applicable."
CONVERSATION_ENABLED_TOOLS = frozenset({"buy"})


def _offered_now(spec: "ToolSpec", world: WorldState, agent_id: str) -> bool:
    """Whether this resident's list should carry the tool this turn.

    Deliberately NOT part of `visible_to`. That predicate also gates
    dispatch, and a tool invisible there is refused as "no such tool" - so a
    `buy` that reached the world anyway (a saved program's step, an engine
    still holding an older list) would lose the precise reason a resident
    can act on. Here it is only left out of the list; a call that still
    arrives meets `commerce._require_dealing`, which says why.
    """
    if spec.name not in CONVERSATION_ENABLED_TOOLS:
        return True
    from world.tools.primitives import commerce

    if not commerce.SALES_NEED_CONVERSATION:
        return True
    return bool(world.conversation_partners(agent_id))


# How a place that depends on the call is written as data. A tool whose
# location gate is a FUNCTION (the place is wherever the thing you name is)
# cannot be put into JSON as-is, and a refusal carrying one crashed the route
# into "Internal Server Error" (50-resident run, `do_in_order`).
WHEREVER_IT_IS = "wherever the thing you name is"


def location_label(gate: Any) -> "str | None":
    """A location gate as plain data: the place id, words, or None."""
    if gate is None or isinstance(gate, str):
        return gate
    return WHEREVER_IT_IS


def tool_registry_rows(
    registry: "ToolRegistry",
    world: WorldState | None = None,
    for_agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """The GET /tools/registry payload every agent boots from.

    With `world` and `for_agent_id`, tools that cannot be called from
    where that resident is standing come back as a one-line index entry
    instead of a full schema - tool spec B3's companion setting. Without
    them the payload is exactly what it always was, so every other caller
    (the standalone server's boot, the tests, the observatory) is
    unchanged.

    ONE builder, deliberately, because there are TWO hosts that serve this
    route - world/main.py (the standalone server) and the embedded app in
    world/run_genesis.py - and they had already drifted: the embedded one
    served it and main.py did not, so a real engine pointed at the
    standalone server died at startup on "405 Method Not Allowed" from
    world_tools.fetch_tool_registry before it could form a single thought.
    That is the same class of break world/tests/test_track_wiring.py was
    written for: two hosts, one contract, no test holding them together.

    The shape is the engine's, not ours: engine/src/sim/world_tools.py's
    fetch_tool_registry reads row["name"] (required) and .get()s
    "description" and "input_schema". Renaming a key here silently gives
    every agent an empty tool list rather than an error.

    TOOL-07. Named callers get the namespace ACL applied here, not only
    at call time. DESIGN.md section 2 says "non-members cannot see a
    confidential tool exists", and enforcement used to begin at
    `execute()` - so the boot payload listed all fifteen GOVERNMENT
    tools, every business's trade secret and every neighbour's `toolify`
    invention, by name, description and schema, to all 20 residents, and
    charged the town for the tokens. `describe_primitives` and
    `authoring_context` already filtered, so the town's own vocabulary
    listing disagreed with its schema listing about which verbs exist.
    Both listings now ask `ToolRegistry.visible_to`, the same predicate
    `execute()` refuses on.

    The ANONYMOUS payload is deliberately unfiltered: with no actor there
    is no membership to test, and its callers are the standalone server's
    boot, the observatory and the tests, none of which is a resident. An
    engine always names itself (world_tools.py sends `agent_id`).
    """
    if for_agent_id is None or world is None:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": json_schema_for(spec.params_model),
            }
            for spec in registry.tools.values()
        ]

    here = world.location_of(for_agent_id)

    # ENG-03. A budget, not just a gate.
    #
    # "Usable here" alone reduced almost nothing: most tools carry no fixed
    # location gate, so they are usable everywhere and every one of the 82
    # came back with a full schema - measured, 59,310 chars against 50,389,
    # a 16% saving and ZERO index lines. Meanwhile the schemas are two
    # thirds of every turn's input bill (run 23: 15-20K input per turn, of
    # which ~14K is this payload).
    #
    # So the rule is now "the tools that matter HERE, up to a budget", in
    # this order:
    #
    #   1. tools gated to this exact place - the reason to be standing here;
    #   2. the verbs a resident always needs (ALWAYS_IN_FULL below);
    #   3. whatever else fits, stably ordered so the payload is identical
    #      for two residents in the same place - which keeps the prompt
    #      prefix cacheable.
    #
    # Everything else is a one-line index entry, and `describe_primitives`
    # already exists so a resident can read a verb in full before using it.
    #
    # TOOL-07: the ACL runs FIRST, so a confidential tool is not merely
    # reduced to an index line - it is absent. An index row still carries
    # the name and the place, which is the whole of the existence the
    # DESIGN rule protects; and filtering before the ranking also spends
    # the budget on verbs this resident can actually call.
    specs = [
        spec
        for spec in registry.tools.values()
        if registry.visible_to(spec, world, for_agent_id)
        and _offered_now(spec, world, for_agent_id)
    ]
    ranked = sorted(
        specs,
        key=lambda spec: (
            0 if _gated_to_here(spec, world, here) else
            1 if spec.name in ALWAYS_IN_FULL else
            2 if spec.name in _COMMON_RANK else 3,
            _COMMON_RANK.get(spec.name, 0),
            spec.name,
        ),
    )
    in_full = {spec.name for spec in ranked[:FULL_SCHEMA_BUDGET]}
    # FULL_ONCE_AUTHORING: outside the budget either way - in full for an
    # author, one line for everybody else, whatever else is in the room.
    authored = has_authored(world, for_agent_id)
    for name in FULL_ONCE_AUTHORING:
        if authored:
            in_full.add(name)
        else:
            in_full.discard(name)

    rows: list[dict[str, Any]] = []
    for spec in specs:
        if spec.name in in_full and _usable_here(spec, world, here):
            rows.append({
                "name": spec.name,
                "description": spec.description,
                "input_schema": json_schema_for(spec.params_model),
            })
        else:
            rows.append(
                _index_row(spec, world, usable_here=_usable_here(spec, world, here))
            )
    return rows


# How many tools come back with a full schema. The review's target is
# "<= ~20 full schemas per (location, role, seat)" and <= 4K tokens of
# tools; 24 leaves room for the always-on verbs plus a well-equipped
# location without reaching the old bill.
FULL_SCHEMA_BUDGET = int(os.environ.get("KASUKABE_FULL_SCHEMA_BUDGET", "24"))

# The verbs a resident needs wherever it is standing. Reducing any of these
# to an index line costs a turn: the resident has to spend a call on
# `describe_primitives` before it can do the obvious thing.
# TRADE-04: three of these named no tool at all - `check_wallet`,
# `check_inventory` and `sleep`. They cost nothing (the ranking only ever
# sees registered specs, so a name with no spec simply never appears),
# but they told every reader that a resident can ask for its wallet and
# go to sleep on demand, and it cannot: money and holdings are in the
# perception every turn, and sleeping is the clock's business, not a
# verb's. A list of verbs that includes verbs the town does not have is
# how the next person writes a prompt promising them.
# `do_in_order` for the reason `toolify` is here, and more so: a program is
# nothing BUT its step shape. Served as an index line it read "Run a short
# program now, in one call ... Needs: steps." with an 85-byte schema and no
# `when`, `repeat` or `bind` - the resident could see programs existed and
# not how to write one (test_one_call_runs_a_program_now.py).
ALWAYS_IN_FULL = frozenset({
    "look_around", "move", "say", "start_conversation",
    "eat", "rest",
    "check_debts",
    "keep_doing", "set_intention", "set_goal",
    "describe_primitives", "skillify", "do_in_order",
})

# X8, operator decision 2026-09-21: `toolify` is a ONE-LINE entry until the
# resident has authored something.
#
# It was in ALWAYS_IN_FULL for the reason `do_in_order` still is: a program
# is nothing but its step shape. Measured against the 50-resident run, that
# cost about 911 input tokens on every resident call - a third of what the
# never-called verbs cost to carry - for a verb with 0 calls in every run
# since 25. Cutting it would cut the town's only route to a tool that
# outlives the turn (T3), so it stays, shrunk:
#
#   * the line keeps what a resident needs to decide - that it makes a
#     reusable tool, and that it is free (the price TOOL-06 made honest);
#   * the step shape is `do_in_order`'s exactly, and that is served in full,
#     so the line says so instead of repeating 2,000 characters;
#   * `describe_primitives` gives the whole description on request;
#   * a resident who has already authored a skill or a tool - who has shown
#     it writes procedures - gets `toolify` in full again, from its next turn.
FULL_ONCE_AUTHORING = frozenset({"toolify"})

INDEX_NOTES: dict[str, str] = {
    "toolify": (
        " Free to compose, and the wrapper itself is free: a tool you make costs "
        "exactly the sum of its steps. "
        "Steps are written as `do_in_order`'s are; `describe_primitives` gives "
        "the whole of it."
    ),
}


def has_authored(world: WorldState, agent_id: str) -> bool:
    """Has this resident filed a skill or a tool of its own?"""
    return any(
        tool.creator_agent_id == agent_id for tool in world.custom_tools.values()
    ) or any(
        skill.author_agent_id == agent_id for skill in world.skill_definitions.values()
    )

# What the rest of the full-schema budget is spent on, in order, before the
# alphabet decides. Runs 25-28b called these; the alphabet had been spending
# the budget on `arrest`, `admit_to_org`, `ask_for_a_child`, `auction_land`,
# `build_structure` and `come_of_age` while `trade`, `transfer` and `give`
# reached a farmhand as one line (run 28's complete trace analysis). Ordered
# by how often a failed or missing call of each held a transaction up.
COMMON_VERBS: tuple[str, ...] = (
    "trade", "buy", "sell", "post_price", "transfer", "give",
    "stock_shelf", "take_from_shelf", "draw_from_till", "deposit_to_till",
    "craft", "cook", "harvest", "fell_timber", "mine",
    "find_people", "describe_place", "describe_person", "list_places",
    "drop_intention", "recall_goals", "retire_goal",
    "pay_wage", "hire_employee", "set_wage", "pay_tax",
)
_COMMON_RANK = {name: rank for rank, name in enumerate(COMMON_VERBS)}


def _gated_to_here(spec: ToolSpec, world: WorldState, here: str | None) -> bool:
    """True when this tool's gate names the place the resident is standing.

    Distinct from `_usable_here`, which is also true for every ungated
    tool. This one answers "is this verb the REASON to be here", which is
    what earns a full schema when the budget is tight.
    """
    gate = spec.location_gate
    if gate is None or not isinstance(gate, str):
        return False
    return satisfies_gate(here, gate)


def _usable_here(spec: ToolSpec, world: WorldState, here: str | None) -> bool:
    """Could this tool be called from where the agent is standing?

    Only a FIXED string gate answers this without params. A resolver gate
    (`buy`'s premises, `craft`'s workshop) depends on which business or
    recipe was named, so it cannot be decided in advance and counts as
    usable here - which is the safe direction. Usable here does not mean
    served in full: the budget still decides that, and since TRADE-02 a
    reduced row for a tool usable here names its required parameters
    (`_index_row`), so the resident can still form the call.
    """
    gate = spec.location_gate
    if gate is None or not isinstance(gate, str):
        return True
    return satisfies_gate(here, gate)


def _index_row(
    spec: ToolSpec, world: WorldState, *, usable_here: bool = False
) -> dict[str, Any]:
    """One line for a tool the budget could not afford in full.

    B3's companion setting: "only the schemas of tools usable at the
    resident's current location sent in full, others as a one-line index".
    A fresh context is about 8,000 tokens before a word of perception, and
    most of that is schemas for verbs the resident cannot use where it is
    standing.

    The NAME survives, always. A resident that cannot see a tool exists
    cannot walk to where it works, and `move` plus a name is the whole
    recovery path. What is dropped is the parameter schema and the body of
    the description - about 90% of the bytes and none of the discovery.
    """
    # TOOL-24: the name the MAP uses, not the gate's own constant. A
    # resident told "only at government_complex" cannot find that string
    # in `list_places`, which says "Kasukabe Government Complex" - and a
    # name plus `move` is the entire recovery path an index row offers.
    where = (
        display_place(spec.location_gate)
        if isinstance(spec.location_gate, str)
        else "elsewhere"
    )
    first_sentence = spec.description.split(".", 1)[0].strip()

    # TRADE-02. An index row for a tool the resident CAN call from here
    # used to declare `properties: {}` - that is, "this tool takes no
    # parameters" - which is false, and the model believed it.
    #
    # Run `deni-a6f9eb4-5h`, every one of these from a different
    # resident: `take_from_shelf({})` five times, `harvest({})` twenty
    # of thirty-eight, `mine({})` nine of twenty-one,
    # `describe_place({})` twenty-three of thirty-two, plus
    # `post_price({})`, `stock_shelf({})`, `give({})`,
    # `draw_from_till({})`. The model was not being lazy. It was told
    # the tool took nothing, called it correctly against that schema,
    # got "Field required", and then guessed names out of English -
    # which is where `business_id`, `to` and `price_sim` come from.
    #
    # 47 of that run's 57 index rows were tools usable exactly where the
    # resident stood. They were reduced for BUDGET, not for distance.
    #
    # So a row for a tool that can be called here carries its REQUIRED
    # parameter names, types only, no descriptions - about 280 tokens a
    # turn across the whole payload, against a budget ENG-03 cut from
    # 14K to 7.6K. A tool gated somewhere else keeps the empty schema:
    # the resident cannot call it from here whatever it passes, and
    # `move` plus a name is the whole recovery path.
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    needs = ""
    if usable_here:
        full = json_schema_for(spec.params_model)
        required = list(full.get("required") or ())
        properties = full.get("properties") or {}
        if required:
            schema = {
                "type": "object",
                "properties": {
                    name: {"type": properties.get(name, {}).get("type", "string")}
                    for name in required
                },
                "required": required,
            }
            needs = f" Needs: {', '.join(required)}."

    reach = (
        ""
        if usable_here
        else f" Only at {where}; `move` takes you there."
    )
    return {
        "name": spec.name,
        # The `Needs:` clause is not redundant with the schema below it:
        # a model builds arguments from the schema, but `describe_
        # primitives` renders this DESCRIPTION, and that listing is where
        # a resident goes when a call has just been refused. No
        # "read it in full" suffix - 42 rows x 40 chars for a sentence
        # every resident already knows is 1,700 characters a turn.
        "description": f"{first_sentence}.{INDEX_NOTES.get(spec.name, '')}{needs}{reach}",
        "input_schema": schema,
        # So a reader of the payload can tell a reduced row from a real
        # one without diffing it against the full registry.
        "elsewhere": True,
    }


class ToolRegistry:
    """The dispatcher. One instance per running world server; bootstrap.py
    builds the default one with every primitive plus the meta-tool
    registered.
    """

    def __init__(self) -> None:
        self.tools: dict[str, ToolSpec] = {}
        # Frozen once, right after every built-in primitive is registered
        # (bootstrap.py) and never touched again - see ToolSpec.is_primitive.
        self.primitive_names: frozenset[str] = frozenset()
        self.lock = asyncio.Lock()
        # Part B: bumped on every registration, so an engine can tell whether
        # its cached tool list is stale without refetching the whole thing.
        # Kept HERE rather than on WorldState because register() is the single
        # choke point every invented tool passes through - define_tool,
        # define_business_tool and define_organization_tool all call it - and
        # a counter that cannot be reached any other way cannot be forgotten.
        self.version: int = 0

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self.tools:
            raise ValueError(f"a tool named {spec.name!r} is already registered")
        self.tools[spec.name] = spec
        self.version += 1

    def freeze_primitives(self) -> None:
        """Call once, after every built-in primitive is registered and
        before define_tool is registered. Fixes the whitelist define_tool
        validates compositions against - see meta.py.
        """
        self.primitive_names = frozenset(
            name for name, spec in self.tools.items() if spec.is_primitive
        )

    def get(self, name: str) -> ToolSpec | None:
        return self.tools.get(name)

    async def is_visible(self, spec: ToolSpec, world: WorldState, actor_id: str) -> bool:
        """DESIGN.md section 2: "Non-members cannot see a confidential
        tool exists." The async face of `visible_to`, kept because every
        caller inside a handler is already a coroutine and reads better
        awaiting the check than reaching for its synchronous twin.

        One rule, one body: TOOL-07 was two listings of the town's
        vocabulary disagreeing about which tools exist, and a second
        implementation of this predicate is how that happens again.
        """
        return self.visible_to(spec, world, actor_id)

    def visible_to(self, spec: ToolSpec, world: WorldState, actor_id: str) -> bool:
        """May `actor_id` know this tool exists?

        PUBLIC is visible to everyone; the other three namespaces are
        visible only to whoever currently holds the matching access
        (sitting government, a business's owner/staff, an organization's
        roster) - access that can change out from under a tool (an
        election, a firing, being expelled), which is exactly why this is
        a live check every call rather than a grant computed once at
        registration.

        Synchronous on purpose. `tool_registry_rows` is the boot payload
        both hosts serve from inside an already-running `async def`
        route, and neither host is ours to re-plumb into an await - so a
        predicate that could only be awaited would have left the payload
        unfiltered, which is TOOL-07 itself. Reading the charter store's
        collections directly rather than through its lock is safe for the
        same reason it is cheap: this body contains no `await`, so no
        other coroutine on the loop can interleave a mutation into the
        middle of it, and every charter mutation is a whole-object
        rebind under that lock.
        """
        if spec.namespace is ToolNamespace.PUBLIC:
            # C1: an INVENTED public tool is still somebody's. Its owner,
            # anyone taught their own copy, and anyone holding a current
            # licence may call it; nobody else may.
            #
            # Before this, an approved invention was callable by the whole
            # town, so `license_tool` would have granted a right everybody
            # already had and C4 - "a tool held by three residents" - could
            # not have a meaningful numerator. The patent stays a public
            # DISCLOSURE (DESIGN.md section 2); what it protects is the
            # right to use, which is what this checks. Built-in primitives
            # are not in `custom_tools`, so this never touches them.
            if not spec.is_primitive and spec.name in world.custom_tools:
                return world.may_call_tool(spec.name, actor_id, world.tick())
            return True
        if spec.namespace is ToolNamespace.GOVERNMENT:
            return world.charter.store.government_state.is_sitting(actor_id)
        if spec.namespace is ToolNamespace.BUSINESS:
            business = world.businesses.get(spec.owner_id or "")
            if business is None:
                return False
            return actor_id == business.owner_agent_id or actor_id in business.employee_agent_ids
        if spec.namespace is ToolNamespace.ORGANIZATION:
            organization = world.charter.store.organizations.get(spec.owner_id or "")
            if organization is None:
                return False
            return actor_id in organization.members
        return False

    def refuse_if_too_weak(self, world: WorldState, actor_id: str, tool_name: str) -> None:
        """ECON-06. A sick or hungry resident sometimes cannot do the work.

        Only LABOUR-category tools: thinking, talking, walking and paying
        are not physical labour, and making a hungry resident unable to
        SPEAK would take away the one action that can get it fed.

        Never raises anything but ToolRefusedError. A missing hunger dict
        or an unknown agent means full capacity - a capacity model that
        cannot read the world must not start refusing work at random.
        """
        from world.ledger.action_costs import DEFAULT_SCHEDULE, ActionCostCategory
        from world.life.upkeep import HungerState, effective_work_capacity

        # DEFAULT_SCHEDULE, not `world.action_costs`. Whether harvesting is
        # LABOUR is a fact about the tool, not about a particular world's
        # pricing object - and `world.action_costs` is only promised to
        # satisfy `ActionCostScheduleLike`, which requires `quote` and
        # nothing else. Reaching for `rule_for` on it broke two legitimate
        # test doubles that implement exactly the protocol they declare.
        rule = DEFAULT_SCHEDULE.rule_for(tool_name)
        if rule.category is not ActionCostCategory.LABOUR:
            return
        agent = world.agents.get(actor_id)
        if agent is None:
            return
        hunger = (world.hunger_by_agent or {}).get(actor_id) or HungerState()
        capacity = effective_work_capacity(agent.health, hunger)
        if capacity >= 1.0 or world.rng.random() < capacity:
            return

        ailment = agent.health.current_ailment
        if ailment is not None and hunger.days_since_meal > 0:
            why = "you are ill and you have not eaten"
        elif ailment is not None:
            why = "you are ill"
        else:
            why = "you have not eaten"
        # ActorRequirementError, the same error every other "you are not
        # in a state to do this" gate raises (age, licence, dormancy). It
        # aggregates reasons and the registry already restores the
        # snapshot around it, so a refusal costs nothing.
        raise ActorRequirementError(
            tool_name,
            [f"{why} - your body would not do it. Rest, eat, or get treatment."],
        )

    async def check_actor_requirements(
        self, spec: ToolSpec, world: WorldState, actor_id: str
    ) -> None:
        agent = world.require_agent(actor_id)
        requirements = spec.actor_requirements
        reasons: list[str] = []

        if requirements.require_alive and not agent.alive:
            reasons.append(f"{actor_id} is not alive")
        if requirements.require_not_dormant and agent.dormancy.is_dormant:
            reasons.append(f"{actor_id} is dormant (cognitive poverty)")
        if requirements.min_age is not None and agent.age_years < requirements.min_age:
            reasons.append(
                f"{actor_id} is {agent.age_years} years old, "
                f"below the minimum of {requirements.min_age}"
            )
        if requirements.max_age is not None and agent.age_years > requirements.max_age:
            reasons.append(
                f"{actor_id} is {agent.age_years} years old, "
                f"above the maximum of {requirements.max_age}"
            )
        if requirements.require_not_confined and actor_id in world.confined:
            held = world.confined[actor_id]
            until = held.get("until_tick")
            reasons.append(
                f"{actor_id} is being held on case {held.get('case_id')}"
                + (f" until tick {until}" if until else " and has not been ruled on")
                + ". `say` still reaches whoever is with you."
            )
        if requirements.require_government_seat:
            government_state = await world.charter.store.get_government_state()
            if not government_state.is_sitting(actor_id):
                reasons.append(f"{actor_id} does not hold a sitting government seat")
        if requirements.required_license_types:
            licenses = await world.charter.store.list_licenses()
            held = {
                license_.license_type
                for license_ in licenses
                if license_.holder_agent_id == actor_id
                and license_.status is LicenseStatus.ACTIVE
            }
            missing = requirements.required_license_types - held
            if missing:
                reasons.append(f"{actor_id} is missing required license(s): {sorted(missing)}")

        if reasons:
            raise ActorRequirementError(spec.name, reasons)

    def validate_params(
        self,
        spec: ToolSpec,
        raw_params: dict[str, Any],
        world: WorldState | None = None,
        actor_id: str | None = None,
    ) -> BaseModel:
        """TRADE-01. Fold what a resident meant, and refuse what it cannot mean.

        Run `deni-a6f9eb4-5h` settled nothing across 6,424 calls, and the
        single cause was here: `post_price` takes
        `on_behalf_of_business`, every resident wrote `business_id`,
        pydantic dropped the unknown field, and the price was posted on
        the PERSON while the goods sat on the shop's shelf. `buy` then
        answered "n13 needs 1 bread, has 0" to two different buyers
        standing in the right shop with the money in hand.

        The silent drop is the defect. It returns SUCCESS for an act
        nobody asked for, and the resident has no way to find out - it
        was told it had posted a price, and it had, on the wrong holder.

        So: known synonyms are folded (`world/tools/param_repair.py`,
        every entry observed in a real session log), and anything still
        unrecognised is a refusal that NAMES the parameters this tool
        actually has. One turn lost and a resident that now knows the
        word beats a run that settles nothing.
        """
        from world.tools import param_repair

        repaired, renamed = param_repair.repair(spec.name, raw_params)
        # After the synonyms are folded, so `to: "Tova"` resolves exactly like
        # `to_agent_id: "Tova"`. Runs 28 and 28b addressed people by number
        # because a person parameter took only an id - see world/tools/names.py.
        if world is not None and actor_id is not None:
            from world.tools import names

            repaired = names.resolve_person_references(
                world, actor_id, spec.name, spec.params_model.model_fields, repaired
            )

        # Validation FIRST, the stray-parameter check second.
        #
        # Ordering, not taste. Several tools diagnose a wrong SHAPE in
        # their own validator and answer with something far better than
        # a parameter list - `skillify` handed the wrong step shape says
        # "these are toolify's steps, not mine". Checking strays first
        # would replace every one of those with a worse message.
        #
        # A call that validates cleanly and still carries a stray is the
        # dangerous one, and that is exactly what falls through to here.
        try:
            model = spec.params_model.model_validate(repaired)
        except ValidationError as exc:
            messages = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
            known = sorted(spec.params_model.model_fields)
            if known:
                # The commonest refusal in run deni-a6f9eb4-5h was
                # "Field required" with no statement of what the fields
                # ARE. A resident that has to guess spends a call per
                # guess; `take_from_shelf` took three.
                messages.append(f"{spec.name} takes: {', '.join(known)}.")
            raise ToolValidationError(spec.name, messages) from exc

        stray = param_repair.unknown_parameters(spec.params_model, repaired)
        if stray:
            known = sorted(spec.params_model.model_fields)
            raise ToolValidationError(
                spec.name,
                [
                    f"no such parameter {', '.join(repr(k) for k in stray)}. "
                    f"{spec.name} takes: {', '.join(known) if known else 'no parameters'}. "
                    "Nothing was done, because ignoring it would have told "
                    "you this worked when it did something else."
                ],
            )
        if renamed:
            # Told, not silently corrected. A resident whose word was
            # changed for it should learn the town's word, or it will
            # spend a call on the same synonym every time.
            self._last_param_notes = renamed
        return model

    def resolve_location_gate(self, spec: ToolSpec, world: WorldState, params: BaseModel) -> str | None:
        if spec.location_gate is None:
            return None
        if isinstance(spec.location_gate, str):
            return spec.location_gate
        try:
            return spec.location_gate(world, params)
        except ValueError as exc:
            # A dynamic gate resolver (e.g. harvest's crop -> farmland
            # strip) can reject params.py's own already-validated field as
            # semantically wrong (a crop name pydantic accepts as a str
            # but that isn't one of the 10 real varieties). Surfacing that
            # as ToolValidationError keeps every registry-raised failure a
            # ToolError subclass - see this module's docstring.
            raise ToolValidationError(spec.name, [str(exc)]) from exc

    def check_location_gate(self, spec: ToolSpec, world: WorldState, actor_id: str, params: BaseModel) -> None:
        required = self.resolve_location_gate(spec, world, params)
        if required is None:
            return
        actual = world.location_of(actor_id)
        # Not `actual != required`. The map names the sector an agent walks
        # to ("Kasukabe Government Complex"); the charter names the place a
        # tool gates on ("government_complex"). A raw string comparison left
        # four residents standing in the town hall unable to vote, petition
        # or register a business. See world/tools/place_aliases.py.
        # `satisfies_gate_in`, not `satisfies_gate`: a business's premises
        # also satisfies the workshops that business provides, and only the
        # world knows which those are (TOOL-02).
        if not satisfies_gate_in(world, actual, required):
            raise LocationGateError(
                spec.name, required, actual, places_that_satisfy(world, required)
            )

    async def dispatch(
        self, world: WorldState, actor_id: str, tool_name: str, raw_params: dict[str, Any]
    ) -> ToolOutcome:
        """Every gate (actor exists, visible, actor requirements, params,
        location) plus the handler call - everything execute() does
        EXCEPT taking the lock, snapshotting, and publishing. This is
        also what a composite (define_tool-invented) tool's handler calls
        once per step (meta.py): those steps run "under the creator's
        identity/wallet" (DESIGN.md section 2) inside the SAME
        lock/snapshot the outer execute() call already holds, so they
        must not re-acquire `self.lock` - asyncio.Lock is not reentrant,
        and a composite tool whose steps called execute() directly would
        deadlock on its own first step.

        A `ToolInvocationRecord` is appended to `world.tool_invocations`
        right here, after the handler succeeds, for every call this method
        completes - both a top-level call from `execute()` and each inner
        step a composite tool's handler dispatches - namespace
        notwithstanding (DESIGN.md section 2: "Everything is still logged
        for researchers regardless of in-world secrecy"). Logging at this
        level, not only in `execute()`, means a business's secret
        composite tool's individual primitive steps are each recorded
        under the creator's agent id too - exactly the granularity a court
        subpoena into a trade secret's actual mechanics needs. Because
        this only runs after `spec.handler` returns without raising, and
        because it happens inside the same lock/snapshot `execute()` holds
        for the whole call (including every nested `dispatch()` a
        composite tool's steps make), a call that fails partway through
        never leaves a record behind: `execute()`'s `world.restore()` on
        exception reverts `tool_invocations` along with everything else -
        see `ToolInvocationRecord`'s own docstring for why that is
        correct, not a gap.
        """
        if actor_id not in world.agents and actor_id not in world.control_actors:
            raise ActorNotFoundError(actor_id)

        spec = self.tools.get(tool_name)
        if spec is None or not await self.is_visible(spec, world, actor_id):
            # Deliberately the same error whether the tool does not exist
            # or exists but is invisible to this actor - see
            # ToolNotFoundError's docstring.
            #
            # The near miss is drawn ONLY from what this actor can already
            # see, so it confirms nothing hidden: a seat-holder who typed
            # `set_tax_rates` is pointed at `set_tax_rate`, and somebody
            # with no seat is pointed at neither, exactly as before.
            visible = [
                name for name, candidate in self.tools.items()
                if self.visible_to(candidate, world, actor_id)
            ]
            raise ToolNotFoundError(tool_name, nearest_name(tool_name, visible))

        await self.check_actor_requirements(spec, world, actor_id)
        params = self.validate_params(spec, raw_params, world, actor_id)
        self.check_location_gate(spec, world, actor_id, params)

        agent = world.require_agent(actor_id)

        # ── Part A: acting costs sim currency ────────────────────────────
        #
        # BEFORE the handler, because a refusal must be a no-op: charge()
        # raises ActionUnaffordableError while the world is still untouched,
        # rather than letting an agent harvest a field and only then discover
        # it could not pay for the swing. The error is left to propagate the
        # way ToolValidationError above already does - execute() restores the
        # snapshot, and the agent sees a real constraint rather than a crash.
        #
        # Charged here rather than in execute() so that a composite tool pays
        # for each primitive step it dispatches, not once for the wrapper.
        # Bundling steps into an invented tool is meant to save an agent
        # THINKING, not labour.
        from world.tools.primitives.movement import resolve_move_distance

        # ── ECON-06: illness and hunger reach the work ──────────────────
        #
        # `health.work_capacity`, `effective_work_capacity`,
        # `WORK_CAPACITY_BY_AILMENT` and
        # `HUNGER_MILD_WORK_CAPACITY_MULTIPLIER` were all written, all
        # tested, and read by NOTHING outside upkeep.py. DESIGN section 5
        # says "sickness cuts work capacity"; a terminal patient harvested
        # at full rate until the day it died, and treatment (ECON-12) had
        # no economic reason to exist.
        #
        # BEFORE the charge, not after. The Part A comment below states the
        # rule this follows: a refusal must be a no-op. Refusing after the
        # charge would take a sick resident's money and give it nothing,
        # which is a worse answer than the silence it replaces.
        #
        # Probabilistic, per the finding, rather than scaling the yield:
        # every LABOUR tool returns a different shape and the registry has
        # no business reaching into a handler's result. At capacity 0.5 a
        # swing lands half the time, and the resident is TOLD which is
        # which, so "I am too ill to work" becomes something it can read
        # about itself rather than a number that quietly moved.
        self.refuse_if_too_weak(world, actor_id, tool_name)

        proficiency = action_costs.proficiency_for(world, actor_id, tool_name)
        # A4: `buy`/`sell` no longer carry a price, so the 1% transaction
        # cost has to be resolved off the price board here - the same
        # caller-resolves-what-the-module-cannot-see arrangement
        # `proficiency_for` already uses. Without it every trade in the
        # town falls to the value_bps rule's 2-sim floor and a
        # ten-thousand-sim purchase costs the same to transact as a loaf.
        # TRADE-04. The same question, asked twice in one turn, is not
        # charged twice.
        #
        # 199 of run `deni-a6f9eb4-5h`'s 1,224 calls - one in six - were
        # an exact repeat of a call the same resident had already made on
        # the same tick. Between two of a resident's own calls nothing
        # but that resident can change the town, so a repeated READ
        # cannot have a new answer. It gets the answer anyway, plus the
        # sentence it was missing, and pays nothing.
        #
        # Reads only. A repeated `harvest` gets two harvests and a
        # repeated `say` is a resident repeating itself - both real, both
        # left alone. See `world/tools/repeated_calls.py`.
        from world.tools import repeated_calls

        if repeated_calls.already_answered_this_tick(
            world, actor_id, tool_name, raw_params
        ):
            outcome = await spec.handler(world, agent, params)
            result = dict(outcome.result or {})
            result["repeat_of_this_turn"] = True
            result["note"] = (
                f"{result['note']} {repeated_calls.REPEAT_NOTE}"
                if result.get("note")
                else repeated_calls.REPEAT_NOTE
            )
            return ToolOutcome(result=result, events=outcome.events)

        # TOOL-06, the fallback fee. An invented tool's own name has no
        # SCHEDULE_TABLE row, so `rule_for` fell to DEFAULT_RULE and took
        # 5 sim for the wrapper on top of every step the composite then
        # paid for itself - flatly contradicting `toolify`'s own
        # description, "Each step still costs what it always did".
        #
        # Skipped here rather than given a zero row in the table, because
        # the table is static and invented tools are not: a town can name
        # a new one at any tick, and a row per invention would have to be
        # written by whoever registers it and would go stale the moment
        # the tool was renamed or its patent lapsed.
        #
        # `charge = None` is a shape the rest of this method already
        # handles - it is what an unpriced tool has always produced - so
        # nothing below needs to know.
        # TRADE-06. A journey of zero tiles is free.
        #
        # `move` was the most repeated verb in run `deni-a6f9eb4-5h` - 64
        # exact repeats - and a resident asking to go where it already
        # stands was charged, told `walked: true`, and given no reason to
        # stop asking. Priced here rather than folded before the handler:
        # the handler still runs and still publishes `agent.moved`, which
        # is how perception and the map learn where the resident is.
        free = tool_name in world.custom_tools or (
            repeated_calls.costs_nothing_because_it_does_nothing(
                world, actor_id, tool_name, raw_params
            )
        )
        charge = None if free else await action_costs.charge_for_action(
            world.ledger,
            tool_name,
            params,
            action_costs.ActionActor(
                actor_id,
                skill_proficiency=proficiency,
                trade_value_sim=action_costs.posted_trade_value(world, tool_name, params),
                # GROW-23: resolved HERE because action_costs is pure and
                # cannot read an inventory - the same reason distance and
                # trade value are resolved here. A field the caller never
                # fills is a discount nobody can ever earn.
                holds_a_tool=world.inventory.quantity(actor_id, wear.TOOL_ITEM) > 0,
                # ECON-18: the one ActionActor field nobody filled.
                #
                # `extract_quantity` returns this for "move", so leaving it
                # at 0 multiplied the per_unit_cost=2 rule by nothing and
                # every move in town fell to the 1-sim min_cost: a step
                # across the room and a walk across Kasukabe cost the same,
                # and distance stopped being a reason to live near work.
                #
                # UNITS: ticks, not tiles, despite the field's name. A
                # street stride is 40 tiles/tick and the map is 140 wide,
                # so raw tiles would bill a cross-town walk ~300 sim. Ticks
                # are also exactly what the `move` result quotes back as
                # `estimated_ticks` - billed the figure you were shown.
                distance_tiles=(
                    resolve_move_distance(world, actor_id, params)
                    if tool_name == "move"
                    else 0
                ),
            ),
            world.tick(),
        )

        # The charge is reversed if the handler raises. execute() restores
        # OWNED_STATE_FIELDS on an exception, but the ledger is NOT in that
        # snapshot - it is a sibling system - so without this a failed action
        # would be rolled back and paid for at the same time, and the actor
        # would be poorer for something the world says never happened.
        # See action_costs.refund_action() for why a reversing move.
        # TOOL-06: remembered so a COMPOSITE that fails three steps later
        # can give back what its earlier steps paid. See
        # `WorldState.charges_this_call`.
        if charge is not None:
            world.charges_this_call.append((tool_name, actor_id, charge.amount))

        try:
            outcome = await spec.handler(world, agent, params)
        except Exception as exc:
            # A tool that BREAKS must leave a mark on the run's own account of
            # itself. `record_refusal` files only what it can classify - a
            # location gate, an age rule, an affordability check - so a handler
            # raising anything else vanished: refunded, re-raised, and absent
            # from "What the town could not do".
            #
            # That is how a run came to report "0 vote(s)" beside thirteen
            # location refusals while 1,233 ballots were in fact being rejected
            # by a TypeError in `cast_vote`. The report named the wrong cause
            # and a reader had no way to tell.
            #
            # Filed under `broken:` so it reads as what it is - the tool at
            # fault, not the resident - and sorts apart from the refusals that
            # are the town working as designed.
            record_broken_tool(world, actor_id, tool_name, exc)
            if charge is not None:
                await action_costs.refund_action(
                    world.ledger, tool_name, actor_id, charge.amount, world.tick()
                )
                # Refunded here, so `execute` must not refund it again.
                # Popped by identity of the entry rather than by index:
                # a handler that itself called `dispatch` will have
                # appended after us.
                entry = (tool_name, actor_id, charge.amount)
                if entry in world.charges_this_call:
                    world.charges_this_call.remove(entry)
            raise

        # GROW-23: manual work wears a tool out.
        #
        # After the handler, so only a COMPLETED act counts - the same rule
        # the proficiency below follows, for the same reason: an action
        # that raised did not happen, and should neither teach you
        # anything nor break your chisel.
        #
        # This is what gives `iron_tool` a consumer. Before it, the only
        # made item the town had any use for was food; "tool wear" was a
        # sim fee with no item behind it, so the artisanry proficiency
        # residents built up converted into nothing anybody needed.
        # No log line: this module deliberately has no logger, and adding
        # one for this would be the tail wagging the dog. The wear is
        # observable without it - `world.tool_wear` carries the count and
        # the inventory carries the consequence.
        if wear.wears_a_tool(tool_name):
            wear.wear_one_use(world, actor_id, tool_name)

        # ── Part A: and doing it is how you get better at it ─────────────
        #
        # After the handler, so only a completed act counts.
        #
        # BUILT-IN TRADES ONLY. An authored skill does NOT earn a repetition
        # from a tool call, even one its own steps name - it earns one by
        # being walked end to end (see advance_skill_workflows below). That
        # asymmetry is deliberate and load-bearing: it is what makes a junk
        # skill worth nothing. A skill that names forty tools would otherwise
        # collect forty repetitions from work the agent was doing anyway.
        builtin = action_costs.SKILL_FOR_TOOL.get(tool_name)
        if builtin:
            level = world.bump_skill_repetition(actor_id, builtin)
            if world.last_practice_moved_the_level:
                outcome.events.append(_practice_event(world, actor_id, builtin, level))

        # ── B2: and it may be the thing you set out to do ────────────────
        #
        # Here rather than in the handler, because the intention names a
        # TOOL and any tool can be the one named. A resident that set out
        # to sell the wheat stops being told to sell the wheat the moment
        # the wheat is sold - which is the difference between a purpose and
        # a line of furniture in the prompt.
        fulfilled = world.fulfil_intention(actor_id, tool_name)
        if fulfilled is not None:
            outcome.events.append(
                WorldEvent(
                    type="agent.intention.dropped",
                    tick=world.tick(),
                    payload={
                        "agent_id": actor_id,
                        "text": fulfilled.text,
                        "reason": "fulfilled",
                        "by_tool": tool_name,
                        "ticks_held": world.tick() - fulfilled.set_tick,
                    },
                )
            )

        # ── ENG-24: and it may be progress on something larger ───────────
        #
        # Here for the same reason `fulfil_intention` is here: a goal's
        # `done_when` names a TOOL, and any tool can be the one named.
        # Progress arrives from the act, never from the resident saying
        # so - `update_goal` exists for the part a resident does know
        # better, but a goal can complete without ever being touched.
        from world.tools.goal_lifecycle import credit_tool_success

        outcome.events.extend(credit_tool_success(world, actor_id, tool_name, outcome.result))

        # ── Part B: and it may be a step in a procedure ──────────────────
        for finished in world.advance_skill_workflows(actor_id, "tool", tool_name, world.tick()):
            outcome.events.append(
                _practice_event(world, actor_id, finished, world.skills[actor_id][finished])
            )

        # Appended to the OUTCOME, not published here. execute() publishes
        # after the whole call has proven it succeeded, and routing these the
        # same way means a practice row can never outlive a rolled-back call -
        # the mistake the action charge and the refusal log each made once.

        # Trimmed to a ring: over a multi-hour run this list is appended to on
        # every successful call by every agent, and an uncapped one is what
        # ends a long run on a small machine. The archive is the trace store;
        # this is the live tail.
        if len(world.tool_invocations) >= MAX_TOOL_INVOCATIONS_KEPT:
            del world.tool_invocations[: len(world.tool_invocations) - MAX_TOOL_INVOCATIONS_KEPT + 1]
        # PROG-01. Say which decision this call belonged to.
        #
        # Two ways a call can be part of a program, and they are read in
        # this order because only one of them can be true at a time:
        #
        #   * `world.program_stack` is non-empty - we are INSIDE a step
        #     that `world/tools/program.py` is running, and it pushed the
        #     frame before dispatching us;
        #   * the handler that just returned named a program in its own
        #     result - we ARE the wrapper, and by now the runtime has
        #     popped its last frame, so the stack cannot tell us.
        #
        # The wrapper is step 0. That is what makes "one model call bought
        # how many acts" a subtraction (rows in the program, minus the
        # wrapper) rather than a join against anything else.
        program_id: str | None = None
        program_step: int | None = None
        if world.program_stack:
            program_id, program_step = world.program_stack[-1]
        elif isinstance(outcome.result, dict) and outcome.result.get("program_id"):
            program_id = str(outcome.result["program_id"])
            program_step = 0
        world.tool_invocations.append(
            ToolInvocationRecord(
                invocation_id=f"invocation-{uuid.uuid4().hex[:12]}",
                tool_name=tool_name,
                namespace=spec.namespace.value,
                actor_id=actor_id,
                tick=world.tick(),
                params=dict(raw_params),
                owner_id=spec.owner_id,
                program_id=program_id,
                program_step=program_step,
            )
        )
        return outcome

    async def execute(
        self, world: WorldState, actor_id: str, tool_name: str, raw_params: dict[str, Any]
    ) -> ToolResult:
        """The single call path DESIGN.md section 2 describes: dispatch()
        under a lock, with tools-owned state snapshotted first so a
        handler that raises partway through (including partway through a
        composite tool's steps) leaves no partial mutation behind - see
        the module docstring for the exact gate order and the atomicity
        contract, and dispatch()'s own docstring for the invocation-log
        write that happens as part of that same contract.
        """
        async with self.lock:
            snapshot = world.snapshot()
            # TOOL-06: this call's charge ledger starts empty. Cleared
            # here rather than at the end, because an execute that raised
            # somewhere unexpected would otherwise leave entries behind
            # for the next call to refund.
            world.charges_this_call.clear()
            # PROG-01, same reason and the same line: a frame left behind
            # by a call that raised somewhere unexpected would file the
            # NEXT resident's calls under a program that has already
            # ended. Cleared going in, not coming out.
            world.program_stack.clear()
            world.program_budget = None
            try:
                outcome = await self.dispatch(world, actor_id, tool_name, raw_params)
            except Exception as exc:
                world.restore(snapshot)
                # TOOL-06. AFTER the restore, like the refusal below.
                #
                # The inventory rolls back and the ledger does not - it is
                # a sibling system, not part of OWNED_STATE_FIELDS - so
                # every step this call paid for has to be given back by
                # hand. `dispatch` already refunded the step that raised
                # and removed it from this list; what is left is the
                # steps that SUCCEEDED inside a call that then failed,
                # and which the restore has just unmade.
                for step_name, payer, amount in reversed(world.charges_this_call):
                    await action_costs.refund_action(
                        world.ledger, step_name, payer, amount, world.tick()
                    )
                world.charges_this_call.clear()
                world.program_stack.clear()
                world.program_budget = None
                # AFTER the restore, never before. A refusal exists only
                # because this call failed, and the restore above is what runs
                # on exactly that path - recording it inside the transaction
                # would roll the record back along with everything else, so
                # the log would be empty precisely when it had something to
                # say. (`tool_refusals` is kept out of OWNED_STATE_FIELDS for
                # the same reason; see its comment in state.py.)
                record_refusal(world, actor_id, tool_name, exc)
                # ENG-24. The refusal log is a thing the REPORT reads; a
                # belief is a thing the RESIDENT reads. Without the
                # second, the same wall is walked into every tick and
                # the log just counts how many times. Written after the
                # restore, exactly as the refusal is, and published
                # before the re-raise because nothing downstream of the
                # raise will get the chance.
                for belief_event in beliefs_from_refusal(world, actor_id, tool_name, exc):
                    await world.event_bus.publish(belief_event)
                raise

            world.charges_this_call.clear()
            world.program_stack.clear()
            world.program_budget = None
            for event in outcome.events:
                await world.event_bus.publish(event)

            return ToolResult(
                tool_name=tool_name, actor_id=actor_id, result=outcome.result, events=outcome.events
            )


def _practice_event(
    world: WorldState, actor_id: str, skill: str, level: int
) -> WorldEvent:
    """One observation of a skill getting better by being used.

    Emitted only when the proficiency actually MOVED, not on every
    repetition. The square-root curve means early practice moves it often and
    late practice rarely, so this is naturally self-limiting - at most one row
    per level per agent per skill, rather than one per tool call, which for
    314 agents would be a trace row every action forever.
    """
    return WorldEvent(
        type="agent.skill.practised",
        tick=world.tick(),
        payload={
            "agent_id": actor_id,
            "skill": skill,
            "proficiency": level,
            "repetitions": world.skill_reps.get(actor_id, {}).get(skill, 0),
        },
    )


def unmet_requirement_for(exc: BaseException) -> tuple[str, str] | None:
    """(stable grouping key, human detail) for a refusal - or None for a
    failure that is not a capability gap.

    The key has to be STABLE, because its whole purpose is to group: "14 agents
    tried to treat illness and lacked the skill" needs the same string
    fourteen times, not fourteen phrasings. So it is built from the
    exception's structured fields, never from its message.

    Returns None for ToolValidationError and anything unrecognised. A
    malformed call is a mistake, not a thing the town cannot do, and letting
    mistakes into the gaps view would bury the real gaps under typos.
    """
    if isinstance(exc, ToolNotFoundError):
        # The most direct statement of a gap there is: an agent reached for a
        # verb the town does not have. This is the row that becomes a prompt
        # to invent one.
        # ToolNotFoundError stores `.name`, not `.tool_name`.
        return f"no-such-tool:{exc.name}", f"nothing in the town does {exc.name!r}"
    if isinstance(exc, LocationGateError):
        return f"location:{exc.required}", f"must be at {exc.required}, was at {exc.actual}"
    if isinstance(exc, ActorRequirementError):
        # Several requirements can fail at once; each becomes its own row, but
        # the key is the sorted join so the same combination groups together.
        reasons = "; ".join(sorted(exc.reasons)) if getattr(exc, "reasons", None) else "unmet"
        return f"actor:{reasons}", reasons
    if isinstance(exc, ActionUnaffordableError):
        return "funds", f"could not afford {exc.tool_name}"

    # TOOL-08/TOOL-12: a `CharterError` is not a `ToolError`, so every
    # refusal raised inside an election, a petition, an organisation or the
    # patent office fell straight through this function and was recorded
    # nowhere. The town's own instruments could not see that its civic
    # surface was refusing anybody - run 23's "what the town could not do"
    # table showed 10 refusals for a run with about 70 corrections.
    #
    # Mapped onto the SAME keys the tool-side errors use where the gap is
    # the same gap: a resident refused for standing in the wrong place is
    # one row whether the registry or the charter said so, or the two halves
    # of one problem group apart and neither looks like a problem.
    if isinstance(exc, NotAtRequiredLocation):
        return f"location:{exc.required}", f"must be at {exc.required}, was at {exc.actual}"
    if isinstance(exc, NotSittingGovernment):
        return "actor:no government seat", f"{exc.agent_id} holds no seat"
    if isinstance(exc, NotEligibleToVote):
        return f"actor:{exc.reason}", f"{exc.agent_id} is not eligible: {exc.reason}"
    if isinstance(exc, CharterError):
        # Everything else the charter can say. Keyed on the class rather
        # than the message so a term limit and a closed ballot stay apart
        # while two of either group together.
        return f"charter:{type(exc).__name__}", str(exc)
    return None


def record_broken_tool(
    world: WorldState, actor_id: str, tool_name: str, exc: BaseException
) -> None:
    """File a handler that raised something nobody designed for.

    Separate from `record_refusal`, which asks `unmet_requirement_for` whether
    the failure is a gap worth counting and drops anything it cannot name. A
    TypeError in a tool is not a gap in the town; it is a gap in the tool, and
    it belongs in the report either way.
    """
    classified = unmet_requirement_for(exc)
    if classified is not None:
        record_refusal(world, actor_id, tool_name, exc)
        return
    if len(world.tool_refusals) >= MAX_TOOL_REFUSALS_KEPT:
        del world.tool_refusals[: len(world.tool_refusals) - MAX_TOOL_REFUSALS_KEPT + 1]
    world.tool_refusals.append(
        ToolRefusalRecord(
            refusal_id=f"refusal-{uuid.uuid4().hex[:12]}",
            agent_id=actor_id,
            tool_name=tool_name,
            tick=world.tick(),
            unmet_requirement=f"broken:{type(exc).__name__}",
            detail=str(exc)[:300],
        )
    )


def record_refusal(
    world: WorldState, actor_id: str, tool_name: str, exc: BaseException
) -> None:
    """Append one refusal, if this failure represents a gap worth counting."""
    classified = unmet_requirement_for(exc)
    if classified is None:
        return
    requirement, detail = classified
    if len(world.tool_refusals) >= MAX_TOOL_REFUSALS_KEPT:
        del world.tool_refusals[: len(world.tool_refusals) - MAX_TOOL_REFUSALS_KEPT + 1]
    world.tool_refusals.append(
        ToolRefusalRecord(
            refusal_id=f"refusal-{uuid.uuid4().hex[:12]}",
            agent_id=actor_id,
            tool_name=tool_name,
            tick=world.tick(),
            unmet_requirement=requirement,
            detail=detail,
        )
    )


def beliefs_from_refusal(
    world: WorldState, actor_id: str, tool_name: str, exc: BaseException
) -> "list[WorldEvent]":
    """Turn a refusal into something the resident carries forward.

    Only refusals that `unmet_requirement_for` already classifies as a
    real gap become beliefs. A validation slip - a mistyped parameter -
    is not something to learn about the world, and a resident whose
    belief list filled with its own typos would be worse off than one
    with no belief list at all.
    """
    classified = unmet_requirement_for(exc)
    if classified is None:
        return []
    _requirement, detail = classified
    # `note_refusal_belief` swallows its own failures and logs them with
    # its own logger; this module deliberately has none (see the tool-wear
    # comment above), and a bare `except` here would be the one kind of
    # silence the project does not allow.
    from world.tools.goal_lifecycle import note_refusal_belief

    return note_refusal_belief(
        world, actor_id, tool_name, detail, world.location_of(actor_id)
    )


def dormant_agent_ids(world: WorldState) -> list[str]:
    """Convenience read used by a couple of tests and, eventually, a
    god-console view: every agent currently in cognitive poverty
    (DESIGN.md section 3). Not a tool - a plain query, no mutation, no
    gate.
    """
    return [
        agent_id
        for agent_id, agent in world.agents.items()
        if isinstance(agent.dormancy, DormancyState) and agent.dormancy.is_dormant
    ]
