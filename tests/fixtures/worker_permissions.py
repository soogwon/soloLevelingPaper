"""#517 제한 계정 권한 계약. 운영 적용 도구가 아닌 로컬 리허설 전용이다."""
from psycopg import sql

# 행 잠금에는 한 열의 UPDATE 권한도 필요하다. 실제 변경은 별도 트리거로 거부한다.
LOCK_ONLY = {
    'server_papers': 'paper_id',
    'server_processing_job_authorizations': 'job_id',
    'server_remote_upload_intents': 'intent_id',
    'server_pdf_uploads': 'upload_id',
    'server_remote_upload_consent_bindings': 'consent_id',
    'server_remote_consents': 'consent_id',
    'server_dispatch_controls': 'control_id',
}
INSERT = (
    'server_processing_result_staging', 'server_processing_provider_attempts',
    'server_parse_revisions', 'server_translation_revisions', 'server_embedding_sets',
    'server_processing_chunks', 'server_translation_chunks', 'server_embedding_vectors',
    'server_embedding_fragments',
    'server_processing_publications',
)
UPDATE = {
    'server_embedding_vectors': ('embedding',),
    'server_embedding_fragments': ('embedding',),
    'server_processing_jobs': ('status', 'attempt', 'retryable', 'lease_owner', 'lease_token',
        'lease_expires_at', 'heartbeat_at', 'updated_at', 'error_code', 'finished_at'),
    'server_dispatch_budget_limits': ('reserved_input_tokens', 'reserved_output_tokens',
        'consumed_input_tokens', 'consumed_output_tokens', 'updated_at'),
    'server_processing_provider_attempts': ('status', 'actual_input_tokens', 'actual_output_tokens', 'settled_at'),
    'server_parse_revisions': ('status', 'completed_at'),
    'server_translation_revisions': ('status', 'completed_at'),
    'server_embedding_sets': ('status', 'completed_at'),
    'server_processing_versions': ('active_publication_id',),
    'server_processing_publications': ('staging_payload_sha256',),
}


def grant_worker(conn, schema, role):
    """테스트에서 만든 비소유자·비특권 역할에만 적용한다."""
    # 로컬에서 준비한 schema만 대상으로 삼는다.
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
    for table in sorted(set(LOCK_ONLY) | set(INSERT) | set(UPDATE)):
        conn.execute(sql.SQL('GRANT SELECT ON {}.{} TO {}').format(sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
    for table in INSERT:
        conn.execute(sql.SQL('GRANT INSERT ON {}.{} TO {}').format(sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
    for table, columns in {**UPDATE, **{k: (v,) for k, v in LOCK_ONLY.items()}}.items():
        conn.execute(sql.SQL('GRANT UPDATE ({}) ON {}.{} TO {}').format(
            sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
    conn.execute(sql.SQL('''CREATE FUNCTION {}.worker_rehearsal_readonly_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF current_user = TG_ARGV[0] THEN
            RAISE EXCEPTION 'worker_readonly_table' USING ERRCODE='42501';
        END IF;
        RETURN NEW;
        END $$''').format(sql.Identifier(schema)))
    for table in LOCK_ONLY:
        conn.execute(sql.SQL('''CREATE TRIGGER worker_rehearsal_readonly_guard BEFORE UPDATE ON {}.{}
            FOR EACH ROW EXECUTE FUNCTION {}.worker_rehearsal_readonly_guard({})''').format(
                sql.Identifier(schema), sql.Identifier(table), sql.Identifier(schema), sql.Literal(role)))
