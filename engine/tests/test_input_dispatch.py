"""M1 — /execute dispatches on input.kind and the request reshape validates.

kind="input" runs a turn (handle_input); kind="block_input" routes to
resume_block. The block_input shape requires a block_id; the dropped v1 fields
(message, edit_message_id, retry_message_id) are no longer accepted.
"""

import pytest

from src.server import ExecuteRequest, InputUnion


def test_input_kind_builds():
    req = ExecuteRequest(input=InputUnion(kind="input", text="hi"))
    assert req.input.kind == "input"
    assert req.input.text == "hi"
    assert req.tool is True and req.telemetry == "off" and req.craft is False


def test_block_input_requires_block_id():
    with pytest.raises(Exception):
        ExecuteRequest(input=InputUnion(kind="block_input", value={"x": 1}))


def test_block_input_with_block_id_builds():
    req = ExecuteRequest(
        thread_id="thr_1",
        input=InputUnion(kind="block_input", block_id="craft:thr_1:9", value={"a": 1}, text="go"),
    )
    assert req.input.block_id == "craft:thr_1:9"
    assert req.input.value == {"a": 1}


def test_dropped_v1_fields_are_ignored():
    req = ExecuteRequest(
        input=InputUnion(kind="input", text="hi"),
        message="old",
        edit_message_id="x",
        retry_message_id="y",
    )
    assert not hasattr(req, "message")
    assert not hasattr(req, "edit_message_id")
    assert not hasattr(req, "retry_message_id")


def test_gates_round_trip_from_request():
    req = ExecuteRequest(
        input=InputUnion(kind="input", text="hi"),
        craft=True,
        tool=False,
        telemetry="trace",
        super_thinking=True,
        image=True,
        direct=True,
        max_turns=5,
    )
    assert req.craft is True and req.tool is False
    assert req.telemetry == "trace" and req.super_thinking is True
    assert req.image is True and req.direct is True and req.max_turns == 5
