"""Ordinary government actions: tax rate, credit-store margin, jobs,
police, permits, builder licenses, tenders, pensions config
(DESIGN.md section 7: "Government powers: taxes, credit-store margin,
jobs, permits, pensions, police, tenders, buildings, land petitions").

These are distinct from charter amendments (`amendments.py`) in exactly
the way DESIGN.md's sentence structure implies: the powers listed above
are exercised directly by the sitting government, while "charter
amendment by 3/5 vote" is called out separately, right after, as the one
power that needs a supermajority. So every action here only requires the
caller to currently hold a seat (PM or minister, checked via
`GovernmentState.is_sitting`) - no cabinet vote, no threshold. DESIGN.md
does not describe collective decision-making for routine governance the
way it explicitly does for amendments and elections, and this module
takes that omission at face value rather than inventing a voting
mechanism the design doesn't ask for.

Every action is gated to `LOCATION_GOVERNMENT_COMPLEX`
(DESIGN.md section 1 lists "government complex" as a genesis building;
section 2: "every tool is location-gated").
"""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationError

from world.charter.errors import (
    CharterValidationError,
    JobNotFound,
    LicenseAlreadyRevoked,
    LicenseNotFound,
    NotAtRequiredLocation,
    NotSittingGovernment,
    TenderNotFound,
    TenderNotOpen,
)
from world.charter.events import (
    EVENT_BUSINESS_LICENSING_SET,
    EVENT_CREDIT_STORE_MARGIN_SET,
    EVENT_JOB_CREATED,
    EVENT_JOB_CUT,
    EVENT_LICENSE_ISSUED,
    EVENT_LICENSE_REVOKED,
    EVENT_PENSION_CONFIG_SET,
    EVENT_POLICE_HIRED,
    EVENT_PRICE_CONTROL_SET,
    EVENT_TAX_RATE_SET,
    EVENT_TENDER_AWARDED,
    EVENT_TENDER_CANCELLED,
    EVENT_TENDER_POSTED,
    EventSink,
    build_event,
    noop_sink,
)
from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    TICKS_PER_GAME_DAY,
    GovernmentPolicy,
    Job,
    JobStatus,
    License,
    LicenseStatus,
    PayrollLine,
    Tender,
    TenderStatus,
)
from world.charter.store import CharterStore

# ECON-10. Emitted when a payer reports that government salary days have
# been settled. Declared beside the method that raises it - every other
# constant in this module's event vocabulary marks a decision a seat
# holder took, and this one marks a payment somebody else made. The name
# follows the same "domain.thing.happened" shape as `events.py`.
EVENT_PAYROLL_PAID = "politics.payroll.paid"


class GovernmentActions:
    def __init__(self, store: CharterStore, event_sink: EventSink | None = None) -> None:
        self.store = store
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def require_sitting(self, agent_id: str, location: str) -> None:
        if location != LOCATION_GOVERNMENT_COMPLEX:
            raise NotAtRequiredLocation(LOCATION_GOVERNMENT_COMPLEX, location)
        government_state = await self.store.get_government_state()
        if not government_state.is_sitting(agent_id):
            raise NotSittingGovernment(agent_id)

    # -- tax and credit-store margin ----------------------------------

    async def set_tax_rate(
        self, tax_rate_bps: int, set_by_agent_id: str, location: str, now_tick: int
    ) -> GovernmentPolicy:
        await self.require_sitting(set_by_agent_id, location)
        current = await self.store.get_policy()
        new_policy = self.build_policy(current, tax_rate_bps=tax_rate_bps)
        await self.store.push_policy(new_policy)
        await self.event_sink(
            build_event(
                EVENT_TAX_RATE_SET,
                {
                    "tax_rate_bps": tax_rate_bps,
                    "set_by_agent_id": set_by_agent_id,
                    "tick": now_tick,
                },
            )
        )
        return new_policy

    async def set_credit_store_margin(
        self, margin_bps: int, set_by_agent_id: str, location: str, now_tick: int
    ) -> GovernmentPolicy:
        await self.require_sitting(set_by_agent_id, location)
        current = await self.store.get_policy()
        new_policy = self.build_policy(current, credit_store_margin_bps=margin_bps)
        await self.store.push_policy(new_policy)
        await self.event_sink(
            build_event(
                EVENT_CREDIT_STORE_MARGIN_SET,
                {
                    "credit_store_margin_bps": margin_bps,
                    "set_by_agent_id": set_by_agent_id,
                    "tick": now_tick,
                },
            )
        )
        return new_policy

    async def set_pension_config(
        self,
        set_by_agent_id: str,
        location: str,
        now_tick: int,
        private_pct: int | None = None,
        government_pct: int | None = None,
        eligibility_age: int | None = None,
    ) -> GovernmentPolicy:
        await self.require_sitting(set_by_agent_id, location)
        current = await self.store.get_policy()
        updates: dict[str, int] = {}
        if private_pct is not None:
            updates["pension_private_pct"] = private_pct
        if government_pct is not None:
            updates["pension_government_pct"] = government_pct
        if eligibility_age is not None:
            updates["pension_eligibility_age"] = eligibility_age
        new_policy = self.build_policy(current, **updates)
        await self.store.push_policy(new_policy)
        await self.event_sink(
            build_event(
                EVENT_PENSION_CONFIG_SET,
                {
                    "pension_private_pct": new_policy.pension_private_pct,
                    "pension_government_pct": new_policy.pension_government_pct,
                    "pension_eligibility_age": new_policy.pension_eligibility_age,
                    "set_by_agent_id": set_by_agent_id,
                    "tick": now_tick,
                },
            )
        )
        return new_policy

    # -- price controls and licensing (GOV-4) ----------------------------

    async def set_price_control(
        self,
        item: str,
        set_by_agent_id: str,
        location: str,
        now_tick: int,
        max_sim: int | None = None,
        min_sim: int | None = None,
        clear: bool = False,
    ) -> GovernmentPolicy:
        """Cap, floor, or free the price of one item.

        Setting only one side keeps the other; `clear` lifts both. A floor
        above the cap is refused by the policy's own validator, so no seat
        can decree a price nobody could legally charge.
        """
        await self.require_sitting(set_by_agent_id, location)
        current = await self.store.get_policy()
        caps = dict(current.price_caps)
        floors = dict(current.price_floors)
        if clear:
            caps.pop(item, None)
            floors.pop(item, None)
        if max_sim is not None:
            caps[item] = max_sim
        if min_sim is not None:
            floors[item] = min_sim
        new_policy = self.build_policy(current, price_caps=caps, price_floors=floors)
        await self.store.push_policy(new_policy)
        await self.event_sink(
            build_event(
                EVENT_PRICE_CONTROL_SET,
                {
                    "item": item,
                    "old_cap_sim": current.price_caps.get(item),
                    "old_floor_sim": current.price_floors.get(item),
                    "cap_sim": new_policy.price_caps.get(item),
                    "floor_sim": new_policy.price_floors.get(item),
                    "set_by_agent_id": set_by_agent_id,
                    "version": new_policy.version,
                    "tick": now_tick,
                },
            )
        )
        return new_policy

    async def set_business_licensing(
        self, mode: str, set_by_agent_id: str, location: str, now_tick: int
    ) -> GovernmentPolicy:
        await self.require_sitting(set_by_agent_id, location)
        current = await self.store.get_policy()
        new_policy = self.build_policy(current, business_licensing=mode)
        await self.store.push_policy(new_policy)
        await self.event_sink(
            build_event(
                EVENT_BUSINESS_LICENSING_SET,
                {
                    "old": current.business_licensing,
                    "new": new_policy.business_licensing,
                    "set_by_agent_id": set_by_agent_id,
                    "version": new_policy.version,
                    "tick": now_tick,
                },
            )
        )
        return new_policy

    def build_policy(self, current: GovernmentPolicy, **updates: object) -> GovernmentPolicy:
        data = current.model_dump()
        data.update(updates)
        data["version"] = current.version + 1
        try:
            return GovernmentPolicy(**data)
        except ValidationError as exc:
            raise CharterValidationError([str(err["msg"]) for err in exc.errors()]) from exc

    # -- jobs -----------------------------------------------------------

    async def create_job(
        self,
        title: str,
        job_type: str,
        salary_sim: int,
        created_by_agent_id: str,
        location: str,
        now_tick: int,
    ) -> Job:
        await self.require_sitting(created_by_agent_id, location)
        try:
            job = Job(
                title=title,
                job_type=job_type,
                salary_sim=salary_sim,
                created_by_agent_id=created_by_agent_id,
                created_tick=now_tick,
            )
        except ValidationError as exc:
            raise CharterValidationError([str(err["msg"]) for err in exc.errors()]) from exc
        await self.store.put_job(job)
        await self.event_sink(
            build_event(
                EVENT_JOB_CREATED, {"job_id": job.job_id, "title": title, "job_type": job_type}
            )
        )
        return job

    async def cut_job(self, job_id: str, cut_by_agent_id: str, location: str, now_tick: int) -> Job:
        await self.require_sitting(cut_by_agent_id, location)
        job = await self.store.get_job(job_id)
        if job is None:
            raise JobNotFound(job_id)
        job.status = JobStatus.CUT
        job.cut_by_agent_id = cut_by_agent_id
        job.cut_tick = now_tick
        await self.store.put_job(job)
        await self.event_sink(
            build_event(EVENT_JOB_CUT, {"job_id": job_id, "cut_by_agent_id": cut_by_agent_id})
        )
        return job

    async def hire_police(
        self, agent_id: str, salary_sim: int, hired_by_agent_id: str, location: str, now_tick: int
    ) -> Job:
        """Sugar over `create_job` with `job_type="police"`, filled
        immediately: DESIGN.md section 6 describes police as "engines
        recruited on treasury salary", i.e. hiring and filling happen in
        the same act, unlike a generic job posting that can sit vacant.
        """
        await self.require_sitting(hired_by_agent_id, location)
        try:
            job = Job(
                title="police officer",
                job_type="police",
                salary_sim=salary_sim,
                assigned_agent_id=agent_id,
                status=JobStatus.FILLED,
                created_by_agent_id=hired_by_agent_id,
                created_tick=now_tick,
            )
        except ValidationError as exc:
            raise CharterValidationError([str(err["msg"]) for err in exc.errors()]) from exc
        await self.store.put_job(job)
        await self.event_sink(
            build_event(
                EVENT_POLICE_HIRED,
                {"job_id": job.job_id, "agent_id": agent_id, "salary_sim": salary_sim},
            )
        )
        return job

    # -- payroll (ECON-10) ------------------------------------------------
    #
    # `create_job` and `hire_police` stored a `salary_sim` that nothing
    # ever read as an obligation: a government could staff the whole town
    # and the money never moved, because no one could ask which posts were
    # filled, what each was owed, or whether a day had already been paid.
    # These three methods are that missing question, answered.
    #
    # The paying itself is not here and must not be: this module holds no
    # reference to the money layer (see `models.py`'s module docstring on
    # the import boundary - charter/ is imported BY the tool layer, so
    # importing back would be a cycle). The shape is the same one GOV-23's
    # licence fee settled on: charter/ decides, the caller pays. A payer
    # reads `payroll_due`, moves the money, and reports back with
    # `record_payroll_paid` so the same game-day cannot be paid twice.

    async def payroll_due(self, now_tick: int) -> list[PayrollLine]:
        """Every filled government post owed salary as of `now_tick`.

        One line per job, never per agent: a resident holding two posts is
        owed for both, and the payer needs the `job_id` to report each one
        settled. Jobs owing nothing - vacant, unpaid, or already settled
        for today - are absent rather than present with a zero, so an
        empty list means "the payroll is clear" with no arithmetic.
        Sorted by `job_id` so a payroll run is deterministic and two runs
        over the same store produce the same trace.
        """
        jobs = await self.store.list_jobs()
        lines: list[PayrollLine] = []
        for job in sorted(jobs, key=lambda j: j.job_id):
            periods = job.payroll_periods_due(now_tick)
            if periods < 1 or job.assigned_agent_id is None:
                continue
            lines.append(
                PayrollLine(
                    job_id=job.job_id,
                    agent_id=job.assigned_agent_id,
                    title=job.title,
                    job_type=job.job_type,
                    salary_sim=job.salary_sim,
                    periods=periods,
                    amount_sim=periods * job.salary_sim,
                    settled_through_tick=job.settled_through_tick(),
                    now_tick=now_tick,
                )
            )
        return lines

    async def payroll_due_sim(self, now_tick: int) -> int:
        """Total integer sim the treasury owes its staff as of `now_tick`.

        The number a payer needs before it starts, and the number a
        treasury view needs to show a wage bill the town cannot cover.
        """
        return sum(line.amount_sim for line in await self.payroll_due(now_tick))

    async def record_payroll_paid(
        self, job_id: str, periods: int, amount_sim: int, now_tick: int
    ) -> Job:
        """Close `periods` game-days on a job after the payer moved the money.

        Not gated on a seat: this is a payer reporting a fact, not a
        government exercising a power, and the day job that runs it holds
        no seat. The guard that matters is the arithmetic one - you cannot
        settle more periods than are actually due, so a payer that runs
        twice in one game-day, or retries after a partial failure, is
        refused rather than quietly paying the same salary again.

        `amount_sim` is what the payer actually moved, recorded as-is: a
        treasury too poor to pay in full still closes the period it paid
        for, and `total_paid_sim` stays an honest cumulative total rather
        than a restatement of what was owed.
        """
        job = await self.store.get_job(job_id)
        if job is None:
            raise JobNotFound(job_id)
        errors: list[str] = []
        if periods < 1:
            errors.append(f"periods={periods} must be >= 1")
        if amount_sim < 0:
            errors.append(f"amount_sim={amount_sim} must be >= 0")
        owed_periods = job.payroll_periods_due(now_tick)
        if periods > owed_periods:
            errors.append(
                f"job {job_id} is owed {owed_periods} period(s) at tick {now_tick}, "
                f"cannot settle {periods} - it is already paid through tick "
                f"{job.settled_through_tick()}"
            )
        if errors:
            raise CharterValidationError(errors)
        job.last_paid_tick = job.settled_through_tick() + periods * TICKS_PER_GAME_DAY
        job.total_paid_sim += amount_sim
        await self.store.put_job(job)
        await self.event_sink(
            build_event(
                EVENT_PAYROLL_PAID,
                {
                    "job_id": job_id,
                    "agent_id": job.assigned_agent_id,
                    "job_type": job.job_type,
                    "periods": periods,
                    "amount_sim": amount_sim,
                    "paid_through_tick": job.last_paid_tick,
                    "tick": now_tick,
                    # GOV-20's interrupt list: the resident who was just
                    # paid should notice, and nobody else should be woken
                    # by somebody else's wage day.
                    "involved_agent_ids": (
                        [job.assigned_agent_id] if job.assigned_agent_id else []
                    ),
                },
            )
        )
        return job

    # -- pension terms (GOV-09) -------------------------------------------

    async def pension_due_sim(
        self, last_salary_sim: int, employment_type: str, age_years: float
    ) -> int:
        """Integer sim owed to one retiree for one pension period, under
        the policy currently in force (GOV-09).

        The `pension_*` fields were levers nothing read: the module that
        pays retirees took its percentages and its retirement age from
        module-level constants, so a government that voted the private
        rate up to 40% or retirement down to 65 changed numbers no payment
        ever consulted. This is the reading end of those levers - a payer
        asks here instead of asking a constant.
        """
        policy = await self.store.get_policy()
        kind: Literal["private", "government"] = (
            "government" if employment_type == "government" else "private"
        )
        return policy.pension_due_sim(last_salary_sim, kind, age_years)

    async def pension_employment_type(self, agent_id: str) -> str:
        """`"government"` if this resident ever held a government post,
        else `"private"` (GOV-09).

        DESIGN.md section 4 pays a government retiree twice what it pays a
        private one, and this store is the only place in the town that
        knows which somebody was: a post that was later cut still counts,
        because a retired police officer did serve.
        """
        for job in await self.store.list_jobs():
            if job.assigned_agent_id == agent_id:
                return "government"
        return "private"

    # -- permits and builder licenses -----------------------------------

    async def issue_license(
        self,
        license_type: str,
        holder_agent_id: str,
        fee_paid_sim: int,
        issued_by_agent_id: str,
        location: str,
        now_tick: int,
    ) -> License:
        await self.require_sitting(issued_by_agent_id, location)
        license_ = License(
            license_type=license_type,
            holder_agent_id=holder_agent_id,
            fee_paid_sim=fee_paid_sim,
            issued_by_agent_id=issued_by_agent_id,
            issued_tick=now_tick,
        )
        await self.store.put_license(license_)
        await self.event_sink(
            build_event(
                EVENT_LICENSE_ISSUED,
                {
                    "license_id": license_.license_id,
                    "license_type": license_type,
                    "holder_agent_id": holder_agent_id,
                    # GOV-04: carried over from the tool handler's own copy
                    # of this event, which is being deleted as a duplicate.
                    # `involved_agent_ids` is GOV-20's interrupt list -
                    # `core/perception.py:52` wakes exactly these residents,
                    # so dropping it would either wake nobody or, if some
                    # later reader defaulted it, wake the whole town.
                    "issued_by_agent_id": issued_by_agent_id,
                    "involved_agent_ids": sorted(
                        {holder_agent_id, issued_by_agent_id}
                    ),
                },
            )
        )
        return license_

    async def issue_permit(
        self,
        permit_subtype: str,
        holder_agent_id: str,
        fee_paid_sim: int,
        issued_by_agent_id: str,
        location: str,
        now_tick: int,
    ) -> License:
        return await self.issue_license(
            f"permit:{permit_subtype}",
            holder_agent_id,
            fee_paid_sim,
            issued_by_agent_id,
            location,
            now_tick,
        )

    async def issue_builder_license(
        self,
        holder_agent_id: str,
        fee_paid_sim: int,
        issued_by_agent_id: str,
        location: str,
        now_tick: int,
    ) -> License:
        return await self.issue_license(
            "builder_license", holder_agent_id, fee_paid_sim, issued_by_agent_id, location, now_tick
        )

    async def revoke_license(
        self, license_id: str, revoked_by_agent_id: str, reason: str, location: str, now_tick: int
    ) -> License:
        await self.require_sitting(revoked_by_agent_id, location)
        license_ = await self.store.get_license(license_id)
        if license_ is None:
            raise LicenseNotFound(license_id)
        if license_.status == LicenseStatus.REVOKED:
            raise LicenseAlreadyRevoked(license_id)
        license_.status = LicenseStatus.REVOKED
        license_.revoked_by_agent_id = revoked_by_agent_id
        license_.revoked_tick = now_tick
        license_.revoke_reason = reason
        await self.store.put_license(license_)
        await self.event_sink(
            build_event(EVENT_LICENSE_REVOKED, {"license_id": license_id, "reason": reason})
        )
        return license_

    # -- tenders ----------------------------------------------------------

    async def post_tender(
        self,
        title: str,
        description: str,
        budget_sim: int,
        posted_by_agent_id: str,
        location: str,
        now_tick: int,
    ) -> Tender:
        await self.require_sitting(posted_by_agent_id, location)
        try:
            tender = Tender(
                title=title,
                description=description,
                budget_sim=budget_sim,
                posted_by_agent_id=posted_by_agent_id,
                posted_tick=now_tick,
            )
        except ValidationError as exc:
            raise CharterValidationError([str(err["msg"]) for err in exc.errors()]) from exc
        await self.store.put_tender(tender)
        await self.event_sink(
            build_event(EVENT_TENDER_POSTED, {"tender_id": tender.tender_id, "title": title})
        )
        return tender

    async def award_tender(
        self,
        tender_id: str,
        awarded_to_agent_id: str,
        awarded_by_agent_id: str,
        location: str,
        now_tick: int,
    ) -> Tender:
        await self.require_sitting(awarded_by_agent_id, location)
        tender = await self.store.get_tender(tender_id)
        if tender is None:
            raise TenderNotFound(tender_id)
        if tender.status != TenderStatus.OPEN:
            raise TenderNotOpen(tender_id, tender.status.value)
        tender.status = TenderStatus.AWARDED
        tender.awarded_to_agent_id = awarded_to_agent_id
        tender.awarded_tick = now_tick
        await self.store.put_tender(tender)
        await self.event_sink(
            build_event(
                EVENT_TENDER_AWARDED,
                {"tender_id": tender_id, "awarded_to_agent_id": awarded_to_agent_id},
            )
        )
        return tender

    async def cancel_tender(
        self, tender_id: str, cancelled_by_agent_id: str, reason: str, location: str, now_tick: int
    ) -> Tender:
        await self.require_sitting(cancelled_by_agent_id, location)
        tender = await self.store.get_tender(tender_id)
        if tender is None:
            raise TenderNotFound(tender_id)
        if tender.status != TenderStatus.OPEN:
            raise TenderNotOpen(tender_id, tender.status.value)
        tender.status = TenderStatus.CANCELLED
        tender.cancel_reason = reason
        await self.store.put_tender(tender)
        await self.event_sink(
            build_event(EVENT_TENDER_CANCELLED, {"tender_id": tender_id, "reason": reason})
        )
        return tender
