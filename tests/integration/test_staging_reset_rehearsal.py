"""전용 로컬 DB에서만 실행하는 초기화·권한 분리 리허설."""
import os
from pathlib import Path
from uuid import uuid4

import pytest

from solo_leveling.interfaces.cli.server_migrations import discover_migrations, migrate


DSN = os.environ.get("SOLO_RESET_REHEARSAL_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="전용 로컬 리허설 DSN 필요")


def test_reset_preserves_login_and_rebuilds_isolated_schema():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    # 전달받은 DB는 삭제하지 않는다. 생성한 고유 DB와 역할만 정리한다.
    params = conninfo_to_dict(DSN)
    assert params.get("host") in {"127.0.0.1", "localhost", "::1"}
    suffix = uuid4().hex[:12]
    database = f"slp_rehearsal_{suffix}"
    owner, web, worker = (f"slp_{kind}_{suffix}" for kind in ("owner", "web", "worker"))
    roles = []
    created = False
    target = make_conninfo(DSN, dbname=database)
    migrations = discover_migrations(Path("migrations/server"))
    with psycopg.connect(DSN, autocommit=True) as control:
        try:
            control.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            created = True
            migrate(target, migrations)
            with psycopg.connect(target) as conn:
                conn.execute("INSERT INTO server_users(owner_user_id, display_name) VALUES ('rollback-probe', '복원 검사')")
            # 삭제 도중 실패하면 트랜잭션 전체가 복원되는지 먼저 확인한다.
            with pytest.raises(RuntimeError, match='의도한 실패'):
                with psycopg.connect(target) as conn:
                    conn.execute("DELETE FROM server_users")
                    raise RuntimeError('의도한 실패')
            with psycopg.connect(target) as conn:
                login = conn.execute("SELECT current_user").fetchone()[0]
                assert conn.execute("SELECT count(*) FROM server_users").fetchone()[0] == 1
                conn.execute("INSERT INTO server_users(owner_user_id, display_name) VALUES ('synthetic', '합성')")
                tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
                assert len(tables) == 47
                assert ('server_annotation_consents',) in tables
                assert ('server_remote_learning_records',) in tables
                assert ('server_question_token_counts',) in tables
                vector_oid = conn.execute("SELECT oid FROM pg_extension WHERE extname='vector'").fetchone()[0]
                # 이 전용 DB에서 방금 만든 테이블만 명시적으로 한 번에 삭제한다.
                conn.execute(sql.SQL("DROP TABLE {} RESTRICT").format(sql.SQL(', ').join(
                    sql.Identifier('public', row[0]) for row in tables)))
                functions = conn.execute("SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'server_%'").fetchall()
                for (signature,) in functions:
                    conn.execute(sql.SQL("DROP FUNCTION {} RESTRICT").format(sql.SQL(signature)))
                for role in (owner, web, worker):
                    control.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
                    roles.append(role)
                for role in (web, worker):
                    control.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD 'local-rehearsal-only'").format(sql.Identifier(role)))
                    control.execute(sql.SQL("ALTER ROLE {} IN DATABASE {} SET search_path=slp_staging").format(sql.Identifier(role), sql.Identifier(database)))
                conn.execute(sql.SQL("CREATE SCHEMA slp_staging AUTHORIZATION {}").format(sql.Identifier(owner)))
                conn.execute("ALTER EXTENSION vector SET SCHEMA slp_staging")
                conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
                conn.execute("REVOKE TEMPORARY ON DATABASE " + database + " FROM PUBLIC")
                assert conn.execute("SELECT oid FROM pg_extension WHERE extname='vector'").fetchone()[0] == vector_oid
                assert conn.execute("SELECT current_user").fetchone()[0] == login

            def migration_connection(dsn):
                conn = psycopg.connect(dsn, options='-c search_path=slp_staging')
                conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner)))
                return conn

            assert len(migrate(target, migrations, connector=migration_connection).applied) == len(migrations)
            with psycopg.connect(target) as conn:
                for role in (web, worker):
                    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA slp_staging TO {}").format(sql.Identifier(role)))
                    conn.execute(sql.SQL("GRANT SELECT ON slp_staging.server_schema_migrations TO {}").format(sql.Identifier(role)))
                # 웹 로그인과 worker의 결과 조회에 필요한 최소 표본 권한이다.
                conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON slp_staging.server_users, slp_staging.web_sessions TO {}").format(sql.Identifier(web)))
                conn.execute(sql.SQL("GRANT SELECT ON slp_staging.server_processing_jobs, slp_staging.server_processing_publications, slp_staging.server_embedding_vectors TO {}").format(sql.Identifier(worker)))
                assert conn.execute("SELECT count(*) FROM slp_staging.server_users").fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM slp_staging.server_remote_learning_records").fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0] == 0
            for role in (web, worker):
                runtime = make_conninfo(target, user=role, password='local-rehearsal-only')
                assert not migrate(runtime, migrations, check=True).pending
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    migrate(runtime, migrations)
                with psycopg.connect(runtime, autocommit=True) as conn:
                    assert conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone() == (False, False, False, False)
                    assert conn.execute("SELECT current_schemas(false)").fetchone()[0] == ['slp_staging']
                    assert conn.execute("SELECT vector_dims('[1,2,3]'::vector)").fetchone()[0] == 3
                    assert conn.execute("SELECT '[1,0]'::vector <=> '[1,0]'::vector").fetchone()[0] == 0
                    for statement in (
                        "CREATE TABLE forbidden(id int)",
                        "CREATE TABLE public.forbidden(id int)",
                        "CREATE TEMP TABLE forbidden(id int)",
                        "DELETE FROM server_schema_migrations",
                        f"SET ROLE {owner}",
                    ):
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):
                            conn.execute(statement)
                    if role == worker:
                        for statement in ("SELECT * FROM web_sessions", "SELECT * FROM server_device_credentials", "DELETE FROM server_remote_consents", "DELETE FROM server_dispatch_budget_limits"):
                            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                                conn.execute(statement)
                        assert conn.execute("SELECT count(*) FROM server_processing_publications").fetchone()[0] == 0
                    else:
                        conn.execute("INSERT INTO server_users(owner_user_id, display_name) VALUES ('synthetic-new', '재로그인')")
                        conn.execute("INSERT INTO web_sessions(session_hash, csrf_hash, owner_user_id, expires_at) VALUES ('\\x01', '\\x02', 'synthetic-new', CURRENT_TIMESTAMP + interval '1 hour')")
                        assert conn.execute("SELECT count(*) FROM server_users").fetchone()[0] == 1
                        assert conn.execute("SELECT count(*) FROM web_sessions").fetchone()[0] == 1
            # 원래 접속 계정과 비밀번호로 계속 연결할 수 있다.
            with psycopg.connect(target) as conn:
                assert conn.execute("SELECT current_user").fetchone()[0] == login
        finally:
            if created:
                control.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            for role in reversed(roles):
                control.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
