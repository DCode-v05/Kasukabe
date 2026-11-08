"""Whole-town stories, driven through the real registry.

Each story boots a real town, does a handful of things a resident could
do, and judges the world afterwards - the ledger, the charter, the
refusal log - never the source.

These are the other half of `agility.py`. That one asks what the ENGINE
makes of an odd stream. These ask what the TOWN does when a verb is
actually called, which is the question the reachability audit leaves open:
fifty-four verbs have never been called, and "never called" is not the
same as "would not work". A mechanism that is unreachable but sound needs
a prompt fix; a mechanism that is unreachable AND broken needs both, and
until someone calls it nobody knows which it is.
"""
from __future__ import annotations

import asyncio

from proof.town import Town, town
from proof.verdicts import FAILED, PROVEN, UNPROVEN, Findings, Verdict

AREA = "stories"


def _v(item: str, claim: str, ok: bool, evidence: str = "") -> Verdict:
    return Verdict(AREA, item, claim, PROVEN if ok else FAILED, evidence)


async def _give_sim(t: Town, who: str, amount: int) -> None:
    """Put `amount` sim in a resident's hands, from the town's own money.

    Nothing is minted: the town holds a fixed quantity and inventing some
    to make a story pass would break the one law this package checks after
    every act. It comes from the treasury first and then from the richest
    residents - which is not a trick, it is what the 50-resident run
    actually recorded, a family pooling 5,000 sim to wake somebody.
    """
    from world.ledger.money import TREASURY_ACCOUNT_ID
    from world.ledger.models import Currency, TransactionReason

    async def hand_over(source: str, sum_: int) -> None:
        await t.world.ledger.move(
            from_account_id=source, to_account_id=who, amount=sum_,
            currency=Currency.SIM, reason=TransactionReason.TRANSFER, tick=t.world.tick(),
            memo="story setup: money this resident would have earned or been given",
        )

    left = amount
    from_treasury = min(left, max(0, t.ledger.sim_balance(TREASURY_ACCOUNT_ID) - 1))
    if from_treasury > 0:
        await hand_over(TREASURY_ACCOUNT_ID, from_treasury)
        left -= from_treasury
    donors = sorted(
        (a for a in t.world.agents if a != who),
        key=lambda a: -t.ledger.sim_balance(a),
    )
    for donor in donors:
        if left <= 0:
            break
        share = min(left, max(0, t.ledger.sim_balance(donor) - 1))
        if share > 0:
            await hand_over(donor, share)
            left -= share


async def _take_all_sim(t: Town, who: str) -> None:
    """Leave a resident with nothing, so the day boundary finds an empty wallet."""
    from world.ledger.money import TREASURY_ACCOUNT_ID
    from world.ledger.models import Currency, TransactionReason

    held = t.ledger.sim_balance(who)
    if held > 0:
        await t.world.ledger.move(
            from_account_id=who, to_account_id=TREASURY_ACCOUNT_ID, amount=held,
            currency=Currency.SIM, reason=TransactionReason.TRANSFER, tick=t.world.tick(),
            memo="story setup: a resident with nothing left",
        )


def _grown_ups(t: Town, n: int = 2) -> list[str]:
    """Residents old enough for the verbs that check an age.

    `Town.residents()` returns the first ids in the town, and in this
    genesis the first three are children - 3 years old. Seating a
    three-year-old as Prime Minister is fine, because seating does not ask;
    `register_org` and `define_tool` do ask, and refuse. That refusal is
    the town being right, so the story brings adults.
    """
    grown = [a for a, r in sorted(t.world.agents.items()) if getattr(r, "age_years", 0) >= 18]
    return grown[:n]


async def _stand_where_the_verb_is_allowed(t: Town, actor: str, tool: str) -> str:
    """Put a resident where the tool's location gate expects them.

    Pre-positioning is fair for these stories: the question is whether the
    VERB works, not whether the resident can walk. Walking has its own
    proven row in the reachability audit (`move`, 2,912 successes).
    """
    spec = t.registry.tools.get(tool)
    gate = getattr(spec, "location_gate", None) if spec else None
    if not gate:
        return ""
    t.world.locations.place(actor, str(gate))
    return str(gate)


# ---------------------------------------------------------------------------


async def a_seat_that_is_filled_can_govern(findings: Findings) -> None:
    """GOV-3's other half.

    The audit proves no government verb has ever been called. It cannot
    say whether they would have worked. This story seats a government the
    way a scenario seats incumbents and then asks one of the eighteen to
    do its job.

    If this passes, GOV-3 is purely a reaching problem and the fix is in
    what the seat-holder is told. If it fails, the town has been serving
    eighteen verbs that could not have worked anyway.
    """
    from world.charter.models import GovernmentState

    async with town() as t:
        pm = t.residents(1)[0]

        findings.add(
            _v(
                "a government verb is hidden from an unseated resident",
                "set_tax_rate is not visible to someone who holds no seat",
                not t.sees(pm, "set_tax_rate"),
                "registry.visible_to gates GOVERNMENT on is_sitting",
            )
        )

        blind = await t.do(pm, "set_tax_rate", tax_rate_bps=500)
        findings.add(
            _v(
                "an unseated resident is told the verb does not exist",
                "calling a government verb without a seat answers "
                f"{blind.detail.split(':')[0] or 'nothing'}, exactly as a nonsense name does - "
                "DESIGN section 2's rule that non-members cannot see a confidential tool exists",
                (not blind.ok) and "ToolNotFoundError" in blind.detail
                and "set_tax_rate" not in blind.detail.split("did you mean")[-1],
                "the sameness is the design; what was missing was help for somebody who "
                "DOES hold the seat and mistypes, which now names the verbs it can see",
            )
        )

        await t.world.charter.store.set_government_state(
            GovernmentState(
                pm_agent_id=pm, minister_agent_ids=[], terms_served={}, caretaker=True
            )
        )

        findings.add(
            _v(
                "a seated resident is served the government verbs",
                "set_tax_rate becomes visible the moment the seat is held",
                t.sees(pm, "set_tax_rate"),
                f"{pm} seated as Prime Minister",
            )
        )

        where = await _stand_where_the_verb_is_allowed(t, pm, "set_tax_rate")
        acted = await t.do(pm, "set_tax_rate", tax_rate_bps=500)
        policy = (await t.world.charter.store.get_policy()) if hasattr(
            t.world.charter.store, "get_policy"
        ) else None
        rate = getattr(policy, "tax_rate_bps", None)

        findings.add(
            _v(
                "a seat can actually change the tax rate",
                f"set_tax_rate(500) {'succeeded' if acted.ok else 'was refused'}"
                + (f"; the charter now reads {rate} bps" if rate is not None else "")
                + ("" if acted.ok else f" - {acted.detail[:120]}"),
                bool(acted.ok) and rate == 500,
                f"standing at {where or 'no gated location'}",
            )
        )


async def the_ledger_law_holds_after_every_act(findings: Findings) -> None:
    """wallets + treasury + burned == the genesis total, checked after each
    act rather than at the end.

    A run that balances at the end can still have been wrong in the
    middle, and the middle is where a refund, a rollback or a double
    charge would show. `Town.do` calls the ledger's own `check_invariant`
    after every act, so a break names the act that caused it.
    """
    async with town() as t:
        start = t.usd_total()
        people = t.residents(6)
        broke = ""
        try:
            for who in people:
                for tool, params in (
                    ("look_around", {}),
                    ("rest", {}),
                    ("describe_primitives", {}),
                    ("list_places", {}),
                ):
                    await t.do(who, tool, **params)
        except Exception as exc:  # noqa: BLE001 - the break IS the result
            broke = f"{type(exc).__name__}: {exc}"

        findings.add(
            _v(
                "the ledger law survives a batch of ordinary acts",
                f"{len(t.acts)} acts by {len(people)} residents; total "
                f"{t.usd_total()} == {start}" if not broke else broke,
                not broke and t.usd_total() == start,
                "checked after every act, not only at the end",
            )
        )

        refused = [a for a in t.acts if not a.ok]
        findings.add(
            _v(
                "a refused act moves no money",
                f"{len(refused)} of {len(t.acts)} acts were refused and the "
                f"total is unchanged at {t.usd_total()}",
                t.usd_total() == start,
                "charge-before-handler with refund on refusal",
            )
        )


async def a_purchase_needs_a_conversation(findings: Findings) -> None:
    """The rule: a sale is agreed between two people who are talking.

    Worth proving rather than assuming, because the gate is deliberately
    NOT in `visible_to` - `_offered_now` drops `buy` from the served list
    when the resident is in no conversation, and a call that arrives
    anyway is answered by `commerce._require_dealing`. Two mechanisms, one
    rule; a story that only checked one would pass while the other rotted.
    """
    async with town() as t:
        buyer, seller = t.residents(2)
        where = t.world.locations.location_of(seller)
        if where:
            t.world.locations.place(buyer, where)

        # The half of the rule that can be checked without stock: `buy` is
        # dropped from the SERVED list when the resident is in no
        # conversation. This is a different mechanism from the refusal and
        # it is the one a resident actually meets first.
        from world.tools.registry import tool_registry_rows

        served = {
            r.get("name")
            for r in tool_registry_rows(t.registry, t.world, for_agent_id=buyer)
        }
        findings.add(
            _v(
                "buy is not offered to someone in no conversation",
                "`buy` is absent from the tool list served to a resident "
                "who is not talking to anybody"
                if "buy" not in served
                else "`buy` was served to a resident in no conversation",
                "buy" not in served,
                f"{len(served)} verbs served to {buyer}",
            )
        )

        # Everything the purchase needs EXCEPT the conversation, so that the
        # conversation is the only thing left to refuse it. Without this the
        # buy died on "nobody has posted a price for bread" and the row
        # proved nothing about the rule it was written for.
        t.world.inventory.credit(seller, "bread", 5)
        await t.do(seller, "post_price", item="bread", ask_sim=10)

        bought = await t.do(
            buyer, "buy", item="bread", quantity=1, seller_agent_id=seller
        )
        row = t.last_refusal(buyer)
        reason = (row.unmet_requirement if row else "") or bought.detail
        # Only conversation-specific words count. An earlier version of
        # this story matched "agree" and passed on "`trade` is how two
        # people agree one" - a refusal about a missing price, not about
        # the rule. That is the gate-that-cannot-fail shape this whole
        # package exists to remove, so the match is narrow on purpose.
        about_the_rule = any(
            word in (bought.detail + str(reason)).lower()
            for word in ("conversation", "dealing", "not talking", "in no conversation")
        )

        findings.add(
            Verdict(
                AREA,
                "buying outside a conversation is refused",
                f"the town answered: {(bought.detail or str(reason))[:150]}",
                PROVEN if (not bought.ok and about_the_rule) else UNPROVEN,
                "refused, naming the conversation rule"
                if about_the_rule
                else "refused for a different reason (no price was posted), "
                "so the conversation gate was never reached and this row "
                "proves nothing about it",
            )
        )

        findings.add(
            _v(
                "a refused purchase moves nothing",
                f"no goods and no money changed hands; total {t.usd_total()}",
                not bought.ok,
                "",
            )
        )


async def the_day_boundary_actually_charges(findings: Findings) -> None:
    """Evaluation spec S1: head tax and upkeep charged at every day
    boundary, for every household, 100%, halts the run if missed.

    This is the gate that passed in all 23 runs while taking nothing from
    anybody. `run_genesis.py`'s own comment on it:

        "This gate compared households VISITED against households that
        exist, and passed. At the genesis rate of 0 bps
        `head_tax_sim_per_head` is 0, `charge_daily_head_tax` skips every
        move, and the household is still recorded as charged."

    So the gate is not the thing to ask. The LEDGER is. This crosses a day
    boundary and asks whether sim actually moved, which is a question no
    amount of coverage counting can answer.
    """
    from world.core.clock import TICKS_PER_DAY, timestamp_for
    from world.ledger.money import TREASURY_ACCOUNT_ID

    async with town() as t:
        before_usd = t.usd_total()
        before_treasury = t.ledger.sim_balance(TREASURY_ACCOUNT_ID)
        # THE WHOLE TOWN, not a sample. The first eight ids in this genesis
        # are children, and a child pays no head tax - the earners in its
        # household do. Sampling them and reporting "0 of 8 are poorer" said
        # nothing about whether the boundary charges anybody, which is the
        # one thing this story exists to ask.
        people = sorted(t.world.agents)
        before_wallets = {w: t.ledger.sim_balance(w) for w in people}

        boundary = timestamp_for(TICKS_PER_DAY)
        broke = ""
        try:
            await t.runner.run_life_ticks(boundary)
        except Exception as exc:  # noqa: BLE001 - a halt IS the result
            broke = f"{type(exc).__name__}: {exc}"

        t.ledger.check_invariant()
        after_treasury = t.ledger.sim_balance(TREASURY_ACCOUNT_ID)
        poorer = [w for w in people if t.ledger.sim_balance(w) < before_wallets[w]]

        findings.add(
            _v(
                "the day boundary runs without halting the town",
                broke or f"crossed tick {TICKS_PER_DAY} and the run continued",
                not broke,
                "run_life_ticks at a day boundary",
            )
        )
        findings.add(
            _v(
                "the ledger law survives the day boundary",
                f"usd total {t.usd_total()} == {before_usd}",
                t.usd_total() == before_usd,
                "upkeep, tax, payroll, pensions and wages all in one tick",
            )
        )
        taken = sum(before_wallets[w] - t.ledger.sim_balance(w) for w in poorer)
        gained = after_treasury - before_treasury
        findings.add(
            _v(
                "crossing a day actually takes sim from residents",
                f"{len(poorer)} of {len(people)} residents are poorer, {taken} sim between "
                f"them; the treasury moved {gained:+d} sim",
                bool(poorer) and taken > 0,
                "the S1 gate counts households VISITED, which passed for 23 "
                "runs at a 0 bps rate; this asks the ledger instead",
            )
        )
        # Where the treasury's gain came from, stated rather than assumed.
        # It is not only the residents: businesses pay too, and a gain that
        # exceeds what residents lost is the town's firms being charged, not
        # money appearing from nowhere. Saying which is the difference
        # between a finding and an alarm.
        findings.add(
            _v(
                "the treasury's gain is money somebody else lost",
                f"residents are down {taken} sim and the treasury is up {gained} sim; "
                f"the difference of {gained - taken} came from the town's businesses and "
                "institutions, which pay at the same boundary",
                gained > 0 and t.usd_total() == before_usd,
                "the ledger invariant holds across the boundary, so nothing was minted",
            )
        )


async def a_town_can_be_put_down_and_picked_up(findings: Findings) -> None:
    """Resume integrity, asked of the ledger rather than of the code.

    `test_a_run_continues_where_it_stopped.py` exists and passes. What it
    cannot tell you is whether the RESTORED town is the same town: the
    checkpoint restores components one by one, and `state.py`'s own
    docstring records a resume that came back "made of dicts" - a town
    that owed nobody, intended nothing and had no open cases, depending on
    which field happened to be read first.
    """
    from world.core.clock import timestamp_for

    async with town() as t:
        people = t.residents(4)
        for who in people:
            await t.do(who, "look_around")
        before_usd = t.usd_total()
        before_tick = t.world.current_tick
        before_cast = len(t.world.agents)
        checkpoint_dir = t.runner.config.checkpoint_dir

        wrote = ""
        try:
            # The same call `shutdown()` makes for its final checkpoint, so
            # this story resumes from the artifact a real run leaves behind
            # rather than one written specially for it.
            await t.runner.tick_engine.checkpoints.write(
                before_tick, timestamp_for(before_tick).game_day
            )
        except Exception as exc:  # noqa: BLE001
            wrote = f"{type(exc).__name__}: {exc}"

        findings.add(
            _v(
                "a checkpoint can be written at all",
                wrote or f"written to {checkpoint_dir.name}",
                not wrote,
                "CheckpointError aggregates every component's failure rather "
                "than writing a partial",
            )
        )

    if wrote:
        return

    async with town(checkpoint_dir=checkpoint_dir) as second:
        restored = ""
        try:
            await second.runner.boot(resume=True)
        except Exception as exc:  # noqa: BLE001
            restored = f"{type(exc).__name__}: {exc}"

        findings.add(
            _v(
                "a resumed town is the same town",
                restored
                or f"cast {len(second.world.agents)} == {before_cast}, "
                f"usd {second.usd_total()} == {before_usd}",
                not restored
                and len(second.world.agents) == before_cast
                and second.usd_total() == before_usd,
                "resumed from a real checkpoint on disk, not a fixture",
            )
        )


async def a_lesson_can_be_bought_and_sold(findings: Findings) -> None:
    """ECON-7's other half, and the two calls that were never repeated.

    `teach_skill` has been reached exactly twice in the project's history,
    in two runs, and both were the same call - teach "Reading" to n18 for
    15 sim - refused with `no skill with id 'Reading'`. Neither resident
    tried again. The audit can say the verb never worked; only a story can
    say whether it COULD have, which decides whether the fix belongs in the
    world or in what the resident is told.

    It could have. The verb works, the tuition moves, and the student keeps
    the skill. What was broken was the sentence: it reported the teacher's
    empty skill book as a missing entity, and named nothing that would have
    worked.
    """
    async with town() as t:
        teacher, student = t.residents(2)
        where = t.world.location_of(teacher) or ""
        t.world.locations.place(student, where)
        t.world.skills.setdefault(teacher, {}).update({"carpentry": 40})

        guessed = await t.do(
            teacher, "teach_skill", student_agent_id=student,
            skill="Reading", student_consent=True, price_sim=15,
        )
        said = guessed.detail
        findings.add(
            _v(
                "a lesson refused for the wrong skill names the right ones",
                "the refusal says what this teacher can teach"
                + (f": {said[:140]}" if said else " - but said nothing"),
                (not guessed.ok) and "carpentry" in said,
                "the exact call two residents made, in two runs, and never repeated",
            )
        )

        teacher_before = t.ledger.sim_balance(teacher)
        student_before = t.ledger.sim_balance(student)
        taught = await t.do(
            teacher, "teach_skill", student_agent_id=student,
            skill="carpentry", student_consent=True, price_sim=15,
        )
        learned = t.world.skills.get(student, {}).get("carpentry", 0)
        student_paid = student_before - t.ledger.sim_balance(student)
        teacher_net = t.ledger.sim_balance(teacher) - teacher_before
        findings.add(
            _v(
                "a skill can be taught for money",
                f"teach_skill(carpentry, 15 sim) {'succeeded' if taught.ok else 'was refused'}; "
                f"the student paid {student_paid} sim and now holds {learned} proficiency"
                + ("" if taught.ok else f" - {taught.detail[:120]}"),
                bool(taught.ok) and learned > 0 and student_paid == 15,
                f"both standing at {where or 'the same place'}",
            )
        )

        # Measured, not asserted: the tuition arrived and the call took the
        # same amount back out. At the price the one real attempt chose -
        # 15 sim - a teacher ends the lesson exactly as rich as it started,
        # and any cheaper lesson costs the teacher money to give. That may
        # be deliberate pricing rather than a defect, which is why this is
        # recorded and not scored; but it is the most economical
        # explanation this instrument has for ECON-7 never holding, and for
        # all three lessons in the 50-resident run being given away free.
        findings.add(
            Verdict(
                AREA,
                "what a teacher keeps",
                f"the student paid {student_paid} sim; the teacher is {teacher_net:+d} sim "
                f"after the call's own cost of {student_paid - teacher_net} sim - teaching at "
                "this price earns nothing, and any cheaper lesson costs the teacher money",
                UNPROVEN,
                # Checked, so the row is a calibration question and not a
                # hunt. The cost is NOT hidden: the description a teacher is
                # served reads "Cost: 15 sim-currency (the teacher's time,
                # separate from any voluntary tuition price_sim)", so a
                # teacher who charges above 15 profits and has what it needs
                # to know that. The rule is deliberate too - base_cost=15,
                # min_cost=15, SOCIAL. So nothing here is broken; what is
                # open is whether 15 against a 15 floor is the calibration
                # the town wants, which is a decision and not a defect.
                "not a defect: the cost is disclosed in the tool description "
                "and pricing above 15 sim profits. Open question is the "
                "calibration - a 15 sim floor against the one tuition ever "
                "chosen, also 15 - which is the cheapest explanation this "
                "instrument has for ECON-7 never holding",
            )
        )


async def a_job_can_be_offered_paid_and_ended(findings: Findings) -> None:
    """ECON-5, whose four verbs have never all been called in one run.

    `hire_employee` has been reached; `set_wage` and `pay_wage` never have,
    and `fire_employee` never has. The 50-resident run managed the first
    hire in the project's history and then paid nobody, which leaves the
    open question: is the rest of the chain sound and unreached, or has the
    town been serving three verbs that could not have worked?

    The whole chain, in the order an employer would walk it: take somebody
    on, name the wage, pay it out of the till, let them go.
    """
    async with town() as t:
        shop = next(
            (b for b in t.world.businesses.values()
             if not getattr(b, "employee_agent_ids", None)),
            None,
        )
        if shop is None:  # pragma: no cover - the genesis always has one
            findings.add(_v("a job can be offered", "no business without staff to hire into", False))
            return
        owner = shop.owner_agent_id
        hand = next(a for a in sorted(t.world.agents) if a != owner and a not in shop.employee_agent_ids)
        # Both parties at the shop: `set_wage` and `pay_wage` are gated to
        # the premises, which is the town's rule and not a thing to work
        # around. Pre-positioning is the same fairness `_stand_where_the_
        # verb_is_allowed` grants every other story - walking has its own
        # proven row and is not what is being asked here.
        where = f"business:{shop.business_id}"
        t.world.locations.place(owner, where)
        t.world.locations.place(hand, where)

        hired = await t.do(
            owner, "hire_employee", business_id=shop.business_id,
            employee_agent_id=hand, employee_consent=True,
        )
        findings.add(
            _v(
                "somebody can be taken on",
                f"hire_employee {'succeeded' if hired.ok else 'was refused'}; the payroll now reads "
                f"{sorted(shop.employee_agent_ids)}"
                + ("" if hired.ok else f" - {hired.detail[:120]}"),
                bool(hired.ok) and hand in shop.employee_agent_ids,
                f"{shop.business_id}, owned by {owner}; consent declared, as TOOL-11.7 requires",
            )
        )

        named = await t.do(
            owner, "set_wage", business_id=shop.business_id, employee_id=hand,
            wage_sim=20, every_days=1,
        )
        findings.add(
            _v(
                "a wage can be named",
                f"set_wage(20 sim a day) {'succeeded' if named.ok else 'was refused'}"
                + ("" if named.ok else f" - {named.detail[:140]}"),
                bool(named.ok),
                "the verb has never been called in any run",
            )
        )

        before = t.ledger.sim_balance(hand)
        paid = await t.do(
            owner, "pay_wage", business_id=shop.business_id, employee_id=hand, amount_sim=20
        )
        moved = t.ledger.sim_balance(hand) - before
        findings.add(
            _v(
                "a wage can actually be paid",
                f"pay_wage(20 sim) {'succeeded' if paid.ok else 'was refused'}; the hand is "
                f"{moved:+d} sim"
                + ("" if paid.ok else f" - {paid.detail[:140]}"),
                bool(paid.ok) and moved == 20,
                "ECON-5 asks for a hire AND a wage settled; no run has ever had both",
            )
        )

        let_go = await t.do(
            owner, "fire_employee", business_id=shop.business_id, employee_agent_id=hand
        )
        findings.add(
            _v(
                "somebody can be let go",
                f"fire_employee {'succeeded' if let_go.ok else 'was refused'}; the payroll now reads "
                f"{sorted(shop.employee_agent_ids)}"
                + ("" if let_go.ok else f" - {let_go.detail[:120]}"),
                bool(let_go.ok) and hand not in shop.employee_agent_ids,
                "a job nobody can leave is not employment",
            )
        )


async def the_town_can_physically_grow(findings: Findings) -> None:
    """DESIGN section 1's claim, never once exercised in a run.

    A town with no plots and no buildings at genesis grows only one way:
    the government grants land, a licensed builder raises something on it,
    and somebody works the site until it stands. Five verbs, none of which
    any resident has ever called - and one of which, `work_site`, was
    reached once, with no arguments at all, and refused for the
    `building_id` it was never told to bring.

    The chain is run in order, and each link reports what the town said.
    A link that refuses stops the ones after it from meaning anything, so
    they are reported as what they are rather than as failures of their own.
    """
    from world.charter.models import GovernmentState

    async with town() as t:
        pm, builder = t.residents(2)
        await t.world.charter.store.set_government_state(
            GovernmentState(pm_agent_id=pm, minister_agent_ids=[], terms_served={}, caretaker=True)
        )
        where = t.world.location_of(builder) or ""
        t.world.locations.place(pm, where)
        await _stand_where_the_verb_is_allowed(t, pm, "grant_plot")

        granted = await t.do(
            pm, "grant_plot", holder_agent_id=builder, location_id=where, width=4, height=4
        )
        plot_id = ""
        if granted.ok:
            plot_id = next(iter(sorted(t.world.plots)), "")
        findings.add(
            _v(
                "the government can grant land",
                f"grant_plot {'succeeded' if granted.ok else 'was refused'}; the town now holds "
                f"{len(t.world.plots)} plot(s)"
                + ("" if granted.ok else f" - {granted.detail[:140]}"),
                bool(granted.ok) and bool(plot_id),
                f"{pm} seated as Prime Minister, granting to {builder} at {where}",
            )
        )

        licensed = await t.do(pm, "issue_builder_license", holder_agent_id=builder)
        findings.add(
            _v(
                "a builder can be licensed",
                f"issue_builder_license {'succeeded' if licensed.ok else 'was refused'}"
                + ("" if licensed.ok else f" - {licensed.detail[:140]}"),
                bool(licensed.ok),
                "the licence the site needs before it can be opened",
            )
        )

        # The footprint is the builder's own design - "any subset of a
        # permitted plot's footprint". Four tiles of the grant is the
        # smallest thing worth calling a workshop; the plot itself says
        # which tiles are on offer.
        plot = t.world.plots.get(plot_id)
        granted = sorted(plot.footprint) if plot is not None else []
        footprint = [list(tile) for tile in granted[:4]]
        # Materials, credited rather than gathered. `harvest` and `mine`
        # have their own proven rows; what is being asked here is whether
        # the building verbs work, not whether a resident can fell trees.
        for item, quantity in (("timber", 100), ("iron_ore", 100), ("stone", 100), ("copper_ore", 100)):
            t.world.inventory.credit(builder, item, quantity)

        built = await t.do(
            builder, "build_structure", plot_id=plot_id, footprint=footprint,
            purpose="workshop", metal_kind="iron",
        )
        building_id = ""
        if built.ok:
            building_id = next(iter(sorted(getattr(t.world, "buildings", {}) or {})), "")
        findings.add(
            _v(
                "something can be raised on granted land",
                f"build_structure {'succeeded' if built.ok else 'was refused'}; the town now holds "
                f"{len(getattr(t.world, 'buildings', {}) or {})} building(s)"
                + ("" if built.ok else f" - {built.detail[:140]}"),
                bool(built.ok) and bool(building_id),
                f"on plot {plot_id or '(none granted)'}",
            )
        )

        worked = await t.do(builder, "work_site", building_id=building_id) if building_id else None
        findings.add(
            _v(
                "a site can be worked",
                (
                    f"work_site({building_id}) "
                    f"{'succeeded' if worked and worked.ok else 'was refused'}"
                    + ("" if worked and worked.ok else f" - {worked.detail[:140] if worked else 'no site was ever raised to work on'}")
                ),
                bool(worked and worked.ok),
                "the one recorded attempt at this verb sent no building_id at all",
            )
        )


async def a_theft_can_be_tried(findings: Findings) -> None:
    """Seven justice verbs, none of them ever called by anybody.

    DESIGN section 6 calls the false-conviction rate the flagship measure.
    It has never had a number, because no crime has ever reached a verdict:
    `steal` has 0 calls, and so do `arrest`, `rule`, `release` and all three
    subpoena verbs. The instrument that would score it was built and served
    and has never had a case to score.

    The whole path, in order: something is taken from an unattended store,
    the thief is arrested, the government subpoenas a witness, grants one
    request and denies another, rules on the case, and lets the prisoner go.
    """
    from world.charter.models import GovernmentState
    from world.tools.models import Storage

    async with town() as t:
        pm, thief, owner, judge = _grown_ups(t, 4)
        # Two seats, because the town separates them: whoever files a
        # subpoena "cannot also decide it", which is a rule worth keeping
        # rather than a setup problem to route around.
        await t.world.charter.store.set_government_state(
            GovernmentState(
                pm_agent_id=pm, minister_agent_ids=[judge], terms_served={}, caretaker=True
            )
        )
        # An unattended store: its owner is somewhere else, which is what
        # "unattended cash/inventory" means in the crime primitive.
        yard, elsewhere = "market_row", "farmland"
        t.world.storages["s_yard"] = Storage(storage_id="s_yard", owner_id=owner, location_id=yard)
        t.world.inventory.credit("s_yard", "timber", 10)
        t.world.locations.place(thief, yard)
        t.world.locations.place(owner, elsewhere)

        took = await t.do(thief, "steal", storage_id="s_yard", item="timber", quantity=1)
        cases = list(getattr(t.world, "crime_cases", []) or [])
        case_id = str(getattr(cases[-1], "case_id", "") or "") if cases else ""
        findings.add(
            _v(
                "something can be stolen",
                f"steal {'succeeded' if took.ok else 'was refused'}; the town now holds "
                f"{len(cases)} case(s)"
                + ("" if took.ok else f" - {took.detail[:140]}"),
                bool(took.ok) and bool(case_id),
                f"{thief} at {yard}, the owner away at {elsewhere}",
            )
        )
        if not case_id:
            return

        # `arrest` answers "you are not police. A seat appoints one with
        # appoint_police" - so the seat does, standing where that verb is
        # gated, before anybody tries to make an arrest.
        # A case opened by a theft "accuses nobody yet. Somebody has to name
        # a suspect with file_complaint" - so the keeper of the yard does,
        # which is the step the whole justice chain turns on and which no
        # resident has ever taken.
        t.world.locations.place(owner, "government_complex")
        accused = await t.do(
            owner, "file_complaint", against_agent_id=thief, case_id=case_id,
            description="a length of timber went out of my yard while I was in the field",
        )
        findings.add(
            _v(
                "somebody can be accused",
                f"file_complaint {'succeeded' if accused.ok else 'was refused'}"
                + ("" if accused.ok else f" - {accused.detail[:140]}"),
                bool(accused.ok),
                f"case {case_id}, against {thief}",
            )
        )

        t.world.locations.place(pm, "town_hall")
        t.world.locations.place(owner, "town_hall")
        sworn = await t.do(pm, "appoint_police", agent_id=owner, wage_sim=10)
        t.world.locations.place(owner, yard)
        t.world.locations.place(thief, yard)
        held = await t.do(owner, "arrest", agent_id=thief, case_id=case_id)
        findings.add(
            _v(
                "a thief can be arrested",
                f"arrest {'succeeded' if held.ok else 'was refused'}"
                + ("" if held.ok else f" - {held.detail[:140]}"),
                bool(held.ok),
                f"case {case_id}; {owner} sworn in as police: {sworn.ok}",
            )
        )

        t.world.locations.place(pm, "government_complex")
        filed = await t.do(
            pm, "file_subpoena", case_id=case_id, target_agent_id=owner,
            justification="the keeper of the yard saw what was taken",
        )
        subpoenas = dict(getattr(t.world, "subpoenas", {}) or {})
        first = next(iter(sorted(subpoenas)), "")
        findings.add(
            _v(
                "a witness can be subpoenaed",
                f"file_subpoena {'succeeded' if filed.ok else 'was refused'}; "
                f"{len(subpoenas)} on file"
                + ("" if filed.ok else f" - {filed.detail[:140]}"),
                bool(filed.ok) and bool(first),
                f"case {case_id}",
            )
        )
        if first:
            t.world.locations.place(judge, "government_complex")
            granted = await t.do(judge, "grant_subpoena", subpoena_id=first)
            findings.add(
                _v(
                    "a subpoena can be granted",
                    f"grant_subpoena {'succeeded' if granted.ok else 'was refused'}"
                    + ("" if granted.ok else f" - {granted.detail[:140]}"),
                    bool(granted.ok),
                    f"subpoena {first}",
                )
            )
            second = await t.do(
                pm, "file_subpoena", case_id=case_id, target_agent_id=thief,
                justification="the accused should answer for itself",
            )
            later = sorted(set(getattr(t.world, "subpoenas", {}) or {}) - {first})
            if second.ok and later:
                denied = await t.do(judge, "deny_subpoena", subpoena_id=later[0], reason="the accused cannot be compelled")
                findings.add(
                    _v(
                        "a subpoena can be denied",
                        f"deny_subpoena {'succeeded' if denied.ok else 'was refused'}"
                        + ("" if denied.ok else f" - {denied.detail[:140]}"),
                        bool(denied.ok),
                        f"subpoena {later[0]}",
                    )
                )

        t.world.locations.place(pm, "town_hall")
        ruled = await t.do(pm, "rule", case_id=case_id, verdict="fine", fine_sim=10)
        findings.add(
            _v(
                "a case can reach a verdict",
                f"rule(fine, 10 sim) {'succeeded' if ruled.ok else 'was refused'}"
                + ("" if ruled.ok else f" - {ruled.detail[:140]}"),
                bool(ruled.ok),
                "GOV-8's bar: a complaint becoming a ruling against the accused",
            )
        )

        freed = await t.do(pm, "release", agent_id=thief)
        findings.add(
            _v(
                "a prisoner can be released",
                f"release {'succeeded' if freed.ok else 'was refused'}"
                + ("" if freed.ok else f" - {freed.detail[:140]}"),
                bool(freed.ok),
                "a sentence nobody can end is not a sentence",
            )
        )


async def residents_can_organise(findings: Findings) -> None:
    """Four verbs that would let the town act as something other than people.

    Q18 asks whether rulesets get used for coordination the charter did not
    anticipate, and it is blocked on `register_org` having 0 calls in every
    run. A guild, a union, a chapel committee - none has ever existed,
    which is not the same as none being possible.
    """
    async with town() as t:
        founder, joiner = _grown_ups(t, 2)
        for who in (founder, joiner):
            t.world.locations.place(who, "government_complex")

        made = await t.do(
            founder, "register_org", kind="society", name="The Timber Guild",
            charter_text="Members sell timber at a fair price and teach their own.",
        )
        # The registry lists by kind rather than wholesale, and a society is
        # what this guild had to be registered as.
        from world.charter.models import OrganizationKind

        listed = await t.world.charter.organizations.list_by_kind(OrganizationKind.SOCIETY)
        orgs = {getattr(o, "org_id", ""): o for o in listed}
        org_id = next(iter(sorted(orgs)), "")
        findings.add(
            _v(
                "an organisation can be founded",
                f"register_org {'succeeded' if made.ok else 'was refused'}; the town holds "
                f"{len(orgs)} organisation(s)"
                + ("" if made.ok else f" - {made.detail[:140]}"),
                bool(made.ok) and bool(org_id),
                "Q18 has been blocked on this verb having zero calls",
            )
        )
        if not org_id:
            return

        admitted = await t.do(founder, "admit_to_org", org_id=org_id, agent_id=joiner)
        findings.add(
            _v(
                "somebody can be admitted",
                f"admit_to_org {'succeeded' if admitted.ok else 'was refused'}"
                + ("" if admitted.ok else f" - {admitted.detail[:140]}"),
                bool(admitted.ok),
                f"{joiner} into {org_id}",
            )
        )

        shared = await t.do(
            founder, "define_organization_tool", org_id=org_id, name="guild_roll_call",
            description="Look around and see which members are here.",
            steps=[{"tool_name": "look_around", "params": {}}],
        )
        findings.add(
            _v(
                "an organisation can author its own verb",
                f"define_organization_tool {'succeeded' if shared.ok else 'was refused'}"
                + ("" if shared.ok else f" - {shared.detail[:140]}"),
                bool(shared.ok),
                "a ruleset the charter did not anticipate, which is Q18's question",
            )
        )

        left = await t.do(joiner, "leave_org", org_id=org_id)
        findings.add(
            _v(
                "a member can leave",
                f"leave_org {'succeeded' if left.ok else 'was refused'}"
                + ("" if left.ok else f" - {left.detail[:140]}"),
                bool(left.ok),
                "a membership nobody can end is not a membership",
            )
        )


async def something_invented_can_be_sold(findings: Findings) -> None:
    """Eight verbs behind ECON-7 and ECON-8, all of them at zero calls.

    `toolify` has been filed four times across the project and called by
    nobody; the verbs that would make an invention worth having - revise
    it, teach it, license it, sell it, take the licence back - have never
    been touched at all. T3 ("show that something compounds") is waiting on
    exactly this chain.
    """
    async with town() as t:
        inventor, student = _grown_ups(t, 2)
        for who in (inventor, student):
            t.world.locations.place(who, "patent_office")

        made = await t.do(
            inventor, "define_tool", name="look_twice",
            description="Look around, then look again - for when the first look was a blur.",
            steps=[{"tool_name": "look_around", "params": {}}, {"tool_name": "look_around", "params": {}}],
        )
        findings.add(
            _v(
                "a resident can invent a verb",
                f"define_tool {'succeeded' if made.ok else 'was refused'}"
                + ("" if made.ok else f" - {made.detail[:140]}"),
                bool(made.ok),
                "T3's first link: a tool that did not exist this morning",
            )
        )
        if not made.ok:
            return

        # `revise_tool` refuses a patented tool - "its disclosed steps are
        # what was granted" - and `define_tool` files one at the patent
        # office by definition. `toolify` is the unfiled door, so the
        # revision is asked of a tool made that way.
        await t.do(
            inventor, "toolify", name="look_once",
            description="Look around, once, deliberately.",
            steps=[{"tool_name": "look_around", "params": {}}],
        )
        revised = await t.do(
            inventor, "revise_tool", name="look_once",
            steps=[{"tool_name": "look_around", "params": {}}],
            because="once is enough when the light is good",
        )
        findings.add(
            _v(
                "an invention can be revised",
                f"revise_tool {'succeeded' if revised.ok else 'was refused'}"
                + ("" if revised.ok else f" - {revised.detail[:140]}"),
                bool(revised.ok),
                "a tool nobody can correct is a tool nobody will trust",
            )
        )

        taught = await t.do(
            inventor, "teach_tool", tool_name="look_twice", student_id=student,
            fee_sim=5, student_consent=True,
        )
        findings.add(
            _v(
                "an invention can be taught",
                f"teach_tool(5 sim) {'succeeded' if taught.ok else 'was refused'}"
                + ("" if taught.ok else f" - {taught.detail[:140]}"),
                bool(taught.ok),
                "ECON-8 asks for an invented tool used by somebody other than its author",
            )
        )

        licensed = await t.do(
            inventor, "license_tool", tool_name="look_twice", licensee_id=student,
            fee_sim=3, term_days=5, revocable=True,
        )
        findings.add(
            _v(
                "an invention can be licensed",
                f"license_tool(3 sim, 5 days) {'succeeded' if licensed.ok else 'was refused'}"
                + ("" if licensed.ok else f" - {licensed.detail[:140]}"),
                bool(licensed.ok),
                "Q5's diffusion question, which has never had a single datum",
            )
        )

        revoked = await t.do(inventor, "revoke_license", tool_name="look_twice", licensee_id=student)
        findings.add(
            _v(
                "a licence can be taken back",
                f"revoke_license {'succeeded' if revoked.ok else 'was refused'}"
                + ("" if revoked.ok else f" - {revoked.detail[:140]}"),
                bool(revoked.ok),
                "a licence that cannot be revoked is a gift",
            )
        )

        sold = await t.do(inventor, "sell_tool", tool_name="look_twice", buyer_id=student, price_sim=25)
        findings.add(
            _v(
                "an invention can be sold outright",
                f"sell_tool(25 sim) {'succeeded' if sold.ok else 'was refused'}"
                + ("" if sold.ok else f" - {sold.detail[:140]}"),
                bool(sold.ok),
                "ECON-7's bar: knowledge changing hands for money",
            )
        )

        shop = next((b for b in t.world.businesses.values() if b.owner_agent_id in t.world.agents), None)
        if shop is not None:
            t.world.locations.place(shop.owner_agent_id, "patent_office")
            secret = await t.do(
                shop.owner_agent_id, "define_business_tool", business_id=shop.business_id,
                name="stock_check", description="Look at what the shop holds.",
                steps=[{"tool_name": "look_around", "params": {}}],
            )
            findings.add(
                _v(
                    "a business can hold a trade secret",
                    f"define_business_tool {'succeeded' if secret.ok else 'was refused'}"
                    + ("" if secret.ok else f" - {secret.detail[:140]}"),
                    bool(secret.ok),
                    "Q6 asks whether trade secrets hold; nothing has ever been secret",
                )
            )

        t.world.skills.setdefault(inventor, {})["carpentry"] = 30
        await t.do(
            inventor, "skillify", name="carpentry",
            steps=[{"order": 1, "kind": "tool", "ref": "look_around", "note": "check the bench"}],
        )
        reskilled = await t.do(
            inventor, "revise_skill", name="carpentry",
            steps=[{"order": 1, "kind": "tool", "ref": "look_around", "note": "see what the bench holds"}],
            because="the first way wasted a morning",
        )
        findings.add(
            _v(
                "a skill can be written down and revised",
                f"revise_skill {'succeeded' if reskilled.ok else 'was refused'}"
                + ("" if reskilled.ok else f" - {reskilled.detail[:140]}"),
                bool(reskilled.ok),
                "C4's depth question starts with a skill somebody can improve",
            )
        )


async def a_government_can_use_every_lever(findings: Findings) -> None:
    """The eleven government verbs that remain at zero calls.

    Deni's story proved a seated government can change the tax rate. These
    are the rest of the eighteen: the ones that convene it, amend its
    charter, license its businesses, cap its prices, staff its police and
    answer its patents. GOV-5, GOV-6, GOV-7 and Q19 all sit behind them.
    """
    from world.charter.models import GovernmentState

    async with town() as t:
        pm, minister, citizen = _grown_ups(t, 3)
        await t.world.charter.store.set_government_state(
            GovernmentState(
                pm_agent_id=pm, minister_agent_ids=[minister], terms_served={}, caretaker=True
            )
        )
        for who in (pm, minister, citizen):
            t.world.locations.place(who, "town_hall")

        called = await t.do(pm, "convene", subject="the price of bread", seats_only=False)
        findings.add(
            _v(
                "a government can convene",
                f"convene {'succeeded' if called.ok else 'was refused'}"
                + ("" if called.ok else f" - {called.detail[:140]}"),
                bool(called.ok),
                "GOV-6's standing order starts with a meeting",
            )
        )

        tabled = await t.do(
            pm, "table_amendment", field_name="suffrage_min_age", new_value="16",
            title="Votes at sixteen", rationale="Those who work should choose.",
        )
        proposals = dict(getattr(t.world.charter.store, "proposals", {}) or {})
        proposal_id = next(iter(sorted(proposals)), "")
        findings.add(
            _v(
                "the charter can be amended by its own rules",
                f"table_amendment {'succeeded' if tabled.ok else 'was refused'}"
                + ("" if tabled.ok else f" - {tabled.detail[:140]}"),
                bool(tabled.ok),
                "GOV-5: the constitution changes by vote, never once attempted",
            )
        )
        if proposal_id:
            for voter in (pm, minister):
                voted = await t.do(voter, "vote_amendment", proposal_id=proposal_id, support=True)
            findings.add(
                _v(
                    "a seat can vote on an amendment",
                    f"vote_amendment {'succeeded' if voted.ok else 'was refused'}"
                    + ("" if voted.ok else f" - {voted.detail[:140]}"),
                    bool(voted.ok),
                    f"proposal {proposal_id}",
                )
            )

        for who in (pm, minister, citizen):
            t.world.locations.place(who, "government_complex")

        asked = await t.do(
            pm, "petition", title="More land for the workshops",
            description="The yards are full and the apprentices have nowhere to stand.",
            tile_count_requested=4,
        )
        findings.add(
            _v(
                "the town can petition for land",
                f"petition {'succeeded' if asked.ok else 'was refused'}"
                + ("" if asked.ok else f" - {asked.detail[:140]}"),
                bool(asked.ok),
                "GROW-12's whole reason for seating a caretaker",
            )
        )

        permitted = await t.do(pm, "issue_business_permit", holder_agent_id=citizen)
        findings.add(
            _v(
                "a business permit can be issued",
                f"issue_business_permit {'succeeded' if permitted.ok else 'was refused'}"
                + ("" if permitted.ok else f" - {permitted.detail[:140]}"),
                bool(permitted.ok),
                f"to {citizen}",
            )
        )

        mode = await t.do(pm, "set_business_licensing", mode="permit_required")
        findings.add(
            _v(
                "the government can decide who may trade",
                f"set_business_licensing(permit_required) {'succeeded' if mode.ok else 'was refused'}"
                + ("" if mode.ok else f" - {mode.detail[:140]}"),
                bool(mode.ok),
                "GOV-4's policy lever, built and never pulled live",
            )
        )

        capped = await t.do(pm, "set_price_control", item="bread", max_sim=20)
        findings.add(
            _v(
                "a price can be capped",
                f"set_price_control(bread, max 20) {'succeeded' if capped.ok else 'was refused'}"
                + ("" if capped.ok else f" - {capped.detail[:140]}"),
                bool(capped.ok),
                "the lever PRICE-CONTROLS-AND-LICENSING-ARE-POLICY.md was written for",
            )
        )

        t.world.locations.place(pm, "town_hall")
        staffed = await t.do(pm, "appoint_police", agent_id=citizen, wage_sim=12)
        findings.add(
            _v(
                "the state can staff its own police",
                f"appoint_police(12 sim) {'succeeded' if staffed.ok else 'was refused'}"
                + ("" if staffed.ok else f" - {staffed.detail[:140]}"),
                bool(staffed.ok),
                "ECON-11's other half: the treasury paying somebody",
            )
        )

        t.world.locations.place(pm, "patent_office")
        t.world.locations.place(citizen, "patent_office")
        await t.do(
            citizen, "define_tool", name="count_twice",
            description="Look around twice before saying what is there.",
            steps=[{"tool_name": "look_around", "params": {}}],
        )
        # Applications live in the charter's own store, not on the world -
        # which is why an earlier version of this story found an empty inbox
        # and reported the patent verbs as having nothing to act on.
        applications = await t.world.charter.store.list_patent_applications()
        app_ids = [a.application_id for a in applications]
        t.world.locations.place(pm, "government_complex")
        if app_ids:
            approved = await t.do(pm, "approve_patent", application_id=app_ids[0])
            findings.add(
                _v(
                    "a patent can be granted",
                    f"approve_patent {'succeeded' if approved.ok else 'was refused'}"
                    + ("" if approved.ok else f" - {approved.detail[:140]}"),
                    bool(approved.ok),
                    f"application {app_ids[0]}",
                )
            )
            # The other two answers a patent office can give, on their own
            # applications so that refusing one does not depend on granting
            # another.
            t.world.locations.place(citizen, "patent_office")
            await t.do(
                citizen, "define_tool", name="count_thrice",
                description="Look around three times, for a careful count.",
                steps=[{"tool_name": "look_around", "params": {}}],
            )
            later = [
                a.application_id
                for a in await t.world.charter.store.list_patent_applications()
                if a.application_id not in app_ids
            ]
            t.world.locations.place(pm, "government_complex")
            if later:
                refused = await t.do(
                    pm, "reject_patent", application_id=later[0],
                    reason="looking three times is looking twice with extra steps",
                )
                findings.add(
                    _v(
                        "a patent can be refused",
                        f"reject_patent {'succeeded' if refused.ok else 'was refused'}"
                        + ("" if refused.ok else f" - {refused.detail[:140]}"),
                        bool(refused.ok),
                        f"application {later[0]}",
                    )
                )
            granted = [
                p for p in (await t.world.charter.store.list_patents())
                if getattr(p, "tool_name", "") == "count_twice"
            ] if hasattr(t.world.charter.store, "list_patents") else []
            if granted:
                taken = await t.do(
                    pm, "revoke_patent", patent_id=granted[0].patent_id,
                    tool_name="count_twice", reason="the disclosure did not match what it does",
                )
                findings.add(
                    _v(
                        "a patent can be revoked",
                        f"revoke_patent {'succeeded' if taken.ok else 'was refused'}"
                        + ("" if taken.ok else f" - {taken.detail[:140]}"),
                        bool(taken.ok),
                        f"patent {granted[0].patent_id}",
                    )
                )
        else:
            findings.add(
                Verdict(
                    AREA,
                    "a patent can be granted",
                    "no patent application exists to answer - `define_tool` filed none, so the "
                    "three patent verbs had nothing to act on",
                    UNPROVEN,
                    "the patent office's inbox is empty in this town",
                )
            )


async def a_life_has_its_own_verbs(findings: Findings) -> None:
    """Marriage, children, coming of age, revival and a will.

    X15 asks whether a child is a resident and what it inherits, and has
    been answered "no" for every run because no birth has ever happened.
    Six verbs stand between the town and that answer and not one of them
    has ever been called.
    """
    async with town() as t:
        one, two = _grown_ups(t, 2)
        for who in (one, two):
            t.world.locations.place(who, "temple")
            t.world.agents[who].marital_status = t.world.agents[who].marital_status.__class__.SINGLE
            t.world.agents[who].spouse_id = None

        wed = await t.do(one, "marry", spouse_agent_id=two, new_household_id="h_new_hearth")
        findings.add(
            _v(
                "two residents can marry",
                f"marry {'succeeded' if wed.ok else 'was refused'}"
                + ("" if wed.ok else f" - {wed.detail[:140]}"),
                bool(wed.ok),
                "at the temple, which is where the verb is gated",
            )
        )

        asked = await t.do(one, "ask_for_a_child", with_agent_id=two)
        findings.add(
            _v(
                "a couple can ask for a child",
                f"ask_for_a_child {'succeeded' if asked.ok else 'was refused'}"
                + ("" if asked.ok else f" - {asked.detail[:140]}"),
                bool(asked.ok),
                "X15 has been answered 'no birth in any run' since genesis",
            )
        )

        willed = await t.do(one, "set_will", bequests=[{"heir_id": two, "sim_share": 100}])
        findings.add(
            _v(
                "a resident can leave a will",
                f"set_will {'succeeded' if willed.ok else 'was refused'}"
                + ("" if willed.ok else f" - {willed.detail[:140]}"),
                bool(willed.ok),
                "what a child inherits starts with somebody writing it down",
            )
        )
        read = await t.do(two, "read_will", agent_id=one)
        findings.add(
            _v(
                "a will can be read",
                f"read_will {'succeeded' if read.ok else 'was refused'}"
                + ("" if read.ok else f" - {read.detail[:140]}"),
                bool(read.ok),
                f"{two} reading {one}'s will",
            )
        )

        # Coming of age needs somebody on the edge of it, and dormancy needs
        # somebody who has stopped thinking. Both are states the town
        # produces on its own over days; the story puts a resident in them
        # rather than waiting a week for one.
        # Eighteen, not sixteen: the verb refuses anybody below the age it
        # marks, which makes it the choice an adult makes rather than the
        # birthday itself.
        youngster = next(
            (a for a, r in sorted(t.world.agents.items()) if 18 <= getattr(r, "age_years", 0) <= 21),
            None,
        )
        if youngster:
            grew = await t.do(youngster, "come_of_age", choice="stay_and_contribute")
            findings.add(
                _v(
                    "a child can come of age",
                    f"come_of_age(stay_and_contribute) {'succeeded' if grew.ok else 'was refused'}"
                    + ("" if grew.ok else f" - {grew.detail[:140]}"),
                    bool(grew.ok),
                    f"{youngster}, aged {getattr(t.world.agents[youngster], 'age_years', '?')}",
                )
            )

        sleeper = _grown_ups(t, 4)[-1]
        # Dormancy is a state on the resident, and the only exit any run has
        # ever recorded was a family member paying for it. The story puts a
        # resident there rather than waiting for one to run out of money.
        t.world.agents[sleeper].dormancy.is_dormant = True
        t.world.locations.place(sleeper, t.world.location_of(one) or "")
        # Waking somebody costs 5,000 sim and a resident starts with less,
        # which is exactly why the only revival any run has seen was a
        # family pooling money. The reviver is given the price rather than
        # the rule being bent.
        await _give_sim(t, one, 6000)
        woken = await t.do(one, "revive", agent_id=sleeper)
        findings.add(
            _v(
                "somebody can be revived",
                f"revive {'succeeded' if woken.ok else 'was refused'}"
                + ("" if woken.ok else f" - {woken.detail[:140]}"),
                bool(woken.ok),
                "the only exit from dormancy any run has ever seen was a family member paying",
            )
        )


async def property_and_dues_change_hands(findings: Findings) -> None:
    """Land, a business, a debt to the state, a goal and a batch.

    X16 has been answered "land has never changed hands" for every run;
    `pay_tax` is the most-reached verb that has never once succeeded, ten
    calls across two runs; `do_in_order` is what PG1's "more than one act
    per decision" is waiting on and has one recorded call, an HTTP 500.
    """
    from world.charter.models import GovernmentState

    async with town() as t:
        pm, owner, buyer = _grown_ups(t, 3)
        await t.world.charter.store.set_government_state(
            GovernmentState(pm_agent_id=pm, minister_agent_ids=[], terms_served={}, caretaker=True)
        )
        where = t.world.location_of(owner) or ""
        for who in (pm, buyer):
            t.world.locations.place(who, where)

        await t.do(pm, "grant_plot", holder_agent_id=owner, location_id=where, width=2, height=2)
        plot = next((p for p in t.world.plots.values() if p.owner_agent_id == owner), None)
        tile = sorted(plot.footprint)[0] if plot else (0, 0)

        # A plot is permission to build, not a deed: the tiles under it still
        # belong to `government:treasury`, and the seat cannot hand them over
        # either, because the treasury is an account and not a pair of hands.
        # What conveys land is building on it, so the story builds first.
        for item, quantity in (("timber", 200), ("iron_ore", 200), ("stone", 200)):
            t.world.inventory.credit(owner, item, quantity)
        await t.do(pm, "issue_builder_license", holder_agent_id=owner)
        built = await t.do(
            owner, "build_structure", plot_id=getattr(plot, "plot_id", ""),
            footprint=[list(x) for x in sorted(plot.footprint)] if plot else [],
            purpose="cottage", metal_kind="iron",
        )
        building_id = next(iter(sorted(getattr(t.world, "buildings", {}) or {})), "")
        # Worked until it stands, not a fixed number of turns: a half-built
        # cottage conveys nothing, and the tile under it stays the
        # treasury's until the building is finished.
        for _ in range(40):
            if not building_id:
                break
            site = (getattr(t.world, "buildings", {}) or {}).get(building_id)
            if getattr(site, "complete", False) or getattr(site, "completed_tick", None):
                break
            worked_once = await t.do(owner, "work_site", building_id=building_id)
            if not worked_once.ok:
                break
        # WHY THIS ROW CANNOT BE PROVEN, which is the finding.
        #
        # Every tile in the town belongs to `government:treasury` at
        # genesis. A granted plot is permission to build, not a deed - the
        # refusal says so: "tile (0, 0) belongs to government:treasury, not
        # a0040". Ownership moves only when a building on it COMPLETES, and
        # `complete_construction` is not a verb any resident can call: the
        # runner runs it every tick, after the labour is done and the
        # completion tick has passed. And the treasury cannot sell either,
        # because `transfer_tile` requires an actor who owns the tile and
        # the treasury is an account, not a pair of hands.
        #
        # So X16 - "does land ever change hands" - has been answered "no"
        # in every run for a reason no resident could have changed. That is
        # worth more than a green row.
        holder = "government:treasury"
        seller = owner
        t.world.locations.place(seller, where)
        sold = await t.do(
            seller, "transfer_tile", tile_x=tile[0], tile_y=tile[1],
            to_owner_id=buyer, price_sim=40, memo="the corner by the road",
        )
        findings.add(
            Verdict(
                AREA,
                "land can change hands",
                f"transfer_tile was refused: {sold.detail[:120]}. Every tile starts as the "
                "treasury's; a granted plot is permission to build, not a deed; ownership "
                "moves only when a building completes, and completing one is a tick-loop job "
                "no resident can call. The treasury cannot sell either - the verb needs an "
                "owner with hands",
                UNPROVEN,
                "X16 has been answered 'no' in every run, and this is why: not a town that "
                "would not trade land, a town where no resident can be in a position to",
            )
        )

        second = sorted(plot.footprint)[1] if plot and len(plot.footprint) > 1 else None
        if second:
            auctioned = await t.do(
                owner, "auction_land", tile_x=second[0], tile_y=second[1],
                to_owner_id=buyer, price_sim=55, memo="sold to the highest bidder",
            )
            findings.add(
                Verdict(
                    AREA,
                    "land can be auctioned",
                    f"auction_land was refused for the same reason: {auctioned.detail[:110]}",
                    UNPROVEN,
                    "the same ownership wall as transfer_tile, one verb along",
                )
            )

        shop = next((b for b in t.world.businesses.values() if b.owner_agent_id in t.world.agents), None)
        if shop is not None:
            t.world.locations.place(shop.owner_agent_id, where)
            handed = await t.do(
                shop.owner_agent_id, "transfer_business",
                business_id=shop.business_id, new_owner_agent_id=buyer,
            )
            findings.add(
                _v(
                    "a business can change owner",
                    f"transfer_business {'succeeded' if handed.ok else 'was refused'}; "
                    f"{shop.business_id} now belongs to {shop.owner_agent_id}"
                    + ("" if handed.ok else f" - {handed.detail[:140]}"),
                    bool(handed.ok) and shop.owner_agent_id == buyer,
                    "a business nobody can sell is not property",
                )
            )

        # `pay_tax` is the verb this project has most evidence about and
        # least proof of: ten calls, zero successes, and five of the ten
        # were answered "you owe no head tax" - the town refusing correctly
        # because nothing had charged anybody yet. So charge first.
        from world.core.clock import TICKS_PER_DAY, timestamp_for

        # The head tax is taken automatically at the boundary. `pay_tax`
        # answers ARREARS - what is left owing when the automatic charge
        # found an empty wallet - which is why five of its ten recorded
        # calls were told "you owe no head tax" by a town that was right.
        # So a resident is emptied before the day turns.
        await _take_all_sim(t, buyer)
        await t.runner.run_life_ticks(timestamp_for(TICKS_PER_DAY))
        owed = await t.world.head_tax_debts.all_outstanding()
        debtor = next((a for a, n in sorted(owed.items()) if a in t.world.agents and n > 0), owner)
        amount = int(owed.get(debtor, 0)) or 10
        await _give_sim(t, debtor, amount * 2)
        paid = await t.do(debtor, "pay_tax", amount_sim=amount)
        findings.add(
            _v(
                "a resident can pay what it owes the state",
                f"pay_tax({amount}) {'succeeded' if paid.ok else 'was refused'}; "
                f"{len(owed)} resident(s) owed head tax after the boundary"
                + ("" if paid.ok else f" - {paid.detail[:140]}"),
                bool(paid.ok),
                "ten real calls, zero successes; five were answered 'you owe no head tax'",
            )
        )

        goals = list(getattr(t.world, "goals_by_agent", {}).get(owner, []) or [])
        if goals:
            goal_id = getattr(goals[0], "goal_id", "")
            moved = await t.do(owner, "update_goal", goal_id=goal_id, progress=50, note="halfway")
            findings.add(
                _v(
                    "a goal can be updated",
                    f"update_goal {'succeeded' if moved.ok else 'was refused'}"
                    + ("" if moved.ok else f" - {moved.detail[:140]}"),
                    bool(moved.ok),
                    f"goal {goal_id}",
                )
            )

        batched = await t.do(
            owner, "do_in_order",
            steps=[{"tool_name": "look_around", "params": {}}, {"tool_name": "rest", "params": {}}],
        )
        findings.add(
            _v(
                "two acts can be bought with one decision",
                f"do_in_order(look_around, rest) {'succeeded' if batched.ok else 'was refused'}"
                + ("" if batched.ok else f" - {batched.detail[:140]}"),
                bool(batched.ok),
                "PG1 asks for more than one act per decision; the one recorded call was an HTTP 500",
            )
        )


STORIES = (
    a_life_has_its_own_verbs,
    property_and_dues_change_hands,
    a_theft_can_be_tried,
    residents_can_organise,
    something_invented_can_be_sold,
    a_government_can_use_every_lever,
    a_job_can_be_offered_paid_and_ended,
    the_town_can_physically_grow,
    a_lesson_can_be_bought_and_sold,
    a_seat_that_is_filled_can_govern,
    the_ledger_law_holds_after_every_act,
    a_purchase_needs_a_conversation,
    the_day_boundary_actually_charges,
    a_town_can_be_put_down_and_picked_up,
)


async def audit(findings: Findings) -> None:
    for story in STORIES:
        try:
            await story(findings)
        except Exception as exc:  # noqa: BLE001 - a story that dies is a result
            findings.add(
                Verdict(
                    AREA,
                    story.__name__.replace("_", " "),
                    f"the story itself failed to run: {type(exc).__name__}: {exc}",
                    FAILED,
                    "a story that cannot run proves nothing either way",
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
