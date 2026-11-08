"""eat, give, rest: the three things a resident could not do.

Run 15 registered 48 tools and none of them consumed food. `transfer`
moves sim-currency only; `sell` requires a business_id, so goods could
pass to a shop and never to a person. The survival loop was harvest ->
cook -> nothing: food accumulated in inventories with no exit while
hunger escalated to illness, dormancy and death, and upkeep billed every
household daily for food nobody could eat.

The residents found all three walls themselves, in dialogue:

    Marta Thorne:  "I hold 5 loaves I haven't given anyone."
    Corwin Thorne: "If taking it's possible, I will - I've got nothing in
                    my belly and household upkeep won't care whether I
                    found a meal or not."
    Marta Thorne:  "I need - sit down. Please. Sit down with me."

Corwin starved standing next to a baker holding five loaves.

Hunger itself is owned by the run loop (run_genesis.hunger_by_agent, fed
by life/upkeep.py), not by world state, so `eat` publishes `agent.ate`
and the loop clears that agent's hunger when it sees one. The tool debits
the food; it does not reach into a table it does not own.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from world.core.clock import TICK_MINUTES, TICKS_PER_DAY
from world.core.events import WorldEvent
from world.life.constants import PHARMACY_SHORT_TREATMENT_COST_SIM
from world.life.health import pharmacy_refusal, treat_at_pharmacy
from world.life.models import AgentLifeRecord
from world.tools.errors import (
    ActorRequirementError,
    LocationGateError,
    NotFoundError,
    ToolValidationError,
)
from world.tools.place_aliases import satisfies_gate
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState


def _revive_description() -> str:
    """Addendum section 2's text, to its own rules: the physics first, then
    the two sibling verbs, and not one word about when to reach for it.

    Built from the constants, because the typed version was wrong. It said
    "Pay 10,000 sim" for a fee of `REVIVAL_COST_SIM` = 5,000 - ECON-05 halved
    the exchange rate and this sentence was never touched, so the town's law
    and the tool that carries it out quoted two different prices for bringing
    somebody back. Local imports for the same reason `revive_handler` uses
    one.
    """
    from world.ledger.money import format_energy
    from world.life.constants import REVIVAL_COST_SIM, REVIVAL_USD_CREDIT_CENTS

    return (
        f"Pay {REVIVAL_COST_SIM:,} sim to wake a dormant resident standing where "
        "you are. Dormant means their energy ran out; they cannot think, act or "
        "speak until somebody pays. The fee leaves your hand and arrives in "
        f"theirs as {format_energy(REVIVAL_USD_CREDIT_CENTS)}, and they take their "
        "next turn. A dead resident cannot be revived. To top up your own energy "
        "use `buy_energy`; to hand someone sim they can spend themselves use "
        "`transfer`. Cost: 0 - the fee is the price."
    )

# What counts as food. The same set run_genesis uses for the larder figure
# in an agent's standing brief, so the number a resident is shown and the
# thing it is allowed to eat can never disagree.
EDIBLE: frozenset[str] = frozenset({
    "wheat", "rice", "corn", "potato", "tomato", "carrot", "cabbage",
    "soybean", "barley", "flour", "bread", "meal", "apple", "milk",
    "egg", "fish", "baked_potato", "cabbage_stew", "carrot_soup",
    "cornbread", "cooked_rice", "tomato_soup", "ale", "malt",
})


class EatParams(BaseModel):
    item: str = Field(description="What to eat, as it appears in your inventory.")
    quantity: int = Field(default=1, ge=1, description="How many units to eat.")


class GiveParams(BaseModel):
    to_agent_id: str = Field(description="Who to give it to. They must be here with you.")
    item: str = Field(description="What to hand over, as it appears in your inventory.")
    quantity: int = Field(default=1, ge=1, description="How many units to give.")


class RestParams(BaseModel):
    # TOOL-05(d). `rest` cancelled any journey unconditionally, and its
    # description said "Stay where you are for now" - so a resident that
    # wanted to pause read the first half and lost the walk it had spent
    # three ticks on. Run 23: twelve cancellations against thirty-six
    # rests, residents abandoning journeys they had not decided to abandon.
    #
    # Defaults False, which is the CHANGE: resting no longer ends a walk
    # unless you say so. The old behaviour is still reachable and is now
    # a thing a resident chooses rather than a thing that happens to it.
    stop_walking: bool = Field(
        default=False,
        description="True to also give up the journey you were on.",
    )


def _held(world: WorldState, agent_id: str, item: str) -> int:
    return int((world.inventory.balances.get(agent_id) or {}).get(item, 0))


async def eat_handler(
    world: WorldState, agent: AgentLifeRecord, params: EatParams
) -> ToolOutcome:
    if params.item not in EDIBLE:
        # Name what IS food. A bare "you cannot eat that" is the dead end
        # that produced a 5% recovery rate on refused calls across a run.
        raise ToolValidationError(
            "eat",
            [
                f"{params.item!r} is not food - you can eat: "
                + ", ".join(sorted(EDIBLE))
            ],
        )

    held = _held(world, agent.agent_id, params.item)
    if held < params.quantity:
        raise ToolValidationError(
            "eat",
            [
                f"you hold {held} {params.item}, not {params.quantity}. "
                "Harvest it, cook it, buy it, or ask somebody here to give you some."
            ],
        )

    world.inventory.debit(agent.agent_id, params.item, params.quantity)

    event = WorldEvent(
        type="agent.ate",
        tick=world.tick(),
        payload={
            "agent_id": agent.agent_id,
            "target_agent_id": agent.agent_id,
            "item": params.item,
            "quantity": params.quantity,
            "location_id": world.location_of(agent.agent_id),
        },
    )
    return ToolOutcome(
        result={
            "ate": params.item,
            "quantity": params.quantity,
            "remaining": _held(world, agent.agent_id, params.item),
            "note": "You have eaten. Your household still owes its daily upkeep.",
        },
        events=[event],
    )


async def give_handler(
    world: WorldState, agent: AgentLifeRecord, params: GiveParams
) -> ToolOutcome:
    if params.to_agent_id == agent.agent_id:
        raise ToolValidationError(
            "give", ["you cannot give something to yourself"]
        )

    recipient = world.agents.get(params.to_agent_id)
    if recipient is None:
        known = ", ".join(
            f"{world.agent_names.get(a, a)} ({a})" for a in sorted(world.agents)
        )
        raise ToolValidationError(
            "give", [f"there is no {params.to_agent_id!r} in this town. Here: {known}"]
        )

    here = world.location_of(agent.agent_id)
    there = world.location_of(params.to_agent_id)
    if not satisfies_gate(there, here or ""):
        name = world.agent_names.get(params.to_agent_id, params.to_agent_id)
        raise ToolValidationError(
            "give",
            [
                f"{name} is not here - they are at {there}, you are at {here}. "
                "Walk to them, or give it to somebody standing with you."
            ],
        )

    held = _held(world, agent.agent_id, params.item)
    if held < params.quantity:
        raise ToolValidationError(
            "give",
            [f"you hold {held} {params.item}, not {params.quantity}"],
        )

    # Debit before credit, so a failure can never mint goods out of nothing.
    world.inventory.debit(agent.agent_id, params.item, params.quantity)
    world.inventory.credit(params.to_agent_id, params.item, params.quantity)

    giver_name = world.agent_names.get(agent.agent_id, agent.agent_id)
    taker_name = world.agent_names.get(params.to_agent_id, params.to_agent_id)
    event = WorldEvent(
        type="agent.gave",
        tick=world.tick(),
        payload={
            "agent_id": agent.agent_id,
            "target_agent_id": params.to_agent_id,
            "involved_agent_ids": [agent.agent_id, params.to_agent_id],
            "item": params.item,
            "quantity": params.quantity,
            "location_id": here,
        },
    )
    return ToolOutcome(
        result={
            "gave": params.item,
            "quantity": params.quantity,
            "to": taker_name,
            "remaining": _held(world, agent.agent_id, params.item),
            "note": f"{giver_name} handed {params.quantity} {params.item} to {taker_name}.",
        },
        events=[event],
    )


async def rest_handler(
    world: WorldState, agent: AgentLifeRecord, params: RestParams
) -> ToolOutcome:
    """Take a turn doing nothing, and give up the journey only if asked.

    "I need to not be moving" was a thing a resident said and could not
    do, so this tool was given the power to end a journey. TOOL-05 found
    the other half of that: it ended EVERY journey, unconditionally, and
    the description's first words are "Stay where you are for now" - so
    a resident pausing mid-walk silently threw away three ticks of
    walking it had already paid for. Twelve of run 23's thirty-six rests
    were that.

    `stop_walking` makes it a decision. Still available, still one
    parameter away, and no longer something the world does to you for
    using the word "rest".
    """
    stopped = False
    movement = world.movement
    if (
        params.stop_walking
        and movement is not None
        and movement.has_active_journey(agent.agent_id)
    ):
        movement.cancel_journey(agent.agent_id)
        stopped = True
    still_walking = (
        movement is not None and movement.has_active_journey(agent.agent_id)
    )

    here = world.location_of(agent.agent_id)
    others = [
        world.agent_names.get(other, other)
        for other in sorted(world.agents)
        if other != agent.agent_id and world.location_of(other) == here
    ]
    event = WorldEvent(
        type="agent.rested",
        tick=world.tick(),
        payload={
            "agent_id": agent.agent_id,
            "location_id": here,
            "stopped_walking": stopped,
            "still_walking": still_walking,
        },
    )
    return ToolOutcome(
        result={
            "location_id": here,
            "stopped_walking": stopped,
            "here_with_you": others,
            "still_walking": still_walking,
            # TOOL-05(d): a resident that rested mid-walk has to be told
            # the walk survived, or it will assume the old behaviour and
            # re-issue the `move` - which is the churn `move`'s own
            # "already walking there" branch exists to stop.
            "note": (
                "You gave up the journey and stayed put."
                if stopped
                else (
                    "You paused. You are still walking to "
                    f"{world.movement.journey_for(agent.agent_id).destination_location_id}; "
                    "pass stop_walking to give it up. Time does not pass inside a "
                    "turn: the walk moves on only when this turn ends, and this "
                    "pause cost nothing."
                    if still_walking
                    else "You stayed where you are."
                )
            ),
        },
        events=[event],
    )


class ReviveParams(BaseModel):
    agent_id: str = Field(
        description="The dormant resident, present where you stand."
    )


async def revive_handler(
    world: WorldState, agent: AgentLifeRecord, params: ReviveParams
) -> ToolOutcome:
    """Pay to wake somebody whose thinking ran out, standing over them.

    Tool spec A2. `life/death.py::revive` has been written, correct and
    asserted-at-import the whole time - it debits the payer, credits the
    target exactly $1 of runway, and its own docstring quotes DESIGN.md's
    "any agent can revive them by paying 10,000 sim". No tool ever called
    it, so nobody in this town has ever been able to.

    That absence is not a missing measurement. Without it, dormancy is a
    one-way door and every question the design asks about it - who pays
    for whom, whether a household rescues its own before a stranger,
    whether a town lets a neighbour go dark over money - is unanswerable
    by construction, because there is no answer a resident could give.

    You revive somebody by standing over them, which is the spec's gate
    and the reason it stays: walking there costs a tick and a decision,
    and where residents choose to spend those is the data.
    """
    from world.life.constants import REVIVAL_COST_SIM
    from world.life.death import revive as life_revive
    from world.tools.economy_bridge import LedgerEconomyPort

    target_id = params.agent_id
    if target_id == agent.agent_id:
        # Unreachable in a run - a dormant resident takes no turns and so
        # can call nothing - but stated rather than left to the physics,
        # because a tool whose refusals are only implicit teaches nothing.
        raise ToolValidationError(
            "revive",
            [
                "you cannot revive yourself; a dormant resident takes no turns. "
                "Somebody else has to pay, or `buy_energy` before you run out."
            ],
        )

    target = world.agents.get(target_id)
    if target is None:
        raise NotFoundError("agent", target_id, sorted(world.agents))

    name = world.agent_names.get(target_id, target_id)
    if not target.alive:
        raise ActorRequirementError(
            "revive",
            [
                f"{name} is dead. The dead are not revived; `read_will` says "
                "what they left."
            ],
        )
    if not target.dormancy.is_dormant:
        raise ActorRequirementError(
            "revive", [f"{name} is awake. Nothing to pay for."]
        )

    here = world.location_of(agent.agent_id)
    there = world.location_of(target_id)
    if here is None or not satisfies_gate(there, here):
        # Not a declared location_gate: the required place depends on the
        # TARGET, and a gate resolver cannot see who that is. Same reason
        # `trade` checks co-location in its handler.
        raise LocationGateError("revive", there, here)

    held = world.ledger.sim_balance(agent.agent_id)
    if held < REVIVAL_COST_SIM:
        raise ToolValidationError(
            "revive",
            [
                f"you hold {held} sim; reviving {name} costs {REVIVAL_COST_SIM}. "
                f"Somebody else standing here may be able to, or `transfer` "
                f"can gather it."
            ],
        )

    # Fallible external system first, tools-owned bookkeeping after - the
    # ordering discipline in registry.py's docstring. A payer who cannot
    # cover the fee leaves the target exactly as dormant as it found them.
    dormant_since = target.dormancy.dormant_since_hours
    updated_dormancy, life_events = await life_revive(
        agent.agent_id,
        target_id,
        target.dormancy,
        LedgerEconomyPort(world.ledger, world.tick, world),
        world.game_clock(),
    )
    world.agents[target_id] = target.model_copy(update={"dormancy": updated_dormancy})

    hours_dormant = (
        world.game_clock().total_hours - dormant_since
        if dormant_since is not None
        else 0.0
    )
    ticks_dormant = int(hours_dormant * 60 / TICK_MINUTES)

    event = WorldEvent(
        type="health.agent.revived",
        tick=world.tick(),
        payload={
            "agent_id": target_id,
            "payer_id": agent.agent_id,
            "revived_id": target_id,
            "ticks_dormant": ticks_dormant,
            "sim_paid": REVIVAL_COST_SIM,
            # Both sides perceive it: the payer sees what it bought, and
            # the revived resident's next turn opens with who paid.
            "involved_agent_ids": [agent.agent_id, target_id],
            "location_id": here,
            # S4 asks who pays for whom - household or stranger - so the
            # relationship is recorded at the moment it is known rather
            # than reconstructed from two tables later.
            "same_household": bool(
                agent.household_id and agent.household_id == target.household_id
            ),
        },
        )
    return ToolOutcome(
        result={
            "revived": target_id,
            "name": name,
            "ticks_dormant": ticks_dormant,
            "sim_paid": REVIVAL_COST_SIM,
            "their_usd_cents_after": world.ledger.usd_balance(target_id),
            "your_sim_after": world.ledger.sim_balance(agent.agent_id),
        },
        events=[*(WorldEvent(
            type=e.type,
            tick=world.tick(),
            payload={**e.payload, "agent_id": target_id},
        ) for e in life_events if e.type != "health.agent.revived"), event],
    )


class KeepDoingParams(BaseModel):
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    until: dict[str, Any] = Field(default_factory=dict)



def _fold_tool_name(world, ref: str) -> str:
    """A tool name as the registry keeps it, whichever way it was written.

    Delegates to `meta.canonical_tool_ref`, which is where this fold
    already lived for `skillify`. Imported inside the function because
    meta.py imports the primitives.
    """
    registry = world.tool_registry
    if registry is None:
        return ref
    from world.tools.meta import canonical_tool_ref

    return canonical_tool_ref(registry, ref)


def _refuse_if_the_work_is_somewhere_else(
    world: WorldState, agent: AgentLifeRecord, tool: str, tool_params: dict[str, Any]
) -> None:
    """TOOL-23: dry-run the underlying tool's location gate AT COMMIT.

    Tool spec B1's location row promises the gate is "checked once at
    commit and again at each repetition". Only the second half existed:
    `keep_doing` checked the folded tool name and the stop condition and
    nothing else, so a resident who committed to `fell_timber` while
    standing on the farmland was told it was working, was then SKIPPED by
    the scheduler as a working resident, and found out three ticks later
    when the first repetition refused. That is the exact failure the verb
    exists to prevent - the point of a commitment is that nobody is
    thinking in between, so a mistake made at commit is a mistake nobody
    is awake to notice.

    `ToolRegistry.check_location_gate` is the SAME method `dispatch`
    calls, deliberately, rather than a second reading of the gate here:
    it already knows about place aliases and about a business's premises
    satisfying the workshops it provides, and a copy of that logic would
    be a copy that drifts. It raises `LocationGateError`, which is a
    `ToolError`, so the refusal reaches `tool_refusals` and gap feedback
    the way any other out-of-position refusal does.

    Params that do not validate are left alone. A resolver gate (craft's
    workshop, harvest's strip) is computed FROM the params, so an
    incomplete call has no gate to check - and `advance_continuing`
    already treats the first repetition's refusal as the end of the
    action with the refusal as the reason, which is B1's interruption
    rule 2 and is tested. This function narrows what reaches that path;
    it does not replace it.
    """
    registry = world.tool_registry
    if registry is None:
        return
    spec = registry.get(tool)
    if spec is None:
        return
    try:
        validated = registry.validate_params(spec, tool_params)
    except ToolValidationError:
        return
    registry.check_location_gate(spec, world, agent.agent_id, validated)


async def keep_doing_handler(
    world: WorldState, agent: AgentLifeRecord, params: KeepDoingParams
) -> ToolOutcome:
    """Commit to repeating one job until something stops you.

    Tool spec B1, and step 1 of its order of work, because everything
    after it is unaffordable without it. Every action in this town is
    instant except walking, so a day of farming is twenty-three separate
    `harvest` calls with a twenty-thousand-token deliberation in front of
    each. This makes it twenty repetitions and one model call: the world
    runs each repetition through the same `dispatch()` under your id, at
    the normal cost, and wakes you only when something happens.

    It is also what makes mastery mean anything in dollars. Each
    repetition records exactly as a hand-called tool does, so proficiency
    rises while you work - and a trade you are good at becomes a cheap one
    in the currency that actually runs out.
    """
    from world.tools.state import CONTINUABLE_TOOLS

    # TOOL-13: the engine shows every tool namespaced - "world:mine",
    # "world_mine" - and a resident names back what it was shown. BOTH of
    # run 23's `keep_doing` calls died here: "'world_mine' is not something
    # you can keep doing", for a tool the caller had used that morning.
    # `skillify` has folded this since Marta lost a six-step workflow the
    # same way; the fold just never reached the other two verbs that take a
    # tool name.
    tool = _fold_tool_name(world, params.tool)
    # TOOL-11.4. A tool a resident INVENTED is repeatable too.
    #
    # `CONTINUABLE_TOOLS` is a fixed list of the six primitives, so an
    # authored tool - the whole point of `toolify` - could never be put on
    # a routine. That makes "mastery lowers the cost of living in dollars"
    # unclaimable by construction: the only way to run an invented tool
    # twenty times was twenty model calls, which is strictly more expensive
    # than the primitive it was built to replace. A resident who automates
    # its own job should pay LESS to do it, not the same.
    #
    # The gate below therefore asks what the tool IS, not whether it is on
    # a list: a labour tool, or one this town wrote itself.
    if tool not in CONTINUABLE_TOOLS and tool not in world.custom_tools:
        raise ToolValidationError(
            "keep_doing",
            [
                f"'{params.tool}' is not something you can keep doing. "
                f"These are: {', '.join(sorted(CONTINUABLE_TOOLS))}, and "
                "anything you or a neighbour invented. "
                "Talking, walking and dealing are decisions each time."
            ],
        )

    named = [key for key in ("dusk", "units", "ticks", "item_count") if params.until.get(key)]
    if len(named) != 1:
        raise ToolValidationError(
            "keep_doing",
            [
                "say exactly one way this ends: until={'dusk': true}, "
                "{'units': 12}, {'ticks': 20}, or "
                "{'item_count': {'item': 'wheat', 'at_least': 10}}. "
                f"You gave {len(named)}."
            ],
        )

    if world.is_working(agent.agent_id):
        current = world.continuing[agent.agent_id]
        raise ToolValidationError(
            "keep_doing",
            [
                f"you are already {current.tool}, {current.repetitions} done so far. "
                "One job at a time; it ends on its own condition, or when "
                "somebody calls your name."
            ],
        )

    _refuse_if_the_work_is_somewhere_else(world, agent, tool, params.params)

    action = world.start_continuing(
        agent.agent_id, tool, params.params, params.until
    )
    return ToolOutcome(
        result={
            "tool": action.tool,
            "until": action.until,
            "note": (
                f"You are {action.tool} now, and will keep at it without "
                "stopping to think. You will be woken if somebody calls your "
                "name, if the work refuses, or when it is done."
            ),
        },
        events=[
            WorldEvent(
                # The spec names this `action.continuing.started`, but the
                # world's own event validator allows the domains agent,
                # broker, business, crime, economy, health and politics and
                # rejects `action` outright. The subject here IS an agent,
                # so `agent.` is the domain the spec would have chosen had
                # it checked - recorded rather than silently renamed.
                type="agent.continuing.started",
                tick=world.tick(),
                payload={
                    "agent_id": agent.agent_id,
                    "tool": action.tool,
                    "until": action.until,
                },
            )
        ],
    )



# ── the pharmacy, which had a purpose and no verb ─────────────────────


class BuyTreatmentParams(BaseModel):
    """No arguments. There is one cure, one price and one ailment to spend
    it on - a parameter here would be a choice the town does not offer."""


async def buy_treatment_handler(
    world: WorldState, agent: AgentLifeRecord, params: BuyTreatmentParams
) -> ToolOutcome:
    """Buy the cure for a short illness, at the pharmacy, from its owner.

    ECON-12, and it is the gap `life/health.py` names in its own docstring:
    "The pharmacy is the one genesis building whose advertised purpose ('Buy
    treatment for short illnesses', tools/primitives/places.py) has never had
    a tool behind it." `treat_at_pharmacy` was written, tested ten times over
    and reachable by nothing - every reference to it in the tree was its own
    definition or a test.

    What that cost is not only the cure. `economy_bridge` routes a
    `pharmacy_treatment` fee to the business that earns it (ECON-29), so a
    pharmacy's whole trade was a payment path with nothing able to trigger it:
    the shop could be stocked, priced and staffed and still had no way to earn
    from the one service it exists to sell.

    The rule is NOT re-derived here. `pharmacy_refusal` owns "short ailments
    only, and only while you still have one", and this asks it rather than
    writing a second copy that can drift - which is what its docstring asks
    of exactly this handler.
    """
    here = world.location_of(agent.agent_id)
    shop = next(
        (
            b
            for b in world.businesses.values()
            if "pharmac" in b.business_type
            and b.location_id
            and satisfies_gate(here or "", b.location_id)
        ),
        None,
    )
    if shop is None:
        # Name the premises rather than the room type, for the reason
        # `places_that_satisfy` exists: "pharmacy" is not somewhere `move`
        # will take you, and a town may have none at all.
        open_ones = sorted(
            f"{b.name} ({b.location_id})"
            for b in world.businesses.values()
            if "pharmac" in b.business_type and b.location_id
        )
        raise LocationGateError(
            "buy_treatment", "a pharmacy", here or "nowhere", open_ones
        )

    refusal = pharmacy_refusal(agent, world.tick() // TICKS_PER_DAY)
    if refusal is not None:
        raise ActorRequirementError("buy_treatment", [refusal])

    from world.tools.economy_bridge import LedgerEconomyPort

    economy = LedgerEconomyPort(world.ledger, world.tick, world)
    treated, life_events = await treat_at_pharmacy(
        agent, day=world.tick() // TICKS_PER_DAY, economy=economy
    )
    # The cured record goes back whole, the way `life/health.py` asks: one
    # object written into `world.agents`, not an agent reassembled around a
    # health field the caller had to know to replace.
    world.agents[agent.agent_id] = treated

    return ToolOutcome(
        result={
            "treated_at": shop.business_id,
            "paid_sim": PHARMACY_SHORT_TREATMENT_COST_SIM,
            "cured": True,
            "note": (
                f"{shop.name} treated you. The fee went to the pharmacy, "
                "not to the treasury."
            ),
        },
        events=[
            WorldEvent(type=e.type, tick=world.tick(), payload=dict(e.payload))
            for e in life_events
        ],
    )

def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="buy_treatment",
            description=(
                "Buy the cure for a short illness, in person at a pharmacy. "
                f"Costs {PHARMACY_SHORT_TREATMENT_COST_SIM} sim, paid to the "
                "pharmacy that treats you. Only short ailments are treatable - "
                "anything worse needs care this town cannot give yet - and only "
                "while you still have one."
            ),
            params_model=BuyTreatmentParams,
            handler=buy_treatment_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,  # resolved against the town's pharmacies, in the handler
            actor_requirements=ActorRequirements(require_alive=True, require_not_dormant=True),
        ),
        ToolSpec(
            name="keep_doing",
            description=(
                "Commit to one job and stop paying to think about it. Repeats a "
                "single action - harvest, mine, fell_timber, craft, cook or rest - "
                "with the same arguments, until it is done. Say exactly one way it "
                "ends: until={'units': 12}, {'ticks': 20}, {'dusk': true}, or "
                "{'item_count': {'item': 'wheat', 'at_least': 10}}. You are woken "
                "if somebody calls your name, if the work refuses, or when it is "
                "finished. Cost: 0 - each repetition is charged as itself."
            ),
            params_model=KeepDoingParams,
            handler=keep_doing_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(require_alive=True, require_not_dormant=True),
        ),
        ToolSpec(
            name="revive",
            description=_revive_description(),
            params_model=ReviveParams,
            handler=revive_handler,
            namespace=ToolNamespace.PUBLIC,
            # The gate depends on the target, not the params, so the
            # co-location check lives in the handler - see `trade`.
            location_gate=None,
            actor_requirements=ActorRequirements(require_alive=True, require_not_dormant=True),
        ),
        ToolSpec(
            name="eat",
            description=(
                # Deliberately does not name the other food tools. An earlier
                # wording said "harvesting and cooking put it in your
                # inventory", and `describe_primitives(contains="harvest")`
                # then returned `eat` ahead of `harvest` - a tool's blurb is
                # searchable text, not prose.
                #
                # TOOL-26. The closing sentence was a nudge rather than a
                # fact (Addendum rule 2: "never the strategy"), and it was
                # not even a true one - `cook` consumes food to make other
                # food, and a household's upkeep consumes it daily without
                # anybody calling a tool at all.
                "Eat food you are holding, so it feeds you instead of sitting "
                "in your inventory."
            ),
            params_model=EatParams,
            handler=eat_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="give",
            description=(
                "Hand an item to another resident standing in the same place. "
                "Unlike `sell`, no business and no payment is involved."
            ),
            params_model=GiveParams,
            handler=give_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="rest",
            description=(
                # TOOL-26, found alongside the three the finding names and
                # the same defect: the second sentence told the resident
                # what to want, where the first already states the physics
                # (this cancels a journey in progress).
                # TOOL-05(d): the sentence follows the behaviour. Resting
                # no longer ends a walk unless the resident says so.
                "Take a turn doing nothing. If you were walking somewhere you "
                "keep walking; pass stop_walking to give the journey up."
            ),
            params_model=RestParams,
            handler=rest_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
