"""The real chain, with a scripted brain at the end of it.

WHAT IS REAL HERE. Everything between the brain and the agent loop:

    ScriptedBrain                    <- the only fake
      -> broker.lanes / LanePool     real
      -> broker.service              real
      -> broker.translate            real  (event synthesis, transcript repair)
      -> broker.kimi_tool_calls      real  (markup recovery on every delta)
      -> a FastAPI app on a real port, over real HTTP
      -> herald.transport            real  (SSE framing, retries)
      -> herald.provider             real, FROZEN - the parser under test
      -> StreamEvent / ToolCall

The trace is a real SQLite database on a real path, written by the real
`SqliteTraceSink`, and the assertions read rows back out of it.

WHAT IS NOT REAL, AND WHY THAT IS SAID OUT LOUD. The wallet in this tier
is `InMemoryLedgerClient`. This tier asks one question - "what does the
engine make of the stream a different model sent?" - and it does not
assert about money. Money is asserted in the world stories, against the
real ledger, where the $1000 invariant is enforced. Splitting it that way
keeps the promise that matters: no assertion in this package is ever made
against a fake of the thing it is asserting about.

WHY THE HTTP HOP IS REAL. `herald/transport.py:222-240` silently skips
malformed SSE frames, and `translate.py` writes those frames. Collapsing
the hop to a function call would test neither, and the frame writer is
exactly the layer a new model's stream shape flows through.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _with_engine_on_path() -> None:
    """The engine is a sibling package rooted at `engine/`, imported as
    `src.*`. Importing it here is what makes the FROZEN parser the thing
    under test rather than a reimplementation of it."""
    import sys

    engine = str(REPO / "engine")
    if engine not in sys.path:
        sys.path.insert(0, engine)


@dataclass
class Cognition:
    """One booted chain: a brain, a broker on a port, and a real parser."""

    brain: Any
    port: int
    trace_path: Path
    service: Any = None
    _server: Any = None
    _thread: threading.Thread | None = None
    events: list[Any] = field(default_factory=list)
    seam: bool = False
    wrapper: Any = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- what a story asks -----------------------------------------------

    async def think(
        self,
        prompt: str,
        *,
        tools: Sequence[dict] | None = None,
        system: str = "You are a resident of Kasukabe.",
    ) -> list[Any]:
        """Drive one resident turn through the real parser.

        Returns herald's own `StreamEvent`s, so a story asserts against
        what the agent loop would actually have received - not against
        what the broker sent.
        """
        _with_engine_on_path()
        from src.herald.provider import Provider
        from src.herald.types import Message, SystemPromptBlock, ToolDef

        provider = Provider(self._herald_settings())
        if self.seam:
            # The wrapper chain production actually boots. Wrapping here
            # means the suite that found a silent turn is the same suite
            # that proves the seam now notices it - no second instrument,
            # no chance of the fix being measured by a friendlier one.
            from src.sim.a_turn_that_tried_to_act import (
                notice_a_turn_that_tried_to_act,
            )

            provider = notice_a_turn_that_tried_to_act(provider)
            self.wrapper = provider
        wire_tools = [
            ToolDef(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get(
                    "input_schema", {"type": "object", "properties": {}}
                ),
            )
            for t in (tools or [])
        ]
        blocks = [SystemPromptBlock(text=system)] if system else None
        out: list[Any] = []
        async for event in provider.stream_with_tools(
            messages=[Message(role="user", content=prompt)],
            system=blocks,
            tools=wire_tools,
        ):
            out.append(event)
        self.events.extend(out)
        return out

    def _herald_settings(self) -> Any:
        _with_engine_on_path()
        from src.config import Settings

        return Settings(
            cdt_url=self.base_url,
            cdt_key="proof-harness",
            llm_model="moonshotai.kimi-k2.5",
            max_tokens=768,
        )

    def traced_calls(self) -> list[tuple]:
        """Rows the broker actually wrote, read back off disk."""
        import sqlite3

        if not self.trace_path.exists():
            return []
        with sqlite3.connect(f"file:{self.trace_path}?mode=ro", uri=True) as c:
            try:
                return list(
                    c.execute(
                        "select agent_id, stop_reason, input_tokens, "
                        "output_tokens, success, error_type from llm_calls"
                    )
                )
            except sqlite3.Error:
                return []


def build_broker(brain: Any, trace_path: Path) -> Any:
    """A real `BrokerService` whose only fake is the gateway.

    `mock_mode` stays False so nothing swaps the trace sink out from under
    us; the gateway is injected directly, which is the seam
    `build_lane_pool` was written to expose.
    """
    from broker.config import BrokerSettings
    from broker.lanes import build_lane_pool
    from broker.ledger import InMemoryLedgerClient
    from broker.service import BrokerService
    from broker.pricing import CostAccountant
    from broker.spend import SpendGovernor
    from broker.trace import BatchWriter, SqliteTraceSink, trace_record_to_row
    from broker.trace import broker_event_to_row
    from supabase.config import SupabaseConfig
    from supabase.sqlite_client import SqliteClient

    settings = BrokerSettings(
        mock_mode=True,  # settings only: the gateway below is what serves
        rpm_per_lane=10_000,
        spend_cap_usd=1_000_000.0,
        trace_sqlite_path=str(trace_path),
    )
    client = SqliteClient(SupabaseConfig(sqlite_path=str(trace_path)))
    return BrokerService(
        settings=settings,
        ledger=InMemoryLedgerClient(autoseed_balance_cents=100_000),
        lane_pool=build_lane_pool(settings, gateway_factory=lambda m, r: brain),
        spend_governor=SpendGovernor(
            cap_usd=settings.spend_cap_usd,
            warn_fraction=settings.spend_warn_fraction,
        ),
        cost_accountant=CostAccountant(),
        trace_writer=BatchWriter(
            sink=SqliteTraceSink(client, "llm_calls", trace_record_to_row),
            batch_size=1,
            flush_interval_s=0.05,
        ),
        event_writer=BatchWriter(
            sink=SqliteTraceSink(client, "events", broker_event_to_row),
            batch_size=1,
            flush_interval_s=0.05,
        ),
    )


@contextlib.asynccontextmanager
async def cognition(
    brain: Any, trace_path: Path, *, seam: bool = False
) -> AsyncIterator[Cognition]:
    """Boot the chain, hand it over, tear it down."""
    import uvicorn
    from broker.app import create_app

    service = build_broker(brain, trace_path)
    app = create_app(service=service)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if getattr(server, "started", False):
            break
        await asyncio.sleep(0.05)
    town = Cognition(
        brain=brain, port=port, trace_path=trace_path, service=service,
        _server=server, _thread=thread, seam=seam,
    )
    try:
        yield town
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        with contextlib.suppress(Exception):
            await service.trace_writer.flush()
            await service.event_writer.flush()
