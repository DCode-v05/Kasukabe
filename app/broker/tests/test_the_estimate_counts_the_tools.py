"""The wallet check has to see the tool schemas.

`INFRA-13`. `request_text_chars` feeds the pre-dispatch estimate that
decides whether a resident can afford a call. It counted message text and
the system prompt, and deliberately skipped `tool_use` inputs and images -
correctly, because char-counting a base64 blob wildly overestimates.

That reasoning does not extend to the TOOL DEFINITIONS. They are plain
JSON text, they are sent on every single call, and `ENG-03` measured them
at 59,310 chars - about 14.8K tokens by the engine's own estimator, two
thirds of a typical turn's input bill. Skipping them meant the wallet
check cleared calls it should have refused: a resident with a cent left
was passed for a request whose input was dominated by a cost the estimate
could not see.

Undercounting here is not the conservative direction. It is the direction
that spends money that is not there.
"""
from __future__ import annotations

from broker.service import request_text_chars


class _Req:
    """The narrowest shape `request_text_chars` reads."""

    def __init__(self, messages=None, system=None, tools=None):
        self.messages = messages or []
        self.system = system
        self.tools = tools


def _tool(name: str) -> dict:
    return {
        "name": name,
        "description": "Buy an item from a business you are standing in.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "item": {"type": "string"},
                "quantity": {"type": "integer"},
            },
            "required": ["business_id", "item", "quantity"],
        },
    }


def test_a_request_with_tools_costs_more_than_one_without() -> None:
    words = [{"role": "user", "content": "It is 06:10. You are at the bakery."}]
    bare = request_text_chars(_Req(messages=words))
    armed = request_text_chars(_Req(messages=words, tools=[_tool("buy")]))

    assert armed > bare, (
        "INFRA-13: the tool schemas are invisible to the estimate, so the "
        "wallet check clears calls whose input it cannot see"
    )


def test_the_schemas_dominate_a_small_turn() -> None:
    """The proportion is the finding. A short perception plus 24 tools is
    mostly tools, and the estimate said it was mostly perception."""
    words = [{"role": "user", "content": "It is 06:10."}]
    tools = [_tool(f"tool_{i}") for i in range(24)]

    bare = request_text_chars(_Req(messages=words))
    armed = request_text_chars(_Req(messages=words, tools=tools))

    assert armed > bare * 10


def test_no_tools_is_unchanged() -> None:
    """Every existing caller that sends none must estimate exactly as
    before."""
    words = [{"role": "user", "content": "hello"}]
    assert request_text_chars(_Req(messages=words, tools=None)) == len("hello")
    assert request_text_chars(_Req(messages=words, tools=[])) == len("hello")


def test_an_unserialisable_tool_does_not_break_the_estimate() -> None:
    """A tool definition that will not serialise is the translation
    layer's problem a few lines later, not this estimate's - and an
    exception here would refuse a call for the wrong reason."""
    class Odd:
        pass

    words = [{"role": "user", "content": "hello"}]
    assert request_text_chars(_Req(messages=words, tools=[{"x": Odd()}])) >= len("hello")
