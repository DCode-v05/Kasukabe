"""Single entry point for getting a SupabaseClient.

world/ and broker/ (and this package's own tests) call get_client() and
never construct SqliteClient directly - that is what keeps "one SQLite
database, opened the same way everywhere" (config.py's module docstring)
a configuration decision instead of something scattered across call
sites. There was a second backend here before DESIGN.md's 2026-07-30
decision (PostgrestClient, talking to a real Supabase project); it is
gone, along with the driver-detection logic that used to choose between
them, but callers were never supposed to know which backend they had, so
nothing above this module changes.
"""

from __future__ import annotations

from supabase.client import SupabaseClient
from supabase.config import SupabaseConfig
from supabase.sqlite_client import SqliteClient


def get_client(config: SupabaseConfig | None = None) -> SupabaseClient:
    """Build the trace store client. Defaults to SupabaseConfig.from_env().

    A missing config falls through to from_env()'s own default: an
    in-memory SQLite database, which is what every test in this repo gets
    unless it sets KASUKABE_SQLITE_PATH.
    """
    resolved = config or SupabaseConfig.from_env()
    return SqliteClient(resolved)
