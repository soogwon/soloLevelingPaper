"""승인된 관리자 연결에서 취소 전용 역할의 최소 권한만 구성한다."""
import hashlib


def grant_processing_cancel_role(conn, schema, role):
    from psycopg import sql
    def ident(value):
        return sql.Identifier(value)
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(ident(schema), ident(role)))
    for table in ('server_papers','server_processing_jobs','server_processing_publications',
                  'server_processing_job_authorizations','server_remote_upload_intents',
                  'server_processing_cancel_requests'):
        conn.execute(sql.SQL('GRANT SELECT ON {}.{} TO {}').format(ident(schema),ident(table),ident(role)))
    conn.execute(sql.SQL('GRANT INSERT ON {}.server_processing_cancel_requests TO {}').format(ident(schema),ident(role)))
    conn.execute(sql.SQL('GRANT UPDATE(status,retryable,lease_owner,lease_token,lease_expires_at,heartbeat_at,finished_at,updated_at) ON {}.server_processing_jobs TO {}').format(ident(schema),ident(role)))
    conn.execute(sql.SQL('GRANT UPDATE(paper_id) ON {}.server_papers TO {}').format(ident(schema),ident(role)))
    suffix=hashlib.sha256(role.encode()).hexdigest()[:12]
    for table, function in (('server_papers','server_question_lock_guard'),
                            ('server_processing_jobs','server_processing_cancel_guard')):
        trigger='cancel_guard_'+suffix
        conn.execute(sql.SQL('DROP TRIGGER IF EXISTS {} ON {}.{}').format(ident(trigger),ident(schema),ident(table)))
        conn.execute(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON {}.{} FOR EACH ROW EXECUTE FUNCTION {}.{}({})').format(
            ident(trigger),ident(schema),ident(table),ident(schema),ident(function),sql.Literal(role)))
