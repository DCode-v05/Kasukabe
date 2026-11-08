"""A remark addressed to three people was recorded as addressed to one.

`CORE-28`. `say` carries both `listener_agent_id` (one id, the primary
addressee) and `addressed_agent_ids` (everyone the remark named). Run 23
filled the second on **172 of 229** utterances. Two readers ignore it:

  * `run_genesis._phrase_activity` renders a multi-addressee remark as
    "speaking to everyone here", which reads as a broadcast to the room
    when it was in fact aimed at named people;
  * `run_report`'s thread grouping builds each thread's `people` list from
    `speaker` + `listener` only, so a remark that pulled two more residents
    into a conversation does not put them in the thread.

The second matters beyond cosmetics: `people` is what decides whether the
next utterance OVERLAPS an existing thread, so under-counting participants
splits one conversation into several in the report - and thread shape is
what G3, B3 and B4 are scored on.

The record has been right the whole time; only the readers were narrow.
"""
from __future__ import annotations

import pytest

import world.run_report as rr


class TestTheReportGroupsByEveryoneNamed:
    def _utterances(self):
        return [
            {
                "tick": 10, "speaker_agent_id": "n01",
                "listener_agent_id": "n02",
                "addressed_agent_ids": ["n02", "n03"],
                "location_id": "Kasukabe Bakery", "content": "Anselm, Girl - bread?",
                "conversation_id": None,
            },
            # n03 - named by the remark above, never its speaker or its
            # listener - now talks to n04. The ONLY thing that can join this
            # to the thread above is n03 having been counted as a
            # participant, which happens only if `addressed_agent_ids` was
            # read. A reply aimed back at n01 would overlap through the
            # speaker and prove nothing.
            {
                "tick": 11, "speaker_agent_id": "n03",
                "listener_agent_id": "n04",
                "addressed_agent_ids": ["n04"],
                "location_id": "Kasukabe Bakery", "content": "Two loaves, then.",
                "conversation_id": None,
            },
        ]

    def test_an_addressee_is_a_participant(self):
        threads = rr.threads(self._utterances())
        assert threads, "no thread was built at all"
        assert "n03" in threads[0]["people"], (
            "CORE-28: a resident the remark named by name is not counted as "
            "part of the conversation"
        )

    def test_the_reply_joins_one_thread_not_two(self):
        """The consequence. `people` decides thread overlap, so a missing
        participant splits one conversation into two and every thread-shape
        metric (G3, B3, B4) is computed on the wrong shape."""
        threads = rr.threads(self._utterances())
        assert len(threads) == 1, (
            f"CORE-28: one conversation was split into {len(threads)} threads"
        )


class TestTheActivityFeedNamesThem:
    """The viewer's caption, read from the route the viewer actually calls.

    Asserted end to end rather than by grepping `build_embedded_world_app`
    for a substring: CORE-23 and EVAL-21 exist because this repository was
    full of tests that matched source text and passed on a comment. A
    caption test that reads the source would be one more of them.
    """

    @pytest.mark.asyncio
    async def test_a_remark_naming_two_people_names_them_both(self, tmp_path):
        from fastapi.testclient import TestClient

        from world.run_genesis import (
            GenesisRunner,
            GenesisRunnerConfig,
            build_embedded_world_app,
        )

        runner = GenesisRunner(GenesisRunnerConfig(
            mock_mode=True,
            checkpoint_dir=tmp_path / "checkpoints",
            world_mode="embedded",
            world_port=0,
            budget_cap_cents=40_000,
        ))
        await runner.boot()
        world = runner.world_state

        speaker, *rest = sorted(world.agents)
        if len(rest) < 2:
            pytest.skip("this cast is too small to address two people")
        first, second = rest[0], rest[1]

        world.utterances.append({
            "tick": world.tick(),
            "speaker_agent_id": speaker,
            "listener_agent_id": first,
            "addressed_agent_ids": [first, second],
            "location_id": world.location_of(speaker),
            "text": "Both of you - bread?",
        })

        app = build_embedded_world_app(
            world, runner.registry, runner.ledger, runner.metrics,
            tick_engine=runner.tick_engine, genesis=runner.genesis,
            map_service=runner.map_service,
        )
        with TestClient(app) as client:
            body = client.get("/world/activity").json()

        # The route wraps its payload: {tick, count, agents}.
        doing = ((body.get("agents") or {}).get(speaker) or {}).get("doing", "")
        names = world.agent_names
        assert names.get(second, second) in doing, (
            "CORE-28: a remark that named two residents is captioned as "
            f"though it named one or none: {doing!r}"
        )
