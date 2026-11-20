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

    assert [item.version for item in items] == [f"{number:04d}" for number in range(1, 72)]
    assert items[-1].name == "0071_remote_chat_summaries.sql"


def test_remote_annotation_request_ledger_is_separate_from_device_sync_identity():
    sql = (__import__("pathlib").Path("migrations/server") /
           "0034_remote_annotation_create_requests.sql").read_text(encoding="utf-8")
    assert "server_context_annotations" in sql
    assert "server_annotation_create_requests" in sql
    assert "PRIMARY KEY (owner_user_id, service_id, context_id, request_key)" in sql
    assert "fingerprint_version" in sql
    assert "client_confirmed_at" in sql
    assert "source_instance_id" not in sql
    assert "local_annotation_id" not in sql


def test_remote_upload_consents_bind_owner_intent_purpose_and_revision():
    sql = (__import__("pathlib").Path("migrations/server") /
           "0033_remote_upload_consents.sql").read_text(encoding="utf-8")
    assert "server_remote_upload_consent_bindings" in sql
    assert "server_remote_upload_consent_requests" in sql
    assert "PRIMARY KEY (owner_user_id, purpose, request_key)" in sql
    assert "REFERENCES server_remote_upload_intents" in sql
    assert "REFERENCES server_remote_consents" in sql
    assert "WHERE revoked_at IS NULL" in sql


def test_tombstone_cleanup_only_allows_clearing_active_publication_pointer():
    sql = (__import__("pathlib").Path("migrations/server") /
           "0031_remote_deletion_active_pointer.sql").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION server_reject_tombstoned_paper" in sql
    assert "TG_TABLE_NAME = 'server_processing_versions'" in sql
    assert "OLD.active_publication_id IS NOT NULL AND NEW.active_publication_id IS NULL" in sql
    assert "NEW.owner_user_id = OLD.owner_user_id" in sql
    assert "RAISE EXCEPTION 'paper_tombstoned'" in sql


def test_remote_operation_controls_separate_intake_and_dispatch():
    sql = (__import__("pathlib").Path("migrations/server") / "0028_remote_operation_controls.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN intake_enabled" in sql
    assert "ALTER COLUMN intake_enabled SET NOT NULL" in sql


def test_remote_processing_job_keeps_upload_and_consent_authorization():
    sql = (__import__("pathlib").Path("migrations/server") / "0027_remote_processing_admission.sql").read_text(encoding="utf-8")

    assert "server_processing_job_authorizations" in sql
    assert "UNIQUE (owner_user_id, upload_intent_id)" in sql
    assert "REFERENCES server_remote_upload_intents" in sql
    assert sql.count("REFERENCES server_remote_consents") == 2


def test_remote_evidence_is_bound_to_execution():
    sql = (__import__("pathlib").Path("migrations/server") / "0026_remote_execution_evidence.sql").read_text(encoding="utf-8")

    assert "ADD COLUMN execution_id text NULL" in sql
    assert "FOREIGN KEY (owner_user_id, execution_id)" in sql
    assert "uq_server_processing_evidences_execution" in sql


def test_remote_execution_schema_binds_replay_and_dispatch_scope():
    sql = (__import__("pathlib").Path("migrations/server") / "0025_remote_executions.sql").read_text(encoding="utf-8")

    assert "UNIQUE (owner_user_id, operation, request_key)" in sql
    assert "FOREIGN KEY (owner_user_id, context_id)" in sql
    assert "FOREIGN KEY (owner_user_id, execution_id)" in sql
    assert "CHECK ((payload_json IS NULL) = (payload_sha256 IS NULL))" in sql
    assert "'cancel_requested'" in sql
    assert "CREATE TRIGGER trg_remote_executions_reject_tombstone" in sql
    assert "NEW.state IN ('cancel_requested', 'cancelled', 'failed')" in sql


def test_remote_dispatch_ledger_has_explicit_limits_and_unknown_state():
    sql = (__import__("pathlib").Path("migrations/server") / "0024_remote_dispatch_ledger.sql").read_text(encoding="utf-8")

    assert "server_remote_consents" in sql
    assert "server_dispatch_controls" in sql
    assert "server_dispatch_budget_limits" in sql
    assert "server_provider_attempts" in sql
    assert "UNIQUE (owner_user_id, execution_id, dispatch_key)" in sql
    assert "status IN ('reserved', 'observed', 'unknown')" in sql
    assert "max_input_tokens bigint NOT NULL" in sql
    assert "max_output_tokens bigint NOT NULL" in sql
    assert "window_key text NOT NULL" in sql


def test_remote_deletion_tombstone_blocks_every_processing_write_path():
    sql = (__import__("pathlib").Path("migrations/server") / "0023_remote_deletion_tombstones.sql").read_text(encoding="utf-8")

    assert "deleted_at timestamptz" in sql
    assert "server_deletion_requests" in sql
    for table in (
        "server_processing_jobs",
        "server_processing_versions",
        "server_parse_revisions",
        "server_processing_chunks",
        "server_translation_revisions",
        "server_translation_chunks",
        "server_embedding_sets",
        "server_embedding_vectors",
        "server_processing_publications",
        "server_execution_contexts",
        "server_processing_evidences",
        "server_pdf_uploads",
        "server_remote_upload_intents",
    ):
        assert f"BEFORE INSERT OR UPDATE ON {table}" in sql


def test_remote_deletion_cleanup_has_retry_and_completion_state():
    sql = (__import__("pathlib").Path("migrations/server") / "0029_remote_deletion_cleanup.sql").read_text(encoding="utf-8")
    assert "attempt_count" in sql
    assert "last_error_code" in sql
    assert "completed_at" in sql
    assert "status IN ('blocking', 'failed')" in sql


def test_remote_identity_mapping_uses_exact_issuer_subject_and_owner_fk():
    item = discover_migrations(__import__("pathlib").Path("migrations/server"))[18]

    assert "PRIMARY KEY (issuer, subject)" in item.sql
    assert "REFERENCES server_users(owner_user_id)" in item.sql
    assert "WHERE revoked_at IS NULL" in item.sql


def test_remote_pgvector_rows_keep_owner_revision_and_chunk_scope():
    item = discover_migrations(__import__("pathlib").Path("migrations/server"))[19]

    assert "CREATE EXTENSION IF NOT EXISTS vector" in item.sql
    assert "ADD COLUMN section_id" in item.sql
    assert "PRIMARY KEY (owner_user_id, embedding_set_id, chunk_id)" in item.sql
    assert item.sql.count("FOREIGN KEY (owner_user_id, paper_id, server_version_id") == 1
    assert "FOREIGN KEY (owner_user_id, translation_revision_id, chunk_id)" in item.sql
    assert "CHECK (vector_dims(embedding) = dimension)" in item.sql
    assert "CREATE TRIGGER trg_server_embedding_vectors_generation" in item.sql
    assert "parent.status <> 'building'" in item.sql
    assert "server_processing_require_lease(" in item.sql
    assert "processing_row_identity_fixed" in item.sql


def test_remote_processing_schema_uses_owner_scoped_composite_references():
    item = discover_migrations(__import__("pathlib").Path("migrations/server"))[13]

    assert "PRIMARY KEY (owner_user_id, paper_id, server_version_id)" in item.sql
    assert "FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id)" in item.sql
    assert "FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id," in item.sql
    assert "active_publication_id" in item.sql
    assert "publication_policy IN ('complete', 'partial')" in item.sql


def test_remote_job_schema_guards_lease_generation_and_idempotency():
    item = discover_migrations(__import__("pathlib").Path("migrations/server"))[14]

    assert "UNIQUE (owner_user_id, request_key)" in item.sql
    assert "lease_token uuid NULL" in item.sql
    assert "attempt integer NOT NULL DEFAULT 0" in item.sql
    assert "source_lease_token" in item.sql


def test_remote_processing_integrity_adds_delete_path_and_finalized_guards():
    item = discover_migrations(__import__("pathlib").Path("migrations/server"))[15]

    assert item.sql.count("ON DELETE CASCADE") == 3
    assert "processing_revision_finalized" in item.sql
    assert "BEFORE INSERT OR UPDATE OR DELETE ON server_processing_chunks" in item.sql
    assert "BEFORE INSERT OR UPDATE OR DELETE ON server_translation_chunks" in item.sql
    assert "FOR SHARE" in item.sql


def test_remote_processing_generation_binds_writes_to_the_job_lease():
    item = discover_migrations(__import__("pathlib").Path("migrations/server"))[16]

    assert item.sql.count("ADD COLUMN source_lease_token uuid NULL") == 3
    assert item.sql.count("DEFERRABLE INITIALLY DEFERRED") == 3
    assert "processing_lease_lost" in item.sql
    assert "processing_revision_generation_fixed" in item.sql
    assert "uq_server_processing_publications_source_job" in item.sql
    assert "BEFORE INSERT OR UPDATE ON server_embedding_sets" in item.sql


def test_remote_processing_rows_cannot_change_revision_or_identity():
    item = discover_migrations(__import__("pathlib").Path("migrations/server"))[17]

    assert item.sql.count("processing_row_identity_fixed") == 2
    assert "NEW.translation_revision_id" in item.sql and "OLD.translation_revision_id" in item.sql
    # 0017의 lease 확인과 확정 뒤 쓰기 거부를 그대로 유지한 채 함수를 교체한다.
    assert item.sql.count("server_processing_require_lease(") == 2
    assert item.sql.count("processing_revision_finalized") == 2
    assert item.sql.count("FOR SHARE") == 2


def test_remote_upload_intent_links_upload_in_same_owner_version_scope():
    item = discover_migrations(__import__("pathlib").Path("migrations/server"))[20]

    assert "uq_server_pdf_uploads_owner_version_upload" in item.sql
    assert "FOREIGN KEY (owner_user_id, paper_id, server_version_id, upload_id)" in item.sql
    assert (
        "REFERENCES server_pdf_uploads(owner_user_id, paper_id, "
        "server_version_id, upload_id)" in item.sql
    )


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
