"""PostgreSQL 서버 스키마 마이그레이션을 순서대로 검증하고 적용한다."""

from argparse import ArgumentParser
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Callable, Sequence

from solo_leveling.interfaces.http.settings import load_database_settings


_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_LOCK_ID = 7_614_083_307_671_001


class MigrationError(RuntimeError):
    """마이그레이션 파일 또는 적용 이력이 안전하지 않다."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    checksum: str
    sql: str


@dataclass(frozen=True)
class MigrationResult:
    applied: tuple[str, ...]
    pending: tuple[str, ...]


def discover_migrations(directory: Path) -> tuple[Migration, ...]:
    if not directory.is_dir():
        raise MigrationError(f"마이그레이션 디렉터리를 찾을 수 없습니다: {directory}")
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"마이그레이션 파일명이 올바르지 않습니다: {path.name}")
        raw = path.read_bytes()
        try:
            sql = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            raise MigrationError(f"UTF-8 SQL 파일이 아닙니다: {path.name}") from None
        if not sql.strip():
            raise MigrationError(f"비어 있는 마이그레이션입니다: {path.name}")
        migrations.append(Migration(match.group(1), path.name, sha256(sql.encode("utf-8")).hexdigest(), sql))
    if not migrations:
        raise MigrationError("적용할 서버 마이그레이션이 없습니다.")
    versions = [item.version for item in migrations]
    if len(set(versions)) != len(versions):
        raise MigrationError("서버 마이그레이션 버전이 중복되었습니다.")
    expected = [f"{value:04d}" for value in range(1, len(versions) + 1)]
    if versions != expected:
        raise MigrationError("서버 마이그레이션은 0001부터 빠짐없이 순서대로 있어야 합니다.")
    return tuple(migrations)


def migrate(dsn: str, migrations: Sequence[Migration], *, check: bool = False,
            connector: Callable[[str], Any] | None = None) -> MigrationResult:
    if connector is None:
        from psycopg import connect
        connector = connect
    with connector(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_ID,))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS server_schema_migrations (
                   version varchar(4) PRIMARY KEY,
                   name text NOT NULL UNIQUE,
                   checksum varchar(64) NOT NULL,
                   applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        rows = conn.execute(
            "SELECT version, name, checksum FROM server_schema_migrations ORDER BY version"
        ).fetchall()
        known = {}
        for row in rows:
            values = (dict(row) if hasattr(row, "keys") else
                      {"version": row[0], "name": row[1], "checksum": row[2]})
            known[str(values["version"])] = values
        available = {item.version: item for item in migrations}
        unknown = sorted(set(known) - set(available))
        if unknown:
            raise MigrationError(f"코드에 없는 적용 이력이 있습니다: {', '.join(unknown)}")
        for version, row in known.items():
            item = available[version]
            if row["name"] != item.name or row["checksum"] != item.checksum:
                raise MigrationError(f"이미 적용된 마이그레이션이 변경되었습니다: {item.name}")
        pending = tuple(item for item in migrations if item.version not in known)
        if check:
            return MigrationResult((), tuple(item.name for item in pending))
        applied = []
        for item in pending:
            conn.execute(item.sql)
            conn.execute(
                "INSERT INTO server_schema_migrations(version, name, checksum) VALUES (%s, %s, %s)",
                (item.version, item.name, item.checksum),
            )
            applied.append(item.name)
        return MigrationResult(tuple(applied), ())


def _default_directory() -> Path:
    return Path(__file__).resolve().parents[4] / "migrations" / "server"


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(description="Solo Leveling Paper 서버 DB 마이그레이션")
    parser.add_argument("--check", action="store_true", help="미적용 파일만 확인하고 DB를 변경하지 않습니다.")
    parser.add_argument("--migrations-dir", type=Path, default=_default_directory())
    args = parser.parse_args(argv)
    settings = load_database_settings()
    if settings is None:
        parser.error("DATABASE_URL이 필요합니다.")
    try:
        result = migrate(settings.dsn, discover_migrations(args.migrations_dir), check=args.check)
    except MigrationError as exc:
        parser.exit(1, f"migration error: {exc}\n")
    if args.check:
        if result.pending:
            print("pending: " + ", ".join(result.pending))
            return 2
        print("server schema is current")
        return 0
    print("applied: " + (", ".join(result.applied) if result.applied else "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
