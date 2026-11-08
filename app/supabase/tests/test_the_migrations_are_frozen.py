"""`15`.7: `supabase/migrations/` is frozen, not deleted.

The question was whether to delete the dormant Postgres migrations or keep
extending them in lockstep with `sqlite_schema.sql`. Extending them costs
three edits per schema change and is covered by tests that skip everywhere
(`test_supabase_migrations_apply_live.py` needs a live Postgres, which no
developer or CI box has). Deleting them loses the Postgres path entirely.

The answer of 2026-09-04 is neither: **freeze**. The directory stays, so the
Postgres path survives and can be revived; nothing new is added to it, so
the three-edits tax stops. `sqlite_schema.sql` plus `TraceRecord` are the
single source of truth, and `test_schema_gen.py` already enforces that pair.

This test is the freeze. It records the count at the moment of the decision.
A new migration makes it fail, and the failure says what to do.
"""
from __future__ import annotations

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

# The count at the freeze, 2026-09-04.
FROZEN_AT = 22


def test_no_new_migration_has_been_added_since_the_freeze():
    found = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert len(found) == FROZEN_AT, (
        f"{len(found)} migrations, frozen at {FROZEN_AT}. If you changed the "
        "schema, change `supabase/sqlite_schema.sql` and the dataclass in "
        "`broker/trace.py` - `test_schema_gen.py` keeps those two honest and "
        "is the only pair that runs. If the Postgres path is genuinely being "
        "revived, raise FROZEN_AT deliberately and say why in the commit."
    )


def test_the_directory_still_exists():
    """Frozen means kept. Deleting it would lose the Postgres path, which is
    a bigger decision than a schema edit and is not this test's to make."""
    assert MIGRATIONS.is_dir()
    assert any(MIGRATIONS.glob("*.sql"))
