"""SQLite async connection pool with sqlite-vec and FTS5 support.

Single-connection wrapper for per-user SQLite databases. Each engine
container has one user, one process, one SQLite file — no connection
pooling needed. WAL mode enables concurrent reads with single writer.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import aiosqlite


def serialize_f32(vec: list[float]) -> bytes:
    """Serialize a float list to compact binary for sqlite-vec.

    sqlite-vec expects embeddings as little-endian float32 blobs.
    """
    return struct.pack(f"<{len(vec)}f", *vec)


def deserialize_f32(blob: bytes, dim: int = 1536) -> list[float]:
    """Deserialize a sqlite-vec float32 blob back to a Python list."""
    return list(struct.unpack(f"<{dim}f", blob))


class SqlitePool:
    """Async SQLite connection for per-user memory database.

    Not actually a pool — SQLite doesn't need one. Single connection
    with WAL mode handles the engine's single-writer workload.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database, enable WAL mode, load sqlite-vec extension.

        Any failure after the connection opens closes it before re-raising.
        aiosqlite starts a worker thread inside ``connect``, so an exception
        from a PRAGMA would otherwise leave that thread alive with no handle to
        close it — it then outlives the event loop and raises "Event loop is
        closed" from whatever unrelated code is running when it is collected.
        """
        conn = await aiosqlite.connect(self.db_path)
        try:
            conn.row_factory = aiosqlite.Row

            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA busy_timeout=5000")

            try:
                import sqlite_vec

                await conn.enable_load_extension(True)
                await conn.load_extension(sqlite_vec.loadable_path())
                await conn.enable_load_extension(False)
                self.vec_available = True
            except (ImportError, Exception):
                self.vec_available = False
        except BaseException:
            await conn.close()
            raise

        self.conn = conn

    async def execute(self, sql: str, params: tuple | list = ()) -> aiosqlite.Cursor:
        """Execute a SQL statement. Returns cursor for rowcount/lastrowid access."""
        return await self.db.execute(sql, params)

    async def executemany(self, sql: str, params_seq: list[tuple]) -> aiosqlite.Cursor:
        """Execute a SQL statement against multiple parameter sets."""
        return await self.db.executemany(sql, params_seq)

    async def executescript(self, sql: str) -> None:
        """Execute multiple SQL statements (for migrations)."""
        await self.db.executescript(sql)

    async def fetch(self, sql: str, params: tuple | list = ()) -> list[aiosqlite.Row]:
        """Fetch all rows."""
        cursor = await self.db.execute(sql, params)
        return list(await cursor.fetchall())

    async def fetchrow(self, sql: str, params: tuple | list = ()) -> aiosqlite.Row | None:
        """Fetch one row."""
        cursor = await self.db.execute(sql, params)
        return await cursor.fetchone()

    async def fetchval(self, sql: str, params: tuple | list = ()) -> Any:
        """Fetch a single scalar value."""
        cursor = await self.db.execute(sql, params)
        row = await cursor.fetchone()
        return row[0] if row else None

    async def commit(self) -> None:
        """Commit the current transaction."""
        await self.db.commit()

    async def health_check(self) -> bool:
        """Return True if the database is accessible."""
        val = await self.fetchval("SELECT 1")
        return val == 1

    async def close(self) -> None:
        """Close the database connection."""
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    @property
    def db(self) -> aiosqlite.Connection:
        """Return the connection, raising if not connected."""
        if self.conn is None:
            raise RuntimeError("SqlitePool is not connected — call connect() first")
        return self.conn

    async def __aenter__(self) -> SqlitePool:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
