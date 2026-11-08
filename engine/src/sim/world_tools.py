"""World tools: engine ToolDefs whose handlers ARE calls into the world server.

Task 5's "translates the engine's tool-use output into world-server tool
calls" needs no separate interception layer. src/engine_core/thread.py's
execute_tool_call already does exactly one thing with a tool_use block: look
up its ToolDef by name and await ``tool_def.handler(tool_input)`` (see
thread.py around ``result_text = await self.execute_tool(call.name,
effective_input)``). So the translation IS the handler: each ToolDef built
here closes over a WorldClient and its handler is a single HTTP POST to the
world server. By the time ExecutionThread.execute() returns, every tool call
the model made during that turn has already reached the world server -
nothing downstream needs to inspect tool_calls after the fact.

Wire contract (this module's own; app/world/ does not exist yet as of this
task - see DESIGN.md section 12's build path: "world+tools -> broker+ledger
-> herald seam"). This module is written against the contract below so the
day app/world/ ships a real server, only WorldClient's two HTTP calls need to
match - nothing in pinned_boot.py, agent_adapter.py, or the tests changes:

    GET  {base_url}/tools/registry
        -> 200 [{"name": str, "description": str, "input_schema": <JSON Schema>}, ...]
        The full tool-registry primitive set (DESIGN.md section 2): move,
        harvest, mine, fell, craft, cook, buy, sell, trade, store, withdraw,
        build, teach, marry, pray, vote, petition, register, complain,
        steal, arrest, pay, transfer - plus anything patent-office-registered
        or namespace-visible to this agent. Location-gating is enforced by
        the world server AT CALL TIME, not by hiding tools here: the agent
        sees the whole law of physics and finds out which parts apply to
        where it is standing, same as DESIGN.md section 2's human-readable
        framing implies ("you must be at the mill to mill").

    POST {base_url}/agents/{agent_id}/tool_calls
        body: {"tool": str, "input": dict}
        -> 200 {"result": str}   the call ran; result is what the model reads
        -> 4xx {"error": str}    world rejected it (wrong location, no
                                  funds, no permit, ...)
        A non-2xx response or a transport failure becomes a ToolResultBlock
        reading "Error: ..." rather than an exception through the agent
        loop - a bad move is data the agent should reason about (and can
        retry or route around), not a crash that ends the tick.

Every world tool is registered under an engine-side name prefixed
"world:<name>" (mirroring the existing "ad:" prefix asset_directory tools
use - see thread.py's ``tool.name.startswith("ad:")`` check). The world
server itself only ever sees the UNPREFIXED name in the tool_calls POST
body; the prefix exists purely so pinned_boot.py can grant one
PermissionRule("world:*") instead of enumerating every primitive by hand,
and so future non-world tools never collide with a world tool by name.
"""

from __future__ import annotations

import hashlib
import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from src.sim.memory_query import hint_after_refusal
from src.sim.world_corrections import record_correction

from src.sandbox.permissions import PermissionBehavior, PermissionRule
from src.sandbox.permissions import ToolSource
from src.utility_directory.tool_runtime import ToolDef, ToolRegistry

logger = logging.getLogger(__name__)

# TRADE-05. How many times a call is posted before the seam gives up, and
# how long it waits between tries.
#
# Three attempts and a 0.25s step: the failures in run `deni-a6f9eb4-5h`
# were bursts under fifty engines posting at once, which clear in
# milliseconds. Long enough to ride one out, short enough that three
# failures still fit inside a tick's dispatch window rather than eating
# the turn they were meant to save.
WORLD_CALL_ATTEMPTS = 3
WORLD_CALL_BACKOFF_S = 0.25

WORLD_TOOL_NAME_PREFIX = "world:"

# Granted into the pinned thread's session_rules by install_permissions()
# below. World tools have no PERMISSION_MATRIX entry that auto-allows a
# WRITE/DESTRUCTIVE action (see src/sandbox/permissions.py: an unmatched
# (source, action) pair fails CLOSED - "requires_permission = True" - which
# with no human operator behind ask_user_callback would hang every world
# tool call on the 72h wait). The world server is DESIGN.md section 2's
# actual authority on whether a call is allowed (location, funds, permits);
# the engine's own HITL permission system has no useful opinion here and
# must get out of the way instead of guessing.
WORLD_TOOL_ALLOW_RULE = PermissionRule(
    tool=f"{WORLD_TOOL_NAME_PREFIX}*",
    pattern="",
    behavior=PermissionBehavior.ALLOW,
    reason="world server is the sole authority over world-tool calls (DESIGN.md section 2)",
    source="engine",
)


def install_permissions(thread: Any) -> None:
    """Grant the world-tool allow rule on an already-constructed thread.

    Appends to ``thread.session_rules`` (a plain list attribute
    ExecutionThread already exposes and already folds into
    ``current_permission_rules()``) rather than editing thread.py - the
    thread's own DENY rules (settings.deny_rules, e.g. the fork block) are
    still checked first on every call and always win: check_permission's
    step 2 (DENY) runs and can return before step 3 (ALLOW) is ever reached.
    """
    if WORLD_TOOL_ALLOW_RULE not in thread.session_rules:
        thread.session_rules.append(WORLD_TOOL_ALLOW_RULE)


@dataclass(frozen=True)
class WorldToolSpec:
    """One row of the world server's tool registry, as the engine sees it."""

    name: str
    description: str
    input_schema: dict[str, Any]


class WorldClient:
    """HTTP client for the world server's tool-registry + tool-call surface.

    One instance per agent (scoped to ``agent_id``); safe to share across
    every tick this engine instance runs since it is stateless besides the
    underlying connection pool.
    """

    def __init__(
        self,
        base_url: str,
        agent_id: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout)
        # WIRE-02. What this engine's calls to the world actually did.
        #
        # Run `deni-a6f9eb4-5h` lost 97 calls to transport - 8% of every
        # tool call the town made - and NOTHING in the running system
        # knew. The world cannot count them (it is the thing that was
        # unreachable), the runner had no counter, no endpoint reported
        # them, and they were findable only by grepping session logs
        # after the run was over. A failure mode with no instrument is a
        # failure mode nobody fixes.
        #
        # `recovered` is the interesting one: it is the number TRADE-05
        # is worth, and it separates "the network is fine" from "the
        # network is bad and the retry is carrying us".
        self.calls_attempted = 0
        self.calls_refused = 0
        self.transport_failures = 0
        self.transport_recovered = 0
        # Of the failures, the ones where nobody can say whether the act
        # happened: the world accepted the request and did not answer in
        # time. Counted apart from `transport_failures` because the two
        # need different fixes - a connect failure means the world was
        # unreachable, this means the world was too SLOW - and because a
        # run whose economy figures are off by a few double-applied acts
        # should be able to say so instead of being quietly wrong.
        self.transport_unknown = 0
        # ROADMAP T4, the world as corrector. Set by pinned_boot once the
        # engine's learning-centre store exists; None until then, and None
        # in every test that does not care, because a correction is
        # bookkeeping and must never be the reason a turn fails.
        self.signal_store: Any | None = None
        self.thread_id: str = ""
        self.corrections_recorded = 0
        # How often a repeated refusal named `search_memory`. Counted so
        # a run can show whether the hint fires at all - the failure this
        # whole change exists to end was a mechanism nobody could see.
        self.hints_offered = 0
        # ENG-19. What the last /tools/registry answer was, so the next one
        # can be conditional. `registry_etag` is the world's own validator
        # when it sends one; `registry_body_digest` is this side's fallback
        # for a world that does not (see fetch_tool_registry).
        self.registry_etag: str = ""
        self.registry_body_digest: str = ""
        self.registry_unchanged = 0

    async def fetch_tool_registry(
        self, *, only_if_changed: bool = False
    ) -> list[WorldToolSpec] | None:
        """GET the world's current tool registry (see module docstring).

        Names this agent, so the world can serve full schemas only for what
        is callable from where it is standing and a one-line index for the
        rest - tool spec B3's companion setting. An older world ignores the
        parameter and returns everything, which is exactly what happened
        before and is still correct, only larger.

        ENG-19. With ``only_if_changed`` this returns ``None`` when the
        answer is byte-for-byte what this client already has, and the caller
        skips rebuilding every ToolDef. Two independent ways to find that
        out, in this order:

          1. HTTP conditional GET. The world stamps an ``ETag`` on the
             payload; this sends it back as ``If-None-Match`` and a
             matching world answers ``304 Not Modified`` with no body at
             all. That is where the 53 KB per engine per tick goes.
          2. A digest of the body this client last parsed. A world that
             sends no ETag (or a proxy that strips it) still gets the
             ToolDef rebuild skipped - the transfer happens, the 76
             closures are not rebuilt, and nothing has to be true about the
             server for it to work.

        Never (2) alone as the whole fix: an ETag is what stops the bytes
        leaving the world in the first place, and at 250 engines that is
        the number in the finding.
        """
        headers: dict[str, str] = {}
        if only_if_changed and self.registry_etag:
            headers["If-None-Match"] = self.registry_etag
        resp = await self.client.get(
            f"{self.base_url}/tools/registry",
            params={"agent_id": self.agent_id},
            headers=headers or None,
        )
        if only_if_changed and resp.status_code == 304:
            self.registry_unchanged += 1
            return None
        resp.raise_for_status()
        # Remembered even when this call was unconditional, so the FIRST
        # refresh after boot is already able to be answered with a 304 -
        # boot has just built these exact ToolDefs.
        self.registry_etag = resp.headers.get("etag", "") or ""
        digest = hashlib.sha256(resp.content).hexdigest()
        if only_if_changed and digest and digest == self.registry_body_digest:
            self.registry_unchanged += 1
            return None
        self.registry_body_digest = digest
        rows = resp.json()
        specs: list[WorldToolSpec] = []
        for row in rows:
            specs.append(
                WorldToolSpec(
                    name=row["name"],
                    description=row.get("description", ""),
                    input_schema=row.get("input_schema")
                    or {"type": "object", "properties": {}},
                )
            )
        return specs

    async def fetch_tool_registry_version(self) -> int:
        """The world's current registry version - one small number.

        Separate from fetch_tool_registry() so a tick can ask "has anything
        changed" without pulling the whole vocabulary. An older world with no
        such route returns 0, which reads as "never changes" and leaves the
        engine behaving exactly as it did before this existed.
        """
        try:
            resp = await self.client.get(f"{self.base_url}/tools/registry/version")
            if resp.status_code != 200:
                return 0
            return int(resp.json().get("version", 0))
        except (httpx.HTTPError, ValueError, TypeError):
            # A staleness check is not worth failing a tick over: the engine
            # keeps the tools it already has and tries again next tick.
            return 0

    async def call_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """POST one tool call. Never raises - failures become "Error: ..." text
        so the agent loop reads a bad move as data, not a crash (module docstring).

        TRADE-05. A transport failure is RETRIED; a refusal is not.

        Run `deni-a6f9eb4-5h` lost **97 calls** to
        `Error: world server unreachable` - 8% of every tool call the
        town managed to make. The runner log for that run is clean (four
        dispatch failures across 351 ticks), so these were not a world
        that was down: they were a healthy world briefly unreachable,
        under fifty engines all posting at once.

        What it cost is worse than the call. A resident cannot tell a
        transport blip from a refusal - both arrive as `Error: ...` - so
        it reads "the world would not let me" and reasons from that for
        the rest of the turn, having done nothing. One of those 97 was a
        `take_from_shelf`.

        Two retries, short backoff, and ONLY on `httpx.HTTPError`, which
        is the transport layer. A 4xx or a 5xx is an answer and is never
        retried - a refusal repeated is still a refusal, and re-posting a
        request the world may already have applied is how one act becomes
        three.

        WHICH transport failures may be retried, because the original rule
        here was too broad. It read: "an `HTTPError` means no response was
        received at all, which is the one case where the request provably
        did not complete." That is true of a CONNECT failure and false of a
        READ timeout. A connect error means the request never left; a read
        timeout means it was sent, the world accepted it, and the answer did
        not come back in time - so the act may well have been applied, and
        `post` is not idempotent.

        The election30 run is the evidence: of about 1,800 lost calls, the
        failures logged were `ReadTimeout`. Every one of those was retried
        up to twice against a world that may already have done the thing,
        and then reported to the resident as "Nothing was done" - so a
        harvest could be applied two or three times while its own actor was
        told it had not happened once. (The queue those timeouts came from
        is fixed separately, in `app/world/tools/state.py`: the snapshot
        every call paid for was deep-copying a 5,000-record ring.)

        So a read timeout is now reported honestly and NOT retried, and a
        connect failure keeps the retry that TRADE-05 added, because for
        that case the old reasoning holds exactly.
        """
        self.calls_attempted += 1
        last_error: Exception | None = None
        for attempt in range(WORLD_CALL_ATTEMPTS):
            try:
                resp = await self.client.post(
                    f"{self.base_url}/agents/{self.agent_id}/tool_calls",
                    json={"tool": tool_name, "input": tool_input},
                )
                if attempt:
                    # Got there on a retry: this call would have been one
                    # of the 97 before TRADE-05.
                    self.transport_recovered += 1
                break
            except httpx.HTTPError as exc:
                last_error = exc
                # A read timeout is not a call that failed to leave. The
                # world took it and did not answer in time, so it may be
                # applied already: re-posting it is how one harvest becomes
                # three, and telling this resident "nothing was done" is a
                # claim nothing here can support.
                arrived = isinstance(exc, httpx.ReadTimeout)
                if not arrived and attempt + 1 < WORLD_CALL_ATTEMPTS:
                    await asyncio.sleep(WORLD_CALL_BACKOFF_S * (attempt + 1))
                    continue
                self.transport_failures += 1
                if arrived:
                    self.transport_unknown += 1
                logger.warning(
                    "world tool call transport failure after %d attempt(s): "
                    "tool=%s err=%s outcome=%s",
                    attempt + 1, tool_name, exc,
                    "unknown (read timeout)" if arrived else "never arrived",
                )
                if arrived:
                    # The honest sentence. A resident told this can ask the
                    # world what is true - `look_around`, check an inventory
                    # - which is the one useful move available. Told "nothing
                    # was done", it would instead repeat the act.
                    return (
                        f"Error: the world took your {tool_name} call but did "
                        f"not answer in time ({exc}). It MAY have been done "
                        "and it may not - this is the one case where nothing "
                        "can tell you which. Check the world before repeating "
                        "it, or the act may happen twice."
                    )
                # Says it was tried, so a resident reading this knows the
                # difference between "the world refused me" and "the world
                # could not be reached" - which it could not tell before.
                return (
                    f"Error: could not reach the world to call {tool_name} "
                    f"({WORLD_CALL_ATTEMPTS} attempts, {exc}). Nothing was "
                    "done and nothing was refused; the call never arrived."
                )
        else:  # pragma: no cover - the loop always breaks or returns
            return f"Error: world server unreachable ({last_error})"

        if resp.status_code >= 400:
            stated_reason: str | None = None
            try:
                body = resp.json()
                stated_reason = body.get("error") if isinstance(body, dict) else None
                message = stated_reason or resp.text
            except ValueError:
                message = resp.text
            self.calls_refused += 1
            refusal = f"Error: {message}"
            # ENG-18. A 5xx is the world BREAKING, not the world refusing.
            #
            # Every status at or above 400 was written to the resident's
            # learning store as a correction, so a world server that fell
            # over taught the resident that "the world refused `harvest`" -
            # and the learning pipeline consolidated that during sleep into
            # a durable belief about what it is not allowed to do. An
            # outage became a lesson.
            #
            # A refusal is a 4xx WITH a reason: the world declining a
            # specific act and saying why. A 4xx with no `error` body is
            # not a refusal either - it is a malformed answer, and there is
            # nothing in it for the resident to learn from.
            #
            # The resident still SEES the error this turn; only the durable
            # record is withheld. Teaching nothing is the right outcome
            # when there is nothing true to teach.
            is_a_refusal = resp.status_code < 500 and stated_reason is not None
            if not is_a_refusal:
                logger.info(
                    "not recording a correction for %s: status %s is not a "
                    "refusal the resident can learn from",
                    tool_name, resp.status_code,
                )
                return refusal
            # Asked BEFORE the write below, so a hit is prior history and
            # not the row we are about to add. See
            # `memory_query.hint_after_refusal` for why a repeated refusal
            # is the one moment worth naming `search_memory` at: the
            # sentence explaining the FIRST refusal is sitting in
            # `learning_signals`, which is the one store the turn's
            # automatic recall does not read.
            hint = await hint_after_refusal(self.signal_store, tool_name)

            if await record_correction(
                self.signal_store,
                agent_id=self.agent_id,
                tool_name=tool_name,
                tool_input=tool_input,
                result=refusal,
                thread_id=self.thread_id or self.agent_id,
            ):
                self.corrections_recorded += 1
            if hint:
                self.hints_offered += 1
                return f"{refusal}\n{hint}"
            return refusal

        try:
            body = resp.json()
        except ValueError:
            return resp.text
        return str(body.get("result", ""))

    async def record_memory(self, record: dict[str, Any]) -> None:
        """POST one memory record to the world's intake.

        Never raises, for the same reason `call_tool` never does: an agent
        whose memory write failed should keep thinking, not crash. A dropped
        memory row is a gap in the research record; a dropped turn is a dead
        agent. The engine's own memory_stream.jsonl is written first and is
        unaffected either way, so nothing is actually lost - see
        memory_stream.py.
        """
        try:
            resp = await self.client.post(
                f"{self.base_url}/agents/{self.agent_id}/memories", json=record
            )
        except httpx.HTTPError as exc:
            logger.warning("memory record transport failure: %s", exc)
            return
        if resp.status_code >= 400:
            logger.warning("world refused a memory record: %s %s", resp.status_code, resp.text[:200])

    def wire_health(self) -> dict[str, int]:
        """What this engine's link to the world has been doing.

        Served on `GET /health` so the runner, which already polls it,
        can see a degrading link while the run is still going rather
        than after somebody greps the logs.
        """
        return {
            "world_calls_attempted": self.calls_attempted,
            "world_calls_refused": self.calls_refused,
            "world_transport_failures": self.transport_failures,
            "world_transport_recovered": self.transport_recovered,
            "world_transport_unknown": self.transport_unknown,
        }

    async def aclose(self) -> None:
        if self.owns_client:
            await self.client.aclose()


def build_tool_def(spec: WorldToolSpec, world: WorldClient) -> ToolDef:
    """One engine ToolDef; its handler is the whole translation to a
    world-server tool call (see module docstring)."""

    async def handler(tool_input: dict[str, Any]) -> str:
        return await world.call_tool(spec.name, tool_input)

    return ToolDef(
        name=f"{WORLD_TOOL_NAME_PREFIX}{spec.name}",
        description=spec.description,
        input_schema=spec.input_schema,
        handler=handler,
        source=ToolSource.NETWORK,
        # World tools mutate world state by definition (DESIGN.md section 2:
        # "World state mutates ONLY through tool calls") - never treat one as
        # read-only or safe to run concurrently with itself by default.
        is_concurrency_safe=lambda _: False,
        is_read_only=lambda _: False,
    )


async def build_world_tool_registry(world: WorldClient) -> ToolRegistry:
    """Fetch the world's current tool registry and build one ToolRegistry."""
    registry = ToolRegistry()
    # Unconditional: there is nothing to compare against yet, and a 304 at
    # boot would leave the agent with no tools at all.
    for spec in await world.fetch_tool_registry() or []:
        registry.register(build_tool_def(spec, world))
    return registry


async def refresh_world_tool_registry(
    world: WorldClient, registry: ToolRegistry, known_version: int, force: bool = False
) -> int:
    """Add any tools the world has gained since `known_version`.

    THE blocker this exists to remove: build_world_tool_registry runs once, at
    boot, so a tool an agent invented at tick 500 was invisible to every
    engine - including its inventor's - until the whole town restarted. An
    agent could compose a tool and then never be able to call it, which makes
    the invention machinery decorative.

    Adds to the existing registry rather than rebuilding one. The engine
    registers its own non-world tools (session log, memory stream) onto the
    same registry after boot, and a fresh registry would silently drop them.

    ENG-19. The fetch is CONDITIONAL. With `force` on - which is how every
    real run is configured, because schemas are served by location and the
    world's version counter cannot see a resident walk - this ran the full
    GET and rebuilt all 76 ToolDefs on every tick of every engine, whether
    or not the resident had moved or the town had invented anything. Fine
    at 20 agents, 250 x 53 KB per tick at the roadmap's size. Now the world
    is asked "has this changed for me" and answers with 304 or a body; a
    world that has not learned the question yet answers with a body, this
    side notices the body is identical, and the rebuild is skipped anyway.
    """
    version = await world.fetch_tool_registry_version()
    if version == known_version and not force:
        return known_version
    specs = await world.fetch_tool_registry(only_if_changed=True)
    if specs is None:
        # Same registry, same location, same seat: there is nothing to add
        # and nothing to re-describe. `version` rather than `known_version`
        # because the world's counter may legitimately have moved for a
        # tool this agent cannot see.
        return version
    for spec in specs:
        # Registry keys carry WORLD_TOOL_NAME_PREFIX (build_tool_def), so the
        # bare world name must be prefixed before asking whether it is already
        # here - comparing the raw name would never match and would rebuild
        # every ToolDef on every change.
        key = f"{WORLD_TOOL_NAME_PREFIX}{spec.name}"
        if key not in registry.tools:
            registry.register(build_tool_def(spec, world))
        elif force:
            # B3: the same tool, re-described. When schemas are served by
            # location, a tool's row changes as the resident walks - full
            # at the mill, one line from the field - and the version
            # counter cannot see that, because the WORLD's registry did not
            # change. Replacing the def in place is what makes the saving
            # real rather than a boot-time snapshot that goes stale the
            # first time anybody moves.
            registry.tools[key] = build_tool_def(spec, world)
    # And a world tool the world no longer offers leaves. This only ever
    # added and re-described, so anything offered once stayed in front of
    # the model for good - a government verb after the seat was lost, and
    # `buy` after a conversation ended (it is offered only while the
    # resident is in one, the operator's rule of 2026-09-21). The world
    # refused the call either way; the model still spent one finding out.
    #
    # Only WORLD tools, by prefix: the engine's own (session log, memory
    # stream) live on the same registry and are not the world's to remove.
    # And only on a real list - a 304 returns above, before this, so "no
    # change" is never read as "offers nothing".
    offered = {f"{WORLD_TOOL_NAME_PREFIX}{spec.name}" for spec in specs}
    for key in [k for k in registry.tools if k.startswith(WORLD_TOOL_NAME_PREFIX)]:
        if key not in offered:
            del registry.tools[key]
    return version
