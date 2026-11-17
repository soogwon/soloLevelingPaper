"""직접 생성한 로컬 DB만 pg_dump/pg_restore하는 opt-in 복구 리허설."""

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLO_BACKUP_DOCKER_CONTAINER"), reason="로컬 Docker 백업 리허설 opt-in 필요"
)


def test_backup_restore_preserves_data_acl_and_guards():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from solo_leveling.interfaces.cli.server_migrations import discover_migrations, migrate
    from solo_leveling.interfaces.http.papers import PostgresPaperStore, PaperMetadata
    from solo_leveling.interfaces.http.web_auth import PostgresWebSessionStore, WebIdentity
    from tests.fixtures.web_permissions import grant_web

    dsn = os.environ["SOLO_TEST_POSTGRES_DSN"]
    assert conninfo_to_dict(dsn).get("host") in ("localhost", "127.0.0.1")
    container = os.environ["SOLO_BACKUP_DOCKER_CONTAINER"]
    assert re.fullmatch(r"(?:slp517-[a-z0-9-]+|[a-f0-9]{64})", container)
    docker = os.environ.get("SOLO_TEST_DOCKER", "docker")
    suffix = uuid4().hex[:12]
    source = "backup_source_" + suffix
    restored = "backup_restore_" + suffix
    role = "backup_web_" + suffix
    archive = "/tmp/slp517-" + suffix + ".dump"
    databases = []
    role_created = False

    def command(*args):
        return subprocess.run(
            [docker, "exec", container, *args], check=True, capture_output=True, text=True, timeout=60
        ).stdout

    def snapshot(database):
        with psycopg.connect(make_conninfo(dsn, dbname=database)) as conn:
            tables = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            ).fetchall()
            rows = {}
            for (table,) in tables:
                rows[table] = conn.execute(
                    sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text").format(
                        sql.Identifier("public", table)
                    )
                ).fetchall()
            acl = conn.execute(
                "SELECT relname,relacl::text FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY relname"
            ).fetchall()
            triggers = conn.execute(
                "SELECT tgname,pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgrelid::regclass::text,tgname"
            ).fetchall()
            return rows, acl, triggers

    with psycopg.connect(dsn, autocommit=True) as control:
        # DSN과 docker exec가 다른 클러스터를 가리키면 생성 전에 중단한다.
        cluster = control.execute("SELECT system_identifier::text FROM pg_control_system()").fetchone()[0]
        assert (
            command("psql", "-U", "postgres", "-Atc", "SELECT system_identifier FROM pg_control_system()").strip()
            == cluster
        )
        try:
            for database in (source, restored):
                control.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                databases.append(database)
            target = make_conninfo(dsn, dbname=source)
            migrate(target, discover_migrations(Path("migrations/server")))
            control.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD 'local-backup-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                ).format(sql.Identifier(role))
            )
            role_created = True
            with psycopg.connect(target) as conn:
                grant_web(conn, "public", role)
            runtime = make_conninfo(target, user=role, password="local-backup-only")
            with ConnectionPool(runtime, kwargs=dict(row_factory=dict_row), open=True) as rp:
                PostgresWebSessionStore(rp).create_session(
                    WebIdentity("synthetic", "복구 검증", None), datetime.now(timezone.utc)
                )
                PostgresPaperStore(rp).register(
                    "synthetic",
                    str(uuid4()),
                    PaperMetadata(str(uuid4()), "sample", "복구 논문", ("합성",), 2026, datetime.now(timezone.utc)),
                )
            before = snapshot(source)
            command("sh", "-c", f"umask 077; pg_dump -U postgres -d {source} -Fc -f {archive}")
            assert "TABLE DATA public server_papers" in command("pg_restore", "--list", archive)
            command("pg_restore", "-U", "postgres", "--exit-on-error", "--single-transaction", "-d", restored, archive)
            assert snapshot(restored) == before
            with psycopg.connect(make_conninfo(runtime, dbname=restored), autocommit=True) as conn:
                assert conn.execute("SELECT count(*) FROM server_papers").fetchone()[0] == 1
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute("DELETE FROM server_schema_migrations")
                conn.execute(
                    "INSERT INTO server_processing_versions(owner_user_id,paper_id,server_version_id) SELECT owner_user_id,paper_id,'v1' FROM server_papers"
                )
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute("UPDATE server_processing_versions SET server_version_id=server_version_id")
            # 원본 합성 데이터 삭제 후에도 같은 백업으로 원래 DB를 복구한다.
            with psycopg.connect(target) as conn:
                conn.execute("DELETE FROM web_sessions")
                conn.execute("DELETE FROM server_users")
            command(
                "pg_restore",
                "-U",
                "postgres",
                "--clean",
                "--if-exists",
                "--exit-on-error",
                "--single-transaction",
                "-d",
                source,
                archive,
            )
            assert snapshot(source) == before
            assert not migrate(target, discover_migrations(Path("migrations/server")), check=True).pending
        finally:
            try:
                command("rm", "-f", archive)
            finally:
                for database in reversed(databases):
                    control.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
                if role_created:
                    control.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
