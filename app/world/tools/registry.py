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
    require_not_confined: bool = False


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
    owner_id: str | None = None
    is_primitive: bool = True


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    actor_id: str
    result: dict[str, Any]
    events: list[WorldEvent]


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
    return dict(cached)


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


FULL_SCHEMA_BUDGET = int(os.environ.get("KASUKABE_FULL_SCHEMA_BUDGET", "24"))

ALWAYS_IN_FULL = frozenset({
    "look_around", "move", "say", "start_conversation",
    "eat", "rest",
    "check_debts",
    "keep_doing", "set_intention", "set_goal",
    "describe_primitives", "skillify", "do_in_order",
})

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
    where = (
        display_place(spec.location_gate)
        if isinstance(spec.location_gate, str)
        else "elsewhere"
    )
    first_sentence = spec.description.split(".", 1)[0].strip()

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
        "description": f"{first_sentence}.{INDEX_NOTES.get(spec.name, '')}{needs}{reach}",
        "input_schema": schema,
        "elsewhere": True,
    }


class ToolRegistry:
    """The dispatcher. One instance per running world server; bootstrap.py
    builds the default one with every primitive plus the meta-tool
    registered.
    """

    def __init__(self) -> None:
        self.tools: dict[str, ToolSpec] = {}
        self.primitive_names: frozenset[str] = frozenset()
        self.lock = asyncio.Lock()
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
        if world is not None and actor_id is not None:
            from world.tools import names

            repaired = names.resolve_person_references(
                world, actor_id, spec.name, spec.params_model.model_fields, repaired
            )

        try:
            model = spec.params_model.model_validate(repaired)
        except ValidationError as exc:
            messages = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
            known = sorted(spec.params_model.model_fields)
            if known:
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
            raise ToolValidationError(spec.name, [str(exc)]) from exc

    def check_location_gate(self, spec: ToolSpec, world: WorldState, actor_id: str, params: BaseModel) -> None:
        required = self.resolve_location_gate(spec, world, params)
        if required is None:
            return
        actual = world.location_of(actor_id)
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
            visible = [
                name for name, candidate in self.tools.items()
                if self.visible_to(candidate, world, actor_id)
            ]
            raise ToolNotFoundError(tool_name, nearest_name(tool_name, visible))

        await self.check_actor_requirements(spec, world, actor_id)
        params = self.validate_params(spec, raw_params, world, actor_id)
        self.check_location_gate(spec, world, actor_id, params)

        agent = world.require_agent(actor_id)

        from world.tools.primitives.movement import resolve_move_distance

        self.refuse_if_too_weak(world, actor_id, tool_name)

        proficiency = action_costs.proficiency_for(world, actor_id, tool_name)
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
                holds_a_tool=world.inventory.quantity(actor_id, wear.TOOL_ITEM) > 0,
                distance_tiles=(
                    resolve_move_distance(world, actor_id, params)
                    if tool_name == "move"
                    else 0
                ),
            ),
            world.tick(),
        )

        if charge is not None:
            world.charges_this_call.append((tool_name, actor_id, charge.amount))

        try:
            outcome = await spec.handler(world, agent, params)
        except Exception as exc:
            record_broken_tool(world, actor_id, tool_name, exc)
            if charge is not None:
                await action_costs.refund_action(
                    world.ledger, tool_name, actor_id, charge.amount, world.tick()
                )
                entry = (tool_name, actor_id, charge.amount)
                if entry in world.charges_this_call:
                    world.charges_this_call.remove(entry)
            raise

        if wear.wears_a_tool(tool_name):
            wear.wear_one_use(world, actor_id, tool_name)

        builtin = action_costs.SKILL_FOR_TOOL.get(tool_name)
        if builtin:
            level = world.bump_skill_repetition(actor_id, builtin)
            if world.last_practice_moved_the_level:
                outcome.events.append(_practice_event(world, actor_id, builtin, level))

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

        from world.tools.goal_lifecycle import credit_tool_success

        outcome.events.extend(credit_tool_success(world, actor_id, tool_name, outcome.result))

        for finished in world.advance_skill_workflows(actor_id, "tool", tool_name, world.tick()):
            outcome.events.append(
                _practice_event(world, actor_id, finished, world.skills[actor_id][finished])
            )


        if len(world.tool_invocations) >= MAX_TOOL_INVOCATIONS_KEPT:
            del world.tool_invocations[: len(world.tool_invocations) - MAX_TOOL_INVOCATIONS_KEPT + 1]
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
            world.charges_this_call.clear()
            world.program_stack.clear()
            world.program_budget = None
            try:
                outcome = await self.dispatch(world, actor_id, tool_name, raw_params)
            except Exception as exc:
                world.restore(snapshot)
                for step_name, payer, amount in reversed(world.charges_this_call):
                    await action_costs.refund_action(
                        world.ledger, step_name, payer, amount, world.tick()
                    )
                world.charges_this_call.clear()
                world.program_stack.clear()
                world.program_budget = None
                record_refusal(world, actor_id, tool_name, exc)
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
        return f"no-such-tool:{exc.name}", f"nothing in the town does {exc.name!r}"
    if isinstance(exc, LocationGateError):
        return f"location:{exc.required}", f"must be at {exc.required}, was at {exc.actual}"
    if isinstance(exc, ActorRequirementError):
        reasons = "; ".join(sorted(exc.reasons)) if getattr(exc, "reasons", None) else "unmet"
        return f"actor:{reasons}", reasons
    if isinstance(exc, ActionUnaffordableError):
        return "funds", f"could not afford {exc.tool_name}"

    if isinstance(exc, NotAtRequiredLocation):
        return f"location:{exc.required}", f"must be at {exc.required}, was at {exc.actual}"
    if isinstance(exc, NotSittingGovernment):
        return "actor:no government seat", f"{exc.agent_id} holds no seat"
    if isinstance(exc, NotEligibleToVote):
        return f"actor:{exc.reason}", f"{exc.agent_id} is not eligible: {exc.reason}"
    if isinstance(exc, CharterError):
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
