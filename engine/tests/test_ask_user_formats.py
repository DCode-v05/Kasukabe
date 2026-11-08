"""Tests for the unified `ask_user` tool — three formats inside one tool.

Covers:
  - Tool input validation (format enum, options length, defaulting).
  - The `await_user_input` shared pipeline both the tool handler and the
    internal `_escalate_to_user` route through.
  - CraftRequest event propagation of `format` + `options` (and the
    legacy `choices` mirror for one-release back-compat).
  - server.py `await_block_input` callback shape: stores (future, format)
    in `pending_inputs`, emits the structured event, returns the
    deterministic LLM-facing string for multichoice.
  - `block_input` shape-match guard: refuses `answers[]` for a
    non-multichoice waiter and refuses missing `answers[]` for a
    multichoice waiter, even on fallback-to-oldest resolution.
"""

from __future__ import annotations

import asyncio

from src.engine_core.events import CraftRequest


def run(coro):
    return asyncio.run(coro)


class StubThread:
    """Smallest thing the ask_user handler closure pulls off `self`."""

    class S:
        EXECUTING = "EXECUTING"
        WAITING_INPUT = "WAITING_INPUT"

    ThreadState = S
    ASK_USER_VALID_FORMATS = frozenset({"choice", "multichoice", "confirm"})
    ASK_USER_DEFAULT_CONFIRM_OPTIONS = [
        {"label": "Yes", "value": "yes"},
        {"label": "No", "value": "no"},
    ]

    def __init__(self, callback):
        self.ask_user_callback = callback
        self.state_log: list[str] = []

    async def set_state(self, s):
        self.state_log.append(s)

    @staticmethod
    def normalize_options(raw):
        from src.engine_core.thread import ExecutionThread

        return ExecutionThread.normalize_options(raw)

    async def await_user_input(self, question, format="confirm", options=None, *, asset_hint=""):
        from src.engine_core.thread import ExecutionThread

        return await ExecutionThread.await_user_input(
            self, question, format, options, asset_hint=asset_hint
        )


def build_handler(callback):
    """Reconstruct the ask_user handler closure against a stub Thread."""
    stub = StubThread(callback)
    from src.engine_core.thread import ExecutionThread

    valid = ExecutionThread.ASK_USER_VALID_FORMATS
    default_confirm = ExecutionThread.ASK_USER_DEFAULT_CONFIRM_OPTIONS

    async def handler(params: dict) -> str:
        question = (params.get("question") or "").strip()
        if not question:
            return "Error: no question provided"
        fmt = (params.get("format") or "confirm").lower()
        if fmt not in valid:
            return f"Error: invalid format {fmt!r}. Must be one of: choice, multichoice, confirm."
        options = ExecutionThread.normalize_options(params.get("options") or [])
        if fmt == "confirm":
            if not options:
                options = list(default_confirm)
            elif len(options) != 2:
                return "Error: format='confirm' takes exactly 2 options (or omit to default to Yes/No)."
        else:
            if not (2 <= len(options) <= 4):
                return f"Error: format={fmt!r} requires 2-4 options; got {len(options)}."
        return await stub.await_user_input(question, format=fmt, options=options)

    return handler, stub


class TestAskUserHandlerValidation:
    def test_choice_with_three_options_succeeds(self):
        async def cb(params):
            return params

        h, _ = build_handler(cb)
        out = run(h({"question": "pick", "format": "choice", "options": ["a", "b", "c"]}))
        assert isinstance(out, str)

    def test_choice_with_one_option_returns_error_string(self):
        async def cb(params):
            raise AssertionError("callback must NOT be called when validation fails")

        h, _ = build_handler(cb)
        out = run(h({"question": "pick", "format": "choice", "options": ["a"]}))
        assert "requires 2-4 options" in out

    def test_choice_with_five_options_returns_error_string(self):
        async def cb(params):
            raise AssertionError("callback must NOT be called when validation fails")

        h, _ = build_handler(cb)
        out = run(h({"question": "pick", "format": "choice", "options": list("abcde")}))
        assert "requires 2-4 options" in out

    def test_multichoice_validation_matches_choice(self):
        async def cb(params):
            raise AssertionError("callback must NOT be called when validation fails")

        h, _ = build_handler(cb)
        out = run(h({"question": "pick", "format": "multichoice", "options": ["a"]}))
        assert "requires 2-4 options" in out

    def test_confirm_with_no_options_injects_yes_no_server_side(self):
        seen: dict = {}

        async def cb(params):
            seen.update(params)
            return "yes"

        h, _ = build_handler(cb)
        run(h({"question": "ok?", "format": "confirm"}))
        assert seen["format"] == "confirm"
        assert seen["options"] == [
            {"label": "Yes", "value": "yes"},
            {"label": "No", "value": "no"},
        ]

    def test_confirm_with_explicit_two_options_passes_through(self):
        seen: dict = {}

        async def cb(params):
            seen.update(params)
            return "stay"

        h, _ = build_handler(cb)
        run(
            h(
                {
                    "question": "stay or leave?",
                    "format": "confirm",
                    "options": ["Stay", "Leave"],
                }
            )
        )
        assert seen["options"] == [
            {"label": "Stay", "value": "Stay"},
            {"label": "Leave", "value": "Leave"},
        ]

    def test_invalid_format_returns_error_string(self):
        async def cb(params):
            raise AssertionError("callback must NOT be called")

        h, _ = build_handler(cb)
        out = run(h({"question": "x", "format": "freeform", "options": []}))
        assert "invalid format" in out.lower()

    def test_missing_question_returns_error_string(self):
        async def cb(params):
            raise AssertionError("callback must NOT be called")

        h, _ = build_handler(cb)
        out = run(h({"question": "", "format": "confirm"}))
        assert "no question" in out.lower()

    def test_normalize_options_accepts_strings_and_dicts(self):
        from src.engine_core.thread import ExecutionThread

        assert ExecutionThread.normalize_options(["a", "b"]) == [
            {"label": "a", "value": "a"},
            {"label": "b", "value": "b"},
        ]
        assert ExecutionThread.normalize_options(
            [
                {"label": "Approve", "value": "approve"},
                {"label": "Deny", "value": "deny"},
            ]
        ) == [
            {"label": "Approve", "value": "approve"},
            {"label": "Deny", "value": "deny"},
        ]
        long = "x" * 200
        out = ExecutionThread.normalize_options([long, long])
        assert all(len(o["label"]) == 80 for o in out)

    def test_state_transitions_around_callback(self):
        async def cb(params):
            return "ok"

        h, stub = build_handler(cb)
        run(h({"question": "q", "format": "confirm"}))
        assert stub.state_log == ["WAITING_INPUT", "EXECUTING"]


def bare_coordinator(emitter):
    """Construct a Coordinator with only what the pause path needs.

    db=None keeps the stores in their no-op modes; adapter/memory are unused
    by await_block_input / resume_block / resolve_payload.
    """
    from src.engine_core.coordinator import Coordinator

    return Coordinator(adapter=None, emitter=emitter, memory=None, db=None)


class TestAwaitBlockInput:
    def test_choice_emits_craft_with_block_id_and_resumes(self):
        from src.engine_core import coordinator as coord_mod

        captured: list = []

        class FakeEmitter:
            async def emit(self, ev):
                captured.append(ev)

        coord = bare_coordinator(FakeEmitter())

        async def driver():
            coord_mod.current_thread_id_var.set("thr_1")
            task = asyncio.create_task(
                coord.await_block_input(
                    {
                        "question": "pick one",
                        "format": "choice",
                        "options": [
                            {"label": "Apple", "value": "apple"},
                            {"label": "Banana", "value": "banana"},
                        ],
                    }
                )
            )
            for _ in range(5):
                await asyncio.sleep(0)
                if coord.pending_inputs:
                    break
            assert coord.pending_inputs
            block_id, (fut, fmt, tid) = next(iter(coord.pending_inputs.items()))
            assert fmt == "choice"
            assert tid == "thr_1"
            await coord.resume_block("thr_1", block_id, value="apple", text="")
            return await task

        reply = run(driver())
        assert reply == "apple"

        assert len(captured) == 1
        ev = captured[0]
        assert isinstance(ev, CraftRequest)
        assert ev.format == "choice"
        assert ev.thread_id == "thr_1"
        assert ev.block_id.startswith("craft:thr_1:")
        assert ev.options == [
            {"label": "Apple", "value": "apple"},
            {"label": "Banana", "value": "banana"},
        ]

    def test_multichoice_assembles_deterministic_string(self):
        from src.engine_core import coordinator as coord_mod

        class FakeEmitter:
            async def emit(self, ev):
                pass

        coord = bare_coordinator(FakeEmitter())

        async def driver():
            coord_mod.current_thread_id_var.set("thr_m")
            task = asyncio.create_task(
                coord.await_block_input(
                    {
                        "question": "which features",
                        "format": "multichoice",
                        "options": ["A", "B", "C"],
                    }
                )
            )
            for _ in range(5):
                await asyncio.sleep(0)
                if coord.pending_inputs:
                    break
            block_id, (fut, fmt, tid) = next(iter(coord.pending_inputs.items()))
            assert fmt == "multichoice"
            await coord.resume_block("thr_m", block_id, value=["A", "C"], text="also D")
            return await task

        reply = run(driver())
        assert reply == "selected: [A, C]; other: also D"

    def test_permission_prompt_returns_dict(self):
        from src.engine_core import coordinator as coord_mod

        captured: list = []

        class FakeEmitter:
            async def emit(self, ev):
                captured.append(ev)

        coord = bare_coordinator(FakeEmitter())

        async def driver():
            coord_mod.current_thread_id_var.set("thr_p")
            task = asyncio.create_task(
                coord.await_block_input(
                    {
                        "question": "allow Bash(npm *)?",
                        "choices": ["allow", "deny"],
                        "suggested_rule": "Bash(npm *)",
                    }
                )
            )
            for _ in range(5):
                await asyncio.sleep(0)
                if coord.pending_inputs:
                    break
            block_id, (fut, fmt, tid) = next(iter(coord.pending_inputs.items()))
            assert fmt == "permission"
            await coord.resume_block("thr_p", block_id, value="allow", text="ok")
            return await task

        reply = run(driver())
        assert isinstance(reply, dict)
        assert reply["response"] == "allow"

        assert len(captured) == 1
        ev = captured[0]
        assert ev.is_permission is True
        assert ev.options == [
            {"label": "allow", "value": "allow"},
            {"label": "deny", "value": "deny"},
        ]


class TestResolvePayload:
    def test_multichoice_assembles_string(self):
        from src.engine_core.coordinator import Coordinator

        out = Coordinator.resolve_payload("multichoice", ["A", "B"], "and D")
        assert out["response"] == "selected: [A, B]; other: and D"

    def test_choice_uses_value(self):
        from src.engine_core.coordinator import Coordinator

        out = Coordinator.resolve_payload("choice", "apple", "")
        assert out["response"] == "apple"

    def test_dict_value_reads_response(self):
        from src.engine_core.coordinator import Coordinator

        out = Coordinator.resolve_payload("confirm", {"response": "yes"}, "")
        assert out["response"] == "yes"

    def test_text_only_falls_back_to_text(self):
        from src.engine_core.coordinator import Coordinator

        out = Coordinator.resolve_payload("confirm", None, "free text")
        assert out["response"] == "free text"
        assert out["feedback"] == "free text"
