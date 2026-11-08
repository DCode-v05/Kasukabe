"""parse_dispatch is documented "Never raises". A non-string target made it.

Every other model-supplied field was type-checked - `isinstance(parsed, dict)`,
`isinstance(raw_segments, list)`, `isinstance(item, dict)`, `str(extract)`, and
explicitly `if not isinstance(rid, str): continue` for related_ids. `target`
alone went straight to `candidate.strip()` behind a truthiness guard, so a
number or a list raised AttributeError/TypeError inside the function. The
caller (ThreadRouter.dispatch) wraps parse_dispatch in the same try that catches
transport errors, so the exception was logged as "dispatch error on primary" and
retried - indistinguishable from a provider blip - and the input then landed on
a brand-new thread.

Two triggers are realistic, and both are the parser's own documented territory:
  - an all-digit hex suffix shorthand comes back as a bare JSON number
    (`"target": 8325`), and suffix shorthand is behaviour resolve_thread_id
    exists to forgive;
  - `target` sits in the same object as the array-valued `related_ids`, so
    `"target": ["thread-a41b"]` is a routine LLM shape slip.
"""

import json

import pytest

from src.engine_core.dispatch_parser import parse_dispatch, resolve_thread_id

KNOWN = {"thread-a41b", "thread-9f2c", "thread-c771"}
MESSAGE = "and bump the retry cap"


def payload(*segments, reasoning="because"):
    return json.dumps({"segments": list(segments), "reasoning": reasoning})


BAD_TARGETS = [
    pytest.param(8325, id="bare-number"),
    pytest.param(["thread-a41b"], id="list"),
    pytest.param({"id": "thread-a41b"}, id="dict"),
    pytest.param(True, id="bool"),
    pytest.param(4.2, id="float"),
]


class TestResolveThreadId:
    @pytest.mark.parametrize("bad", BAD_TARGETS)
    def test_non_string_resolves_to_none_without_raising(self, bad):
        assert resolve_thread_id(bad, KNOWN) is None

    def test_it_rejects_rather_than_coercing(self):
        """str(8325) would suffix-match an id ending in 8325 and misroute
        silently. None degrades to a new thread, which the shape rules handle."""
        known = {"thread-8325", "thread-9f2c"}
        assert resolve_thread_id(8325, known) is None
        assert resolve_thread_id("8325", known) == "thread-8325"

    def test_strings_are_unaffected(self):
        assert resolve_thread_id("thread-a41b", KNOWN) == "thread-a41b"
        assert resolve_thread_id("a41b", KNOWN) == "thread-a41b"
        assert resolve_thread_id(None, KNOWN) is None
        assert resolve_thread_id("", KNOWN) is None


class TestParseDispatch:
    @pytest.mark.parametrize("bad", BAD_TARGETS)
    def test_a_bad_target_does_not_raise(self, bad):
        raw = payload({"extract": MESSAGE, "verdict": "continue", "target": bad})
        dispatch = parse_dispatch(raw, MESSAGE, KNOWN)
        assert dispatch.segments

    @pytest.mark.parametrize("bad", BAD_TARGETS)
    def test_a_bad_target_degrades_to_open(self, bad):
        """The shape rule already covers 'targeted verdict, no known target'.
        The type guard just lets it be reached instead of exploding first."""
        raw = payload({"extract": MESSAGE, "verdict": "continue", "target": bad})
        seg = parse_dispatch(raw, MESSAGE, KNOWN).segments[0]
        assert seg.verdict == "open"
        assert seg.target is None
        assert seg.extract == MESSAGE

    def test_a_bad_target_is_logged_as_a_type_problem(self, caplog):
        """It must be distinguishable from an id that simply named no thread,
        or an operator reading the logs cannot tell a prompt bug from drift."""
        raw = payload({"extract": MESSAGE, "verdict": "continue", "target": 8325})
        with caplog.at_level("WARNING"):
            parse_dispatch(raw, MESSAGE, KNOWN)
        assert any("non-string thread id" in r.getMessage() for r in caplog.records)

    def test_a_good_target_still_routes(self):
        raw = payload({"extract": MESSAGE, "verdict": "continue", "target": "thread-a41b"})
        seg = parse_dispatch(raw, MESSAGE, KNOWN).segments[0]
        assert seg.verdict == "continue"
        assert seg.target == "thread-a41b"

    def test_one_bad_target_does_not_lose_its_siblings(self):
        raw = payload(
            {"extract": MESSAGE, "verdict": "continue", "target": ["thread-a41b"]},
            {"extract": MESSAGE, "verdict": "continue", "target": "thread-9f2c"},
        )
        dispatch = parse_dispatch(raw, MESSAGE, KNOWN)
        assert len(dispatch.segments) == 2
        assert dispatch.segments[1].target == "thread-9f2c"

    def test_related_ids_of_the_wrong_type_are_still_skipped(self):
        raw = payload(
            {
                "extract": MESSAGE,
                "verdict": "claimed",
                "related_ids": [123, "thread-a41b", None],
            }
        )
        seg = parse_dispatch(raw, MESSAGE, KNOWN).segments[0]
        assert seg.related_ids == ["thread-a41b"]
