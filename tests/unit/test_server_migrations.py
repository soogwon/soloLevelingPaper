from contextlib import nullcontext
from hashlib import sha256

import pytest

from solo_leveling.interfaces.cli.server_migrations import (
    Migration, MigrationError, discover_migrations, migrate,
)


class Result:
    def __init__(self, rows=()):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, applied=()):
        self.applied = applied
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("SELECT to_regclass"):
            return Result([(bool(self.applied),)])
        if sql.startswith("SELECT version"):
            return Result(self.applied)
        return Result()


def migration(version, sql="SELECT 1"):
    name = f"{version}_change.sql"
    return Migration(version, name, sha256(sql.encode()).hexdigest(), sql)


def test_discovers_repository_server_migrations_in_strict_order():
    items = discover_migrations(__import__("pathlib").Path("migrations/server"))

    assert [item.version for item in items] == [f"{number:04d}" for number in range(1, 11)]
    assert items[-1].name == "0010_server_resume_requests.sql"


def test_rejects_gaps_and_invalid_sql_files(tmp_path):
    (tmp_path / "0001_first.sql").write_text("SELECT 1", encoding="utf-8")
    (tmp_path / "0003_third.sql").write_text("SELECT 3", encoding="utf-8")

    with pytest.raises(MigrationError, match="빠짐없이"):
        discover_migrations(tmp_path)

    (tmp_path / "0003_third.sql").unlink()
    (tmp_path / "notes.sql").write_text("SELECT 2", encoding="utf-8")
    with pytest.raises(MigrationError, match="파일명"):
        discover_migrations(tmp_path)


def test_applies_pending_migrations_under_lock_and_records_checksums():
    first, second = migration("0001"), migration("0002", "SELECT 2")
    conn = Connection([{"version": "0001", "name": first.name, "checksum": first.checksum}])

    result = migrate("postgresql://secret", (first, second), connector=lambda _: nullcontext(conn))

    assert result.applied == (second.name,)
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert (second.sql, ()) in conn.calls
    assert conn.calls[-1][1] == (second.version, second.name, second.checksum)


def test_check_reports_pending_without_executing_migration_sql():
    item = migration("0001")
    conn = Connection()

    result = migrate("postgresql://secret", (item,), check=True,
                     connector=lambda _: nullcontext(conn))

    assert result.pending == (item.name,)
    assert all(sql != item.sql and "CREATE TABLE" not in sql for sql, _ in conn.calls)


def test_accepts_psycopg_default_tuple_rows():
    item = migration("0001")
    conn = Connection([(item.version, item.name, item.checksum)])

    result = migrate("postgresql://secret", (item,), connector=lambda _: nullcontext(conn))

    assert result.applied == ()


def test_rejects_changed_or_unknown_applied_history():
    item = migration("0001")
    changed = Connection([{"version": "0001", "name": item.name, "checksum": "0" * 64}])
    with pytest.raises(MigrationError, match="변경"):
        migrate("postgresql://secret", (item,), connector=lambda _: nullcontext(changed))

    unknown = Connection([{"version": "9999", "name": "9999_unknown.sql", "checksum": "0" * 64}])
    with pytest.raises(MigrationError, match="코드에 없는"):
        migrate("postgresql://secret", (item,), connector=lambda _: nullcontext(unknown))


@pytest.mark.parametrize("check", [True, False])
def test_incomplete_history_blocks_check_and_apply(check):
    first, second = migration("0001"), migration("0002")
    conn = Connection([(second.version, second.name, second.checksum)])
    with pytest.raises(MigrationError, match="누락"):
        migrate("unused", (first, second), check=check, connector=lambda _: nullcontext(conn))
    assert (first.sql, ()) not in conn.calls
