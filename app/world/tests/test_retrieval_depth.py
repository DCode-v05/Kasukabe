"""Tests for the memory-retrieval depth knob (world/retrieval_depth.py)."""

from __future__ import annotations

import pytest

from world.core.perception import AmbientInfo, PerceivedEvent, Perception
from world.retrieval_depth import (
    BASELINE_INPUT_TOKENS,
    ENV_VAR,
    MAX_INPUT_TOKENS,
    MAX_HEARING_WINDOW_TICKS,
    MIN_HEARING_WINDOW_TICKS,
    RetrievalDepthConfig,
    clamp_fraction,
    render_perception_text,
)


def make_perception(
    *,
    visible_agents: list[str] | None = None,
    heard_events: list[PerceivedEvent] | None = None,
    is_daytime: bool = True,
) -> Perception:
    return Perception(
        agent_id="a0001",
        tick=10,
        location_id="home:h0001",
        visible_agents=visible_agents or [],
        heard_events=heard_events or [],
        ambient=AmbientInfo(game_day=1, hour=9, minute=30, is_daytime=is_daytime),
    )


class TestClampFraction:
    def test_clamps_below_zero(self) -> None:
        assert clamp_fraction(-0.5) == 0.0

    def test_clamps_above_one(self) -> None:
        assert clamp_fraction(1.5) == 1.0

    def test_passes_through_valid_value(self) -> None:
        assert clamp_fraction(0.42) == 0.42


class TestRetrievalDepthConfig:
    def test_default_is_baseline(self) -> None:
        depth = RetrievalDepthConfig()
        assert depth.fraction == 0.0
        assert depth.estimated_input_tokens == BASELINE_INPUT_TOKENS

    def test_constructor_clamps_out_of_range_fraction(self) -> None:
        depth = RetrievalDepthConfig(fraction=3.0)
        assert depth.fraction == 1.0

    def test_max_depth_reaches_token_ceiling(self) -> None:
        depth = RetrievalDepthConfig(fraction=1.0)
        assert depth.estimated_input_tokens == MAX_INPUT_TOKENS

    def test_set_fraction_mutates_in_place(self) -> None:
        depth = RetrievalDepthConfig(fraction=0.0)
        depth.set_fraction(1.0)
        assert depth.fraction == 1.0
        assert depth.estimated_input_tokens == MAX_INPUT_TOKENS

    def test_set_fraction_clamps(self) -> None:
        depth = RetrievalDepthConfig()
        depth.set_fraction(-1.0)
        assert depth.fraction == 0.0

    def test_input_tokens_monotonic_in_fraction(self) -> None:
        low = RetrievalDepthConfig(fraction=0.2)
        high = RetrievalDepthConfig(fraction=0.8)
        assert low.estimated_input_tokens < high.estimated_input_tokens

    def test_hearing_window_ticks_bounds(self) -> None:
        assert RetrievalDepthConfig(fraction=0.0).hearing_window_ticks == MIN_HEARING_WINDOW_TICKS
        assert RetrievalDepthConfig(fraction=1.0).hearing_window_ticks == MAX_HEARING_WINDOW_TICKS

    def test_hearing_window_ticks_always_at_least_one(self) -> None:
        for fraction in (0.0, 0.1, 0.5, 0.9, 1.0):
            assert RetrievalDepthConfig(fraction=fraction).hearing_window_ticks >= 1

    def test_relationship_notes_off_at_low_depth(self) -> None:
        assert RetrievalDepthConfig(fraction=0.0).include_relationship_notes is False

    def test_relationship_notes_on_at_high_depth(self) -> None:
        assert RetrievalDepthConfig(fraction=1.0).include_relationship_notes is True

    def test_visible_agent_bios_scale_with_depth(self) -> None:
        assert RetrievalDepthConfig(fraction=0.0).max_visible_agent_bios == 0
        assert RetrievalDepthConfig(fraction=1.0).max_visible_agent_bios > 0

    def test_cost_per_call_rises_with_depth(self) -> None:
        low = RetrievalDepthConfig(fraction=0.0).estimated_cost_per_call_usd
        high = RetrievalDepthConfig(fraction=1.0).estimated_cost_per_call_usd
        assert high > low

    def test_cost_per_call_matches_measured_baseline_order_of_magnitude(self) -> None:
        depth = RetrievalDepthConfig(fraction=0.0)
        assert depth.estimated_cost_per_call_usd == pytest.approx(0.001764, abs=0.0001)

    def test_estimated_cost_per_call_cents_is_integer(self) -> None:
        depth = RetrievalDepthConfig(fraction=0.5)
        assert isinstance(depth.estimated_cost_per_call_cents, int)

    def test_from_env_defaults_to_baseline(self) -> None:
        depth = RetrievalDepthConfig.from_env({})
        assert depth.fraction == 0.0

    def test_from_env_reads_valid_float(self) -> None:
        depth = RetrievalDepthConfig.from_env({ENV_VAR: "0.75"})
        assert depth.fraction == 0.75

    def test_from_env_clamps_out_of_range(self) -> None:
        depth = RetrievalDepthConfig.from_env({ENV_VAR: "2.0"})
        assert depth.fraction == 1.0

    def test_from_env_falls_back_on_garbage(self) -> None:
        depth = RetrievalDepthConfig.from_env({ENV_VAR: "not-a-number"})
        assert depth.fraction == 0.0


class TestRenderPerceptionText:
    def test_includes_ambient_time(self) -> None:
        perception = make_perception()
        text = render_perception_text(perception, depth=RetrievalDepthConfig(fraction=0.0))
        assert "game-day 1" in text
        assert "09:30" in text

    def test_night_marked_as_night(self) -> None:
        perception = make_perception(is_daytime=False)
        text = render_perception_text(perception, depth=RetrievalDepthConfig(fraction=0.0))
        assert "night" in text

    def test_no_visible_agents_says_so(self) -> None:
        perception = make_perception(visible_agents=[])
        text = render_perception_text(perception, depth=RetrievalDepthConfig(fraction=0.0))
        assert "Nobody else is here" in text

    def test_visible_agents_named_at_baseline_depth(self) -> None:
        perception = make_perception(visible_agents=["a0002", "a0003"])
        text = render_perception_text(perception, depth=RetrievalDepthConfig(fraction=0.0))
        assert "a0002" in text
        assert "a0003" in text

    def test_bios_included_only_at_sufficient_depth(self) -> None:
        perception = make_perception(visible_agents=["a0002"])
        bios = {"a0002": "a cheerful baker"}

        shallow = render_perception_text(
            perception, depth=RetrievalDepthConfig(fraction=0.0), visible_agent_bios=bios
        )
        deep = render_perception_text(
            perception, depth=RetrievalDepthConfig(fraction=1.0), visible_agent_bios=bios
        )
        assert "cheerful baker" not in shallow
        assert "cheerful baker" in deep

    def test_heard_events_rendered(self) -> None:
        event = PerceivedEvent(
            type="world.weather.changed", tick=10, payload={"to": "rain"},
            involves_self=False
        )
        perception = make_perception(heard_events=[event])
        text = render_perception_text(perception, depth=RetrievalDepthConfig(fraction=0.5))
        assert "world.weather.changed" in text

    def test_no_events_says_so(self) -> None:
        perception = make_perception(heard_events=[])
        text = render_perception_text(perception, depth=RetrievalDepthConfig(fraction=0.5))
        assert "Nothing notable happened recently" in text

    def test_heard_events_capped_by_depth(self) -> None:
        events = [
            PerceivedEvent(
                type="world.weather.changed", tick=10, payload={}, involves_self=False
            )
            for _ in range(50)
        ]
        perception = make_perception(heard_events=events)
        shallow_depth = RetrievalDepthConfig(fraction=0.0)
        text = render_perception_text(perception, depth=shallow_depth)
        rendered_lines = [line for line in text.splitlines() if line.startswith("  - [")]
        assert len(rendered_lines) == shallow_depth.max_heard_events_rendered
        assert "more event" in text

    def test_relationship_notes_only_at_high_depth(self) -> None:
        perception = make_perception(visible_agents=["a0002"])
        notes = {"a0002": "lent me 500 sim last week"}

        shallow = render_perception_text(
            perception, depth=RetrievalDepthConfig(fraction=0.0), relationship_notes=notes
        )
        deep = render_perception_text(
            perception, depth=RetrievalDepthConfig(fraction=1.0), relationship_notes=notes
        )
        assert "lent me 500 sim" not in shallow
        assert "lent me 500 sim" in deep

    def test_missing_bios_degrades_gracefully(self) -> None:
        perception = make_perception(visible_agents=["a0002"])
        text = render_perception_text(
            perception, depth=RetrievalDepthConfig(fraction=1.0), visible_agent_bios=None
        )
        assert "a0002" in text

    def test_text_length_grows_with_depth(self) -> None:
        events = [
            PerceivedEvent(
                type="world.weather.changed", tick=10, payload={"n": i},
                involves_self=False
            )
            for i in range(30)
        ]
        perception = make_perception(
            visible_agents=[f"a{i:04d}" for i in range(6)], heard_events=events
        )
        bios = {f"a{i:04d}": "a resident of Kasukabe with a long backstory" for i in range(6)}
        notes = {f"a{i:04d}": "owes me money" for i in range(6)}

        shallow = render_perception_text(
            perception,
            depth=RetrievalDepthConfig(fraction=0.0),
            visible_agent_bios=bios,
            relationship_notes=notes,
        )
        deep = render_perception_text(
            perception,
            depth=RetrievalDepthConfig(fraction=1.0),
            visible_agent_bios=bios,
            relationship_notes=notes,
        )
        assert len(deep) > len(shallow)


class TestPeopleAreNamed:
    def test_names_appear_at_every_depth_including_zero(self) -> None:
        """`say` needs listener_agent_id, so the id must stay - but an agent
        cannot greet a0031. Naming is not "richer context" the depth dial
        may withhold; it is the difference between a room of strangers and
        a town."""
        text = render_perception_text(
            make_perception(visible_agents=["a0031", "a0087"]),
            depth=RetrievalDepthConfig(fraction=0.0),
            visible_agent_names={"a0031": "Halcyon Quarshie", "a0087": "Milo Quarshie"},
        )
        assert "Halcyon Quarshie (a0031)" in text
        assert "Milo Quarshie (a0087)" in text

    def test_an_unnamed_agent_still_renders_its_id(self) -> None:
        text = render_perception_text(
            make_perception(visible_agents=["a0031"]),
            depth=RetrievalDepthConfig(fraction=0.0),
            visible_agent_names={},
        )
        assert "a0031" in text

    def test_names_survive_the_bio_path_too(self) -> None:
        text = render_perception_text(
            make_perception(visible_agents=["a0031"]),
            depth=RetrievalDepthConfig(fraction=1.0),
            visible_agent_bios={"a0031": "a careful bookkeeper"},
            visible_agent_names={"a0031": "Halcyon Quarshie"},
        )
        assert "Halcyon Quarshie (a0031): a careful bookkeeper" in text


class TestStandingBrief:
    def test_an_agent_is_told_its_trade_and_where_work_is(self) -> None:
        text = render_perception_text(
            make_perception(),
            depth=RetrievalDepthConfig(fraction=0.0),
            self_brief={"name": "Ulrike Greenhow", "occupation": "miner",
                        "location_id": "home:h0066",
                        "workplace": "Kasukabe Ore Outcrop"},
        )
        assert "You are Ulrike Greenhow, the town's miner." in text
        assert "Your work is at Kasukabe Ore Outcrop." in text

    def test_standing_at_work_is_not_told_to_travel(self) -> None:
        text = render_perception_text(
            make_perception(),
            depth=RetrievalDepthConfig(fraction=0.0),
            self_brief={"name": "Ulrike Greenhow", "occupation": "miner",
                        "location_id": "Kasukabe Ore Outcrop",
                        "workplace": "Kasukabe Ore Outcrop"},
        )
        assert "You are at your work" in text
        assert "Your work is at" not in text

    def test_an_empty_larder_says_what_follows_from_it(self) -> None:
        """DESIGN.md section 3: upkeep is charged whether or not an agent
        earns, so an empty larder is a countdown and the agent is entitled
        to know the number."""
        text = render_perception_text(
            make_perception(), depth=RetrievalDepthConfig(fraction=0.0),
            self_brief={"name": "X", "food_units": 0},
        )
        assert "You have no food." in text
        assert "hungry, then ill, then dormant" in text

    def test_a_low_larder_is_counted_not_alarmed(self) -> None:
        """The count still carries; only the noun changed. These two used
        to assert "unit(s) of food" / "units of food", the wording doc 01
        blocker 8 names as the source of `eat: 'food' is not food` x4 -
        the brief taught a word the tool refuses. See
        test_the_brief_names_the_food_it_holds.py."""
        text = render_perception_text(
            make_perception(), depth=RetrievalDepthConfig(fraction=0.0),
            self_brief={"name": "X", "food_units": 2, "food_items": {"bread": 2}},
        )
        assert "down to 2 edible item(s)" in text
        assert "bread 2" in text
        assert "no food" not in text

    def test_a_full_larder_is_stated_plainly(self) -> None:
        text = render_perception_text(
            make_perception(), depth=RetrievalDepthConfig(fraction=0.0),
            self_brief={"name": "X", "food_units": 12, "food_items": {"bread": 12}},
        )
        assert "holding 12 edible item(s)" in text
        assert "bread 12" in text

    def test_no_brief_changes_nothing(self) -> None:
        """Every existing caller passes no brief and must render exactly
        as it did."""
        plain = render_perception_text(
            make_perception(), depth=RetrievalDepthConfig(fraction=0.0))
        assert "You are Marta" not in plain
        assert "Your work is at" not in plain
        assert plain.startswith("It is game-day")
        assert "You are at" in plain

    def test_an_occupation_with_no_workplace_is_not_given_one(self) -> None:
        """"Your work is at the town hall", told to a barista, is worse
        than silence."""
        text = render_perception_text(
            make_perception(), depth=RetrievalDepthConfig(fraction=0.0),
            self_brief={"name": "X", "occupation": "child",
                        "location_id": "home:h0001", "workplace": None},
        )
        assert "Your work is at" not in text
        assert "You are at home:h0001." in text


class TestSpeechIsAudibleWhenTheListenerNextActs:
    """23 utterances over 613 ticks, in 19 "exchanges", almost every one a
    single turn with no reply.

    The cause was arithmetic, not the model. The scheduler runs each agent
    every `cadence_ticks` - 3 at the default target_fraction of 0.3 - and
    the hearing window at depth 0 was 1 tick. An utterance was audible for
    one tick and the person it was addressed to did not act for another
    three, so two of every three remarks were inaudible by construction.
    """

    def test_the_window_covers_the_default_agent_cadence(self) -> None:
        from world.core.scheduler import SchedulerConfig

        cadence = SchedulerConfig(target_fraction=0.3).cadence_ticks
        window = RetrievalDepthConfig(fraction=0.0).hearing_window_ticks
        assert window > cadence, (
            f"speech is audible for {window} tick(s) but the listener does not "
            f"act for {cadence} - a reply is impossible whatever the model does"
        )

    def test_the_floor_holds_at_every_depth(self) -> None:
        """Depth governs how much of the world an agent takes in. It must
        not be able to turn the town deaf."""
        from world.core.scheduler import SchedulerConfig

        cadence = SchedulerConfig(target_fraction=0.3).cadence_ticks
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            window = RetrievalDepthConfig(fraction=fraction).hearing_window_ticks
            assert window > cadence, f"deaf at depth {fraction}"

    def test_the_window_still_widens_with_depth(self) -> None:
        assert (RetrievalDepthConfig(fraction=1.0).hearing_window_ticks
                > RetrievalDepthConfig(fraction=0.0).hearing_window_ticks)


class TestSpeechReadsAsSpeech:
    """Speech arrived as a raw payload dict under a bracketed event type:

        - [agent.utterance.spoken, about you] {'speaker_agent_id': 'a0121',
          'listener_agent_id': None, 'conversation_id': '6a468f2e-...', ...}

    A model has to reverse-engineer that somebody spoke before it can
    consider answering, and the speaker is an id it cannot address.
    Measured against that rendering: 28 utterances and zero replies.
    """

    def spoken(self, **over):
        payload = {
            "speaker_agent_id": "a0121", "listener_agent_id": "a0087",
            "conversation_id": "6a468f2e-bac4-4cf2-a135-208289f0a8a6",
            "location_id": "Hobbs Cafe", "content": "Any work going today?",
            "is_public": False,
        }
        payload.update(over.pop("payload", {}))
        return PerceivedEvent(
            type="agent.utterance.spoken", tick=40,
            involves_self=over.pop("involves_self", True), payload=payload,
        )

    def render(self, event, **kw):
        p = Perception(
            agent_id="a0087", tick=41, location_id="Hobbs Cafe",
            visible_agents=["a0121"], heard_events=[event],
            ambient=AmbientInfo(game_day=1, hour=9, minute=30, is_daytime=True),
        )
        return render_perception_text(
            p, depth=RetrievalDepthConfig(fraction=0.0),
            visible_agent_names={"a0121": "Arjun Holloway"}, **kw)

    def test_the_speaker_is_named_and_the_words_are_quoted(self) -> None:
        text = self.render(self.spoken())
        assert 'Said to you, and not yet answered:' in text
        assert 'Arjun Holloway (a0121): "Any work going today?"' in text

    def test_the_conversation_uuid_never_reaches_the_model(self) -> None:
        """It is bookkeeping. It cost prompt tokens on every remark and told
        the agent nothing it could act on."""
        assert "6a468f2e" not in self.render(self.spoken())

    def test_addressed_to_you_is_distinguishable_from_overheard(self) -> None:
        """A question to answer gets its own section; a remark to the room
        stays a bullet in the event log. The raw dump made them identical."""
        to_me = self.render(self.spoken())
        overheard = self.render(
            self.spoken(involves_self=False, payload={"listener_agent_id": None}))
        assert "Said to you, and not yet answered:" in to_me
        assert "Said to you, and not yet answered:" not in overheard
        assert "said" in overheard

    def test_speech_from_elsewhere_says_where(self) -> None:
        """The window spans several ticks; an agent that has walked on would
        otherwise read old speech as happening in front of it."""
        p = Perception(
            agent_id="a0001", tick=41, location_id="Kasukabe Farmland",
            visible_agents=[], heard_events=[self.spoken()],
            ambient=AmbientInfo(game_day=1, hour=9, minute=0, is_daytime=True),
        )
        text = render_perception_text(
            p, depth=RetrievalDepthConfig(fraction=0.0),
            visible_agent_names={"a0121": "Arjun Holloway"})
        assert "at Hobbs Cafe" in text

    def test_speech_outranks_other_events_in_a_full_window(self) -> None:
        """A remark addressed to this agent must not fall off the end of
        max_events under a pile of arrivals and departures."""
        noise = [
            PerceivedEvent(type="agent.moved", tick=t, involves_self=False,
                           payload={"agent_id": f"a{t:04d}"})
            for t in range(30, 39)
        ]
        p = Perception(
            agent_id="a0087", tick=41, location_id="Hobbs Cafe",
            visible_agents=[], heard_events=[*noise, self.spoken()],
            ambient=AmbientInfo(game_day=1, hour=9, minute=30, is_daytime=True),
        )
        text = render_perception_text(
            p, depth=RetrievalDepthConfig(fraction=0.0),
            visible_agent_names={"a0121": "Arjun Holloway"})
        assert "Said to you, and not yet answered:" in text

    def test_a_non_speech_event_still_renders(self) -> None:
        """An event nothing renders specially still reaches the resident
        as its raw shape rather than vanishing.

        Not `agent.moved` any more: TOOL-05 gave that one words of its
        own ("You arrived at X"), which is the same argument this class
        makes about speech. Any type with no branch does.
        """
        odd = PerceivedEvent(type="world.weather.changed", tick=40,
                             involves_self=False, payload={"to": "rain"})
        assert "world.weather.changed" in self.render(odd)

    def test_an_arrival_reads_as_an_arrival(self) -> None:
        """TOOL-05(b). This used to arrive as
        `- [agent.moved, nearby] {'agent_id': 'a0121', ...}`, which is
        exactly the "reverse-engineer it from a dict" defect this class
        exists to hold against speech."""
        arrived = PerceivedEvent(
            type="agent.moved", tick=40, involves_self=False,
            payload={"agent_id": "a0121", "location_id": "Hobbs Cafe", "arrived": True},
        )
        text = self.render(arrived)
        assert "arrived" in text
        assert "agent.moved" not in text, "the raw event type reached the resident"
        assert "{" not in text, "a payload dict reached the resident"


class TestPresenceIsAConstraintNotSceneSetting:
    """Liora Thorne, shown "With you: Wren Thorne, Marta Thorne, Idris
    Thorne", called out "Perrin! You walking or what?" twice. Perrin was
    across town. A list of three names reads as "these are here too" unless
    it says otherwise."""

    def test_the_list_says_it_is_exhaustive(self) -> None:
        text = render_perception_text(
            make_perception(visible_agents=["a0031", "a0087"]),
            depth=RetrievalDepthConfig(fraction=0.0),
            visible_agent_names={"a0031": "Wren Thorne", "a0087": "Marta Thorne"},
        )
        assert "and nobody else" in text

    def test_an_empty_room_says_nobody_will_hear(self) -> None:
        text = render_perception_text(
            make_perception(visible_agents=[]),
            depth=RetrievalDepthConfig(fraction=0.0))
        assert "heard by nobody" in text

    def test_the_bio_path_says_it_too(self) -> None:
        text = render_perception_text(
            make_perception(visible_agents=["a0031"]),
            depth=RetrievalDepthConfig(fraction=1.0),
            visible_agent_bios={"a0031": "a patient farmer"},
            visible_agent_names={"a0031": "Wren Thorne"})
        assert "and nobody else" in text


def _utterance(speaker: str, listener: str, where: str, text: str):
    from world.core.perception import PerceivedEvent

    return PerceivedEvent(
        type="agent.utterance.spoken",
        tick=5,
        payload={
            "speaker_agent_id": speaker,
            "listener_agent_id": listener,
            "location_id": where,
            "content": text,
        },
        involves_self=True,
    )


def _perception_for(agent_id: str, events, where="market", visible=()):
    from world.core.perception import AmbientInfo, Perception

    return Perception(
        agent_id=agent_id,
        tick=5,
        location_id=where,
        visible_agents=list(visible),
        heard_events=list(events),
        ambient=AmbientInfo(game_day=0, hour=9, minute=0, is_daytime=True),
    )


def test_an_agent_is_not_asked_to_answer_itself() -> None:
    """involves_self is true for the SPEAKER too - involved_agent_ids
    carries both ends - and an utterance is an event at the speaker's own
    location, so an agent's own words came back to it under "Said to you,
    and not yet answered" with an instruction to reply. Observed live: 3
    of 9 delivered remarks were the reader's own, Ainsley among them.
    """
    from world.retrieval_depth import format_addressed_to_you

    own = _utterance("n02", "n03", "market", "the market's not empty, Tobias")
    assert format_addressed_to_you(_perception_for("n02", [own])) is None


def test_a_remark_from_somebody_else_still_arrives() -> None:
    from world.retrieval_depth import format_addressed_to_you

    theirs = _utterance("n03", "n02", "market", "Ainsley - where's the trade?")
    rendered = format_addressed_to_you(
        _perception_for("n02", [theirs], visible=["n03"]), {"n03": "Tobias Holloway"}
    )
    assert rendered is not None
    assert "Tobias Holloway" in rendered
    assert "where's the trade" in rendered


def test_own_words_do_not_crowd_out_a_real_question() -> None:
    """Only the last three are rendered, so self-echo could push a genuine
    question off the end of the list."""
    from world.retrieval_depth import format_addressed_to_you

    events = [
        _utterance("n02", "n03", "market", "mine one"),
        _utterance("n02", "n03", "market", "mine two"),
        _utterance("n02", "n03", "market", "mine three"),
        _utterance("n03", "n02", "market", "the real question"),
    ]
    rendered = format_addressed_to_you(_perception_for("n02", events))
    assert rendered is not None
    assert "the real question" in rendered
    assert "mine one" not in rendered


def test_an_open_question_is_read_before_the_town_directory() -> None:
    """Ordering is the last lever there is on whether a question gets
    answered.

    The remark first arrived inside "Recently:", as one bullet among
    arrivals and departures. Pulled out, it then sat fifth - under the
    agent's identity, larder, balance, household and a nine-name roster of
    the town. An open question addressed to you by name outranks a
    directory: the roster is reference material, the question is the one
    thing in the prompt another resident is actively waiting on.
    """
    from world.core.perception import AmbientInfo, Perception
    from world.retrieval_depth import RetrievalDepthConfig, render_perception_text

    perception = Perception(
        agent_id="n04",
        tick=5,
        location_id="market",
        visible_agents=["n02"],
        heard_events=[_utterance("n02", "n04", "market", "Perrin. Walk home.")],
        ambient=AmbientInfo(game_day=0, hour=9, minute=0, is_daytime=True),
    )
    text = render_perception_text(
        perception,
        depth=RetrievalDepthConfig(),
        visible_agent_names={"n02": "Ainsley Holloway"},
        self_brief={
            "name": "Perrin Holloway",
            "occupation": "student",
            "location_id": "market",
            "food_units": 0,
            "sim_balance": 399,
            "household": ["Bram Holloway (n01), miner"],
            "townspeople": ["Wren Thorne (n06), farmer"],
        },
    )

    assert "Said to you" in text
    assert text.index("Said to you") < text.index("The rest of the town:")
    assert text.index("Said to you") < text.index("Your household:")
    assert text.index("game-day") < text.index("Said to you")
