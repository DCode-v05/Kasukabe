"""G1 must count everyone a remark named, not just its primary listener.

`EVAL-11`. `say` records both `listener_agent_id` - one id - and
`addressed_agent_ids` - everyone the remark named. Run 23 filled the second
on **172 of its 229** utterances. `loop_report.reply_rate`, which computes
G1, read only the first.

Two consequences, and the second is the one that skews the gate:

  * a remark aimed at three people counted as ONE question owed by ONE
    person, so the town's conversational load was under-reported;
  * a reply from one of the OTHER named people did not close it, so G1
    counted an answered question as unanswered.

G1 is the headline number in every run report ("reply rate"), and this is
the same record-vs-reader gap as CORE-28 in the run report's threads.

`loop_report.py` sits at the app root with no package of its own, so it is
loaded by path here rather than imported.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

LOOP_REPORT = Path(__file__).resolve().parents[1] / "loop_report.py"


@pytest.fixture(scope="module")
def lr():
    spec = importlib.util.spec_from_file_location("loop_report_under_test", LOOP_REPORT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _remark(speaker, listener, addressed, tick):
    return {
        "speaker": speaker, "listener": listener, "addressed": list(addressed),
        "content": "…", "tick": tick, "location": "Kasukabe Bakery",
    }


def test_a_reply_from_a_named_addressee_closes_the_question(lr):
    """n01 asks n02 AND n03. n03 answers. That is an answered question."""
    utts = [
        _remark("n01", "n02", ["n02", "n03"], 10),
        _remark("n03", "n01", ["n01"], 11),
    ]
    answered, directed, _ = lr.reply_rate(utts)
    assert (answered, directed) == (1, 2), (
        "EVAL-11: a reply from someone the remark named, but who was not the "
        "primary listener, did not count as an answer"
    )


def test_silence_from_everyone_named_is_still_unanswered(lr):
    """The guard on the guard: widening who may answer must not make every
    question answer itself."""
    utts = [
        _remark("n01", "n02", ["n02", "n03"], 10),
        _remark("n04", "n05", ["n05"], 11),
    ]
    answered, directed, _ = lr.reply_rate(utts)
    assert answered == 0


def test_a_remark_with_only_a_listener_still_counts(lr):
    """Backwards compatibility: rows written before `addressed_agent_ids`
    existed carry an empty list and must behave exactly as before."""
    utts = [
        _remark("n01", "n02", [], 10),
        _remark("n02", "n01", [], 11),
    ]
    answered, directed, _ = lr.reply_rate(utts)
    assert (answered, directed) == (1, 2)


def test_an_undirected_remark_is_not_a_question(lr):
    utts = [_remark("n01", None, [], 10)]
    answered, directed, _ = lr.reply_rate(utts)
    assert (answered, directed) == (0, 0)


def test_addressees_decode_from_json_text(lr):
    """SQLite stores the column as JSON text; a report that assumed a list
    counted zero addressees on every row."""
    assert lr._addressees('["n02","n03"]') == ["n02", "n03"]
    assert lr._addressees(["n02"]) == ["n02"]
    assert lr._addressees(None) == []
    assert lr._addressees("not json") == []
