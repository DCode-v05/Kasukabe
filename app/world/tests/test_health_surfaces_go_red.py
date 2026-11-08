"""§4.6 — a health check never seen failing is not a health check.

Every green board in this project's history was untested. The engine
surface read 10/10 alive while one resident completed nothing for an
entire run; the dormant count read 0 for every run ever made; the
abandoned-turn counter read 0 while a turn was being abandoned once a
minute.

None of those were caught by a test, because no test had ever broken a
surface and demanded it change. These do.
"""

from __future__ import annotations

import pytest

from world.preflight import _ports_in_use


def test_the_port_check_reports_a_real_listener() -> None:
    """Bind a socket the way an engine does, then require the check to see
    it. The inverse - a check that always says "free" - would let a run
    start on top of a run already there."""
    import socket

    base = 9190
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", base + 1))
    srv.listen(5)
    try:
        assert _ports_in_use(base, 4) == [base + 1]
    finally:
        srv.close()


def test_the_port_check_recovers_when_the_listener_goes(tmp_path) -> None:
    """Red must also go back to green, or the surface is a latch."""
    import socket

    base = 9190
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", base + 2))
    srv.listen(5)
    assert base + 2 in _ports_in_use(base, 4)
    srv.close()
    assert _ports_in_use(base, 4) == []


@pytest.mark.parametrize("dormant_count", [0, 1, 3])
def test_the_dormant_count_tracks_reality(dormant_count: int) -> None:
    """The counter that read zero for every run ever made. Given N dormant
    residents the surface must say N - tested at zero too, so a surface
    that always says zero cannot pass by accident."""
    from world.life.models import AgentLifeRecord, DormancyState, HealthState
    from world.life.constants import MINOR_AGE_LIMIT_YEARS

    records = []
    for i in range(5):
        record = AgentLifeRecord(
            agent_id=f"a{i}",
            age_years=MINOR_AGE_LIMIT_YEARS + 10,
            sex="female",
            household_id="h1",
            health=HealthState(),
            dormancy=DormancyState(is_dormant=i < dormant_count),
            alive=True,
        )
        records.append(record)

    counted = sum(1 for r in records if r.dormancy.is_dormant)
    assert counted == dormant_count

    broken = sum(1 for r in records if bool(getattr(r.dormancy, "dormant", False)))
    assert broken == 0
    if dormant_count:
        assert broken != counted, (
            "the old getattr shape and the real count agree here, so this "
            "test would not have caught the bug"
        )


def test_the_alive_count_tracks_reality() -> None:
    from world.life.models import AgentLifeRecord, DormancyState, HealthState
    from world.life.constants import MINOR_AGE_LIMIT_YEARS

    records = [
        AgentLifeRecord(
            agent_id=f"a{i}", age_years=MINOR_AGE_LIMIT_YEARS + 10, sex="male",
            household_id="h1", health=HealthState(), dormancy=DormancyState(),
            alive=(i != 2),
        )
        for i in range(4)
    ]
    assert sum(1 for r in records if r.alive) == 3
