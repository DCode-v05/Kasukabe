"""SQL migration runner — applies .sql files in order (SQLite)."""

from __future__ import annotations

from pathlib import Path

from src.infra.sqlite_pool import SqlitePool


class MigrationRunner:
    """Applies SQL migration files from a directory, tracking applied state."""

    def __init__(self, db: SqlitePool, migrations_dir: Path) -> None:
        self.db = db
        self.migrations_dir = migrations_dir

    async def ensure_table(self) -> None:
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await self.db.commit()

    async def get_applied(self) -> list[str]:
        rows = await self.db.fetch("SELECT filename FROM schema_migrations ORDER BY filename")
        return [row["filename"] for row in rows]

    async def run(self) -> list[str]:
        """Apply unapplied migrations in filename order. Returns newly applied filenames."""
        await self.ensure_table()
        applied = set(await self.get_applied())

        sql_files = sorted(self.migrations_dir.glob("*.sql"))

        newly_applied: list[str] = []
        for sql_file in sql_files:
            if sql_file.name in applied:
                continue
            sql = sql_file.read_text(encoding="utf-8")
            await self.db.executescript(sql)
            await self.db.execute(
                "INSERT INTO schema_migrations (filename) VALUES (?) ON CONFLICT DO NOTHING",
                (sql_file.name,),
            )
            await self.db.commit()
            newly_applied.append(sql_file.name)

        return newly_applied


async def run_migrations(db: SqlitePool, migrations_dir: str | None = None) -> list[str]:
    """Run all pending migrations from the given (or package default) migrations directory."""
    if migrations_dir:
        path = Path(migrations_dir)
    else:
        from src.package import PACKAGE_ROOT

        path = PACKAGE_ROOT / "migrations"
    runner = MigrationRunner(db, path)
    return await runner.run()
