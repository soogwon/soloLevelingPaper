"""로컬 검증용 웹 역할 계약. 관리자·worker·cleanup 권한과 분리한다."""

from psycopg import sql

WRITE = (
    "server_users",
    "web_sessions",
    "server_devices",
    "server_device_credentials",
    "server_idempotency_records",
    "server_papers",
    "server_learning_records",
    "server_summaries",
    "server_annotations",
    "server_paper_reading_states",
    "server_collections",
    "server_collection_papers",
    "server_resume_requests",
    "server_pdf_uploads",
    "server_remote_upload_intents",
    "server_remote_consents",
    "server_remote_upload_consent_bindings",
    "server_remote_upload_consent_requests",
)
INSERT_ONLY = (
    "server_processing_versions",
    "server_processing_jobs",
    "server_processing_job_authorizations",
    "server_deletion_requests",
)
READ_ONLY = (
    "server_schema_migrations",
    "server_processing_publications",
    "server_parse_revisions",
    "server_translation_revisions",
    "server_embedding_sets",
    "server_dispatch_controls",
    "server_remote_learning_records",
    "server_context_annotations",
    "server_execution_contexts",
)


def grant_web(conn, schema, role):
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
    for tables, privileges in (
        (WRITE, "SELECT, INSERT, UPDATE"),
        (INSERT_ONLY, "SELECT, INSERT"),
        (READ_ONLY, "SELECT"),
    ):
        for table in tables:
            conn.execute(
                sql.SQL("GRANT {} ON {}.{} TO {}").format(
                    sql.SQL(privileges), sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)
                )
            )
    for table in ("web_login_attempts", "server_collections", "server_collection_papers"):
        conn.execute(
            sql.SQL("GRANT SELECT, INSERT, DELETE ON {}.{} TO {}").format(
                sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)
            )
        )
    conn.execute(
        sql.SQL(
            "GRANT UPDATE(status,retryable,error_code,lease_owner,lease_token,lease_expires_at,heartbeat_at,finished_at,updated_at) ON {}.server_processing_jobs TO {}"
        ).format(sql.Identifier(schema), sql.Identifier(role))
    )
    # 접수의 행 잠금만 허용하고 버전 게시 포인터·dispatch 정책 변경은 거부한다.
    conn.execute(
        sql.SQL("""CREATE FUNCTION {}.web_readonly_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF current_user=TG_ARGV[0] THEN RAISE EXCEPTION 'web_readonly_table' USING ERRCODE='42501'; END IF;
        RETURN NEW; END $$""").format(sql.Identifier(schema))
    )
    for table, column in (
        ("server_processing_versions", "server_version_id"),
        ("server_dispatch_controls", "control_id"),
        ("server_processing_job_authorizations", "job_id"),
        ("server_deletion_requests", "request_id"),
    ):
        conn.execute(
            sql.SQL("GRANT UPDATE({}) ON {}.{} TO {}").format(
                sql.Identifier(column), sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)
            )
        )
        conn.execute(
            sql.SQL(
                "CREATE TRIGGER web_readonly_guard BEFORE UPDATE ON {}.{} FOR EACH ROW EXECUTE FUNCTION {}.web_readonly_guard({})"
            ).format(sql.Identifier(schema), sql.Identifier(table), sql.Identifier(schema), sql.Literal(role))
        )
