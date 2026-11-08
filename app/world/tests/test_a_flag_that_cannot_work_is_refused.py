"""A knob that boots a doomed run must fail at validation, not at tick 1.

`CORE-27`. `--world-mode external` skips the embedded world server and
points every engine at a separate host. That host is `world/main.py`, and
`main.py` does not serve the engine contract: no `/tools/registry`, no
`/ledger/payer`, no `/ledger/debit`. So the flag was accepted, the
supervisor started N engine processes, and the run died at the first
dispatch with a connection error that named none of this.

Refusing it at `validate()` costs nothing and turns a confusing runtime
failure into a sentence that says what to do instead.

(The finding's other half - `checkpoint_every_day` being inert - is no
longer true: `run_tick` reads it at the day boundary. Left unasserted here
rather than guarded, because a test for a knob that already works belongs
with the checkpointing tests, not in a file about refusals.)
"""
from __future__ import annotations

from world.run_genesis import GenesisRunnerConfig


def test_external_world_mode_is_refused() -> None:
    errors = GenesisRunnerConfig(world_mode="external").validate()
    assert any("external" in e for e in errors), (
        "CORE-27: --world-mode external still validates, and boots a run "
        f"whose engines cannot fetch a tool or spend a cent. Errors: {errors}"
    )


def test_the_refusal_says_what_is_missing() -> None:
    """A refusal that teaches. The operator needs to know it is the ROUTES
    that are absent, not the host."""
    errors = GenesisRunnerConfig(world_mode="external").validate()
    message = " ".join(errors)
    assert "/tools/registry" in message and "embedded" in message


def test_embedded_still_validates() -> None:
    """The other half: a guard that refuses the working mode is an outage."""
    errors = GenesisRunnerConfig(world_mode="embedded").validate()
    assert not any("world_mode" in e for e in errors)
