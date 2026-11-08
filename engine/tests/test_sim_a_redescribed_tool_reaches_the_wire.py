"""ENG-16: re-describing a tool must actually change what is sent.

`provider.get_cached_tools` keys its cache on
`frozenset(t.name for t in tools)` - the NAME SET. When schemas are served
by location the names never change: all 82 tools are always present, some
in full and some as a one-line index row. Only the descriptions and the
schemas move.

So the cache returned the first wire array it ever built, for the whole run.
ENG-03's saving reached the wire exactly once - on the very first call - and
the resident spent every later turn being billed for schemas describing
where it used to be standing. `STAKES_TIME_COMPOUNDING.md` §5's "13% saving"
was measured on characters, not on requests, which is why nobody noticed.

herald/ is frozen under ROADMAP Law 1, so the reset lives in the seam.
"""
from __future__ import annotations

from src.sim.pinned_boot import forget_the_cached_tool_wire


class _Provider:
    """Shaped like herald's provider: the two fields the cache lives in."""

    def __init__(self):
        self.cached_tool_names = frozenset({"world:mine", "world:bake"})
        self.cached_tool_schemas = [{"name": "world:mine", "description": "stale"}]


class _Wrapper:
    """The resident-voice adapter, which wraps the provider."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, item):
        return getattr(self._inner, item)


def test_the_cache_is_cleared_on_a_bare_provider():
    provider = _Provider()
    assert forget_the_cached_tool_wire(provider) is True
    assert provider.cached_tool_names is None
    assert provider.cached_tool_schemas is None


def test_the_cache_is_cleared_through_the_resident_voice_wrapper():
    """The real shape: the provider is behind `speak_as_resident`."""
    provider = _Provider()
    assert forget_the_cached_tool_wire(_Wrapper(provider)) is True
    assert provider.cached_tool_names is None, (
        "the wrapper hid the provider, so the cache survived and every turn "
        "after the first was billed for stale schemas (ENG-16)"
    )


def test_the_cache_is_cleared_through_TWO_wrappers():
    """The shape as of ENG-21: speak_as_resident AND the monologue cap.

    Neither wrapper defines `__setattr__`, so `hasattr(wrapper,
    "cached_tool_names")` is True (it walks `__getattr__` to the provider)
    while the assignment lands on the WRAPPER and shadows the provider's
    field. The old two-element walk reached the provider on its second
    candidate and would have stopped one link short the moment a second
    wrapper went on - restoring exactly the run-long staleness ENG-16
    exists to remove, silently, with every test still green.
    """
    provider = _Provider()
    assert forget_the_cached_tool_wire(_Wrapper(_Wrapper(provider))) is True
    assert provider.cached_tool_names is None, (
        "the walk stopped at the first wrapper, so the provider kept the "
        "wire array it built on the very first call (ENG-16 via ENG-21)"
    )
    assert provider.cached_tool_schemas is None


def test_the_boot_chain_is_walkable_end_to_end():
    """Whatever pinned_boot stacks on the adapter, the provider is reachable.

    Reads the real wrappers rather than a stand-in, so adding a third one
    without a `_inner` attribute breaks here instead of in a run report.
    """
    from src.sim.bounded_monologue import end_the_turn_on_a_cut_monologue
    from src.sim.resident_voice import speak_as_resident

    provider = _Provider()
    stacked = end_the_turn_on_a_cut_monologue(speak_as_resident(provider, agent_name="a1"))
    assert forget_the_cached_tool_wire(stacked) is True
    assert provider.cached_tool_names is None
    assert provider.cached_tool_schemas is None


def test_an_adapter_with_no_cache_is_reported_honestly():
    """Returns False rather than pretending. The difference between this
    working and this silently doing nothing IS the finding."""

    class _NoCache:
        pass

    assert forget_the_cached_tool_wire(_NoCache()) is False


def test_the_tick_path_calls_it():
    """A reset nothing calls is exactly the bug it is meant to fix."""
    import ast
    import inspect
    import textwrap

    from src.sim.pinned_boot import SimEngineState

    src = textwrap.dedent(inspect.getsource(SimEngineState))
    called = {
        node.func.id
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "forget_the_cached_tool_wire" in called, (
        "the tool registry is refreshed every tick and the herald cache is "
        "never dropped, so the refresh changes nothing on the wire"
    )


def test_the_cache_key_really_is_only_the_names():
    """The premise, pinned against the frozen engine.

    If herald ever starts hashing the schema this whole workaround becomes
    unnecessary - and this test is where a reader finds that out.
    """
    import inspect

    from src.herald import provider

    src = inspect.getsource(provider)
    i = src.index("def get_cached_tools")
    src = src[i : i + 700]
    assert "frozenset(t.name for t in tools)" in src, (
        "herald's cache key has changed; re-check whether the seam still "
        "needs to drop the cache by hand (ENG-16)"
    )
