"""The tick loop and the socket under it must wait the same length of time.

They did not. The loop guarded each tick with
asyncio.wait_for(..., timeout=config.tick_timeout_s), which is 120s, while
HttpAgentGateway was constructed without a timeout and used its own 30s
default. The inner one always won.

What that looked like from outside: a turn that honestly needed 40 seconds
came back as httpx.ReadTimeout - which carries no message, so the runner
logged "tick dispatch failed for n07: " with a blank where the reason
goes - while the engine finished the work and returned 200 OK to a caller
that had already hung up. The outer guard never fired, so `timed_out_ticks`
stayed at 0 and /world/engines reported the run perfectly healthy.

Measured on a live 10-resident run: Marta Thorne, whose perception is the
longest in the cast (1175 chars against a median near 700), lost every
turn of a 19-tick run - 18 cancelled sessions, zero actions, zero tool
calls - while the other nine completed all of theirs. One resident in ten,
silently doing nothing at all, with every dashboard green.
"""

from __future__ import annotations

import inspect

from world.run_genesis import GenesisRunner, GenesisRunnerConfig, HttpAgentGateway


def test_the_gateway_is_given_the_runs_tick_budget() -> None:
    """CORE-23: the AST, not the call's source text.

    This used to slice `inspect.getsource(start_agents)` from
    "HttpAgentGateway(" to the next ")" and look for substrings - which a
    comment satisfies, and which breaks on any line the reader reformats.
    It now parses the construction and reads its actual keywords.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(GenesisRunner.start_agents)))
    built = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "HttpAgentGateway"
    ]
    assert built, "start_agents never constructs an HttpAgentGateway"
    keywords = {kw.arg for node in built for kw in node.keywords}
    assert "timeout_s" in keywords, (
        "without it the socket gives up at its own 30s default, well before "
        "the tick loop's 120s guard, and throws away a finished turn"
    )


def test_the_gateway_default_is_shorter_than_the_loop_guard() -> None:
    """The premise, pinned. If these two ever become equal by coincidence
    this test says so rather than letting the bug quietly reappear the next
    time either number moves."""
    default = inspect.signature(HttpAgentGateway.__init__).parameters["timeout_s"].default
    assert default < GenesisRunnerConfig().tick_timeout_s


def test_a_gateway_built_with_the_budget_actually_carries_it() -> None:
    gateway = HttpAgentGateway({"n07": 9106}, timeout_s=120.0)
    assert gateway.timeout_s == 120.0
    client = gateway.client_for("n07")
    assert client.timeout.read == 120.0
    assert client.timeout.connect == 120.0


def test_a_dispatch_failure_names_the_exception_type() -> None:
    """httpx's timeout and protocol errors stringify to nothing, so logging
    str(exc) alone produced a blank reason once a minute for a resident who
    took no action all run. Which class it was IS the diagnosis.

    REWRITTEN to check the BEHAVIOUR rather than the source string
    `type(exc).__name__`. That string moved into `engine_failure_detail`
    when a 500's response body was added to the same line, and a test that
    goes red because correct code moved is a test that gets deleted. What
    must hold is that a nameless exception still produces a named reason,
    which is what this now asserts - and it would have caught the original
    defect just as well.
    """
    import httpx

    from world.run_genesis import engine_failure_detail

    nameless = httpx.ReadTimeout(
        "", request=httpx.Request("POST", "http://127.0.0.1:9106/tick")
    )
    assert str(nameless) == "", "premise: this exception carries no message"
    assert "ReadTimeout" in engine_failure_detail(nameless)
