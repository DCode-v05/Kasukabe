"""Tests for the codename scrubber (src/server_sanitizer.py)."""

from src.server_sanitizer import (
    FORBIDDEN_SUBSTRINGS,
    REDACTION_MARKER,
    contains_forbidden,
    scrub_mapping,
    scrub_text,
)


class TestScrubText:
    def test_clean_text_unchanged(self):
        assert scrub_text("nothing forbidden here") == "nothing forbidden here"

    def test_empty_string_unchanged(self):
        assert scrub_text("") == ""

    def test_replaces_src_herald(self):
        assert scrub_text("import src.herald as h") == f"import {REDACTION_MARKER} as h"

    def test_replaces_src_raven(self):
        assert scrub_text("import src.raven as r") == f"import {REDACTION_MARKER} as r"

    def test_replaces_src_herald_subpath(self):
        assert scrub_text("from src.herald.policy import check_model") == (
            f"from {REDACTION_MARKER}.policy import check_model"
        )

    def test_replaces_src_raven_subpath(self):
        assert scrub_text("src.raven.events") == f"{REDACTION_MARKER}.events"

    def test_multiple_occurrences_replaced(self):
        text = "src.herald and src.herald and src.herald"
        result = scrub_text(text)
        assert result.count(REDACTION_MARKER) == 3
        assert "src.herald" not in result

    def test_multiple_different_substrings(self):
        text = "src.herald and src.raven"
        result = scrub_text(text)
        assert result.count(REDACTION_MARKER) == 2
        assert "src.herald" not in result
        assert "src.raven" not in result

    def test_substring_mid_word_replaced(self):
        text = "prefix_src.herald_suffix"
        result = scrub_text(text)
        assert "src.herald" not in result
        assert REDACTION_MARKER in result

    def test_case_sensitive(self):
        assert scrub_text("SRC.HERALD") == "SRC.HERALD"

    def test_idempotent(self):
        text = "src.herald and src.raven"
        once = scrub_text(text)
        twice = scrub_text(once)
        assert once == twice

    def test_realistic_stacktrace_fragment(self):
        trace = (
            'File "/app/src/herald/api.py", line 42, in set_config\n'
            "    return src.herald.policy.check_model(model)"
        )
        result = scrub_text(trace)
        assert "src.herald" not in result


class TestScrubMapping:
    def test_plain_string_leaf(self):
        assert scrub_mapping("src.herald.quota") == f"{REDACTION_MARKER}.quota"

    def test_int_leaf(self):
        assert scrub_mapping(42) == 42

    def test_none_leaf(self):
        assert scrub_mapping(None) is None

    def test_bool_leaf(self):
        assert scrub_mapping(True) is True

    def test_float_leaf(self):
        assert scrub_mapping(3.14) == 3.14

    def test_simple_dict(self):
        obj = {"module": "src.herald.policy", "count": 5}
        result = scrub_mapping(obj)
        assert result == {"module": f"{REDACTION_MARKER}.policy", "count": 5}

    def test_nested_dict(self):
        obj = {
            "error": {
                "code": "internal",
                "message": "traceback in src.herald.quota",
            },
            "retryable": False,
        }
        result = scrub_mapping(obj)
        assert result["error"]["message"] == f"traceback in {REDACTION_MARKER}.quota"
        assert result["error"]["code"] == "internal"
        assert result["retryable"] is False

    def test_list_of_strings(self):
        obj = ["clean", "src.herald", "src.raven"]
        result = scrub_mapping(obj)
        assert result == ["clean", REDACTION_MARKER, REDACTION_MARKER]

    def test_list_of_dicts(self):
        obj = [{"path": "src.herald.api"}, {"path": "clean"}]
        result = scrub_mapping(obj)
        assert result == [{"path": f"{REDACTION_MARKER}.api"}, {"path": "clean"}]

    def test_dict_with_forbidden_key(self):
        obj = {"src.herald.field": "value"}
        result = scrub_mapping(obj)
        assert f"{REDACTION_MARKER}.field" in result
        assert "src.herald.field" not in result
        assert result[f"{REDACTION_MARKER}.field"] == "value"

    def test_tuple_leaf(self):
        obj = ("clean", "src.herald")
        result = scrub_mapping(obj)
        assert result == ("clean", REDACTION_MARKER)
        assert isinstance(result, tuple)

    def test_deeply_nested(self):
        obj = {
            "a": {
                "b": {
                    "c": ["src.herald.x", {"d": "src.raven.y"}],
                }
            }
        }
        result = scrub_mapping(obj)
        assert result["a"]["b"]["c"][0] == f"{REDACTION_MARKER}.x"
        assert result["a"]["b"]["c"][1]["d"] == f"{REDACTION_MARKER}.y"

    def test_unknown_type_passthrough(self):
        class Opaque:
            pass

        obj = Opaque()
        assert scrub_mapping(obj) is obj

    def test_empty_dict(self):
        assert scrub_mapping({}) == {}

    def test_empty_list(self):
        assert scrub_mapping([]) == []


class TestContainsForbidden:
    def test_clean_text_false(self):
        assert contains_forbidden("nothing to hide") is False

    def test_empty_string_false(self):
        assert contains_forbidden("") is False

    def test_src_herald_true(self):
        assert contains_forbidden("src.herald.quota") is True

    def test_src_raven_true(self):
        assert contains_forbidden("src.raven.events") is True

    def test_case_sensitive(self):
        assert contains_forbidden("SRC.HERALD") is False


class TestForbiddenConstants:
    def test_forbidden_substrings_non_empty(self):
        assert len(FORBIDDEN_SUBSTRINGS) > 0

    def test_forbidden_substrings_are_strings(self):
        assert all(isinstance(s, str) for s in FORBIDDEN_SUBSTRINGS)

    def test_both_modules_covered(self):
        assert "src.herald" in FORBIDDEN_SUBSTRINGS
        assert "src.raven" in FORBIDDEN_SUBSTRINGS

    def test_redaction_marker_not_forbidden(self):
        assert not contains_forbidden(REDACTION_MARKER)
