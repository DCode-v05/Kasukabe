"""Kasukabe sim profile: the herald seam between one engine and the broker.

This package is how a single September engine instance boots as one of the
250 agents in the Kasukabe town simulation (see /Vat/kasukabe/DESIGN.md
sections 4 and 9). It answers exactly one question: how does a production
engine, whose defaults assume Coordinator/ThreadRouter triage, a dozen chat
model roles, and forkable sub-agents, get pointed at a single local broker
with a ONE-model allowlist, one pinned thread, and no forking?

Nothing here is invasive. Every module composes existing engine seams
(Settings fields, herald.create_llm, Coordinator, ExecutionThread,
settings.deny_rules) from the outside; src/bootstrap.py, src/config.py,
src/engine_core/coordinator.py, src/engine_core/thread.py, and
src/herald/defaults.py are unmodified. See pinned_boot.py's module
docstring for why the sim boot sequence is its own lean function rather
than a call into bootstrap.bootstrap().

Modules:
    model_profile, moonshotai.kimi-k2.5 is the ONLY model, for every
        herald consumer (main thread, learning centre, summarizer,
        importance scoring, anything that asks herald for a model); flex
        pricing metadata; the catalog + adapter guards that make a second
        model structurally unreachable.
    config, SimProfile (per-agent settings) to env vars to Settings, every
        model-role env var pinned to the one model id.
    learning_gate, sleep-hours gate that runs the learning centre batch
        only when the agent is asleep, on the same kimi-k2.5 adapter every
        other herald consumer here uses.
    world_tools, translates the world server's tool registry into engine
        ToolDefs whose handlers call back into the world server (task 5's
        "translation" layer).
    agent_adapter, the thin HTTP receiver for world-server tick/perception
        payloads, wired to the pinned thread.
    pinned_boot, the lean, coordinator-owning-but-router-less boot
        sequence: one pinned thread, FORK_SUBAGENT=false, spawn_sub_agent
        hard-denied.
    sim_main, the process entrypoint (`python -m src.sim.sim_main`).
"""

from __future__ import annotations
