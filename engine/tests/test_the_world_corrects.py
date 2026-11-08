"""T4: a refusal from the world becomes a learning signal.

The roadmap's fourth track is "the world as corrector" and the assessment
records it as not started: *"No `SignalStore` writes from the seam.
`tool_refusals` is recorded and goes nowhere."*

That is the state, and it is precisely checkable. Every refusal the world
issues arrives at exactly one place - `WorldClient.call_tool`, which turns
a 4xx into the string "Error: ..." and hands it to the agent loop as text.
The resident reads it, and nothing else in the system ever does. The
learning centre has a `correction` signal type sitting unused for it, and
the pipeline that would turn corrections into durable knowledge has been
waiting on a caller.

What that costs, in the roadmap's own terms: C5 - "correction signals
emitted by the world and consumed by the pipeline" - cannot be greater
than zero, so the compounding the project is named for has no input at
all. A town that refuses the same resident the same way every morning
teaches it nothing between mornings.

These tests are the seam's half: a refusal is recorded, a success is not,
and recording never breaks the turn.
"""

from __future__ import annotations

import asyncio

from src.sim.world_corrections import (
    correction_from_refusal,
    is_refusal,
)


def test_a_world_refusal_is_recognised() -> None:
    assert is_refusal("Error: craft requires location 'sawmill', actor is at 'mill'")
    assert is_refusal("Error: world server unreachable (timeout)")


def test_a_successful_result_is_not_a_refusal() -> None:
    """A result that merely contains the word error - a resident saying
    "no error here" - must not be recorded as a correction."""
    assert not is_refusal('{"conversation_id": "c1", "heard_by": ["Nell"]}')
    assert not is_refusal("You harvested 3 wheat. No error occurred.")
    assert not is_refusal("")


def test_the_signal_says_which_tool_and_what_the_world_said() -> None:
    """A correction that does not name the verb it corrects is a sentence
    the pipeline cannot attach to anything."""
    signal = correction_from_refusal(
        agent_id="n07",
        tool_name="craft",
        tool_input={"recipe": "bread"},
        result="Error: craft requires location 'kitchen', actor is at 'home:h_field'",
        thread_id="t-n07",
    )
    assert signal is not None
    assert signal.type == "correction"
    assert "craft" in signal.content
    assert "kitchen" in signal.content
    assert signal.subject == "craft"
    assert signal.thread_id == "t-n07"


def test_the_source_is_one_the_pipeline_reads() -> None:
    """CORE-29. This test used to assert `source != "user"`, on the stated
    principle that a world refusal is the town's physics and not a person
    correcting anybody.

    The principle was tidy and it made the mechanism inert.
    `learning_centre/pipeline.py`'s `step11_skill_evolution` selects
    `WHERE type = 'correction' AND source = 'user'`, so all 70 of run 23's
    corrections were written, stored, counted, and read by nothing. A test
    that pins a value the consumer filters out is a test that certifies
    the bug.

    `SIGNAL_SOURCES` is {user, engine, wm_flush} and has no third option
    meaning "the world said so"; in that vocabulary `user` means "arising
    from the conversation, worth learning from" - the frozen post-turn
    hook already stamps it on signals the engine itself writes. See
    `test_a_correction_reaches_the_pipeline.py` for the end-to-end proof
    that the pipeline now finds these."""
    from src.learning_centre.signals import SIGNAL_SOURCES

    signal = correction_from_refusal(
        agent_id="n07", tool_name="craft", tool_input={},
        result="Error: nope", thread_id="t",
    )
    assert signal.source in SIGNAL_SOURCES
    assert signal.source == "user"


def test_a_success_produces_no_signal() -> None:
    assert correction_from_refusal(
        agent_id="n07", tool_name="say", tool_input={},
        result='{"heard_by": []}', thread_id="t",
    ) is None


def test_recording_never_breaks_the_turn() -> None:
    """A learning signal is bookkeeping. If the store is unwritable the
    resident must still get its refusal text back and carry on - the
    opposite of the trace-store failure that halted a run."""
    from src.sim.world_corrections import record_correction

    class _Broken:
        async def write(self, signal):  # noqa: ANN001
            raise RuntimeError("disk full")

    result = asyncio.run(
        record_correction(
            _Broken(), agent_id="n07", tool_name="craft", tool_input={},
            result="Error: nope", thread_id="t",
        )
    )
    assert result is False, "a failed write should report itself, not raise"


def test_a_written_correction_reaches_the_store() -> None:
    from src.sim.world_corrections import record_correction

    written: list = []

    class _Store:
        async def write(self, signal):  # noqa: ANN001
            written.append(signal)
            return 1

    assert asyncio.run(
        record_correction(
            _Store(), agent_id="n07", tool_name="craft", tool_input={"recipe": "bread"},
            result="Error: craft requires location 'kitchen'", thread_id="t-n07",
        )
    ) is True
    assert len(written) == 1
    assert written[0].type == "correction"


def test_the_seam_is_wired_to_the_live_tool_path() -> None:
    """A corrector nothing calls is the failure this project keeps
    finding. `WorldClient.call_tool` is where every refusal in the town
    arrives."""
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "src" / "sim" / "world_tools.py"
    text = source.read_text(encoding="utf-8")
    assert "world_corrections" in text, "refusals still vanish into text"
    assert "record_correction" in text


def test_the_boot_path_attaches_a_real_store() -> None:
    """The wiring half. A corrector with `signal_store = None` records
    nothing and passes every test above - which is exactly the shape of
    failure this project keeps finding, so it gets its own assertion."""
    from pathlib import Path

    boot = Path(__file__).resolve().parent.parent / "src" / "sim" / "pinned_boot.py"
    text = boot.read_text(encoding="utf-8")
    assert "signal_store = SignalStore(" in text, (
        "nothing gives the world client a store, so every correction is dropped"
    )
    assert "world.thread_id = thread_id" in text, (
        "a signal with no thread cannot be grouped with the turn it came from"
    )
