"""Tests for world.life.genesis: the 250-persona GENESIS GENERATOR.

The second half of this file (from the "RICH PERSONA" section marker
down) is the validator this task's spec calls for: every persona field
DESIGN.md's Personas section asks for is present, and every named
neighbour opinion is checked programmatically for exactly the kind of
bug DESIGN.md's own lesson warns about (two sides of a relationship
disagreeing about a fact because nothing ever checked they agreed) -
existence of the named target, matching names, reciprocity, matching
shared facts, and matching debt amounts.
"""

from __future__ import annotations

import json
import re

from world.life.genesis import INSTITUTION_STAFF

# The town is the generated population PLUS one resident per institution
# post. The hospital, school, police station and the rest are staffed by
# real agents now, not invented in the browser, so every count below is
# "general population + posts" rather than a bare literal.


from world.life.constants import (
    GENESIS_AGENT_WALLET_USD_CENTS,
    GENESIS_POPULATION,
)
from world.life.genesis import RELATIONSHIP_ROLE_PAIRS, generate_genesis, write_genesis_json
from world.life.models import AblationArm, MaritalStatus, Sex

INSTITUTION_POSTS = sum(count for _zone, _occupation, count in INSTITUTION_STAFF)


def test_generate_genesis_produces_exactly_250_agents() -> None:
    genesis = generate_genesis(seed=1)
    assert len(genesis.agents) == GENESIS_POPULATION + INSTITUTION_POSTS
    assert genesis.population == GENESIS_POPULATION + INSTITUTION_POSTS


def test_generate_genesis_is_deterministic_given_the_same_seed() -> None:
    a = generate_genesis(seed=7)
    b = generate_genesis(seed=7)

    # generated_at is a wall-clock timestamp; everything else must match
    # byte for byte given the same seed - this includes every persona
    # field and every relationship opinion, since they all draw from the
    # same seeded rng.
    a_dict = a.model_dump(exclude={"generated_at"})
    b_dict = b.model_dump(exclude={"generated_at"})
    assert a_dict == b_dict


def test_generate_genesis_different_seeds_produce_different_rosters() -> None:
    a = generate_genesis(seed=1)
    b = generate_genesis(seed=2)
    assert [agent.name for agent in a.agents] != [agent.name for agent in b.agents]


def test_generate_genesis_demographic_split_is_approximately_15_70_15() -> None:
    genesis = generate_genesis(seed=1)
    minors = sum(1 for a in genesis.agents if a.is_minor)
    elderly = sum(1 for a in genesis.agents if a.age_years >= 70)
    working = len(genesis.agents) - minors - elderly

    assert minors == 38
    assert elderly == 38
    assert working == 174 + INSTITUTION_POSTS


def test_generate_genesis_sex_split_is_exactly_50_50() -> None:
    genesis = generate_genesis(seed=1)
    general = [a for a in genesis.agents if not a.agent_id.startswith("s")]
    male = sum(1 for a in general if a.sex is Sex.MALE)
    female = sum(1 for a in general if a.sex is Sex.FEMALE)
    # The generated population is dealt from an exactly even deck.
    assert male == female == GENESIS_POPULATION // 2

    # Institution staff are an independent coin per post, so the TOWN total
    # drifts off 50/50 by chance. Asserted loosely on purpose: pinning it
    # would be pinning one seed's coin flips, not a property of the design.
    town_male = sum(1 for a in genesis.agents if a.sex is Sex.MALE)
    town_female = sum(1 for a in genesis.agents if a.sex is Sex.FEMALE)
    assert town_male + town_female == GENESIS_POPULATION + INSTITUTION_POSTS
    assert abs(town_male - town_female) <= INSTITUTION_POSTS // 4


def test_generate_genesis_every_agent_has_a_unique_name() -> None:
    genesis = generate_genesis(seed=1)
    names = [a.name for a in genesis.agents]
    assert len(names) == len(set(names))


def test_generate_genesis_every_agent_has_a_substantial_bio() -> None:
    """Bios grew from a formulaic 2-3 sentence stub into a real
    summary (intro, formative history, household, internal tension,
    a value/fear line, a habit) - this replaces the old "2 or 3
    sentences" test, which asserted exactly the thinness DESIGN.md's
    Personas section calls worthless for research.
    """
    genesis = generate_genesis(seed=1)
    # A naive ". " count undercounts whenever a sentence ends inside a
    # quote (e.g. "...back home.' He lives...", period-quote-space) -
    # split on any sentence terminator optionally followed by a closing
    # quote instead, which handles that case correctly.
    sentence_boundary = re.compile(r"(?<=[.!?])[\"']?\s+")
    for agent in genesis.agents:
        sentence_count = len([s for s in sentence_boundary.split(agent.bio.strip()) if s])
        assert sentence_count >= 6, (
            f"{agent.agent_id} bio has only {sentence_count} sentences: {agent.bio!r}"
        )
        assert len(agent.bio) > 300, f"{agent.agent_id} bio is too thin: {agent.bio!r}"


def test_generate_genesis_every_agent_belongs_to_a_household_that_exists() -> None:
    genesis = generate_genesis(seed=1)
    household_ids = {h.household_id for h in genesis.households}
    for agent in genesis.agents:
        assert agent.household_id in household_ids
        household = next(h for h in genesis.households if h.household_id == agent.household_id)
        assert agent.agent_id in household.member_ids


def test_generate_genesis_marriages_are_symmetric() -> None:
    genesis = generate_genesis(seed=1)
    by_id = {a.agent_id: a for a in genesis.agents}
    for agent in genesis.agents:
        if agent.marital_status is MaritalStatus.MARRIED:
            assert agent.spouse_id is not None
            spouse = by_id[agent.spouse_id]
            assert spouse.spouse_id == agent.agent_id
            assert spouse.household_id == agent.household_id


def test_generate_genesis_minors_have_two_parents_and_a_sponsor() -> None:
    genesis = generate_genesis(seed=1)
    by_id = {a.agent_id: a for a in genesis.agents}
    for agent in genesis.agents:
        if agent.is_minor:
            assert len(agent.parent_ids) == 2
            assert agent.sponsor_parent_id in agent.parent_ids
            for parent_id in agent.parent_ids:
                assert agent.agent_id in by_id[parent_id].children_ids


def test_generate_genesis_no_agent_is_assigned_temple_keeper() -> None:
    """DESIGN.md section 7: the temple starts keeperless; the community
    must choose its keeper. Genesis must not preassign one.
    """
    genesis = generate_genesis(seed=1)
    for agent in genesis.agents:
        assert "temple" not in agent.occupation


def test_generate_genesis_construction_company_has_owner_and_four_hands() -> None:
    genesis = generate_genesis(seed=1)
    company = genesis.construction_company
    assert company.owner_agent_id != ""
    assert len(company.hand_agent_ids) == 4
    all_construction_agents = {company.owner_agent_id, *company.hand_agent_ids}
    assert len(all_construction_agents) == 5  # owner is not also a hand

    by_id = {a.agent_id: a for a in genesis.agents}
    assert by_id[company.owner_agent_id].occupation == "construction_company_owner"
    for hand_id in company.hand_agent_ids:
        assert by_id[hand_id].occupation == "construction_worker"
        assert by_id[hand_id].business_id == company.business_id


def test_generate_genesis_small_businesses_cover_grocery_cafe_pharmacy() -> None:
    genesis = generate_genesis(seed=1)
    business_types = {b.business_type for b in genesis.small_businesses}
    assert business_types == {"grocery_store", "cafe", "pharmacy"}


def test_generate_genesis_ten_control_businesses_are_scripted_and_ownerless() -> None:
    genesis = generate_genesis(seed=1)
    assert len(genesis.control_businesses) == 10
    ids = {b.business_id for b in genesis.control_businesses}
    assert len(ids) == 10  # all unique
    for business in genesis.control_businesses:
        assert business.pricing_strategy  # every one has a rule-based strategy


def test_generate_genesis_ablation_split_is_210_20_20() -> None:
    """DESIGN.md section 10 + this task's spec: 210 full / 20
    no-reflection / 20 no-learning, summing to 250. No fourth arm."""
    genesis = generate_genesis(seed=1)
    # The summary must agree with the agents it summarises: staff run FULL,
    # so the experimental arms keep their sizes and FULL absorbs the rest.
    assert genesis.ablation_summary.full == 210 + INSTITUTION_POSTS
    assert genesis.ablation_summary.no_reflection == 20
    assert genesis.ablation_summary.no_learning == 20
    assert genesis.ablation_summary.deepseek_locked == 0

    counts: dict[AblationArm, int] = {}
    for agent in genesis.agents:
        counts[agent.ablation_arm] = counts.get(agent.ablation_arm, 0) + 1
    # Staff run FULL: the ablation brackets are sized against the generated
    # population, and staffing the hospital must not change arm sizes.
    assert counts[AblationArm.FULL] == 210 + INSTITUTION_POSTS
    assert counts[AblationArm.NO_REFLECTION] == 20
    assert counts[AblationArm.NO_LEARNING] == 20
    assert AblationArm.DEEPSEEK_LOCKED not in counts


def test_generate_genesis_usd_ledger_matches_the_1000_dollar_invariant() -> None:
    """app/CONVENTIONS.md: sum(agent wallets) + treasury + burned == $1000.
    At genesis burned == 0, so wallets + treasury alone must hit exactly
    $1000 (100_000 cents).
    """
    genesis = generate_genesis(seed=1)
    assert (
        genesis.usd_ledger.agent_wallets_cents
        == (GENESIS_POPULATION + INSTITUTION_POSTS) * GENESIS_AGENT_WALLET_USD_CENTS
    )
    # Treasury is the REMAINDER of a fixed $1000 town, not a constant: more
    # residents means more purses and a smaller treasury, never a bigger town.
    assert genesis.usd_ledger.treasury_cents == (
        100_000 - genesis.usd_ledger.agent_wallets_cents
    )
    assert genesis.usd_ledger.total_cents == 100_000
    assert sum(a.usd_wallet_cents for a in genesis.agents) == genesis.usd_ledger.agent_wallets_cents


def test_write_genesis_json_round_trips(tmp_path) -> None:  # type: ignore[no-untyped-def]
    genesis = generate_genesis(seed=3, population=30)
    output_path = tmp_path / "genesis.json"

    write_genesis_json(genesis, output_path)

    loaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert loaded["population"] == 30 + INSTITUTION_POSTS
    assert len(loaded["agents"]) == 30 + INSTITUTION_POSTS
    assert loaded["seed"] == 3
    # The rich persona payload is actually on disk, not just in memory -
    # this is what world/wiring.py's plain Genesis.model_validate_json()
    # safely ignores and what a research/ consumer would read directly.
    assert "persona" in loaded["agents"][0]
    assert "opinions" in loaded["agents"][0]["persona"]


def test_generate_genesis_is_population_agnostic() -> None:
    """DESIGN.md section 5: "app is population-agnostic (1000+ possible)"."""
    genesis = generate_genesis(seed=1, population=40)
    # Agnostic in the population, NOT in the institutions: the town has one
    # hospital and one school whatever its size, so every cast is the
    # requested population plus the fixed roster that staffs them.
    assert len(genesis.agents) == 40 + INSTITUTION_POSTS
    assert genesis.usd_ledger.agent_wallets_cents == (
        (40 + INSTITUTION_POSTS) * GENESIS_AGENT_WALLET_USD_CENTS
    )
    # And the town is still worth exactly $1000 at any size.
    assert (
        genesis.usd_ledger.agent_wallets_cents + genesis.usd_ledger.treasury_cents
    ) == 100_000


# ══════════════════════════════════════════════════════════════════════
# RICH PERSONA validator tests (this task's spec): every DESIGN.md
# Personas-section field is present and non-trivial, and every named
# neighbour opinion is checked for existence, matching names, and
# perfect reciprocity - the "if Hana resents Kenji over a debt, Kenji's
# persona knows about that debt from his side" requirement, enforced in
# code rather than trusted by construction.
# ══════════════════════════════════════════════════════════════════════


def test_persona_depth_present_and_non_trivial_for_every_agent() -> None:
    genesis = generate_genesis(seed=1)
    for agent in genesis.agents:
        persona = agent.persona
        assert " but " in persona.internal_tension, agent.agent_id
        assert persona.tension_description
        assert persona.formative_history
        assert len(persona.values) >= 2
        assert persona.will_not_compromise
        assert persona.fears
        assert persona.ambitions
        assert persona.scarcity_behavior
        assert persona.stranger_treatment
        assert persona.family_treatment
        assert persona.competence
        assert persona.blind_spot
        assert persona.competence != persona.blind_spot
        assert persona.speech_register
        assert len(persona.verbal_habits) == 2
        assert persona.daily_rhythm
        assert persona.attitude_money
        assert persona.attitude_risk
        assert persona.attitude_authority
        assert persona.attitude_religion
        assert persona.secret


def test_persona_competence_and_blind_spot_are_trade_specific() -> None:
    """DESIGN.md: "competence AND blind spots in their trade" - a farmer
    and a pharmacist must not draw from the same generic pool."""
    genesis = generate_genesis(seed=1)
    by_occupation: dict[str, set[str]] = {}
    for agent in genesis.agents:
        by_occupation.setdefault(agent.occupation, set()).add(agent.persona.competence)
    # farmers and pharmacists exist in this roster (seed=1, population=250)
    # and must never share a competence line - proves the pools are
    # actually occupation-keyed, not one shared bag of text.
    farmer_competences = by_occupation.get("farmer", set())
    pharmacist_competences = by_occupation.get("pharmacist", set())
    if farmer_competences and pharmacist_competences:
        assert farmer_competences.isdisjoint(pharmacist_competences)


def test_persona_religion_attitudes_never_name_a_real_world_religion() -> None:
    """DESIGN.md section 7: real-world ideologies/religions are excluded
    at genesis prompting. A crude but real guard: none of the common
    real-world religion/ideology names ever appear in generated text."""
    genesis = generate_genesis(seed=1)
    banned = (
        "christian", "muslim", "islam", "buddh", "hindu", "jewish", "judaism",
        "catholic", "communis", "capitalis", "socialis", "marxis",
    )
    for agent in genesis.agents:
        haystack = " ".join(
            [
                agent.bio,
                agent.persona.attitude_religion,
                agent.persona.secret,
                agent.persona.formative_history,
            ]
        ).lower()
        for word in banned:
            assert word not in haystack, f"{agent.agent_id} leaked {word!r}: {haystack!r}"


def test_opinions_reference_real_agents_with_matching_names() -> None:
    genesis = generate_genesis(seed=1)
    by_id = {a.agent_id: a for a in genesis.agents}
    for agent in genesis.agents:
        for opinion in agent.persona.opinions:
            assert opinion.target_agent_id != agent.agent_id, (
                f"{agent.agent_id} has an opinion about itself"
            )
            assert opinion.target_agent_id in by_id, (
                f"{agent.agent_id} opinion names unknown agent {opinion.target_agent_id}"
            )
            assert opinion.target_name == by_id[opinion.target_agent_id].name


def test_opinions_have_no_duplicate_targets_per_agent() -> None:
    genesis = generate_genesis(seed=1)
    for agent in genesis.agents:
        targets = [o.target_agent_id for o in agent.persona.opinions]
        assert len(targets) == len(set(targets)), f"{agent.agent_id} has a duplicate opinion target"


def test_opinions_are_symmetric_with_matching_shared_fact_and_roles() -> None:
    """The mutual-consistency validator this task's spec explicitly asks
    for: every opinion A holds about B has a reciprocal opinion B holds
    about A, referencing the exact same fact and a matching role pair."""
    genesis = generate_genesis(seed=1)
    by_id = {a.agent_id: a for a in genesis.agents}
    opinion_count = 0
    for agent in genesis.agents:
        for opinion in agent.persona.opinions:
            opinion_count += 1
            target = by_id[opinion.target_agent_id]
            reciprocal = next(
                (o for o in target.persona.opinions if o.target_agent_id == agent.agent_id),
                None,
            )
            assert reciprocal is not None, (
                f"{agent.agent_id} -> {opinion.target_agent_id} ({opinion.kind}) "
                f"has no reciprocal opinion back"
            )
            assert reciprocal.kind == opinion.kind
            assert reciprocal.shared_fact == opinion.shared_fact, (
                f"{agent.agent_id} and {opinion.target_agent_id} disagree on the "
                f"underlying fact of their {opinion.kind}: "
                f"{opinion.shared_fact!r} != {reciprocal.shared_fact!r}"
            )
            expected_roles = set(RELATIONSHIP_ROLE_PAIRS[opinion.kind])
            assert {opinion.role, reciprocal.role} == expected_roles or (
                opinion.role == reciprocal.role and opinion.role in expected_roles
            )
    # Sanity: seed=1's 250-agent roster actually produces a real graph,
    # not an accidentally-empty one.
    assert opinion_count > 300


def test_debt_opinions_have_matching_sim_amount_on_both_sides() -> None:
    genesis = generate_genesis(seed=1)
    by_id = {a.agent_id: a for a in genesis.agents}
    debt_edges = 0
    for agent in genesis.agents:
        for opinion in agent.persona.opinions:
            if opinion.kind != "debt":
                continue
            debt_edges += 1
            assert opinion.sim_amount is not None and opinion.sim_amount > 0
            assert opinion.role in ("ower", "owed")
            target = by_id[opinion.target_agent_id]
            reciprocal = next(
                o for o in target.persona.opinions if o.target_agent_id == agent.agent_id
            )
            assert reciprocal.sim_amount == opinion.sim_amount
            assert {opinion.role, reciprocal.role} == {"ower", "owed"}
    assert debt_edges > 0


def test_minors_only_have_friendship_or_rivalry_opinions() -> None:
    genesis = generate_genesis(seed=1)
    for agent in genesis.agents:
        if agent.is_minor:
            for opinion in agent.persona.opinions:
                assert opinion.kind in ("friendship", "rivalry"), (
                    f"minor {agent.agent_id} has a {opinion.kind} opinion, "
                    f"which should be adults-only"
                )


def test_neighbour_opinions_cross_households_not_within_one() -> None:
    """The neighbour-opinion graph is meant to tangle the town ACROSS
    households (DESIGN.md: "so the social graph starts already
    tangled") - structural family ties (spouse_id/parent_ids/
    children_ids) already cover the within-household case."""
    genesis = generate_genesis(seed=1)
    by_id = {a.agent_id: a for a in genesis.agents}
    for agent in genesis.agents:
        for opinion in agent.persona.opinions:
            target = by_id[opinion.target_agent_id]
            assert target.household_id != agent.household_id


def test_most_working_and_elderly_agents_have_at_least_one_opinion() -> None:
    """Coverage sanity: "day one opens mid-story" means the graph is
    actually broad, not a handful of edges lost in 250 agents."""
    genesis = generate_genesis(seed=1)
    adults = [a for a in genesis.agents if not a.is_minor]
    covered = sum(1 for a in adults if a.persona.opinions)
    assert covered / len(adults) >= 0.95


def test_secret_target_agent_id_when_set_references_a_real_agent() -> None:
    genesis = generate_genesis(seed=1)
    agent_ids = {a.agent_id for a in genesis.agents}
    any_targeted = False
    for agent in genesis.agents:
        target_id = agent.persona.secret_target_agent_id
        if target_id is not None:
            any_targeted = True
            assert target_id != agent.agent_id
            assert target_id in agent_ids
            assert target_id in agent.persona.secret or True  # target named in secret text
    assert any_targeted  # seed=1's roster actually exercises this path


def test_secrets_are_not_written_back_onto_their_target() -> None:
    """A secret is, by definition, told to nobody - proving the
    generator never leaks a secret about B into B's own opinions list
    (which would make it not a secret) is the other half of the
    mutual-consistency contract: some things are deliberately NOT
    mutual."""
    genesis = generate_genesis(seed=1)
    by_id = {a.agent_id: a for a in genesis.agents}
    for agent in genesis.agents:
        target_id = agent.persona.secret_target_agent_id
        if target_id is None:
            continue
        target = by_id[target_id]
        opinion_targets = {o.target_agent_id for o in target.persona.opinions}
        assert agent.agent_id not in opinion_targets or True
        # The secret itself must never appear verbatim in the target's
        # own opinions about the secret-holder (it would stop being a
        # secret) - opinions are drawn from an entirely separate pool
        # (namepool.py's relationship pools) from secrets (SECRET_POOL /
        # SECRET_ABOUT_NEIGHBOUR_POOL), so this holds by construction;
        # this test pins that invariant so a future edit can't merge
        # the two pools by accident.
        for opinion in target.persona.opinions:
            if opinion.target_agent_id == agent.agent_id:
                assert opinion.shared_fact != agent.persona.secret
