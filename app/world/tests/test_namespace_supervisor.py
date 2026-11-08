"""One process, one SEPTEMBER_HOME per agent - the backend that makes 30-50
agents possible on an 8 GB laptop.

Measured before this existed: a separate OS process costs ~110 MB per engine
(50 agents = 5.5 GB, which does not fit); a namespaced engine sharing the
process costs 2.5 MB (50 agents = 182 MB). The tests below are about the
isolation contract, because the memory win is worthless if agents can see each
other's state.
"""

import asyncio

import pytest

from world.namespace_supervisor import NamespaceSupervisor
from world.run_genesis import UnitAlreadyExistsError, UnitSpec, UnitState, UnknownUnitError


class FakeEngine:
    def __init__(self, agent_id, home):
        self.agent_id, self.home = agent_id, home
        self.ticks, self.shut = [], False

    async def send_tick(self, text):
        self.ticks.append(text)
        return f"{self.agent_id} handled {text}"

    async def shutdown(self):
        self.shut = True


def make(tmp_path, boot=None):
    async def default_boot(ns, spec):
        return FakeEngine(ns.agent_id, ns.home)

    return NamespaceSupervisor(
        home_root=str(tmp_path), world_base_url="http://world.test",
        boot=boot or default_boot,
    )


def spec(agent_id):
    return UnitSpec(unit_id=f"u-{agent_id}", engine_id=agent_id,
                    cdt_url="http://world.test", cdt_key="k",
                    extra_env={"AGENT_ID": agent_id})


@pytest.mark.asyncio
async def test_each_agent_gets_its_own_september_home(tmp_path):
    """The isolation boundary. Same layout ProcessSupervisor exports, so a run
    can move between backends without touching a file on disk."""
    sup = make(tmp_path)
    await sup.start(spec("a0001"))
    await sup.start(spec("a0002"))

    assert (tmp_path / "a0001").is_dir()
    assert (tmp_path / "a0002").is_dir()
    assert sup.home_for("a0001") != sup.home_for("a0002")


@pytest.mark.asyncio
async def test_one_agents_home_is_not_reachable_from_another(tmp_path):
    """Everything an engine writes - SQLite, skills, memory - lives under its
    own home, so this is the whole of the isolation claim."""
    sup = make(tmp_path)
    await sup.start(spec("a0001"))
    await sup.start(spec("a0002"))

    (sup.home_for("a0001") / "kasukabe.db").write_text("agent one's ledger")
    assert not (sup.home_for("a0002") / "kasukabe.db").exists()


@pytest.mark.asyncio
async def test_a_restart_keeps_the_namespace(tmp_path):
    """An agent's SQLite, memory and skills are what make a restart a
    continuation rather than a new resident wearing the same name."""
    sup = make(tmp_path)
    await sup.start(spec("a0001"))
    (sup.home_for("a0001") / "skills").mkdir()
    (sup.home_for("a0001") / "skills" / "limbing.json").write_text("{}")

    await sup.restart("u-a0001")

    assert (sup.home_for("a0001") / "skills" / "limbing.json").exists()


@pytest.mark.asyncio
async def test_start_reports_running(tmp_path):
    sup = make(tmp_path)
    status = await sup.start(spec("a0001"))
    assert status.state is UnitState.RUNNING
    assert status.is_alive
    assert await sup.health("u-a0001") is True


@pytest.mark.asyncio
async def test_starting_the_same_unit_twice_is_refused(tmp_path):
    sup = make(tmp_path)
    await sup.start(spec("a0001"))
    with pytest.raises(UnitAlreadyExistsError):
        await sup.start(spec("a0001"))


@pytest.mark.asyncio
async def test_unknown_units_raise(tmp_path):
    sup = make(tmp_path)
    with pytest.raises(UnknownUnitError):
        await sup.status("nope")
    with pytest.raises(UnknownUnitError):
        await sup.stop("nope")


@pytest.mark.asyncio
async def test_stop_shuts_the_engine_down(tmp_path):
    sup = make(tmp_path)
    await sup.start(spec("a0001"))
    engine = sup.units["u-a0001"].engine

    status = await sup.stop("u-a0001")

    assert engine.shut is True
    assert status.state is UnitState.STOPPED
    assert await sup.health("u-a0001") is False


@pytest.mark.asyncio
async def test_one_agent_failing_to_boot_does_not_abort_the_run(tmp_path):
    """With 50 agents in one process, a single bad profile must not take the
    others down - that fragility is the cost of sharing a process, and this is
    where it is paid back."""
    async def boot(ns, spec_):
        if ns.agent_id == "a0002":
            raise RuntimeError("bad profile")
        return FakeEngine(ns.agent_id, ns.home)

    sup = make(tmp_path, boot=boot)
    ok = await sup.start(spec("a0001"))
    bad = await sup.start(spec("a0002"))
    ok2 = await sup.start(spec("a0003"))

    assert ok.state is UnitState.RUNNING
    assert bad.state is UnitState.FAILED
    assert "bad profile" in bad.detail
    assert ok2.state is UnitState.RUNNING


@pytest.mark.asyncio
async def test_a_tick_goes_straight_into_the_engine(tmp_path):
    """No HTTP, no port per agent - the engine object is in hand."""
    sup = make(tmp_path)
    await sup.start(spec("a0001"))

    reply = await sup.send_tick("u-a0001", "the sun is up")

    assert reply == "a0001 handled the sun is up"
    assert sup.units["u-a0001"].engine.ticks == ["the sun is up"]


@pytest.mark.asyncio
async def test_shutdown_all_is_concurrent(tmp_path):
    """Fifty engines stopped one after another at a 10s timeout each is an
    eight-minute teardown."""
    sup = make(tmp_path)
    for i in range(12):
        await sup.start(spec(f"a{i:04d}"))

    await asyncio.wait_for(sup.shutdown_all(), timeout=5)

    assert all(ns.engine is None for ns in sup.units.values())


@pytest.mark.asyncio
async def test_list_reports_every_namespace(tmp_path):
    sup = make(tmp_path)
    for i in range(5):
        await sup.start(spec(f"a{i:04d}"))
    assert len(await sup.list()) == 5
