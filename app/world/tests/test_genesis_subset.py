"""A smaller cast has to be a valid town, not a truncated file.

A live Bedrock run costs money per agent per tick, so the first real runs use
a subset. The danger is that a subset LOOKS fine and is quietly broken: a shop
whose owner was left behind, a spouse_id pointing at somebody who no longer
exists, a ledger total that no longer matches the wallets it is supposed to
sum. None of those fail at boot; they fail hours in, after money has been
spent. These check the shape before that happens.
"""

from __future__ import annotations

import json

import pytest

from world.make_genesis_subset import GENESIS, choose, repair

SUBSET_SIZES = [10, 30]


@pytest.fixture(scope="module")
def full() -> dict:
    return json.loads(GENESIS.read_text(encoding="utf-8"))


def build(full: dict, n: int) -> dict:
    keep = set(choose(json.loads(json.dumps(full)), n))
    return repair(json.loads(json.dumps(full)), keep)


@pytest.mark.parametrize("n", SUBSET_SIZES)
def test_the_cast_is_the_size_asked_for(full, n):
    out = build(full, n)
    assert out["population"] == len(out["agents"]) == n


@pytest.mark.parametrize("n", SUBSET_SIZES)
def test_every_reference_points_inside_the_cast(full, n):
    """The whole point of the repair pass."""
    out = build(full, n)
    ids = {a["agent_id"] for a in out["agents"]}
    for a in out["agents"]:
        if a.get("spouse_id"):
            assert a["spouse_id"] in ids, f"{a['name']} is married to somebody who is not here"
        for p in a.get("parent_ids") or []:
            assert p in ids
        for c in a.get("children_ids") or []:
            assert c in ids
        if a.get("sponsor_parent_id"):
            assert a["sponsor_parent_id"] in ids
    hh = {h["household_id"] for h in out["households"]}
    for a in out["agents"]:
        if a.get("household_id"):
            assert a["household_id"] in hh
    for h in out["households"]:
        assert h["member_ids"], "an empty household should have been dropped"
        for m in h["member_ids"]:
            assert m in ids


@pytest.mark.parametrize("n", SUBSET_SIZES)
def test_nobody_is_married_to_nobody(full, n):
    out = build(full, n)
    for a in out["agents"]:
        if a.get("marital_status") == "married":
            assert a.get("spouse_id"), f"{a['name']} is married with no spouse in the cast"


@pytest.mark.parametrize("n", SUBSET_SIZES)
def test_every_surviving_business_has_its_owner(full, n):
    """A shop whose owner was left behind can never open."""
    out = build(full, n)
    ids = {a["agent_id"] for a in out["agents"]}
    for b in out["small_businesses"]:
        assert b["owner_agent_id"] in ids
        for e in b.get("employee_agent_ids") or []:
            assert e in ids
    cc = out.get("construction_company")
    if cc and cc.get("owner_agent_id"):
        assert cc["owner_agent_id"] in ids


@pytest.mark.parametrize("n", SUBSET_SIZES)
def test_no_agent_works_for_a_business_that_is_gone(full, n):
    out = build(full, n)
    live = {b["business_id"] for b in out["small_businesses"]}
    live |= {b["business_id"] for b in out["control_businesses"]}
    cc = out.get("construction_company") or {}
    if cc.get("business_id"):
        live.add(cc["business_id"])
    for a in out["agents"]:
        if a.get("business_id"):
            assert a["business_id"] in live, f"{a['name']} works at a business that was dropped"


@pytest.mark.parametrize("n", SUBSET_SIZES)
def test_the_ledger_adds_up(full, n):
    """The $1000 invariant is re-checked on every move, so a stale total is a
    boot failure, not a rounding annoyance.

    Three INTEGERS, matching world/life/models.py's UsdLedgerSummary. This
    test previously asserted a per-agent {agent_id: cents} map, which is the
    shape the generator happened to produce - so the suite agreed with the
    bug and run_genesis.py rejected the file at boot.
    """
    out = build(full, n)
    led = out["usd_ledger"]
    assert isinstance(led["agent_wallets_cents"], int)
    assert isinstance(led["treasury_cents"], int)
    assert led["agent_wallets_cents"] == sum(a["usd_wallet_cents"] for a in out["agents"])
    assert led["total_cents"] == led["agent_wallets_cents"] + led["treasury_cents"]
    assert led["total_cents"] == full["usd_ledger"]["total_cents"]


@pytest.mark.parametrize("n", SUBSET_SIZES)
def test_the_subset_loads_as_the_runner_will_load_it(full, n):
    """The test that would have caught it.

    Every other test here checks a property of a dict this module just built,
    which cannot detect a field whose SHAPE the rest of the system disagrees
    about. This parses the output with the same pydantic model
    world/run_genesis.py's seed_genesis() uses, so "a valid genesis file" is
    decided by the loader rather than by this file's own opinion.
    """
    from world.life.genesis import RichGenesis

    out = build(full, n)
    parsed = RichGenesis.model_validate_json(json.dumps(out))
    assert len(parsed.agents) == n
    assert parsed.population == n


def test_the_shipped_thirty_agent_file_loads_too():
    """The committed file, not a freshly built one - a regenerate that was
    never run leaves the two out of step."""
    from world.life.genesis import RichGenesis

    path = GENESIS.with_name("genesis_30.json")
    if not path.exists():
        pytest.skip("genesis_30.json has not been generated")
    parsed = RichGenesis.model_validate_json(path.read_text(encoding="utf-8"))
    assert parsed.population == len(parsed.agents) == 30


@pytest.mark.parametrize("n", SUBSET_SIZES)
def test_the_town_can_actually_function(full, n):
    """A cast with no food producer and no shop is not a town, whatever its
    referential integrity says."""
    out = build(full, n)
    roles = {a.get("occupation") for a in out["agents"]}
    assert "farmer" in roles, "nobody grows anything"
    assert roles & {"cafe_owner", "grocery_store_owner", "construction_company_owner"}, \
        "no business owner, so no business opens"
    assert any(not a.get("is_minor") for a in out["agents"]), "nobody of working age"


def test_essential_roles_all_exist_in_genesis(full):
    """A role that matches nobody makes its whole selection phase a no-op.

    This caught the real bug: the first version listed doctor, nurse, baker,
    police_officer and school_teacher - none of which genesis.json has, because
    the viewer assigns those itself. The phase silently selected nobody.
    """
    from world.make_genesis_subset import ESSENTIAL_ROLES

    have = {a.get("occupation") for a in full["agents"]}
    missing = [r for r in ESSENTIAL_ROLES if r not in have]
    assert not missing, f"ESSENTIAL_ROLES names occupations genesis.json does not have: {missing}"


def test_the_shipped_thirty_agent_file_matches_the_generator(full):
    """world/genesis_30.json is committed, so it must not drift from the
    policy that produced it."""
    path = GENESIS.with_name("genesis_30.json")
    if not path.exists():
        pytest.skip("genesis_30.json has not been generated")
    shipped = json.loads(path.read_text(encoding="utf-8"))
    fresh = build(full, 30)
    assert [a["agent_id"] for a in shipped["agents"]] == [a["agent_id"] for a in fresh["agents"]]


def test_the_broker_token_is_the_agent_id_when_unsigned():
    """With no AUTH_SHARED_SECRET the broker accepts an unsigned credential,
    but it still reads the agent id straight off it
    (broker/auth.py: `token.split(".", 1)[0]`). So the token must BE the
    agent id.

    It returned the shared API key instead, so every engine authenticated as
    an agent named "kasukabe-sim-broker" and every AWAKE tick died with
    "agent 'kasukabe-sim-broker' is not known to world's ledger", while
    sleeping ticks - which call no model and debit nothing - returned 200.
    A run that looks healthy with spend pinned at $0.
    """
    import os

    from world.run_genesis import sign_broker_token

    old = os.environ.pop("AUTH_SHARED_SECRET", None)
    try:
        assert sign_broker_token("a0045", "kasukabe-sim-broker") == "a0045"
    finally:
        if old is not None:
            os.environ["AUTH_SHARED_SECRET"] = old


def test_the_broker_token_is_signed_when_a_secret_is_set():
    import os

    from world.run_genesis import sign_broker_token

    old = os.environ.get("AUTH_SHARED_SECRET")
    os.environ["AUTH_SHARED_SECRET"] = "s3cret"
    try:
        token = sign_broker_token("a0045", "kasukabe-sim-broker")
        agent_id, _, signature = token.partition(".")
        assert agent_id == "a0045"
        assert len(signature) == 64, "hex sha256"
    finally:
        if old is None:
            os.environ.pop("AUTH_SHARED_SECRET", None)
        else:
            os.environ["AUTH_SHARED_SECRET"] = old
