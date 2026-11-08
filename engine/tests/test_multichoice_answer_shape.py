"""A multichoice answer must never be read as its own envelope's key names.

resolve_payload did `selected = value if isinstance(value, list) else (value or [])`.
InputUnion.value is typed `dict | None`, so over HTTP a multichoice answer is
ALWAYS a dict: the list branch is unreachable and the dict fell through to being
iterated, yielding its KEYS. `{"answer": ["Postgres"]}` became
`selected: [answer]` - a perfectly well-formed reply naming an option that does
not exist, which the suspended turn then reasoned over.

The list case is kept working because Python callers (SDK in-process, tests)
can still pass one.
"""

import pytest

from src.engine_core.coordinator import Coordinator, extract_selections
from src.server import InputUnion


def response_of(value, text: str = "") -> str:
    return Coordinator.resolve_payload("multichoice", value, text)["response"]


class TestTheWireShape:
    """These are the shapes a real client can actually send."""

    @pytest.mark.parametrize("key", ["answers", "selected", "selections", "answer"])
    def test_envelope_is_unwrapped_not_iterated(self, key):
        assert response_of({key: ["Postgres"]}) == "selected: [Postgres]; other: "

    def test_multiple_selections_survive(self):
        assert (
            response_of({"answers": ["Postgres", "MySQL"]})
            == "selected: [Postgres, MySQL]; other: "
        )

    def test_single_string_answer_is_one_selection(self):
        assert response_of({"answer": "Postgres"}) == "selected: [Postgres]; other: "

    def test_response_key_is_accepted(self):
        assert response_of({"response": "Postgres"}) == "selected: [Postgres]; other: "

    def test_free_text_rides_alongside(self):
        assert (
            response_of({"answers": ["Postgres"]}, "but check the licence")
            == "selected: [Postgres]; other: but check the licence"
        )

    def test_unrecognised_envelope_reports_nothing_selected(self):
        """Wrong, but visibly empty - the model is never handed an option name
        the user did not choose."""
        assert response_of({"tenant": "acme"}) == "selected: []; other: "

    def test_empty_and_missing_are_empty(self):
        assert response_of(None) == "selected: []; other: "
        assert response_of({}) == "selected: []; other: "
        assert response_of({"answers": []}) == "selected: []; other: "


class TestTheKeysAreNeverTheAnswer:
    """The precise regression: no envelope key may appear as a selection."""

    @pytest.mark.parametrize(
        "envelope",
        [
            {"answer": ["Postgres", "MySQL"]},
            {"selected": ["Postgres"]},
            {"response": "Postgres, MySQL"},
            {"answers": ["Postgres"], "note": "hi"},
        ],
    )
    def test_no_key_name_leaks_into_the_selection(self, envelope):
        got = response_of(envelope)
        inner = got.split("selected: [", 1)[1].split("]", 1)[0]
        for key in envelope:
            assert key not in inner.split(", "), f"envelope key {key!r} leaked as a selection"


class TestPythonCallersStillWork:
    def test_a_plain_list_is_still_accepted(self):
        assert response_of(["Postgres", "MySQL"]) == "selected: [Postgres, MySQL]; other: "


class TestTheWireTypeMakesTheDictPathTheOnlyOne:
    def test_a_list_value_is_rejected_by_the_request_model(self):
        """Proves the list branch was unreachable over HTTP: pydantic 422s it,
        so every real multichoice answer took the broken path."""
        with pytest.raises(Exception):
            InputUnion(kind="block_input", block_id="b", value=["Postgres"])

    def test_a_dict_value_is_accepted(self):
        inp = InputUnion(kind="block_input", block_id="b", value={"answers": ["Postgres"]})
        assert inp.value == {"answers": ["Postgres"]}


class TestExtractSelections:
    def test_nested_envelope_is_unwrapped_once_more(self):
        assert extract_selections({"answer": {"selected": ["a", "b"]}}) == ["a", "b"]

    def test_never_returns_dict_keys(self):
        assert extract_selections({"x": 1, "y": 2}) == []
