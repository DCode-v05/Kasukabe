"""A free dress rehearsal for the election run: the real world, the real UI, no models.

    TRACE_SQLITE_PATH=/tmp/rehearsal.db .venv/bin/python scripts/rehearse_ui.py

Boots the town from `world/genesis_20_funded.json` (or `--genesis
genesis_50_funded.json`) in mock mode - the
in-process MockSupervisor and MockAgentGateway, which never call Bedrock and
cost nothing - on the same host and port the paid run uses (127.0.0.1:8100),
at one tick a second, so a whole election cycle (candidacy at tick 0, voting
288-432) passes in about seven minutes. Point the game server and
`scripts/record_ui.py` at it exactly as `run20.sh` does, and every panel,
the election strip and the recording pipeline are exercised before a cent
is spent.

Mock residents do not reason, so they neither stand nor vote. To show the
election panel in every state, `--stage-election` files a few candidacies
and ballots through the SAME `ElectionEngine` calls `declare_candidacy` and
`vote` make (the phase, the town-hall location and the age rule all still
apply). That is staging for the viewer, and this output is never research
data: it is written to its own trace file and labelled as a rehearsal.

Operator tooling; nothing imports it.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from world.charter.models import ElectionPhase  # noqa: E402
from world.run_genesis import GenesisRunner, GenesisRunnerConfig, apply_scenario  # noqa: E402
from world.tools.locations import LOCATION_TOWN_HALL  # noqa: E402
from world.tools.primitives.civic import eligibility_facts_for  # noqa: E402


async def stage_election(runner: GenesisRunner, seed: int, stop: asyncio.Event) -> None:
    """File candidacies while candidacy is open, and ballots while voting is."""
    rng = random.Random(seed)
    world = runner.world_state
    adults = sorted(a for a, r in world.agents.items() if r.age_years >= 18)
    candidates = rng.sample(adults, k=min(4, len(adults)))
    voters = [a for a in adults if rng.random() < 0.75]
    declared, voted = set(), set()
    engine = world.charter.elections
    while not stop.is_set():
        now = world.tick()
        for election in await world.charter.store.list_elections():
            if election.phase is ElectionPhase.CANDIDACY_OPEN:
                for who in candidates:
                    if who not in declared and rng.random() < 0.08:
                        await engine.declare_candidacy(election.election_id, who,
                                                       eligibility_facts_for(world, world.agents[who]),
                                                       now, LOCATION_TOWN_HALL)
                        declared.add(who)
                        print(f"rehearsal: {world.agent_names.get(who, who)} stands (tick {now})")
            elif election.phase is ElectionPhase.VOTING_OPEN and election.candidates:
                for who in voters:
                    if who not in voted and rng.random() < 0.05:
                        choice = rng.choice(sorted(election.candidates))
                        await engine.cast_vote(election.election_id, who, choice,
                                               eligibility_facts_for(world, world.agents[who]),
                                               now, LOCATION_TOWN_HALL)
                        voted.add(who)
        await asyncio.sleep(0.5)


async def main(args) -> int:
    os.environ.setdefault("TRACE_SQLITE_PATH", str(Path(tempfile.mkdtemp()) / "rehearsal.db"))
    tmp = Path(tempfile.mkdtemp(prefix="rehearsal-"))
    config = GenesisRunnerConfig(
        mock_mode=True, genesis_path=HERE / "world" / args.genesis,
        checkpoint_dir=tmp / "cp", home_root=str(tmp / "homes"),
        world_mode="embedded", world_host="127.0.0.1", world_port=args.port,
        start_at_dawn=True, tick_interval_s=args.tick_s, max_game_days=4, max_ticks=args.max_ticks,
    )
    if args.scenario:
        config = apply_scenario(config, args.scenario)
        config = dataclasses.replace(config, tick_interval_s=args.tick_s, mock_mode=True)
    runner = GenesisRunner(config)
    await runner.boot(resume=False)
    await runner.start_embedded_world_server()
    await runner.start_agents()
    if runner.scenario:
        print("rehearsal: " + " ".join(runner.scenario.describe()))
        state = await runner.world_state.charter.store.get_government_state()
        print(f"rehearsal: sitting government PM {state.pm_agent_id}, "
              f"ministers {', '.join(state.minister_agent_ids) or 'none'}")
    print(f"rehearsal: mock town of {len(runner.world_state.agents)} on "
          f"http://127.0.0.1:{args.port}, a tick every {args.tick_s}s (trace {os.environ['TRACE_SQLITE_PATH']})")
    stop = asyncio.Event()
    staging = (asyncio.create_task(stage_election(runner, args.seed, stop))
               if args.stage_election else None)
    try:
        await runner.run_continuous()
    finally:
        stop.set()
        if staging:
            staging.cancel()
        elections = await runner.world_state.charter.store.list_elections()
        for e in elections:
            print(f"rehearsal: election {e.election_id} {e.phase.value}; "
                  f"{len(e.candidates)} standing, {len(e.ballots)} ballots"
                  + (f"; PM {e.result.pm_agent_id}" if e.result else ""))
        await runner.shutdown()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--tick-s", type=float, default=1.0)
    ap.add_argument("--max-ticks", type=int, default=480, help="stop after this tick; the paid run stops at 480, after the count at 432")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--genesis", default="genesis_20_funded.json",
                    help="under world/; genesis_50_funded.json rehearses the 50-resident run")
    ap.add_argument("--stage-election", action="store_true")
    ap.add_argument("--scenario", default=None,
                    help="a scenario file (world/scenarios/*.json): rehearses that run's cast, "
                         "ticks, charter and sitting government, free")
    sys.exit(asyncio.run(main(ap.parse_args())))
