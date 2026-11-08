"""The behavioural eval must measure the town, not itself.

Every measurement this project has trusted and later found broken was
broken in one of three ways, and all three are pinned here:

  * a detector that could not see the thing it was pointed at - the
    settlement check that required an exact spoken-number match, so
    "45 per unit" for ten units scored zero against a 450 trade;
  * a query that answered 0 for a column it could not read - which cost a
    dormancy counter, six invisible trades, and every purchase in a
    ten-run series;
  * a threshold moved after seeing a result, which Build Spec v2 section
    10.4 forbids outright.

So these tests do two jobs. They check the arithmetic on data whose answer
is known by construction, and they check that the BARS still match
docs/HUMAN_BASELINES.md, which cites the research. A bar that drifts from
its source fails here rather than quietly becoming our opinion.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3

import pytest

from world.evals import behavioural as B

BASELINES = pathlib.Path(__file__).resolve().parents[2].parent / "docs" / "HUMAN_BASELINES.md"


# -- The bars are the researched ones, still ---------------------------


def test_the_answer_floor_is_the_slowest_human_language() -> None:
    """Stivers et al. measured 64% in Korean and 87% in Dutch. The floor
    is the FLOOR, not the mean: a town under 64% is outside the range
    measured across ten languages, which is a stronger claim than being
    under an average."""
    assert B.ANSWER_RATE_FLOOR == 0.64


def test_the_conversation_ceiling_is_four() -> None:
    assert B.CONVERSATION_SIZE_CEILING == 4


def test_every_bar_appears_in_the_baselines_document() -> None:
    """The document cites the sources; the code cites the document. If a
    bar can be changed here without the doc disagreeing, the citation is
    decoration."""
    text = BASELINES.read_text(encoding="utf-8")
    assert "64%" in text and "Stivers" in text
    assert "3.4" in text and "Dunbar" in text
    assert str(B.CONVERSATION_SIZE_CEILING) in text


def test_the_ceiling_contradicts_our_own_gate_and_says_so() -> None:
    """G3 rewards >=3 speakers with NO ceiling, so it scores a six- or
    eight-speaker thread as a success where humans would already have
    split. That contradiction is the finding; it must stay written down."""
    text = BASELINES.read_text(encoding="utf-8")
    assert "G3" in text
    assert "fission" in text.lower()


# -- The arithmetic --------------------------------------------------


def spoken(tick, speaker, listener, content="...", conversation="c1", location="market"):
    return {
        "speaker": speaker, "listener": listener, "conversation": conversation,
        "location": location, "content": content, "tick": tick,
    }


def test_only_remarks_that_select_somebody_are_counted() -> None:
    """"Morning, all" selects nobody and owes nobody an answer. Counting
    it would drag the rate down for behaviour that is perfectly human -
    SSJ: the selected party is obliged, "and no others have such rights
    or obligations"."""
    utts = [spoken(1, "a", None, "Morning, all."), spoken(2, "a", "b", "Bram. Grain?")]
    assert len(B.selections(utts)) == 1


def test_an_answer_must_go_back_to_the_asker() -> None:
    """b was asked and spoke to c instead. Both remarks select somebody,
    so the denominator is two - answering by turning to a third person is
    still a turn that puts an obligation on that third person."""
    utts = [spoken(1, "a", "b", "Bram. Grain?"), spoken(2, "b", "c", "Nell, later.")]
    answered, selected, _ = B.answer_rate(utts)
    assert (answered, selected) == (0, 2)


def test_answering_the_asker_counts() -> None:
    """Two selections, one of them answered.

    The first draft of this test expected a denominator of one, and was
    wrong about how conversation works: b's answer NAMES a, which selects
    a in turn. Every directed remark is both an answer to the last one
    and a claim on the next, which is what makes an exchange an exchange.
    a never came back, so b's turn is the outstanding one.
    """
    utts = [spoken(1, "a", "b", "Bram. Grain?"), spoken(2, "b", "a", "None left.")]
    answered, selected, unanswered = B.answer_rate(utts)
    assert (answered, selected) == (1, 2)
    assert unanswered[0]["speaker"] == "b"


def test_an_answer_before_the_question_is_not_an_answer() -> None:
    utts = [spoken(5, "b", "a", "None left."), spoken(6, "a", "b", "Bram. Grain?")]
    answered, selected, _ = B.answer_rate(utts)
    assert (answered, selected) == (1, 2), (
        "a's later remark answers b's earlier one, which is correct; what "
        "must not happen is b's remark counting as an answer to a question "
        "asked after it"
    )


def test_conversation_size_counts_distinct_speakers_not_remarks() -> None:
    """One person saying four things is not a four-person conversation.
    The whole Dunbar result is about how many MINDS are in it."""
    utts = [spoken(t, "a", None, conversation="c1") for t in range(4)]
    assert B.sizes(utts) == [1]


def test_an_over_large_thread_with_no_second_conversation_has_not_split() -> None:
    utts = [spoken(t, f"n{t}", None, conversation="c1") for t in range(6)]
    split, big = B.fissioned(utts)
    assert (split, big) == (0, 1)


def test_an_over_large_thread_beside_a_second_one_has_split() -> None:
    utts = [spoken(t, f"n{t}", None, conversation="c1") for t in range(6)]
    utts.append(spoken(3, "n9", None, conversation="c2"))
    split, big = B.fissioned(utts)
    assert (split, big) == (1, 1)


def test_a_conversation_of_four_is_not_over_large() -> None:
    """Four is the ceiling, not the first offence. The observed human mean
    is 3.4 and four-person conversations are ordinary."""
    utts = [spoken(t, f"n{t}", None, conversation="c1") for t in range(4)]
    assert B.fissioned(utts) == (0, 0)


# -- The eval cannot answer 0 for what it cannot see -------------------


def store(tmp_path, tables: dict[str, str]) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    for ddl in tables.values():
        conn.execute(ddl)
    conn.commit()
    return conn


def test_a_missing_table_answers_nothing_to_count(tmp_path) -> None:
    """A store with no `businesses` table is an older store, and "there
    are none" is the honest answer."""
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    assert B.rows(conn, "select 1 from a_table_that_does_not_exist") == []


def test_a_missing_column_refuses_to_grade(tmp_path) -> None:
    """The distinction that matters. A missing table means "nothing to
    count"; a missing column means the store could have answered and we
    would have reported 0 - which is how six real trades vanished."""
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute("create table transactions (id integer, tx_type text)")
    conn.commit()
    with pytest.raises(SystemExit) as caught:
        B.rows(conn, "select counterparty_id from transactions")
    assert "predates the current schema" in str(caught.value)


def test_a_run_that_never_recorded_addressees_says_so(tmp_path) -> None:
    """-1, not 0. A run older than the addressee fix has no evidence
    either way, and reporting 0 would read as a pass for a defect that
    was live at the time."""
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute("create table events (type text, payload text)")
    conn.execute(
        "insert into events values ('agent.utterance.spoken', "
        "'{\"speaker_agent_id\": \"a\", \"listener_agent_id\": \"b\"}')"
    )
    conn.commit()
    assert B.unrecorded_addressees(conn) == -1


def test_an_addressee_left_out_of_involved_is_counted(tmp_path) -> None:
    """The exact defect: the world knew Tova was addressed and did not
    make her involved, so she stopped hearing the remark the moment she
    left the room."""
    conn = sqlite3.connect(str(tmp_path / "new.db"))
    conn.execute("create table events (type text, payload text)")
    conn.execute(
        "insert into events values ('agent.utterance.spoken', "
        '\'{"addressed_agent_ids": ["b", "c"], "involved_agent_ids": ["a", "b"]}\')'
    )
    conn.commit()
    assert B.unrecorded_addressees(conn) == 1


def test_a_correctly_recorded_remark_is_not_counted(tmp_path) -> None:
    conn = sqlite3.connect(str(tmp_path / "new.db"))
    conn.execute("create table events (type text, payload text)")
    conn.execute(
        "insert into events values ('agent.utterance.spoken', "
        '\'{"addressed_agent_ids": ["b", "c"], "involved_agent_ids": ["a", "b", "c"]}\')'
    )
    conn.commit()
    assert B.unrecorded_addressees(conn) == 0


# -- Reciprocity is reported, and must stay reported -------------------


def test_the_favour_measure_has_no_bar() -> None:
    """Gouldner's whole point is that reciprocity is DELAYED. A 72-tick
    run is one morning, so a bar on the return rate would measure the run
    length rather than the town - and would push toward instant
    tit-for-tat, which is the opposite of what the literature describes.
    """
    source = pathlib.Path(B.__file__).read_text(encoding="utf-8")
    assert "FAVOUR_RETURN_FLOOR" not in source
    assert "DEBT" not in source.replace("DEBT_WORDS", "")


def test_a_returned_favour_is_seen(tmp_path) -> None:
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute(
        "create table transactions (from_agent_id text, to_agent_id text, "
        "tick integer, tx_type text)"
    )
    conn.executemany(
        "insert into transactions values (?,?,?,'transfer')",
        [("a", "b", 1), ("b", "a", 9)],
    )
    conn.commit()
    returned, given = B.favours(conn)
    assert (returned, given) == (1, 2), (
        "a gave to b and b gave back: ONE of the two gifts has been "
        "returned. b's own gift is still outstanding - nobody has given "
        "back to b - and counting it as returned would make every pair of "
        "mutual gifts score 100% forever"
    )


def test_a_one_sided_gift_is_not_a_return(tmp_path) -> None:
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute(
        "create table transactions (from_agent_id text, to_agent_id text, "
        "tick integer, tx_type text)"
    )
    conn.execute("insert into transactions values ('a','b',1,'transfer')")
    conn.commit()
    assert B.favours(conn) == (0, 1)


def test_speech_about_owing_is_counted() -> None:
    """The hidden 400 sim is the best material the town has produced. It
    is worth a number even though it is worth no bar."""
    utts = [
        spoken(1, "a", "b", "I've set aside 400 sim for what he owes."),
        spoken(2, "a", "b", "Cold this morning."),
    ]
    assert B.debts_spoken(utts) == 1


# -- No orders, anywhere in the eval ----------------------------------


def test_the_eval_never_tells_the_town_what_to_do() -> None:
    """An eval is a measurement. The moment a bar becomes an instruction
    in a prompt, it measures the prompt - the trap SERIES.md section 4
    names and the reason the open-questions line was cut rather than
    rewritten as an order."""
    source = pathlib.Path(B.__file__).read_text(encoding="utf-8").lower()
    for order in ("you must", "you should", "make sure you", "remember to"):
        assert order not in source


# -- The live judge: its pairing, and its honesty ---------------------
#
# The judge itself needs a real model and real money, so what is pinned
# here is everything AROUND the model call: which exchanges it picks, that
# a broker failure cannot take the run down, and that it never presents
# itself as a gate. One model grading another's conversation is evidence,
# not a verdict, and a tool that forgot that would be the worst kind of
# measurement - confident and unfalsifiable.


def test_the_judge_pairs_a_question_with_the_reply_to_the_asker() -> None:
    from world.evals.answered_or_deflected import exchanges

    utts = [
        spoken(1, "a", "b", "Bram. Have you grain?"),
        spoken(2, "b", "c", "Nell, later."),
        spoken(3, "b", "a", "None left."),
    ]
    pairs = exchanges(utts)
    first = next(p for p in pairs if p[0]["tick"] == 1)
    assert first[1] is not None and first[1]["content"] == "None left.", (
        "the reply to a third person was taken as the answer"
    )


def test_a_reply_after_the_window_is_silence() -> None:
    """The scheduler wakes each resident every three ticks, so an answer
    lands 3-6 ticks later. Twelve is the world's own conversation window;
    past it, the exchange is over and a later remark is a new one."""
    from world.evals.answered_or_deflected import REPLY_WINDOW_TICKS, exchanges

    utts = [
        spoken(1, "a", "b", "Bram. Have you grain?"),
        spoken(1 + REPLY_WINDOW_TICKS + 1, "b", "a", "None left."),
    ]
    assert exchanges(utts)[0][1] is None


def test_never_answering_is_silence_not_a_deflection() -> None:
    """Three outcomes, and the difference matters: somebody who never
    spoke did not deflect, and lumping them together would hide which of
    the two the town is actually doing."""
    from world.evals.answered_or_deflected import exchanges

    assert exchanges([spoken(1, "a", "b", "Bram. Grain?")])[0][1] is None


def test_a_broker_failure_does_not_take_the_run_down() -> None:
    """A judging run that dies halfway leaves no distribution at all. A
    partial one with its failures counted is worth more than nothing."""

    old = os.environ.get("BROKER_BASE_URL")
    os.environ["BROKER_BASE_URL"] = "http://127.0.0.1:1"  # nothing listens
    try:
        import importlib

        from world.evals import answered_or_deflected

        importlib.reload(answered_or_deflected)
        assert answered_or_deflected.ask("q", "r") == "error"
    finally:
        if old is None:
            os.environ.pop("BROKER_BASE_URL", None)
        else:
            os.environ["BROKER_BASE_URL"] = old


def test_the_judge_bills_itself_and_not_a_resident() -> None:
    """Its own wallet and its own purpose tag. Untagged it would land in
    the cognition lane and be billed as a resident's own turn - the RC-5
    defect, reintroduced by the tool built to measure it."""
    source = pathlib.Path(
        pathlib.Path(B.__file__).parent / "answered_or_deflected.py"
    ).read_text(encoding="utf-8")
    assert "X-Kasukabe-Purpose" in source
    assert "eval.judge" in source
    assert "JUDGE_AGENT_ID" in source


def test_the_judge_does_not_present_itself_as_a_gate() -> None:
    """It prints a distribution and the pairs it judged, so a human can
    read the disagreements. A model scoring a model must never print
    PASS."""
    source = pathlib.Path(
        pathlib.Path(B.__file__).parent / "answered_or_deflected.py"
    ).read_text(encoding="utf-8")
    assert "PASS" not in source and "FAIL" not in source
    assert "NOT a gate" in source


def test_a_refusal_counts_as_an_answer_in_the_rubric() -> None:
    """Conversation analysis calls "no" a dispreferred response, not a
    non-response. A rubric that scored refusals as deflection would mark
    the town down for behaving like people."""
    from world.evals.answered_or_deflected import RUBRIC

    assert "refusal" in RUBRIC.lower()
    assert "dispreferred" in RUBRIC.lower()
