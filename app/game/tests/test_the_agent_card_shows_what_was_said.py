"""An agent's card carries the conversation it was part of.

`UI-30`. `AgentDetail.recent_utterances` was hard-coded `[]` in
`_agent_detail_from_world`, so the one panel DESIGN section 8 defines as
"click any agent -> live conversation, memories, wallet, relationships"
showed no conversation - in a town that said 229 things in run 23 and
served every one of them at `/world/utterances`. `UtteranceRecord` was a
model with no producer.

The filter matters more than the fetch. Run 23 carried more than one
addressee on 172 of its 229 utterances, so a reader checking only
`listener_agent_id` would drop the second and third person out of most
conversations in the town - the same narrow read `CORE-15` fixed in
perception and `EVAL-11` fixed in the metrics. Four separate readers made
it, so the case is pinned here explicitly rather than left to the shape
of the fixture.
"""
from __future__ import annotations

import httpx
import pytest

from game.config import GameSettings
from game.world_client import (
    RECENT_UTTERANCES_SHOWN,
    HttpWorldClient,
    _agent_detail_from_world,
    _utterances_for,
)

AGENT = {
    "agent_id": "a-anya",
    "name": "Anya",
    "usd_wallet_cents": 40,
    "sim_balance": 900,
    "location_id": "the Ville:artist's co-living space",
    "alive": True,
}


def _said(speaker, listener=None, addressed=(), content="hello", tick=1):
    return {
        "tick": tick,
        "speaker_agent_id": speaker,
        "speaker_name": speaker.replace("a-", "").title(),
        "listener_agent_id": listener,
        "listener_name": listener.replace("a-", "").title() if listener else None,
        "content": content,
        "is_public": True,
        "addressed_agent_ids": list(addressed),
    }


def test_what_i_said_is_on_my_card() -> None:
    rows = [_said("a-anya", "a-bo", content="the bread is burning")]
    detail = _agent_detail_from_world(AGENT, rows)
    assert [u.content for u in detail.recent_utterances] == ["the bread is burning"]


def test_what_was_said_to_me_is_on_my_card() -> None:
    rows = [_said("a-bo", "a-anya", content="did you buy flour")]
    detail = _agent_detail_from_world(AGENT, rows)
    assert [u.content for u in detail.recent_utterances] == ["did you buy flour"]


def test_a_remark_that_named_me_second_is_on_my_card() -> None:
    """The 172-of-229 case: `listener_agent_id` is somebody else and this
    resident is in `addressed_agent_ids`. A narrow reader loses it."""
    rows = [_said("a-bo", "a-cy", addressed=["a-cy", "a-anya"], content="both of you, come")]
    detail = _agent_detail_from_world(AGENT, rows)
    assert [u.content for u in detail.recent_utterances] == ["both of you, come"], (
        "UI-30: a remark that named this resident among several was dropped, "
        "which was the common shape of speech in run 23, not the edge case"
    )


def test_somebody_elses_conversation_is_not_on_my_card() -> None:
    rows = [_said("a-bo", "a-cy", addressed=["a-cy"], content="private words")]
    assert _agent_detail_from_world(AGENT, rows).recent_utterances == []


def test_the_panel_is_capped_and_keeps_the_newest() -> None:
    rows = [_said("a-anya", "a-bo", content=f"line {i}", tick=i) for i in range(60)]
    shown = _utterances_for("a-anya", rows)
    assert len(shown) == RECENT_UTTERANCES_SHOWN
    assert shown[-1].content == "line 59"


def test_no_rows_is_an_empty_list_not_a_crash() -> None:
    assert _agent_detail_from_world(AGENT, None).recent_utterances == []
    assert _agent_detail_from_world(AGENT, []).recent_utterances == []


def test_one_malformed_row_does_not_cost_the_others() -> None:
    rows = [
        _said("a-anya", "a-bo", content="kept"),
        {"speaker_agent_id": "a-anya", "tick": "not a number"},
    ]
    assert [u.content for u in _utterances_for("a-anya", rows)] == ["kept"]


def _client(handler) -> HttpWorldClient:
    settings = GameSettings(world_base_url="http://world.test", mock_mode=False)
    client = HttpWorldClient(settings)
    client.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://world.test"
    )
    return client


@pytest.mark.asyncio
async def test_the_real_client_asks_the_world_for_speech() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/agents/a-anya":
            return httpx.Response(200, json=AGENT)
        if request.url.path == "/world/utterances":
            return httpx.Response(
                200, json={"utterances": [_said("a-bo", "a-anya", content="live words")]}
            )
        return httpx.Response(404)

    detail = await _client(handler).get_agent_detail("a-anya")
    assert "/world/utterances" in asked, "UI-30: the card never asked for any speech"
    assert [u.content for u in detail.recent_utterances] == ["live words"]


@pytest.mark.asyncio
async def test_an_unreachable_talk_feed_does_not_blank_the_card() -> None:
    """Deliberate: a missing panel must not cost the whole agent card."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agents/a-anya":
            return httpx.Response(200, json=AGENT)
        raise httpx.ConnectError("world's Talk feed is down")

    detail = await _client(handler).get_agent_detail("a-anya")
    assert detail is not None and detail.display_name == "Anya"
    assert detail.recent_utterances == []
