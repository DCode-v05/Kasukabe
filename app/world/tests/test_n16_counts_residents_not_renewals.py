"""N16 asks how many RESIDENTS share a goal, so it has to count residents.

The item is "no single goal for > 50% of residents". The denominator was
residents and the numerator was the number of TIMES the commonest goal text
appeared in `agent.goal.set` - which is not the same quantity and is not
bounded by the number of residents at all. `agent.goal.set` fires on every
renewal, including the ones `goal_lifecycle` raises from a need, so one
resident who keeps wanting breakfast counts as a town that all wants the same
thing.

election30 reported "47 distinct of 85" and was scored not held. Forty-seven
distinct goals across thirty residents is close to the opposite of the failure
N16 is looking for, which is what made it worth chasing rather than believing.
"""

from __future__ import annotations

import json
import sqlite3

from world.run_checklist import score

SCHEMA = """
    create table events (type text, payload text, tick integer);
    create table llm_calls (agent_id text, tick integer, purpose text, success integer,
                            input_tokens integer, cost_nano_dollars integer, created_at text,
                            request_body text);
    create table tool_invocations (agent_id text, tick integer, tool text, namespace text,
                                   owner_id text, program_id text);
    create table transactions (reason text, from_pool text);
    create table utterances (speaker_agent_id text, listener_agent_id text, conversation_id text,
                            content text, tick integer);
"""


def _trace(path, goals: list[tuple[str, str]], residents: tuple[str, ...]) -> str:
    """A trace holding nothing but enough turns to count residents, and goals.

    `goals` is (agent_id, text) in the order they were set - one row per
    `agent.goal.set`, which is what a renewal produces.
    """
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    for agent in residents:
        db.execute(
            "insert into llm_calls values (?,?,?,?,?,?,?,?)",
            (agent, 40, "auto:agent_turn", 1, 9000, 10_000_000, "t40", "hello"),
        )
    for tick, (agent, text) in enumerate(goals):
        db.execute(
            "insert into events values (?,?,?)",
            ("agent.goal.set", json.dumps({"agent_id": agent, "text": text}), tick),
        )
    db.commit()
    db.close()
    return str(path)


def _n16(path) -> dict:
    return next(r for r in score(path) if r["item"] == "N16")


def test_one_resident_renewing_a_goal_is_not_a_town_that_shares_it(tmp_path) -> None:
    """The defect, at its smallest: four renewals, two residents, one wanter."""
    trace = _trace(
        tmp_path / "renewed.db",
        goals=[("a1", "eat breakfast")] * 4 + [("a2", "mill the wheat")],
        residents=("a1", "a2"),
    )

    row = _n16(trace)

    assert row["verdict"] == "held", (
        "a goal held by one of two residents was scored as a goal held by "
        f"more than half the town: {row["value"]}"
    )
    assert "held by 1 of 2" in row["value"], row["value"]


def test_a_goal_most_of_the_town_really_does_share_still_fails(tmp_path) -> None:
    """The item has to keep catching what it exists to catch."""
    trace = _trace(
        tmp_path / "shared.db",
        goals=[("a1", "eat breakfast"), ("a2", "eat breakfast"), ("a3", "eat breakfast")],
        residents=("a1", "a2", "a3", "a4"),
    )

    row = _n16(trace)

    assert row["verdict"] == "not held", (
        f"three of four residents wanted the same thing and N16 allowed it: {row["value"]}"
    )
    assert "held by 3 of 4" in row["value"], row["value"]


def test_exactly_half_the_town_is_allowed(tmp_path) -> None:
    """"> 50%" is the wording, so 50% holds. Pinned because the comparison
    is the kind that gets flipped while tidying."""
    trace = _trace(
        tmp_path / "half.db",
        goals=[("a1", "eat breakfast"), ("a2", "eat breakfast")],
        residents=("a1", "a2", "a3", "a4"),
    )

    assert _n16(trace)["verdict"] == "held"


def test_a_run_with_no_goals_claims_nothing(tmp_path) -> None:
    """Unproven is not the same as passed, the standing rule everywhere else
    in this scorer."""
    trace = _trace(tmp_path / "none.db", goals=[], residents=("a1", "a2"))

    assert _n16(trace)["verdict"] not in ("held", "not held")


def test_the_claim_says_how_many_residents_so_the_number_is_checkable(tmp_path) -> None:
    """The old claim was "47 distinct of 85" - two numbers, neither of which
    is the one the verdict turns on. A reader could not tell why it failed."""
    trace = _trace(
        tmp_path / "claim.db",
        goals=[("a1", "eat breakfast")] * 3 + [("a2", "eat breakfast")],
        residents=("a1", "a2", "a3"),
    )

    claim = _n16(trace)["value"]

    assert "1 distinct of 4" in claim, claim
    assert "held by 2 of 3" in claim, claim
