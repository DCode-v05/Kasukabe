"""define_tool: DESIGN.md section 2's meta-tool.

"Agents may define new tools as compositions of existing primitives
only - no new physics ... Runs under the creator's identity/wallet, same
location gates. Must be registered at the government patent office (fee;
reviewable; revocable)."

Three things make "no new physics possible by construction" literally
true here, not just a policy this module promises to enforce:

1. `ToolRegistry.primitive_names` is frozen once, by bootstrap.py, right
   after every built-in primitive is registered and before this module's
   `define_tool` spec is added. `validate_steps` below checks every
   step's `tool_name` against that frozen set - never against
   `registry.tools` (which grows as more invented tools are approved), so
   an invented tool can never cite another invented tool as a step, and
   `define_tool` itself is never in the whitelist (it is added to
   `registry.tools` after freezing), so a meta-tool that composes
   meta-tools is unrepresentable, not just disallowed.
2. Filing alone never makes a tool callable - `world.custom_tools[name]`
   starts `approved=False`, exactly mirroring
   `charter.patent_office.PatentApplication`'s PENDING/APPROVED split
   (see `approve_patent_handler`), and only `approve_patent` (a
   GOVERNMENT-namespace tool, sitting-seat-only, gated at the patent
   office) calls `registry.register()` to actually wire the composite
   handler in.
3. That composite handler (`build_composite_handler`) never runs
   arbitrary code: it replays the filed `ToolStep`s one at a time through
   `ToolRegistry.dispatch()`, the exact same gate-checked path every
   ordinary tool call takes, under the inventor's own agent id - an
   invented tool is data (a fixed list of primitive calls plus a
   placeholder substitution), never a new code path.

`define_business_tool`/`define_organization_tool`, below, are the OTHER
half of DESIGN.md section 2's namespace rules: "business (trade secrets:
owner + granted employees ... no patent office needed)". A real-world
patent trades public disclosure for protection; a trade secret trades
protection for secrecy instead, and never files anything public. These
two skip `PatentApplication`/`approve_patent` entirely - no fee, no
PENDING state, no ministerial review - and register their composite
handler straight into the registry as BUSINESS/ORGANIZATION-namespace
(owner_id set to the business_id/org_id), immediately `approved=True`,
in the same handler call that validates the composition. They reuse every
one of the three guarantees above unchanged (`validate_steps` against the
same frozen `primitive_names`, `build_composite_handler`'s same replay-
through-`dispatch()` execution) - the only thing that differs from
`define_tool` is which namespace the result lands in and that there is no
government approval gate before it goes live, exactly matching DESIGN.md's
"no patent office needed" for this one path.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError, create_model, model_validator

from world.core.events import WorldEvent
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.life.models import AgentLifeRecord
from world.tools.errors import (
    ActorRequirementError,
    CompositionCycleError,
    CompositionTooDeepError,
    NotFoundError,
    PatentNotApprovedError,
    PrimitiveNotWhitelistedError,
    ToolValidationError,
    UnknownStepPlaceholderError,
)
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX, LOCATION_PATENT_OFFICE
from world.tools.models import (
    PARAM_TYPE_PYTHON,
    CustomToolDefinition,
    ParamType,
    SkillDefinition,
    SkillStep,
    ToolStep,
)
from world.tools import program
from world.tools.place_aliases import place_for_charter, resolve_business
from world.tools.registry import ActorRequirements, ToolHandler, ToolNamespace, ToolOutcome, ToolRegistry, ToolSpec, location_label
from world.tools.state import WorldState

DEFINE_TOOL_MIN_AGE_YEARS = 18.0

MAX_TOOL_DEPTH = program.MAX_TOOL_DEPTH


def discounted_tools(
    world: WorldState, steps: "list[SkillStep]", defining: str | None = None
) -> list[str]:
    """The tools a workflow makes cheaper: its own `tool` steps and the tools
    any skill it runs performs. Mirrors `WorldState.skill_tools`, which is
    what dispatch actually reads, so what is FILED and what is CHARGED
    cannot disagree - run 29's report printed "discounts nothing" for a
    skill whose only working step was another skill.
    """
    followable = {
        name: d for name, d in world.skill_definitions.items() if world.is_followable(d)
    }
    out: list[str] = []

    def walk(items: "list[SkillStep]", path: frozenset[str]) -> None:
        for step in items:
            if step.kind == "tool" and step.ref:
                out.append(step.ref)
            elif step.kind == "skill" and step.ref in followable and step.ref not in path:
                walk(followable[step.ref].steps, path | {step.ref})

    walk(list(steps), frozenset({defining}) if defining else frozenset())
    return out


async def citable_tool_names(
    registry: ToolRegistry, world: WorldState, actor_id: str
) -> "frozenset[str]":
    """The names a composition of this actor's may name as steps.

    Every primitive, plus every invented tool this actor could already
    call one at a time. That last clause is the whole of the rule: a
    resident composing tools it is entitled to call gains no capability
    it did not have, which is what "no new physics" has always meant.
    A trade secret it cannot call is not citable, and the refusal reads
    the same as for a name the town does not have - secrecy is secrecy
    from the composition layer too.
    """
    names = set(registry.primitive_names)
    for name, definition in world.custom_tools.items():
        if not definition.approved:
            continue
        spec = registry.get(name)
        if spec is None:
            continue
        if await registry.is_visible(spec, world, actor_id):
            names.add(name)
    return frozenset(names)


def same_name(a: str, b: str) -> bool:
    """Are these two filings the same name written two ways?

    Run 29. Corwin Thorne filed `Timber Felling` at tick 72; Tobias
    Holloway filed `timber felling` at tick 76. The collision check was
    `params.name in world.skill_definitions` - exact string, so the town
    ended the run holding two procedures for one action, four ticks
    apart, and had no way to notice.

    Case and the space/underscore choice, and nothing else. NOT stemming,
    NOT synonyms, NOT edit distance: `saw_planks` and `saw_plank` are a
    resident's two drafts and telling them apart is the resident's
    business, while `Timber Felling` and `timber felling` are one name
    typed by two hands. A looser rule would start refusing filings that
    are genuinely different, which is worse than the duplicate - a
    refusal costs a turn and cannot be appealed.
    """
    flatten = lambda s: " ".join(s.replace("_", " ").split()).casefold()  # noqa: E731
    return flatten(a) == flatten(b)


def existing_skill_named_like(world: WorldState, name: str) -> "str | None":
    """The skill already filed under this name, however it was written."""
    if name in world.skill_definitions:
        return name
    return next((held for held in world.skill_definitions if same_name(held, name)), None)


def existing_tool_named_like(
    registry: ToolRegistry, world: WorldState, name: str
) -> "str | None":
    """The tool already registered or invented under this name.

    The same rule as `existing_skill_named_like`, applied to the half of
    the authoring surface that has not hit the duplicate yet. Run 29
    invented 0 tools, so this has never fired - which is the reason to
    put it in now rather than after the first `toolify` run reproduces
    the skill half's finding at a run's cost.
    """
    if registry.get(name) is not None or name in world.custom_tools:
        return name
    for held in (*registry.tools, *world.custom_tools):
        if same_name(held, name):
            return held
    return None


def flatten_to_primitives(
    registry: ToolRegistry,
    world: WorldState,
    steps: "list[ToolStep]",
    *,
    filing: str = "this tool",
    defining: "frozenset[str] | None" = None,
    _path: "tuple[str, ...]" = (),
) -> list[str]:
    """Every primitive a composition eventually performs, in order.

    This is the proof that replaced the old one-level whitelist. Before
    TOOL-31 a step had to BE a primitive, which made "no new physics"
    trivially true and also made a library impossible: nothing could
    stand on anything. What the rule protects is that every LEAF is a
    primitive the caller could have called by hand - a property of the
    bottom of the tree, not of its height - and walking the tree proves
    it for a tower exactly as the whitelist proved it for one step.

    Raises rather than returning problems, because each of the three
    failures is the end of the walk: a step naming nothing the town has,
    a tower taller than `MAX_TOOL_DEPTH`, or a loop that would never
    reach a primitive at all.
    """
    leaves: list[str] = []
    for step in steps:
        name = step.tool_name
        if name in registry.primitive_names:
            leaves.append(name)
            continue
        if defining and name in defining:
            raise CompositionCycleError(name, list(_path))
        if name in _path:
            raise CompositionCycleError(name, list(_path))
        definition = world.custom_tools.get(name) if world is not None else None
        if definition is None or not definition.approved:
            raise PrimitiveNotWhitelistedError(name)
        if len(_path) + 1 > MAX_TOOL_DEPTH:
            raise CompositionTooDeepError(filing, [*_path, name], MAX_TOOL_DEPTH)
        leaves.extend(
            flatten_to_primitives(
                registry, world, definition.steps,
                filing=filing, defining=defining, _path=(*_path, name),
            )
        )
    return leaves


def validate_steps(
    registry: ToolRegistry,
    param_schema: dict[str, ParamType],
    steps: list[ToolStep],
    tool_name: str = "define_tool",
    context: "dict[str, Any] | None" = None,
    *,
    world: "WorldState | None" = None,
    citable: "frozenset[str] | None" = None,
    defining: "frozenset[str] | None" = None,
    returns: "dict[str, Any] | None" = None,
) -> None:
    """Aggregates every violation into one ToolValidationError
    (CONVENTIONS.md's aggregate-errors idiom) so an inventor sees
    everything wrong with a composition at once.

    `tool_name` because this is shared by `define_tool`, `toolify` and
    `define_business_tool`, and an inventor told "define_tool: invalid
    params" after calling `toolify` has to work out which of the three it
    is being refused by.

    `context` carries the vocabulary so a refusal teaches - see
    ToolError.context.
    """
    errors: list[str] = []
    if not steps:
        errors.append("a composed tool must have at least one step")
    allowed = citable if citable is not None else registry.primitive_names
    for index, step in enumerate(steps):
        if step.tool_name not in allowed:
            errors.append(str(PrimitiveNotWhitelistedError(step.tool_name, citable)))
            continue
        for value in step.params.values():
            if isinstance(value, str) and value.startswith("$"):
                placeholder = value[1:]
                if placeholder not in param_schema:
                    errors.append(
                        f"step {index} ({step.tool_name}): {str(UnknownStepPlaceholderError(value, frozenset(param_schema.keys())))}"
                    )
        errors.extend(_step_would_be_refused(registry, index, step))
    program_problems = program.program_step_problems(steps, dict(param_schema))
    errors.extend(program_problems)
    answer_problems = program.returns_problems(returns, dict(param_schema), steps)
    program_problems.extend(answer_problems)
    errors.extend(answer_problems)

    if world is not None and not errors:
        try:
            leaves = flatten_to_primitives(
                registry, world, steps, filing=tool_name, defining=defining
            )
        except (CompositionTooDeepError, CompositionCycleError, PrimitiveNotWhitelistedError) as refused:
            errors.append(str(refused))
        else:
            if len(leaves) > program.PROGRAM_MAX_CALLS:
                errors.append(
                    f"one call of this would make {len(leaves)} acts, and a "
                    f"program may make at most {program.PROGRAM_MAX_CALLS}. "
                    "Take some steps out, or build it from tools that do less."
                )
    if errors:
        if program_problems:
            context = {**(context or {}), **program.PROGRAM_HELP_CONTEXT}
        raise ToolValidationError(tool_name, errors, context=context)


def _step_would_be_refused(
    registry: ToolRegistry, index: int, step: ToolStep
) -> list[str]:
    """TOOL-06: dry-run one step's literal params against its own model.

    Filing checked only that a step names a whitelisted primitive and
    that its `$placeholders` exist. Junia's `mill_flour_run` was accepted
    carrying `craft(recipe_id="mill_flour_from_wheat")` - a recipe that
    does not exist - and failed at step 3 on its first call, after the
    first two steps had already run and been paid for.

    That is worse than a refusal at filing: the inventor spent the
    invention fee, the patent office approved it, the tool went into the
    registry, and the resident learned it was broken by watching a turn
    die halfway through. A tool the world can already prove will fail
    should be refused while the inventor is still holding it.

    Only LITERAL params are checked. A step whose parameters come from
    `$placeholders` cannot be validated until it is called with real
    arguments, and guessing at one would refuse a perfectly good
    composition - so those are left to the existing placeholder check.

    PROG-02: `@bindings` are templated in exactly the same sense and are
    skipped for exactly the same reason. `take_from_shelf(quantity=
    "@count.quantity")` is a string here and an integer at run time, and
    dry-running it as written would refuse every program that reads a
    result - which is the one thing programs were added to be able to do.
    Whether the binding EXISTS is still checked, by
    `program.program_step_problems`; what cannot be checked at filing is
    what type it will hold, and a validator that guesses would refuse
    correct programs to catch incorrect ones it cannot identify anyway.
    """
    spec = registry.get(step.tool_name)
    if spec is None:  # pragma: no cover - guarded by the whitelist above
        return []
    literal = {
        key: value
        for key, value in step.params.items()
        if not (isinstance(value, str) and value[:1] in ("$", "@"))
    }
    if len(literal) != len(step.params):
        return []

    problems: list[str] = []
    try:
        spec.params_model.model_validate(literal)
    except ValidationError as exc:
        for error in exc.errors():
            where = ".".join(str(part) for part in error["loc"]) or "params"
            problems.append(
                f"step {index} ({step.tool_name}): {where} - {error['msg']}"
            )
        return problems

    recipe_id = literal.get("recipe_id")
    if isinstance(recipe_id, str) and step.tool_name in _RECIPE_TOOLS:
        from world.tools.recipes import RECIPES

        if recipe_id not in RECIPES:
            known = ", ".join(sorted(RECIPES)[:8])
            problems.append(
                f"step {index} ({step.tool_name}): no recipe called "
                f"{recipe_id!r}. Some that exist: {known}."
            )
    return problems


_RECIPE_TOOLS = frozenset({"craft", "cook"})


def build_params_model(tool_name: str, param_schema: dict[str, ParamType]) -> type[BaseModel]:
    fields: dict[str, Any] = {
        field_name: (PARAM_TYPE_PYTHON[field_type], ...) for field_name, field_type in param_schema.items()
    }
    return create_model(f"{tool_name}_Params", **fields)  # type: ignore[call-overload]


def build_composite_handler(registry: ToolRegistry, tool_name: str) -> ToolHandler:
    async def composite_handler(
        world: WorldState, caller: AgentLifeRecord, params: BaseModel
    ) -> ToolOutcome:
        definition = world.custom_tools.get(tool_name)
        if definition is None or not definition.approved:
            raise PatentNotApprovedError(tool_name)

        call_params = params.model_dump()
        run, events = await program.run_program(
            registry, world, caller, tool_name, definition.steps, call_params,
            returns=definition.returns,
        )

        events.append(
            WorldEvent(
                type="business.invented_tool.invoked",
                tick=world.tick(),
                payload={
                    "tool_name": tool_name,
                    "invoked_by_agent_id": caller.agent_id,
                    "creator_agent_id": definition.creator_agent_id,
                    "involved_agent_ids": [caller.agent_id, definition.creator_agent_id],
                    "program_id": run.program_id,
                    "calls_made": run.calls_made,
                    "stopped_early": run.stopped_early,
                },
            )
        )
        return ToolOutcome(result=run.as_result(), events=events)

    return composite_handler


class DefineToolParams(BaseModel):
    name: str
    description: str
    param_schema: dict[str, ParamType] = {}
    steps: list[ToolStep]
    returns: dict[str, Any] = {}


def build_define_tool_spec(registry: ToolRegistry) -> ToolSpec:
    async def define_tool_handler(
        world: WorldState, agent: AgentLifeRecord, params: DefineToolParams
    ) -> ToolOutcome:
        if (clash := existing_tool_named_like(registry, world, params.name)) is not None:
            raise ToolValidationError("define_tool", [f"a tool named {clash!r} already exists"])
        validate_steps(
            registry, params.param_schema, params.steps,
            world=world, citable=await citable_tool_names(registry, world, agent.agent_id),
            returns=params.returns,
        )

        charter = await world.charter.store.get_charter()
        fee = charter.patent_filing_fee_sim
        location = place_for_charter(world, agent.agent_id, LOCATION_PATENT_OFFICE)

        await world.ledger.move(
            from_account_id=agent.agent_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=fee,
            currency=Currency.SIM,
            reason=TransactionReason.TRANSFER,
            tick=world.tick(),
            memo=f"patent filing fee for {params.name}",
        )
        application = await world.charter.patent_office.file_application(
            agent.agent_id, params.name, params.description, [s.tool_name for s in params.steps],
            fee, location, world.tick(),
        )

        world.custom_tools[params.name] = CustomToolDefinition(
            tool_name=params.name,
            description=params.description,
            creator_agent_id=agent.agent_id,
            param_schema=params.param_schema,
            steps=params.steps,
            returns=params.returns,
            patent_application_id=application.application_id,
            approved=False,
            created_tick=world.tick(),
        )
        event = WorldEvent(
            type="politics.patent.filed",
            tick=world.tick(),
            payload={
                "application_id": application.application_id,
                "tool_name": params.name,
                "inventor_agent_id": agent.agent_id,
                "fee_sim": fee,
                "steps": [step.tool_name for step in params.steps],
            },
        )
        return ToolOutcome(result={"application_id": application.application_id}, events=[event])

    return ToolSpec(
        name="define_tool",
        description="Compose existing primitives into a new tool, filed at the patent office.",
        params_model=DefineToolParams,
        handler=define_tool_handler,
        namespace=ToolNamespace.PUBLIC,
        location_gate=LOCATION_PATENT_OFFICE,
        actor_requirements=ActorRequirements(min_age=DEFINE_TOOL_MIN_AGE_YEARS),
        is_primitive=False,
    )


def build_approve_patent_spec(registry: ToolRegistry) -> ToolSpec:
    class ApprovePatentParams(BaseModel):
        application_id: str

    async def approve_patent_handler(
        world: WorldState, agent: AgentLifeRecord, params: ApprovePatentParams
    ) -> ToolOutcome:
        location = place_for_charter(world, agent.agent_id, LOCATION_GOVERNMENT_COMPLEX)
        application = await world.charter.store.get_patent_application(params.application_id)
        if application is None:
            raise NotFoundError("patent_application", params.application_id)

        patent = await world.charter.patent_office.approve_application(
            params.application_id, agent.agent_id, location, world.tick()
        )

        definition = world.custom_tools.get(application.tool_name)
        if definition is None:
            raise NotFoundError("custom_tool_definition", application.tool_name)
        approved_definition = definition.model_copy(update={"approved": True})
        world.custom_tools[application.tool_name] = approved_definition

        if registry.get(application.tool_name) is None:
            registry.register(
                ToolSpec(
                    name=application.tool_name,
                    description=approved_definition.description,
                    params_model=build_params_model(application.tool_name, approved_definition.param_schema),
                    handler=build_composite_handler(registry, application.tool_name),
                    namespace=ToolNamespace.PUBLIC,
                    location_gate=None,
                    actor_requirements=ActorRequirements(),
                    is_primitive=False,
                )
            )

        event = WorldEvent(
            type="politics.patent.approved",
            tick=world.tick(),
            payload={
                "application_id": params.application_id,
                "patent_id": patent.patent_id,
                "tool_name": patent.tool_name,
            },
        )
        return ToolOutcome(
            result={"patent_id": patent.patent_id, "tool_name": patent.tool_name}, events=[event]
        )

    return ToolSpec(
        name="approve_patent",
        description="Approve a pending patent application, registering its tool.",
        params_model=ApprovePatentParams,
        handler=approve_patent_handler,
        namespace=ToolNamespace.GOVERNMENT,
        location_gate=LOCATION_GOVERNMENT_COMPLEX,
        actor_requirements=ActorRequirements(require_government_seat=True),
        is_primitive=False,
    )


def build_reject_patent_spec(registry: ToolRegistry) -> ToolSpec:
    class RejectPatentParams(BaseModel):
        application_id: str
        reason: str

    async def reject_patent_handler(
        world: WorldState, agent: AgentLifeRecord, params: RejectPatentParams
    ) -> ToolOutcome:
        location = place_for_charter(world, agent.agent_id, LOCATION_GOVERNMENT_COMPLEX)
        application = await world.charter.patent_office.reject_application(
            params.application_id, agent.agent_id, params.reason, location, world.tick()
        )
        event = WorldEvent(
            type="politics.patent.rejected",
            tick=world.tick(),
            payload={"application_id": params.application_id, "reason": params.reason},
        )
        return ToolOutcome(result={"application_id": application.application_id}, events=[event])

    return ToolSpec(
        name="reject_patent",
        description="Reject a pending patent application.",
        params_model=RejectPatentParams,
        handler=reject_patent_handler,
        namespace=ToolNamespace.GOVERNMENT,
        location_gate=LOCATION_GOVERNMENT_COMPLEX,
        actor_requirements=ActorRequirements(require_government_seat=True),
        is_primitive=False,
    )


def build_revoke_patent_spec(registry: ToolRegistry) -> ToolSpec:
    class RevokePatentParams(BaseModel):
        patent_id: str
        tool_name: str
        reason: str

    async def revoke_patent_handler(
        world: WorldState, agent: AgentLifeRecord, params: RevokePatentParams
    ) -> ToolOutcome:
        location = place_for_charter(world, agent.agent_id, LOCATION_GOVERNMENT_COMPLEX)
        patent = await world.charter.patent_office.revoke_patent(
            params.patent_id, agent.agent_id, params.reason, location, world.tick()
        )
        registry.tools.pop(params.tool_name, None)
        definition = world.custom_tools.get(params.tool_name)
        if definition is not None:
            world.custom_tools[params.tool_name] = definition.model_copy(update={"approved": False})
        event = WorldEvent(
            type="politics.patent.revoked",
            tick=world.tick(),
            payload={"patent_id": patent.patent_id, "tool_name": params.tool_name, "reason": params.reason},
        )
        return ToolOutcome(result={"patent_id": patent.patent_id}, events=[event])

    return ToolSpec(
        name="revoke_patent",
        description="Revoke a granted patent, deregistering its tool.",
        params_model=RevokePatentParams,
        handler=revoke_patent_handler,
        namespace=ToolNamespace.GOVERNMENT,
        location_gate=LOCATION_GOVERNMENT_COMPLEX,
        actor_requirements=ActorRequirements(require_government_seat=True),
        is_primitive=False,
    )


class DefineBusinessToolParams(BaseModel):
    business_id: str
    name: str
    description: str
    param_schema: dict[str, ParamType] = {}
    steps: list[ToolStep]
    returns: dict[str, Any] = {}


def build_define_business_tool_spec(registry: ToolRegistry) -> ToolSpec:
    async def define_business_tool_handler(
        world: WorldState, agent: AgentLifeRecord, params: DefineBusinessToolParams
    ) -> ToolOutcome:
        business = resolve_business(world, params.business_id)
        if business is None:
            raise NotFoundError("business", params.business_id, sorted(world.businesses))
        if business.owner_agent_id != agent.agent_id:
            raise ActorRequirementError(
                "define_business_tool", [f"{agent.agent_id} does not own {params.business_id}"]
            )
        if (clash := existing_tool_named_like(registry, world, params.name)) is not None:
            raise ToolValidationError(
                "define_business_tool", [f"a tool named {clash!r} already exists"]
            )
        validate_steps(
            registry, params.param_schema, params.steps, "define_business_tool",
            world=world, citable=await citable_tool_names(registry, world, agent.agent_id),
            returns=params.returns,
        )

        world.custom_tools[params.name] = CustomToolDefinition(
            tool_name=params.name,
            description=params.description,
            creator_agent_id=agent.agent_id,
            param_schema=params.param_schema,
            steps=params.steps,
            returns=params.returns,
            patent_application_id=None,
            approved=True,
            created_tick=world.tick(),
        )
        registry.register(
            ToolSpec(
                name=params.name,
                description=params.description,
                params_model=build_params_model(params.name, params.param_schema),
                handler=build_composite_handler(registry, params.name),
                namespace=ToolNamespace.BUSINESS,
                owner_id=params.business_id,
                location_gate=None,
                actor_requirements=ActorRequirements(),
                is_primitive=False,
            )
        )
        event = WorldEvent(
            type="business.trade_secret.defined",
            tick=world.tick(),
            payload={
                "business_id": params.business_id,
                "tool_name": params.name,
                "created_by_agent_id": agent.agent_id,
            },
        )
        return ToolOutcome(result={"tool_name": params.name}, events=[event])

    return ToolSpec(
        name="define_business_tool",
        description=(
            "Compose primitives into a private trade-secret tool for a business you own. "
            "No patent office, no fee, no review - live immediately, visible only to the "
            "business's owner and employees."
        ),
        params_model=DefineBusinessToolParams,
        handler=define_business_tool_handler,
        namespace=ToolNamespace.PUBLIC,
        location_gate=None,
        actor_requirements=ActorRequirements(min_age=DEFINE_TOOL_MIN_AGE_YEARS),
        is_primitive=False,
    )


class DefineOrganizationToolParams(BaseModel):
    org_id: str
    name: str
    description: str
    param_schema: dict[str, ParamType] = {}
    steps: list[ToolStep]
    returns: dict[str, Any] = {}


def build_define_organization_tool_spec(registry: ToolRegistry) -> ToolSpec:
    async def define_organization_tool_handler(
        world: WorldState, agent: AgentLifeRecord, params: DefineOrganizationToolParams
    ) -> ToolOutcome:
        organization = await world.charter.store.get_organization(params.org_id)
        if organization is None:
            raise NotFoundError("organization", params.org_id)
        if organization.founder_agent_id != agent.agent_id:
            raise ActorRequirementError(
                "define_organization_tool", [f"{agent.agent_id} did not found {params.org_id}"]
            )
        if (clash := existing_tool_named_like(registry, world, params.name)) is not None:
            raise ToolValidationError(
                "define_organization_tool", [f"a tool named {clash!r} already exists"]
            )
        validate_steps(
            registry, params.param_schema, params.steps, "define_organization_tool",
            world=world, citable=await citable_tool_names(registry, world, agent.agent_id),
            returns=params.returns,
        )

        world.custom_tools[params.name] = CustomToolDefinition(
            tool_name=params.name,
            description=params.description,
            creator_agent_id=agent.agent_id,
            param_schema=params.param_schema,
            steps=params.steps,
            returns=params.returns,
            patent_application_id=None,
            approved=True,
            created_tick=world.tick(),
        )
        registry.register(
            ToolSpec(
                name=params.name,
                description=params.description,
                params_model=build_params_model(params.name, params.param_schema),
                handler=build_composite_handler(registry, params.name),
                namespace=ToolNamespace.ORGANIZATION,
                owner_id=params.org_id,
                location_gate=None,
                actor_requirements=ActorRequirements(),
                is_primitive=False,
            )
        )
        event = WorldEvent(
            type="politics.organization_tool.defined",
            tick=world.tick(),
            payload={
                "org_id": params.org_id,
                "tool_name": params.name,
                "created_by_agent_id": agent.agent_id,
            },
        )
        return ToolOutcome(result={"tool_name": params.name}, events=[event])

    return ToolSpec(
        name="define_organization_tool",
        description=(
            "Compose primitives into a private tool for an organization you founded. "
            "No patent office, no fee, no review - live immediately, visible only to members."
        ),
        params_model=DefineOrganizationToolParams,
        handler=define_organization_tool_handler,
        namespace=ToolNamespace.PUBLIC,
        location_gate=None,
        actor_requirements=ActorRequirements(min_age=DEFINE_TOOL_MIN_AGE_YEARS),
        is_primitive=False,
    )


class DoInOrderParams(BaseModel):
    steps: list[ToolStep]
    returns: dict[str, Any] = {}


def build_do_in_order_spec(registry: ToolRegistry) -> ToolSpec:
    """A program, run now, in one call - with nothing filed.

    Runs 26, 27 and 28 made 0 programs in 662 resident turns. The residents
    thought in routines ("walk, harvest, walk back, stock the shelf. Rinse
    and repeat"), but a program existed only as a tool filed with `toolify`
    under a name and a parameter schema and called on a LATER decision -
    and later was literal, because the engine rebuilds its tool list at the
    start of a tick. One resident filed one; nobody ever called one.

    This is the same runtime with that detour removed, not a second one:
    the same `ToolStep` shape, the same `validate_steps` (whitelist, dry-run,
    control-flow checks, the call-ceiling floor), the same
    `program.run_program` under the same four ceilings, every step still
    dispatched through the registry. The handler names a `program_id` in
    its result, so the registry files the wrapper as step 0 exactly as it
    does for a filed composite, and PROG-03 counts it with no change.

    What it deliberately does not do: register anything, persist anything,
    or take parameters. A routine worth keeping, teaching or licensing is
    still `toolify`'s. And `is_primitive=False`, so no step may be another
    program - forty calls bounds ONE run, and a step that started a second
    run would be a second forty.
    """

    async def do_in_order_handler(
        world: WorldState, agent: AgentLifeRecord, params: DoInOrderParams
    ) -> ToolOutcome:
        try:
            validate_steps(
                registry, {}, params.steps, "do_in_order",
                world=world,
                citable=await citable_tool_names(registry, world, agent.agent_id),
                returns=params.returns,
            )
        except ToolValidationError as refused:
            refused.context = {
                **(refused.context or {}),
                **(await world.authoring_context(agent.agent_id)),
            }
            raise
        run, events = await program.run_program(
            registry, world, agent, "do_in_order", params.steps, {},
            returns=params.returns,
        )
        return ToolOutcome(result=run.as_result(), events=events)

    return ToolSpec(
        name="do_in_order",
        description=(
            "Run a short program now, in one call: a list of steps done in order, each a "
            "real action {tool_name, params}. A step may also carry `when`, `repeat` and "
            f"`bind`. `when` is one comparison ({', '.join(sorted(program.CONDITION_OPS))}); "
            "a step whose condition is false is skipped, not failed. `repeat` runs one "
            f"step up to {program.PROGRAM_MAX_REPEAT} times. `bind` names a step's result so "
            "a later step can read it as `@name` or `@name.field`, and a condition reads "
            "only what an earlier step already returned. A step is any action you can "
            "already take, including a tool you invented earlier; the ceilings below are "
            "shared by the whole nest, not one per layer. `returns` answers with what the "
            "steps found - a name (`@bound.field`) or one selector {from, where, take} - "
            "instead of the account of the run. Nothing is filed or kept - "
            "`toolify` keeps a program under a name. Each step is gated and charged "
            "exactly as it would be on its own and the call itself is free, so a run "
            f"costs the sum of its steps. One call makes at most {program.PROGRAM_MAX_CALLS} "
            f"steps and spends at most {program.PROGRAM_WALLET_SHARE_PERCENT}% of the "
            "sim-currency you held when it started; at either limit it stops and keeps "
            "what it did. If the world refuses a step, the whole call is refused and "
            "what its earlier steps were charged is given back."
        ),
        params_model=DoInOrderParams,
        handler=do_in_order_handler,
        namespace=ToolNamespace.PUBLIC,
        location_gate=None,
        actor_requirements=ActorRequirements(),
        is_primitive=False,
    )


def build_specs(registry: ToolRegistry) -> list[ToolSpec]:
    """Unlike primitives/*.py's build_specs(), this one takes the
    registry: define_tool and the patent-office actions all need to read
    `registry.primitive_names` or call `registry.register()` themselves
    (see the module docstring), so bootstrap.py calls this only after
    `registry.freeze_primitives()`.
    """
    return [
        build_define_tool_spec(registry),
        build_approve_patent_spec(registry),
        build_reject_patent_spec(registry),
        build_revoke_patent_spec(registry),
        build_define_business_tool_spec(registry),
        build_define_organization_tool_spec(registry),
        build_skillify_spec(registry),
        build_describe_primitives_spec(registry),
        build_toolify_spec(registry),
        build_do_in_order_spec(registry),
        build_revise_skill_spec(registry),
        build_revise_tool_spec(registry),
    ]


MAX_SKILL_NESTING = 2


class DefineSkillParams(BaseModel):
    name: str
    preconditions: str = ""
    steps: list[SkillStep]

    @model_validator(mode="before")
    @classmethod
    def _name_the_other_shape(cls, data: Any) -> Any:
        """The same confusion in the likelier direction: a resident writes
        a tool first, then a procedure. See ToolifyParams for the whole
        reasoning - the two shapes differ because one is executed and the
        other is read."""
        if isinstance(data, dict):
            steps = data.get("steps")
            if isinstance(steps, list) and any(
                isinstance(step, dict) and "tool_name" in step and "kind" not in step
                for step in steps
            ):
                raise ValueError(
                    "these are toolify's steps, not skillify's. A tool step is a real "
                    "call {tool_name, params}; a skill step is a plan entry "
                    "{order, kind, ref, note}, where kind is \"tool\", \"skill\" or "
                    "\"check\" and ref names what that step uses. Rewrite each step as "
                    "{\"order\": 1, \"kind\": \"tool\", \"ref\": \"<the tool>\"}."
                )
        return data
    business_id: str | None = None


def canonical_tool_ref(registry: ToolRegistry, ref: str) -> str:
    """The registry's own name for a tool an agent referred to.

    The world registers its tools bare - "move", "harvest", "fell_timber" -
    but an agent never sees them that way: the engine presents them
    namespaced, and every tool call in a session log reads "world:move".
    So a resident writing down a workflow wrote the name it had been shown,
    and `skillify` refused it against the name the registry keeps.

    Measured live: Marta Thorne filed a six-step gathering route as
    world_move, world_harvest, world_move, world_move, world_fell_timber,
    world_move, and lost all six steps - "no tool named 'world_move'" - for
    a workflow whose every step was a real tool she had already used that
    morning.

    Folds a leading namespace off, in either spelling, and ONLY when what
    is left is a tool that really exists. "fell_timber" is an exact hit and
    never reaches the split at all, so a real underscore in a tool name is
    never mistaken for a namespace.
    """
    if not ref or registry.get(ref) is not None:
        return ref
    for separator in (":", "_"):
        _, found, rest = ref.partition(separator)
        if found and rest and registry.get(rest) is not None:
            return rest
    return ref


def normalize_skill_steps(registry: ToolRegistry, steps: list[SkillStep]) -> None:
    """Rewrite each tool step to the registry's own name, in place.

    In place and before validation, because the corrected name has to reach
    what gets STORED - a skill filed as "world:move" would pass a lenient
    check and then never run.
    """
    for step in steps:
        if step.kind == "tool" and step.ref:
            step.ref = canonical_tool_ref(registry, step.ref)


def may_follow_skill(world: WorldState, agent_id: str, skill_name: str) -> bool:
    """May this resident walk that procedure at all?

    Public skills, yes. A business's trade secret, only its owner and its
    staff - the same set `teach_skill` checks before a lesson, kept in one
    predicate so the two doors cannot drift apart.
    """
    definition = world.skill_definitions.get(skill_name)
    if definition is None or definition.namespace != "business":
        return True
    business = world.businesses.get(definition.owner_id or "")
    if business is None:
        return False
    return agent_id == business.owner_agent_id or agent_id in business.employee_agent_ids


def _tool_names_inside(ref: str, names) -> list[str]:
    """Real tool names that appear as whole words inside `ref`.

    `craft:wheat->flour` holds `craft`; `move_to_farmland` holds `move`.
    Residents name a step by what it does, and the verb is usually in there.
    """
    import re

    known = set(names)
    out: list[str] = []
    for token in re.split(r"[^a-z0-9]+", (ref or "").lower()):
        if token in known and token not in out:
            out.append(token)
    return out


def _nearest_tools_hint(ref: str, names, limit: int = 6) -> str:
    """The tools closest to what was typed, rather than the first twelve.

    This sentence used to be `sorted(registry.tools)[:12]` - alphabetical,
    the same twelve for every mistake. Run 26: Farid Ravensworth filed a
    seven-step skill of the whole bread chain, naming the steps
    `craft:wheat->flour` and `cook:flour->bread`, and was told tools "are
    named like: admit_to_org, appoint_police, approve_patent, arrest,
    ask_for_a_child, auction_land ...". The two he needed were in his own
    refs. The most ambitious authoring of the run failed on a hint that
    could not have helped anybody.
    """
    import difflib

    candidates = sorted(names)
    picked = _tool_names_inside(ref, candidates)
    for close in difflib.get_close_matches((ref or "").lower(), candidates, n=limit, cutoff=0.5):
        if close not in picked:
            picked.append(close)
    if not picked:
        return "`describe_primitives` lists every tool by name"
    return "the nearest tools are: " + ", ".join(picked[:limit])


def validate_skill_steps(
    registry: ToolRegistry,
    world: WorldState,
    skill_name: str,
    steps: list[SkillStep],
    author_agent_id: str = "",
) -> list[str]:
    """Every problem with a proposed workflow, not just the first.

    Shared by `skillify` and `revise_skill` so the two cannot drift - a
    revision accepting steps the original filing would have refused is a hole
    straight through the guards, and the cheapest way to guarantee that never
    happens is one implementation.

    Aggregated per CONVENTIONS.md: an agent that misremembered three tool
    names is told all three at once rather than discovering them one failed
    call at a time.
    """
    problems: list[str] = []
    for step in steps:
        if step.kind == "check":
            continue
        if step.kind not in ("tool", "skill"):
            problems.append(f"step {step.order}: unknown kind {step.kind!r}")
        elif not step.ref:
            problems.append(f"step {step.order}: a {step.kind} step needs a ref")
        elif step.kind == "tool" and registry.get(step.ref) is None:
            problems.append(
                f"step {step.order}: no tool named {step.ref!r}"
                f" - {_nearest_tools_hint(step.ref, registry.tools)}"
            )
        elif step.kind == "skill":
            if step.ref == skill_name:
                problems.append(f"step {step.order}: a skill cannot call itself")
            elif step.ref not in world.skill_definitions:
                named = _tool_names_inside(step.ref, registry.tools)
                problems.append(
                    f"step {step.order}: no skill named {step.ref!r}"
                    + (
                        f" - {named[0]!r} is a tool, and a tool step is written "
                        f"kind 'tool', ref {named[0]!r}"
                        if named
                        else ""
                    )
                )
            elif not may_follow_skill(world, author_agent_id, step.ref):
                owner = world.skill_definitions[step.ref].owner_id
                problems.append(
                    f"step {step.order}: {step.ref!r} belongs to {owner} and you "
                    "do not work there, so you could never walk this workflow"
                )
    return problems


def _nesting_depth(world: WorldState, definition: SkillDefinition, seen: set[str]) -> int:
    """How many skills deep this workflow reaches, counting itself as 1."""
    deepest = 1
    for step in definition.steps:
        if step.kind != "skill" or not step.ref or step.ref in seen:
            continue
        child = world.skill_definitions.get(step.ref)
        if child is not None:
            deepest = max(deepest, 1 + _nesting_depth(world, child, seen | {step.ref}))
    return deepest


def build_skillify_spec(registry: ToolRegistry) -> ToolSpec:
    async def skillify_handler(
        world: WorldState, agent: AgentLifeRecord, params: DefineSkillParams
    ) -> ToolOutcome:
        clash = existing_skill_named_like(world, params.name)
        if clash is not None:
            raise ToolValidationError(
                "skillify", [f"a skill named {clash!r} already exists"]
            )
        if not params.steps:
            raise ToolValidationError("skillify", ["a skill needs at least one step"])

        normalize_skill_steps(registry, params.steps)
        problems = validate_skill_steps(
            registry, world, params.name, params.steps, agent.agent_id
        )
        if problems:
            raise ToolValidationError(
                "skillify", problems, context=await world.authoring_context(agent.agent_id)
            )

        discounts = discounted_tools(world, params.steps, params.name)

        governmental = any(
            (spec := registry.get(s.ref)) is not None
            and spec.actor_requirements.require_government_seat
            for s in params.steps
            if s.kind == "tool" and s.ref
        )

        namespace, owner_id = "public", None
        if params.business_id is not None:
            business = resolve_business(world, params.business_id)
            if business is None:
                raise NotFoundError("business", params.business_id, sorted(world.businesses))
            if (
                business.owner_agent_id != agent.agent_id
                and agent.agent_id not in business.employee_agent_ids
            ):
                raise ActorRequirementError(
                    "skillify",
                    [f"{agent.agent_id} neither owns nor works at {params.business_id}"],
                )
            if governmental:
                raise ToolValidationError(
                    "skillify",
                    ["a workflow that uses state power cannot be a trade secret"],
                )
            namespace, owner_id = "business", params.business_id

        definition = SkillDefinition(
            name=params.name,
            namespace=namespace,
            owner_id=owner_id,
            author_agent_id=agent.agent_id,
            preconditions=params.preconditions,
            steps=sorted(params.steps, key=lambda s: s.order),
            discounts_tools=discounts,
            governmental=governmental,
            status="PENDING_COUNCIL" if governmental else "ACTIVE",
            created_tick=world.tick(),
        )

        depth = _nesting_depth(world, definition, {params.name})
        if depth > MAX_SKILL_NESTING:
            raise ToolValidationError(
                "skillify",
                [f"workflow nests {depth} skills deep; the limit is {MAX_SKILL_NESTING}"],
            )

        amendment_id: str | None = None
        if governmental:
            charter = world.charter.store.current_charter()
            proposal = await world.charter.amendments.propose_amendment(
                field_name="standing_orders",
                new_value=tuple(charter.standing_orders) + (params.name,),
                title=f"standing order: {params.name}",
                rationale=params.preconditions or f"procedure filed by {agent.agent_id}",
                proposed_by_agent_id=agent.agent_id,
                now_tick=world.tick(),
            )
            amendment_id = proposal.proposal_id
            definition = definition.model_copy(update={"amendment_id": amendment_id})

        world.skill_definitions[params.name] = definition
        world.skills.setdefault(agent.agent_id, {}).setdefault(params.name, 1)

        event = WorldEvent(
            type="agent.skill.authored",
            tick=world.tick(),
            payload={
                "skill_name": params.name,
                "author_agent_id": agent.agent_id,
                "step_count": len(definition.steps),
                "discounts_tools": discounts,
                "governmental": governmental,
                "amendment_id": amendment_id,
                "namespace": namespace,
                "owner_id": owner_id,
                "steps_json": {
                    "name": params.name,
                    "preconditions": params.preconditions,
                    "steps": [step.model_dump() for step in definition.steps],
                },
            },
        )
        return ToolOutcome(
            result={
                "filed": True,
                "skill_name": params.name,
                "steps": len(definition.steps),
                "discounts_tools": discounts,
                "governmental": governmental,
                "status": definition.status,
                "amendment_id": amendment_id,
                "namespace": namespace,
                "note": (
                    "Walk it to find out whether it works. If it does not, "
                    "`revise_skill` changes the steps - a filing is a draft, "
                    "not a promise."
                ),
                **(await world.authoring_context(agent.agent_id)),
            },
            events=[event],
        )

    return ToolSpec(
        name="skillify",
        description=(
            "Write down a procedure you know as an ordered workflow, so you and anyone "
            "you teach can follow it. Each step is {order, kind, ref, note}, where kind "
            "is 'tool' (call this), 'skill' (do this whole other procedure), or 'check' "
            "(look at this before continuing, dispatching nothing) and ref names what it "
            "uses. A 'tool' step may name a tool you invented as well as an ordinary "
            "action, and a 'skill' step may name another procedure, so a workflow can be "
            "built out of the workflows and tools you already have. This differs from "
            "toolify's steps, which are real calls: a procedure is read, a tool is run. "
            "Free, immediate, no approval - unless a step uses a power only a "
            "government seat carries, in which case it is a standing order and goes to "
            "the council as an amendment. Repeating a workflow end to end is what makes "
            "you better at it, and better means cheaper. You do not need to get it "
            "right first time: this answers with every action you can use and what each "
            "costs you, whether it accepts the filing or refuses it, and `revise_skill` "
            "changes the steps afterwards. A draft you walk and then fix beats a "
            "procedure you never write."
        ),
        params_model=DefineSkillParams,
        handler=skillify_handler,
        namespace=ToolNamespace.PUBLIC,
        location_gate=None,
        actor_requirements=ActorRequirements(min_age=DEFINE_TOOL_MIN_AGE_YEARS),
        is_primitive=False,
    )


class DescribePrimitivesParams(BaseModel):
    contains: str | None = None


def _quoted_minimum(
    world: WorldState, name: str, agent_id: str, proficiency: int
) -> int:
    """What one call of `name` would cost this agent, floor.

    For a primitive this is the schedule's own answer. For an INVENTED
    tool the schedule has no row - it cannot hold one for a tool nobody
    has composed yet - so `cost_for` falls to `DEFAULT_RULE` and quotes
    5, which is a price the town does not charge: `dispatch` skips the
    wrapper entirely for a name in `world.custom_tools` (TOOL-06), and
    what the call costs is the sum of its steps.

    Summed with the SAME proficiency the caller has, because that is what
    the steps will be charged at, and a quote priced at somebody else's
    skill is a number nobody can plan against.
    """
    from world.ledger.action_costs import DEFAULT_SCHEDULE, ActionActor, proficiency_for

    if name not in world.custom_tools:
        return DEFAULT_SCHEDULE.cost_for(
            name, {}, ActionActor(agent_id, skill_proficiency=proficiency)
        )
    definition = world.custom_tools[name]
    total = 0
    for step in definition.steps:
        step_proficiency = proficiency_for(world, agent_id, step.tool_name)
        total += DEFAULT_SCHEDULE.cost_for(
            step.tool_name,
            {},
            ActionActor(agent_id, skill_proficiency=step_proficiency),
        )
    return total


def build_describe_primitives_spec(registry: ToolRegistry) -> ToolSpec:
    """The vocabulary, with prices.

    The one thing agents needed and did not have. `define_tool` and `skillify`
    both ask an agent to compose things it can name, and until now the only
    way to learn a name was to have been told it in a prompt - so composition
    was guesswork against half-remembered strings, and a filing that named a
    tool wrongly failed at validation with no way to discover the right one.

    Prices are included deliberately. An agent choosing between two ways to do
    the same job should be able to see which is cheaper before it commits, not
    after - that is what makes the skill discount something to reason about
    rather than a number that happens to it.
    """

    async def describe_primitives_handler(
        world: WorldState, agent: AgentLifeRecord, params: DescribePrimitivesParams
    ) -> ToolOutcome:
        from world.ledger.action_costs import DEFAULT_SCHEDULE, ActionActor, proficiency_for

        needle = (params.contains or "").lower()
        rows = []
        for name, spec in sorted(registry.tools.items()):
            if not await registry.is_visible(spec, world, agent.agent_id):
                continue
            if needle and needle not in name.lower() and needle not in spec.description.lower():
                continue
            proficiency = proficiency_for(world, agent.agent_id, name)
            rows.append(
                {
                    "name": name,
                    "description": spec.description,
                    "params": sorted(spec.params_model.model_fields.keys()),
                    "sim_cost_minimum": _quoted_minimum(
                        world, name, agent.agent_id, proficiency
                    ),
                    "your_proficiency": proficiency,
                    "requires_seat": spec.actor_requirements.require_government_seat,
                    "at_location": location_label(spec.location_gate),
                }
            )
        return ToolOutcome(result={"tools": rows, "count": len(rows)}, events=[])

    return ToolSpec(
        name="describe_primitives",
        description=(
            "List the actions you can actually take: the least each would cost YOU "
            "(your skill discount included), what it needs, and where you must be. "
            "Costs shown are the minimum - a bigger job costs more, and quote_action_cost "
            "prices one exact call."
        ),
        params_model=DescribePrimitivesParams,
        handler=describe_primitives_handler,
        namespace=ToolNamespace.PUBLIC,
        location_gate=None,
        actor_requirements=ActorRequirements(),
        is_primitive=False,
    )


class ToolifyParams(BaseModel):
    name: str
    description: str
    param_schema: dict[str, ParamType] = {}
    steps: list[ToolStep]
    returns: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _name_the_other_shape(cls, data: Any) -> Any:
        """Catch skillify's step shape and say so.

        An agent that has just written a skill and goes on to write a tool
        - the exact sequence this town is built to encourage - used to get
        "steps.0.tool_name: Field required", which names the missing field
        and never says the shape differs, which tool wants which, or why.
        And because a params-level refusal is raised before the handler,
        it is the one authoring refusal carrying no authoring_context, so
        the retry was as blind as the first try.
        """
        if isinstance(data, dict):
            steps = data.get("steps")
            if isinstance(steps, list) and any(
                isinstance(step, dict) and "tool_name" not in step and ("kind" in step or "ref" in step)
                for step in steps
            ):
                raise ValueError(
                    "these are skillify's steps, not toolify's. A skill step is a plan "
                    "entry {order, kind, ref, note} and its kind may be \"check\", which "
                    "dispatches nothing. A tool step is a real call {tool_name, params}, "
                    "because an invented tool RUNS its steps and a check cannot be run. "
                    "Rewrite each step as {\"tool_name\": \"<the tool>\", \"params\": {...}}."
                )
        return data


def build_toolify_spec(registry: ToolRegistry) -> ToolSpec:
    """Compose primitives into a tool that works immediately.

    NOT a thin wrapper over `define_tool`, which is why it exists. That tool
    is the PATENT path: it gates on standing inside the patent office, charges
    `patent_filing_fee_sim` (1,000 at genesis), and files the tool as
    `approved=False` - so an agent that invents something cannot use it until a
    seat-holder gets round to approving it.

    The plan dropped that for ordinary invention: *a village does not run a
    patent office; it keeps a book of rules, and the only things that go in the
    book are the things that bind everybody.* An invented tool binds nobody. It
    orders primitives its author could already call one at a time, so requiring
    permission is requiring permission to do - in a particular order - things
    nobody needs permission for.

    `define_tool` stays exactly as it is. An agent that wants the patent
    office's protection can still pay for it; this is the path for an agent
    that just wants to work faster. The same choice `define_business_tool`
    already offers a business owner, now available to a person.
    """

    async def toolify_handler(
        world: WorldState, agent: AgentLifeRecord, params: ToolifyParams
    ) -> ToolOutcome:
        context = await world.authoring_context(agent.agent_id)
        if (clash := existing_tool_named_like(registry, world, params.name)) is not None:
            raise ToolValidationError(
                "toolify",
                [f"a tool named {clash!r} already exists"],
                context=context,
            )
        if not params.steps:
            raise ToolValidationError(
                "toolify", ["a tool needs at least one step"], context=context
            )
        validate_steps(
            registry, params.param_schema, params.steps, "toolify", context,
            world=world, citable=await citable_tool_names(registry, world, agent.agent_id),
            returns=params.returns,
        )

        world.custom_tools[params.name] = CustomToolDefinition(
            tool_name=params.name,
            description=params.description,
            creator_agent_id=agent.agent_id,
            param_schema=params.param_schema,
            steps=params.steps,
            returns=params.returns,
            patent_application_id=None,
            approved=True,
            created_tick=world.tick(),
        )
        registry.register(
            ToolSpec(
                name=params.name,
                description=params.description,
                params_model=build_params_model(params.name, params.param_schema),
                handler=build_composite_handler(registry, params.name),
                namespace=ToolNamespace.PUBLIC,
                owner_id=agent.agent_id,
                location_gate=None,
                actor_requirements=ActorRequirements(),
                is_primitive=False,
            )
        )
        event = WorldEvent(
            type="agent.tool.invented",
            tick=world.tick(),
            payload={
                "tool_name": params.name,
                "inventor_agent_id": agent.agent_id,
                "step_count": len(params.steps),
                "steps": [s.tool_name for s in params.steps],
                "has_control_flow": program.has_control_flow(params.steps),
            },
        )
        return ToolOutcome(
            result={
                "filed": True,
                "tool_name": params.name,
                "steps": len(params.steps),
                "callable_now": True,
                "registry_version": registry.version,
                "note": (
                    f"`{params.name}` is callable now. Use it, and when it turns "
                    "out to be the wrong shape, `revise_tool` changes the steps."
                ),
                **(await world.authoring_context(agent.agent_id)),
            },
            events=[event],
        )

    return ToolSpec(
        name="toolify",
        description=(
            "Turn a sequence of actions you keep repeating into a single new tool, yours, "
            "callable immediately - no fee, no patent office, no approval. Each step is "
            "{tool_name, params} - a real call, because this tool RUNS its steps, which "
            "is why they are not skillify's {order, kind, ref} plan entries. A step may be "
            "any action you can already take, including a tool you invented earlier - so a "
            f"tool becomes a step of the next one, up to {MAX_TOOL_DEPTH} layers deep, and "
            "however deep it stands every step still ends in ordinary acts. This answers "
            "with all of them, priced for you, "
            "whether it builds the tool or refuses it, so you do not have to go and look "
            "first. Each step still costs what it always did and the wrapper itself is "
            "free, so one call of the new tool costs exactly the sum of its steps; what "
            "you save is deciding. "
            "A step may also carry `when`, `repeat` and `bind`. `when` is one comparison "
            f"({', '.join(sorted(program.CONDITION_OPS))}); a step whose condition is "
            "false is skipped, not failed. `repeat` runs one step up to "
            f"{program.PROGRAM_MAX_REPEAT} times. `bind` names a step's result so a later "
            "step can read it as `@name` or `@name.field` - and a condition reads only "
            "what an earlier step already returned, because there is no way to ask the "
            "world from inside one. "
            f"One call makes at most {program.PROGRAM_MAX_CALLS} steps in total and spends "
            f"at most {program.PROGRAM_WALLET_SHARE_PERCENT}% of the sim-currency you held "
            "when it began; at either ceiling it stops, keeps everything it already did, "
            "and tells you which one it met. "
            "`returns` says what one call ANSWERS with, so the tool hands back the thing "
            "you wanted rather than the whole account of what it did: each entry is either "
            "a name of something a step found (`@bound`, `@bound.field`, or `$your_param`) "
            "or one selector {from, where, take} - `from` names what to read, `where` is "
            "one comparison against each entry ({field, op, value}, the same comparisons as "
            "`when`), and `take` is all, first or count. A list is read entry by entry, and "
            "an answer of shop-to-rows is read as its rows. "
            "`revise_tool` changes the steps later, so a first version that turns out "
            "wrong costs you nothing but the filing."
        ),
        params_model=ToolifyParams,
        handler=toolify_handler,
        namespace=ToolNamespace.PUBLIC,
        location_gate=None,
        actor_requirements=ActorRequirements(min_age=DEFINE_TOOL_MIN_AGE_YEARS),
        is_primitive=False,
    )


class ReviseSkillParams(BaseModel):
    name: str
    preconditions: str | None = None
    steps: list[SkillStep]
    because: str = ""


def build_revise_skill_spec(registry: ToolRegistry) -> ToolSpec:
    """Replace a workflow's steps, keeping every earlier version.

    Only the author may revise a trade skill. Not a permission system - it is
    the same rule that makes provenance meaningful: if anyone could rewrite
    "clearing a stand", the name would stop referring to one agent's idea of
    the job and the town's knowledge would stop being attributable to anyone.

    Proficiency SURVIVES a revision, for the author and for everyone taught.
    An agent that has walked a procedure sixty times and then improves one
    step has not become a beginner; treating a revision as a reset would make
    improving something you are good at strictly worse than leaving it alone,
    which is exactly backwards.

    A governmental workflow cannot be revised here at all. It is a standing
    order: it went into the book of rules as an amendment and it comes out the
    same way, so revising it is the council's business, not its author's.
    """

    async def revise_skill_handler(
        world: WorldState, agent: AgentLifeRecord, params: ReviseSkillParams
    ) -> ToolOutcome:
        existing = world.skill_definitions.get(params.name)
        if existing is None:
            raise NotFoundError("skill", params.name)
        if existing.author_agent_id != agent.agent_id:
            raise ActorRequirementError(
                "revise_skill",
                [f"{params.name!r} was written by {existing.author_agent_id}, not you"],
            )
        if existing.governmental:
            raise ActorRequirementError(
                "revise_skill",
                [
                    f"{params.name!r} is a standing order - it changes by amendment, "
                    "not by its author"
                ],
            )

        normalize_skill_steps(registry, params.steps)
        problems = validate_skill_steps(
            registry, world, params.name, params.steps, agent.agent_id
        )
        if problems:
            raise ToolValidationError(
                "revise_skill",
                problems,
                context=await world.authoring_context(agent.agent_id),
            )

        discounts = discounted_tools(world, params.steps, params.name)
        if any(
            (spec := registry.get(s.ref)) is not None
            and spec.actor_requirements.require_government_seat
            for s in params.steps
            if s.kind == "tool" and s.ref
        ):
            raise ToolValidationError(
                "revise_skill",
                ["a revision cannot add a step that needs a government seat"],
            )

        previous = existing.model_copy(update={"superseded": []})
        revised = existing.model_copy(
            update={
                "preconditions": (
                    params.preconditions if params.preconditions is not None
                    else existing.preconditions
                ),
                "steps": sorted(params.steps, key=lambda s: s.order),
                "discounts_tools": discounts,
                "version": existing.version + 1,
                "superseded": [*existing.superseded, previous],
                "revised_tick": world.tick(),
            }
        )
        world.skill_definitions[params.name] = revised

        for key in [k for k in world.skill_progress if k.endswith(f"::{params.name}")]:
            del world.skill_progress[key]

        event = WorldEvent(
            type="agent.skill.revised",
            tick=world.tick(),
            payload={
                "skill_name": params.name,
                "author_agent_id": agent.agent_id,
                "version": revised.version,
                "because": params.because,
                "step_count": len(revised.steps),
                "steps_json": {
                    "name": params.name,
                    "preconditions": revised.preconditions,
                    "version": revised.version,
                    "because": params.because,
                    "steps": [s.model_dump() for s in revised.steps],
                },
            },
        )
        return ToolOutcome(
            result={
                "skill_name": params.name,
                "version": revised.version,
                "steps": len(revised.steps),
                "your_proficiency_kept": world.skills.get(agent.agent_id, {}).get(params.name, 0),
            },
            events=[event],
        )

    return ToolSpec(
        name="revise_skill",
        description=(
            "Rewrite the steps of a procedure you wrote, when you have learned a better "
            "way. Earlier versions are kept. You and anyone you taught keep the "
            "proficiency you earned - improving something you are good at should not "
            "make you a beginner again."
        ),
        params_model=ReviseSkillParams,
        handler=revise_skill_handler,
        namespace=ToolNamespace.PUBLIC,
        location_gate=None,
        actor_requirements=ActorRequirements(min_age=DEFINE_TOOL_MIN_AGE_YEARS),
        is_primitive=False,
    )


class ReviseToolParams(BaseModel):
    name: str
    description: str | None = None
    steps: list[ToolStep]
    because: str = ""


def build_revise_tool_spec(registry: ToolRegistry) -> ToolSpec:
    """Change what an invented tool does, keeping what it used to do.

    Only tools filed through `toolify` - the ungated path. A patented
    `define_tool` filing is deliberately excluded: its whole value is that the
    disclosure on record is what the tool actually does, and letting the holder
    silently swap the steps behind an approved patent would make the patent
    office's approval refer to something that no longer exists.

    The registry spec is rebuilt rather than mutated, because `ToolSpec` is
    frozen and because rebuilding bumps `registry.version` - which is what
    makes every engine refetch. A revised tool whose engines kept calling the
    old shape would be the Part B blocker again, one layer down.
    """

    async def revise_tool_handler(
        world: WorldState, agent: AgentLifeRecord, params: ReviseToolParams
    ) -> ToolOutcome:
        existing = world.custom_tools.get(params.name)
        if existing is None:
            raise NotFoundError("custom_tool", params.name)
        if existing.creator_agent_id != agent.agent_id:
            raise ActorRequirementError(
                "revise_tool",
                [f"{params.name!r} was invented by {existing.creator_agent_id}, not you"],
            )
        if existing.patent_application_id is not None:
            raise ActorRequirementError(
                "revise_tool",
                [
                    f"{params.name!r} is filed at the patent office - its disclosed steps "
                    "are what was approved, and cannot be swapped"
                ],
            )
        validate_steps(
            registry, existing.param_schema, params.steps, "revise_tool",
            world=world, citable=await citable_tool_names(registry, world, agent.agent_id),
            defining=frozenset({params.name}),
            returns=existing.returns,
        )

        superseded = list(existing.superseded_steps) + [
            [step.model_dump() for step in existing.steps]
        ]
        world.custom_tools[params.name] = existing.model_copy(
            update={
                "steps": params.steps,
                "description": params.description or existing.description,
                "version": existing.version + 1,
                "superseded_steps": superseded,
                "revised_tick": world.tick(),
            }
        )
        spec = registry.get(params.name)
        registry.tools.pop(params.name, None)
        registry.register(
            ToolSpec(
                name=params.name,
                description=params.description or (spec.description if spec else ""),
                params_model=build_params_model(params.name, existing.param_schema),
                handler=build_composite_handler(registry, params.name),
                namespace=spec.namespace if spec else ToolNamespace.PUBLIC,
                owner_id=spec.owner_id if spec else agent.agent_id,
                location_gate=None,
                actor_requirements=ActorRequirements(),
                is_primitive=False,
            )
        )

        event = WorldEvent(
            type="agent.tool.revised",
            tick=world.tick(),
            payload={
                "tool_name": params.name,
                "inventor_agent_id": agent.agent_id,
                "version": existing.version + 1,
                "because": params.because,
                "steps": [s.tool_name for s in params.steps],
            },
        )
        return ToolOutcome(
            result={
                "tool_name": params.name,
                "version": existing.version + 1,
                "registry_version": registry.version,
                **(await world.authoring_context(agent.agent_id)),
            },
            events=[event],
        )

    return ToolSpec(
        name="revise_tool",
        description=(
            "Change the steps of a tool you invented. The old steps are kept. Only for "
            "tools you made with toolify - a patented tool's disclosed steps are what "
            "was approved and cannot be swapped."
        ),
        params_model=ReviseToolParams,
        handler=revise_tool_handler,
        namespace=ToolNamespace.PUBLIC,
        location_gate=None,
        actor_requirements=ActorRequirements(min_age=DEFINE_TOOL_MIN_AGE_YEARS),
        is_primitive=False,
    )
