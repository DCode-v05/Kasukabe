"""Entry point: python -m src.sim.sim_main

Boots ONE Kasukabe agent engine (pinned_boot.boot_sim_engine) and serves its
tick receiver (agent_adapter.build_sim_agent_app) over uvicorn. This is the
"minimal sim_main.py entrypoint" the task calls for instead of editing
src/bootstrap.py or src/server.py: it is a new, small front door that wires
src/sim/*'s pieces together and starts a process, not a change to how the
production engine boots or serves.

Env vars (all optional except AGENT_ID and BROKER_BASE_URL):
    AGENT_ID           this engine's agent id (required)
    BROKER_BASE_URL    the local broker's base URL, e.g. http://localhost:8200
                        (CONVENTIONS.md's default dev broker port) (required)
    BROKER_API_KEY     bearer token the broker expects (default: dev placeholder)
    WORLD_BASE_URL      the world server's base URL, e.g. http://localhost:8100
                        (CONVENTIONS.md's default dev world port)
    KASUKABE_HOME_ROOT  parent directory for this agent's SEPTEMBER_HOME
                        (default: /tmp/kasukabe-agents; production sets this
                        to a real per-microVM data volume)
    SIM_AGENT_PORT      port this agent's /tick receiver listens on (default 9100)

Note this module intentionally does NOT read CDT_URL/CDT_KEY/LLM_MODEL/etc
directly - src.sim.config.build_settings derives all of those from the
SimProfile below, so there is exactly one place (config.py) that decides
what the herald seam looks like.
"""

from __future__ import annotations

import asyncio
import logging
import os

from src.sim.agent_adapter import build_sim_agent_app
from src.sim.config import SimProfile
from src.sim.pinned_boot import boot_sim_engine

logger = logging.getLogger(__name__)

DEFAULT_WORLD_BASE_URL = "http://localhost:8100"
DEFAULT_HOME_ROOT = "/tmp/kasukabe-agents"
DEFAULT_PORT = 9100


def profile_from_env() -> tuple[SimProfile, str, int]:
    """Read env vars into a SimProfile + (world_base_url, port).

    Raises RuntimeError (not ConfigError - this is process wiring, not
    engine settings) naming every missing required var at once.
    """
    missing = [name for name in ("AGENT_ID", "BROKER_BASE_URL") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"sim_main: missing required env var(s): {', '.join(missing)}")

    profile = SimProfile(
        agent_id=os.environ["AGENT_ID"],
        broker_base_url=os.environ["BROKER_BASE_URL"],
        broker_api_key=os.environ.get("BROKER_API_KEY", "kasukabe-sim-broker"),
        home_root=os.environ.get("KASUKABE_HOME_ROOT", DEFAULT_HOME_ROOT),
    )
    world_base_url = os.environ.get("WORLD_BASE_URL", DEFAULT_WORLD_BASE_URL)
    port = int(os.environ.get("SIM_AGENT_PORT", str(DEFAULT_PORT)))
    return profile, world_base_url, port


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    profile, world_base_url, port = profile_from_env()

    engine = await boot_sim_engine(profile, world_base_url)
    app = build_sim_agent_app(engine)

    import uvicorn

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await engine.shutdown()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
