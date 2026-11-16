"""제품 migration 도구로 0032 기존 행 이관과 재실행 불변성을 검증한다."""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from solo_leveling.interfaces.cli.server_migrations import discover_migrations, migrate


DSN = os.environ.get("SOLO_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="실제 PostgreSQL 테스트 DSN 필요")
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations" / "server"


def test_annotation_origin_migration_preserves_existing_rows_and_is_rerunnable():
    import psycopg

    schema = f"migration_replay_{uuid4().hex[:12]}"
    options = f"-c search_path={schema}"

    def connect(dsn):
        return psycopg.connect(dsn, options=options)

    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        # 후속 migration이 추가되어도 0032 직전의 기존 행 이관을 검사한다.
        migrations = tuple(item for item in discover_migrations(MIGRATIONS)
                           if item.version <= "0032")
        assert migrations[-1].name == "0032_annotation_creation_origin.sql"
        before_origin = tuple(item for item in migrations if item.version < "0032")
        first = migrate(DSN, before_origin, connector=connect)
        assert first.applied == tuple(item.name for item in before_origin)

        owner = "migration-owner"
        source_id = uuid4()
        paper_id = uuid4()
        learning_id = uuid4()
        annotation_ids = [uuid4(), uuid4(), uuid4()]
        with connect(DSN) as conn:
            conn.execute(
                "INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,%s)",
                (owner, "Migration Owner"),
            )
            conn.execute(
                """INSERT INTO server_papers
                       (paper_id,owner_user_id,source_instance_id,local_paper_id,
                        original_created_at)
                   VALUES (%s,%s,%s,'paper',CURRENT_TIMESTAMP)""",
                (paper_id, owner, source_id),
            )
            conn.execute(
                """INSERT INTO server_learning_records
                       (learning_record_id,owner_user_id,paper_id,source_instance_id,
                        local_learning_record_id,reading_status,source_updated_at)
                   VALUES (%s,%s,%s,%s,'learning','reading',CURRENT_TIMESTAMP)""",
                (learning_id, owner, paper_id, source_id),
            )
            for number, annotation_id in enumerate(annotation_ids):
                conn.execute(
                    """INSERT INTO server_annotations
                           (annotation_id,owner_user_id,paper_id,learning_record_id,
                            source_instance_id,local_annotation_id,kind,body,segments,
                            original_created_at,source_updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,'highlight',%s,%s,
                               CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                    (
                        annotation_id,
                        owner,
                        paper_id,
                        learning_id,
                        source_id,
                        f"annotation-{number}",
                        f"synthetic-body-{number}",
                        json.dumps([{"page": number + 1}]),
                    ),
                )

        applied = migrate(DSN, migrations, connector=connect)
        assert applied.applied == ("0032_annotation_creation_origin.sql",)
        with connect(DSN) as conn:
            rows = conn.execute(
                """SELECT annotation_id,body,segments,creation_origin
                   FROM server_annotations ORDER BY local_annotation_id"""
            ).fetchall()
            assert [row[0] for row in rows] == annotation_ids
            assert [row[1] for row in rows] == [
                "synthetic-body-0",
                "synthetic-body-1",
                "synthetic-body-2",
            ]
            assert [row[2] for row in rows] == [
                [{"page": 1}],
                [{"page": 2}],
                [{"page": 3}],
            ]
            assert [row[3] for row in rows] == ["unknown", "unknown", "unknown"]
            conn.execute(
                "UPDATE server_annotations SET creation_origin='user' WHERE annotation_id=%s",
                (annotation_ids[0],),
            )
            conn.execute(
                "UPDATE server_annotations SET creation_origin='ai' WHERE annotation_id=%s",
                (annotation_ids[1],),
            )

            # 건수만 같고 행·체크섬·적용 시각이 바뀌는 회귀도 검출한다.
            data_before = conn.execute(
                "SELECT * FROM server_annotations ORDER BY local_annotation_id"
            ).fetchall()
            history_before = conn.execute(
                "SELECT * FROM server_schema_migrations ORDER BY version"
            ).fetchall()

        second = migrate(DSN, migrations, connector=connect)
        assert second.applied == ()
        assert second.pending == ()
        with connect(DSN) as conn:
            assert conn.execute(
                """SELECT body,segments,creation_origin FROM server_annotations
                   ORDER BY local_annotation_id"""
            ).fetchall() == [
                ("synthetic-body-0", [{"page": 1}], "user"),
                ("synthetic-body-1", [{"page": 2}], "ai"),
                ("synthetic-body-2", [{"page": 3}], "unknown"),
            ]
            assert conn.execute(
                "SELECT * FROM server_annotations ORDER BY local_annotation_id"
            ).fetchall() == data_before
            assert conn.execute(
                "SELECT * FROM server_schema_migrations ORDER BY version"
            ).fetchall() == history_before
            assert len(history_before) == len(migrations)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
