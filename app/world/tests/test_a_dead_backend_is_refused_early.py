"""A backend that cannot work says so before the money is spent.

`CORE-20`. `NamespaceAgentGateway` defined `tick(agent_id,
perception_text)` and the runner calls `send_tick(agent_id, tick=,
is_sleeping=, perception=, call_budget=)`. So `--supervisor-backend
namespace` raised `AttributeError` on its first due agent - after
genesis, after every engine was spawned, and after paying for all of it.

The existing refusal only fired when NO unit booted at all, so a backend
that half-booted got all the way to dispatch before failing, three layers
down, on a missing attribute.

`CORE-14` is here too because it is the same shape one level over: the
daily checkpoint "mirror" called `get_client()` with no config, which
falls through to `SupabaseConfig.from_env()` and a `sqlite_path` of
`":memory:"`. Every `world_snapshots` row on every real run went into a
throwaway database and the line beside it logged success, because the
LOCAL write - the authoritative one - had worked. Nothing ever failed.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from world.run_genesis import (
    GenesisRunnerConfig,
    NamespaceAgentGateway,
    SupervisorError,
    mirror_checkpoint_to_shared_store,
)


def _errors(**over) -> list[str]:
    """`validate()` RETURNS its errors and `from_env` is what raises them -
    an earlier draft of this file expected the opposite and was simply
    wrong about the code."""
    return GenesisRunnerConfig(world_mode="embedded", **over).validate()


def test_the_namespace_backend_is_refused_at_config_time() -> None:
    """The finding: refused before genesis, not after it."""
    errors = _errors(mock_mode=False, supervisor_backend="namespace")
    assert any("CORE-20" in error for error in errors), (
        "CORE-20: the namespace backend is accepted at config time and "
        "fails with AttributeError on the first due agent, after every "
        "engine has been spawned and paid for. Errors: " + repr(errors)
    )


def test_the_refusal_says_what_to_use_instead() -> None:
    errors = _errors(mock_mode=False, supervisor_backend="namespace")
    assert any("process" in error for error in errors)


def test_mock_mode_is_left_alone() -> None:
    """Mock mode spawns no engine at all, so a backend that cannot host one
    is not a problem there - and the tests that exercise the namespace
    supervisor's own bookkeeping must keep working."""
    assert _errors(mock_mode=True, supervisor_backend="namespace") == []


def test_the_process_backend_still_passes() -> None:
    assert _errors(mock_mode=False, supervisor_backend="process") == []


# ── the contract, for whoever revives it ───────────────────────────────


def test_the_gateway_now_has_the_method_the_runner_calls() -> None:
    """An AttributeError three layers down tells a reader nothing about
    what is missing."""
    assert hasattr(NamespaceAgentGateway, "send_tick")
    signature = inspect.signature(NamespaceAgentGateway.send_tick)
    for parameter in ("tick", "is_sleeping", "perception", "call_budget"):
        assert parameter in signature.parameters, (
            f"CORE-20: the gateway's send_tick does not take {parameter}, so "
            "it still does not match what the runner calls"
        )


def test_it_refuses_rather_than_forwarding() -> None:
    """Forwarding to `supervisor.send_tick(unit, perception)` would drop
    `is_sleeping` and `call_budget` - the engine would think through the
    night and ignore its per-tick call budget. A wrong answer is worse
    than a refusal."""
    gateway = NamespaceAgentGateway(supervisor=None)
    with pytest.raises(SupervisorError) as caught:
        asyncio.run(gateway.send_tick(
            "a1", tick=1, is_sleeping=False, perception="x", call_budget=5
        ))
    message = str(caught.value)
    assert "is_sleeping" in message and "call_budget" in message, (
        "the refusal does not say WHAT cannot be carried, so a reviver has "
        "to work it out from the supervisor's signature"
    )


# ── CORE-14: the mirror that wrote to nowhere ──────────────────────────


def test_the_checkpoint_mirror_takes_a_client() -> None:
    signature = inspect.signature(mirror_checkpoint_to_shared_store)
    assert "client" in signature.parameters, (
        "CORE-14: the mirror resolves its own client, which falls through "
        "to a ':memory:' sqlite_path - every world_snapshots row on every "
        "real run went into a throwaway database and logged success"
    )


def test_the_runner_passes_its_own_client() -> None:
    """A parameter nobody fills is the same defect with a longer name."""
    source = inspect.getsource(
        __import__("world.run_genesis", fromlist=["GenesisRunner"]).GenesisRunner
        .build_checkpoint_sink
    )
    assert "client=self.trace_store.client" in source, (
        "CORE-14: the mirror accepts a client and the runner does not give "
        "it one"
    )


def test_the_fallback_says_it_may_be_going_nowhere() -> None:
    """The fallback stays for callers that legitimately have no store. It
    must not be silent: silence is what made this cost a run's worth of
    snapshots."""
    source = inspect.getsource(mirror_checkpoint_to_shared_store)
    assert "may be ':memory:'" in source
    assert "logger.warning" in source
