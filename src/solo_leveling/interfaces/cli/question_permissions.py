"""승인된 관리자 연결에서 질문 검토 역할의 열 단위 최소 권한을 구성한다."""
import hashlib


def grant_question_review_role(conn,schema,role):
    from psycopg import sql
    tables=('server_papers','server_execution_contexts','server_processing_publications','server_embedding_sets',
        'server_remote_executions','server_provider_attempts','server_question_reviews','server_remote_consents',
        'server_dispatch_controls','server_dispatch_budget_limits','server_question_budget_reservations')
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
    for table in tables:
        conn.execute(sql.SQL('GRANT SELECT ON {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    for table in ('server_question_reviews','server_remote_consents','server_remote_executions','server_question_budget_reservations'):
        conn.execute(sql.SQL('GRANT INSERT ON {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    updates={'server_question_reviews':('storage_consent_id','ai_consent_id'),
        'server_remote_consents':('revoked_at',),'server_remote_executions':('execution_id',),
        'server_papers':('paper_id',),'server_dispatch_controls':('control_id',),
        'server_dispatch_budget_limits':('reserved_input_tokens','reserved_output_tokens','updated_at'),
        'server_question_budget_reservations':('remaining_input_tokens','remaining_output_tokens')}
    for table,columns in updates.items():
        conn.execute(sql.SQL('GRANT UPDATE({}) ON {}.{} TO {}').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    suffix=hashlib.sha256(role.encode()).hexdigest()[:12]
    for table in ('server_papers','server_dispatch_controls','server_remote_executions'):
        trigger='question_lock_'+suffix
        conn.execute(sql.SQL('DROP TRIGGER IF EXISTS {} ON {}.{}').format(sql.Identifier(trigger),sql.Identifier(schema),sql.Identifier(table)))
        conn.execute(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON {}.{} FOR EACH ROW EXECUTE FUNCTION {}.server_question_lock_guard({})').format(
            sql.Identifier(trigger),sql.Identifier(schema),sql.Identifier(table),sql.Identifier(schema),sql.Literal(role)))


def grant_question_maintenance_role(conn, schema, role):
    """정리 전용 역할은 실행·예산 행만 갱신하고 동의·원문·공급자 원장은 수정하지 않는다."""
    from psycopg import sql
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
    for table in ('server_remote_executions','server_execution_contexts','server_papers','server_remote_consents',
                  'server_question_budget_reservations','server_dispatch_budget_limits'):
        conn.execute(sql.SQL('GRANT SELECT ON {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    updates = {
        'server_remote_executions':('state','terminal_reason','lease_token','lease_expires_at','finished_at','updated_at'),
        'server_question_budget_reservations':('remaining_input_tokens','remaining_output_tokens'),
        'server_dispatch_budget_limits':('reserved_input_tokens','reserved_output_tokens','updated_at'),
    }
    for table, columns in updates.items():
        conn.execute(sql.SQL('GRANT UPDATE({}) ON {}.{} TO {}').format(sql.SQL(',').join(map(sql.Identifier,columns)),
            sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
