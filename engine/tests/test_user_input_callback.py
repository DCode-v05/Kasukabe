"""Permission-prompt callback contract: dict-in/dict-out through the engine.

The engine escalates a permission decision to the user via the same
``ask_user_callback`` the ask_user tool uses, but with a richer dict shape:
``{question, choices, suggested_rule}`` in, ``{response, feedback,
content_blocks}`` out. In v2 that callback is the coordinator's
``await_block_input`` and the answer is delivered by ``resume_block`` — these
tests pin that dict-shape handling end to end.
"""

from __future__ import annotations

import asyncio


from src.engine_core.events import CraftRequest


def run(coro):
    return asyncio.run(coro)


def bare_coordinator(emitter):
    from src.engine_core.coordinator import Coordinator

    return Coordinator(adapter=None, emitter=emitter, memory=None, db=None)


class TestAwaitBlockInputPermission:
    def test_dict_input_resolves_to_dict_payload(self):
        from src.engine_core import coordinator as coord_mod

        captured: list = []

        class FakeEmitter:
            async def emit(self, ev):
                captured.append(ev)

        coord = bare_coordinator(FakeEmitter())

        async def driver():
            coord_mod.current_thread_id_var.set("thr_perm")
            task = asyncio.create_task(
                coord.await_block_input(
                    {
                        "question": "Allow write to /etc/hosts?",
                        "choices": ["allow", "deny"],
                        "suggested_rule": "Write(/etc/hosts)",
                    }
                )
            )
            for _ in range(5):
                await asyncio.sleep(0)
                if coord.pending_inputs:
                    break
            block_id, (fut, fmt, tid) = next(iter(coord.pending_inputs.items()))
            # resume_block resolves the future with feedback + the answer.
            await coord.resume_block(
                "thr_perm",
                block_id,
                value="allow",
                text="sure, go ahead",
            )
            return await task

        reply = run(driver())
        assert isinstance(reply, dict)
        assert reply["response"] == "allow"
        assert reply["feedback"] == "sure, go ahead"
        # Emitted event carries permission metadata
        assert len(captured) == 1
        ev = captured[0]
        assert isinstance(ev, CraftRequest)
        assert ev.is_permission is True
        assert ev.options == [
            {"label": "allow", "value": "allow"},
            {"label": "deny", "value": "deny"},
        ]
        assert ev.suggested_rule == "Write(/etc/hosts)"

    def test_string_input_returns_plain_string(self):
        """A bare string question returns a plain-string answer (ask_user tool path)."""
        from src.engine_core import coordinator as coord_mod

        class FakeEmitter:
            async def emit(self, ev):
                pass

        coord = bare_coordinator(FakeEmitter())

        async def driver():
            coord_mod.current_thread_id_var.set("thr_str")
            task = asyncio.create_task(coord.await_block_input("just a question?"))
            for _ in range(5):
                await asyncio.sleep(0)
                if coord.pending_inputs:
                    break
            block_id, (fut, fmt, tid) = next(iter(coord.pending_inputs.items()))
            await coord.resume_block("thr_str", block_id, value="my answer", text="")
            return await task

        reply = run(driver())
        assert reply == "my answer"
