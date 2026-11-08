"""§4.3 — the store caps what it writes; the metric must not report the cap.

Prompt size was reported as "median 62 KB, max 71 KB, bounded" from
`length(request_body)`. The trace store caps bodies at 64 KB and keeps the
real figure in `_original_bytes`. Read correctly the same data was median
65 KB, p90 178 KB, worst 357 KB.

The measurement was of the cap. The cap does not move, which is exactly
why the number looked so reassuringly stable.
"""

from __future__ import annotations

import json
import sqlite3


from world.preflight import _measured_kb_per_call

CAP = 65536


def _store(tmp_path, rows):
    db = tmp_path / "trace.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE llm_calls (request_body TEXT, success INTEGER)")
    conn.executemany("INSERT INTO llm_calls (request_body, success) VALUES (?, 1)",
                     [(json.dumps(r),) for r in rows])
    conn.commit()
    conn.close()
    return str(db)


def _capped(original_bytes: int) -> dict:
    """A row exactly as broker/trace.py writes one when it truncates."""
    return {
        "_truncated": True,
        "_original_bytes": original_bytes,
        "_limit_bytes": CAP,
        "_why": "trace body cap",
        "_head": "x" * 128,
    }


def test_a_capped_row_reports_its_true_size(tmp_path) -> None:
    """One 357 KB body, stored capped. Naive length() would say ~64 KB."""
    path = _store(tmp_path, [_capped(365_233)])
    measured = _measured_kb_per_call(path)
    assert measured > 300, (
        f"reported {measured:.0f} KB for a 357 KB body - this is reading the cap"
    )


def test_a_mixed_store_is_not_dragged_down_by_the_cap(tmp_path) -> None:
    """Real stores hold both. Averaging capped rows at their stored length
    understates every large call, which is the direction that hides trouble.
    """
    small = {"messages": ["x" * 2000]}
    path = _store(tmp_path, [small, small, _capped(300_000)])
    measured = _measured_kb_per_call(path)
    naive_ceiling = CAP / 1024
    assert measured > naive_ceiling, (
        f"{measured:.0f} KB is at or below the cap - the large call vanished"
    )


def test_an_uncapped_store_still_measures_normally(tmp_path) -> None:
    body = {"messages": ["x" * 10_000]}
    path = _store(tmp_path, [body, body])
    measured = _measured_kb_per_call(path)
    assert 5 < measured < 40


def test_an_empty_store_does_not_invent_a_number(tmp_path) -> None:
    path = _store(tmp_path, [])
    assert _measured_kb_per_call(path) == 0


def test_a_missing_store_does_not_crash_the_preflight(tmp_path) -> None:
    assert _measured_kb_per_call(str(tmp_path / "nope.db")) == 0


def test_a_failed_call_is_not_averaged_in(tmp_path) -> None:
    """A prompt refused for being too large is already a failure. Averaging
    it here once produced a combined 991 KB that blocked a run whose working
    prompts were a healthy 30 KB."""
    db = tmp_path / "mixed.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE llm_calls (request_body TEXT, success INTEGER)")
    conn.execute("INSERT INTO llm_calls VALUES (?, 1)",
                 (json.dumps({"messages": ["x" * 8000]}),))
    conn.execute("INSERT INTO llm_calls VALUES (?, 0)",
                 (json.dumps(_capped(2_000_000)),))
    conn.commit()
    conn.close()
    assert _measured_kb_per_call(str(db)) < 100
