"""기존 extension을 보존하는 비슈퍼유저 복구와 실패 rollback을 검증한다."""

import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLO_BACKUP_DOCKER_CONTAINER"), reason="로컬 복구 리허설 opt-in 필요"
)


def test_nonsuper_restore_preserves_vector_and_rolls_back_failure():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from solo_leveling.interfaces.cli.server_migrations import discover_migrations, migrate

    dsn = os.environ["SOLO_TEST_POSTGRES_DSN"]
    assert conninfo_to_dict(dsn).get("host") in ("localhost", "127.0.0.1")
    container = os.environ["SOLO_BACKUP_DOCKER_CONTAINER"]
    assert re.fullmatch(r"(?:slp517-[a-z0-9-]+|[a-f0-9]{64})", container)
    docker = os.environ.get("SOLO_TEST_DOCKER", "docker")
    uid = uuid4().hex[:12]
    database = "nonsuper_db_" + uid
    admin = "nonsuper_admin_" + uid
    owner = "nonsuper_owner_" + uid
    prefix = "/tmp/slp517-" + uid
    created_roles = []
    created_db = False

    def command(*args, input=None, check=True):
        return subprocess.run(
            [docker, "exec", "-i", container, *args],
            input=input,
            text=True,
            capture_output=True,
            check=check,
            timeout=60,
        )

    def put(suffix, text):
        command("sh", "-c", f"umask 077; cat > {prefix + suffix}", input=text)

    def snapshot(conn):
        rows = {}
        for (name,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"):
            rows[name] = conn.execute(
                sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text").format(
                    sql.Identifier("public", name)
                )
            ).fetchall()
        return rows

    with psycopg.connect(dsn, autocommit=True) as control:
        cluster = control.execute("SELECT system_identifier::text FROM pg_control_system()").fetchone()[0]
        assert (
            command(
                "psql", "-U", "postgres", "-Atc", "SELECT system_identifier FROM pg_control_system()"
            ).stdout.strip()
            == cluster
        )
        try:
            # Render의 기존 extension 소유 상태만 구성한다. 이후 작업 전 superuser를 회수한다.
            control.execute(
                sql.SQL("CREATE ROLE {} LOGIN SUPERUSER PASSWORD 'local-nonsuper-only'").format(sql.Identifier(admin))
            )
            created_roles.append(admin)
            control.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(admin))
            )
            created_db = True
            target = make_conninfo(dsn, dbname=database, user=admin, password="local-nonsuper-only")
            with psycopg.connect(target) as conn:
                conn.execute("CREATE EXTENSION vector")
            control.execute(
                sql.SQL("ALTER ROLE {} NOSUPERUSER CREATEDB CREATEROLE NOBYPASSRLS").format(sql.Identifier(admin))
            )
            migrations = discover_migrations(Path("migrations/server"))
            migrate(target, migrations)
            with psycopg.connect(target) as conn:
                assert conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone() == (False,)
                conn.execute("INSERT INTO server_users(owner_user_id,display_name) VALUES ('synthetic','복구 대상')")
                before = snapshot(conn)
                vector_oid = conn.execute("SELECT oid FROM pg_extension WHERE extname='vector'").fetchone()[0]
            command("sh", "-c", f"umask 077; pg_dump -U {admin} -d {database} -Fc -f {prefix}.dump")
            command(
                "pg_restore",
                "--schema=public",
                "--no-owner",
                "--no-acl",
                "--file=" + prefix + ".app.sql",
                prefix + ".dump",
            )
            app_sql = command("cat", prefix + ".app.sql").stdout
            assert not re.search(r"\b(?:CREATE|DROP|ALTER|COMMENT ON)\s+EXTENSION\b", app_sql, re.I)
            with psycopg.connect(target) as conn:
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(owner)))
                created_roles.append(owner)
                conn.execute(
                    sql.SQL("GRANT {} TO {} WITH SET TRUE").format(sql.Identifier(owner), sql.Identifier(admin))
                )
                conn.execute(sql.SQL("CREATE SCHEMA slp_staging AUTHORIZATION {}").format(sql.Identifier(owner)))
                tables = list(before)
                conn.execute(
                    sql.SQL("DROP TABLE {} RESTRICT").format(
                        sql.SQL(",").join(sql.Identifier("public", t) for t in tables)
                    )
                )
                for (signature,) in conn.execute(
                    "SELECT p.oid::regprocedure::text FROM pg_proc p WHERE p.pronamespace='public'::regnamespace AND p.proname LIKE 'server_%'"
                ).fetchall():
                    conn.execute(sql.SQL("DROP FUNCTION {} RESTRICT").format(sql.SQL(signature)))
                conn.execute("ALTER EXTENSION vector SET SCHEMA slp_staging")

            def owned(dsn):
                conn = psycopg.connect(dsn, options="-c search_path=slp_staging")
                conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner)))
                return conn

            migrate(target, migrations, connector=owned)
            put(".pre.sql", Path("doc/setup/issue-517-restore-prelude.sql").read_text())
            put(".fail.sql", "SELECT 1/0;")
            args = (
                "psql",
                "-X",
                "-1",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                admin,
                "-d",
                database,
                "-v",
                "restore_database=" + database,
                "-v",
                "restore_admin=" + admin,
                "-v",
                "restore_owner=" + owner,
                "-f",
                prefix + ".pre.sql",
                "-f",
                prefix + ".app.sql",
            )
            with psycopg.connect(target) as conn:
                conn.execute("CREATE TABLE slp_staging.unreviewed_table(id integer)")
            unexpected = command(*args, check=False)
            assert unexpected.returncode != 0
            assert "restore_table_manifest_mismatch" in unexpected.stderr
            with psycopg.connect(target) as conn:
                assert conn.execute(
                    "SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname='vector'"
                ).fetchone() == ("slp_staging",)
                conn.execute("DROP TABLE slp_staging.unreviewed_table")
            with psycopg.connect(target) as conn:
                conn.execute(
                    "CREATE FUNCTION slp_staging.server_guard_processing_staging(integer) RETURNS integer LANGUAGE sql AS 'SELECT $1'"
                )
            unexpected_function = command(*args, check=False)
            assert unexpected_function.returncode != 0
            assert "restore_function_manifest_mismatch" in unexpected_function.stderr
            with psycopg.connect(target) as conn:
                conn.execute("DROP FUNCTION slp_staging.server_guard_processing_staging(integer)")
            failed = command(*args, "-f", prefix + ".fail.sql", check=False)
            assert failed.returncode != 0
            with psycopg.connect(target) as conn:
                assert conn.execute(
                    "SELECT extnamespace::regnamespace::text,oid FROM pg_extension WHERE extname='vector'"
                ).fetchone() == ("slp_staging", vector_oid)
                assert conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='slp_staging'").fetchone()[0] == 42
            command(*args)
            with psycopg.connect(target) as conn:
                assert snapshot(conn) == before
                assert conn.execute(
                    "SELECT extnamespace::regnamespace::text,oid FROM pg_extension WHERE extname='vector'"
                ).fetchone() == ("public", vector_oid)
                assert (
                    conn.execute(
                        "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner<>current_user"
                    ).fetchone()[0]
                    == 0
                )
                assert conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone() == (False,)
            assert not migrate(target, migrations, check=True).pending
        finally:
            try:
                command("rm", "-f", *(prefix + x for x in (".dump", ".app.sql", ".pre.sql", ".fail.sql")))
            finally:
                if created_db:
                    control.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
                for role in reversed(created_roles):
                    control.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
