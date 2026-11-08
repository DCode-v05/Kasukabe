"""An engine that returns 500 has already said why. We were throwing it away.

WHAT THE RUNNER LOGGED, verbatim, from the loop5 run:

    INFO  httpx  POST http://127.0.0.1:9107/tick "HTTP/1.1 500 Internal Server Error"
    WARN  kasukabe.genesis_runner  tick dispatch failed for n08:
          HTTPStatusError: Server error '500 Internal Server Error' for url
          'http://127.0.0.1:9107/tick'

And that is all. The engine raised something specific, FastAPI put its
detail in the response body, and the handler kept `str(exc)` - which for
`HTTPStatusError` is a sentence about the status code and the URL, and
never the body. So the one fact worth having crossed the wire, arrived,
and was dropped one line short of the log.

THIS IS WHY AN ERROR DASHBOARD CAN SHOW `broker-internal x325` WITH NO
CAUSE ATTACHED TO ANY OF THEM. Three hundred rows saying a number.

The same handler already learned this lesson once, from the other side:
its own comment records that httpx timeout errors carry no message, so
the line read "tick dispatch failed for n07: " with a blank where the
reason should be, and the fix was to log the exception CLASS. That fixed
the errors with no message. This fixes the ones whose message is
somewhere else.

BOUNDED, because a traceback from a September engine is long and this
runs once per failing agent per tick. The first part of a traceback is
where the exception type and message live.
"""

from __future__ import annotations

import httpx

from world.run_genesis import ENGINE_ERROR_DETAIL_CHARS, engine_failure_detail


def status_error(status: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://127.0.0.1:9107/tick")
    response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError(
        f"Server error '{status}' for url '{request.url}'",
        request=request,
        response=response,
    )


def test_the_engines_own_reason_reaches_the_log() -> None:
    exc = status_error(500, '{"detail":"KeyError: \'workplace\' in agent_adapter.py:212"}')
    detail = engine_failure_detail(exc)
    assert "workplace" in detail
    assert "agent_adapter" in detail


def test_the_status_line_is_kept_as_well() -> None:
    """Both halves are useful: the status says which hop, the body says
    what. Replacing one with the other would trade a known gap for a new
    one."""
    detail = engine_failure_detail(status_error(500, "boom"))
    assert "500" in detail
    assert "boom" in detail


def test_a_long_traceback_is_cut_and_says_so() -> None:
    """This runs once per failing agent per tick. A September traceback is
    long, and 250 of them a tick is a log nobody reads."""
    detail = engine_failure_detail(status_error(500, "x" * 20_000))
    assert len(detail) < ENGINE_ERROR_DETAIL_CHARS + 500
    assert "truncated" in detail or "..." in detail


def test_an_empty_body_does_not_produce_a_dangling_colon() -> None:
    """A 500 with nothing in it is a real answer - it says the engine
    failed before it could say why - and it must read as one."""
    detail = engine_failure_detail(status_error(500, ""))
    assert detail.strip()
    assert not detail.rstrip().endswith(":")


def test_an_error_with_no_response_still_reports_its_class() -> None:
    """The case the previous fix was for. A ReadTimeout carries no message
    and no response, and which of ReadTimeout, ConnectTimeout or
    RemoteProtocolError it was is the whole diagnosis."""
    exc = httpx.ReadTimeout("", request=httpx.Request("POST", "http://127.0.0.1:9107/tick"))
    detail = engine_failure_detail(exc)
    assert "ReadTimeout" in detail


def test_a_body_that_is_not_text_does_not_break_the_handler() -> None:
    """Reading a failure must never be able to raise. This runs inside the
    except block for the tick that already went wrong."""

    class Unreadable:
        status_code = 500

        @property
        def text(self):  # pragma: no cover - the point is that it raises
            raise ValueError("no decoder for this content type")

    exc = status_error(500, "ignored")
    exc.response = Unreadable()  # type: ignore[assignment]
    assert engine_failure_detail(exc)


def test_a_4xx_is_reported_too() -> None:
    """A 422 from the engine means the runner sent something the engine
    would not accept, which is our bug and the most useful kind to see."""
    detail = engine_failure_detail(
        status_error(422, '{"detail":[{"loc":["body","perception"],"msg":"field required"}]}')
    )
    assert "perception" in detail
