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


def grant_question_execution_role(conn, schema, role):
    """질문 생성 역할의 검색·답변·사용량 권한. 원문·동의·정책 변경은 허용하지 않는다."""
    from psycopg import sql
    grant_question_maintenance_role(conn, schema, role)
    reads = ('server_processing_publications','server_embedding_sets','server_parse_revisions',
             'server_translation_revisions','server_processing_chunks','server_translation_chunks',
             'server_embedding_vectors','server_processing_evidences','server_provider_attempts','server_dispatch_controls')
    for table in reads:
        conn.execute(sql.SQL('GRANT SELECT ON {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    for table in ('server_provider_attempts','server_processing_evidences'):
        conn.execute(sql.SQL('GRANT INSERT ON {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    updates = {
        'server_remote_executions': ('lease_generation','budget_state','payload_json','payload_sha256'),
        'server_provider_attempts': ('status','actual_input_tokens','actual_output_tokens','settled_at'),
        'server_dispatch_budget_limits': ('consumed_input_tokens','consumed_output_tokens'),
    }
    # PostgreSQL 행 잠금에는 UPDATE 권한이 필요하지만 실제 수정은 역할별 trigger로 차단한다.
    locks = {'server_papers':'paper_id', 'server_dispatch_controls':'control_id',
             'server_remote_consents':'consent_id','server_processing_publications':'publication_id',
             'server_parse_revisions':'parse_revision_id','server_translation_revisions':'translation_revision_id',
             'server_embedding_sets':'embedding_set_id'}
    updates.update({table:(column,) for table,column in locks.items()})
    for table, columns in updates.items():
        conn.execute(sql.SQL('GRANT UPDATE({}) ON {}.{} TO {}').format(sql.SQL(',').join(map(sql.Identifier,columns)),
            sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    trigger = 'question_lock_' + hashlib.sha256(role.encode()).hexdigest()[:12]
    for table in locks:
        conn.execute(sql.SQL('DROP TRIGGER IF EXISTS {} ON {}.{}').format(sql.Identifier(trigger),sql.Identifier(schema),sql.Identifier(table)))
        conn.execute(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON {}.{} FOR EACH ROW EXECUTE FUNCTION {}.server_question_lock_guard({})').format(
            sql.Identifier(trigger),sql.Identifier(schema),sql.Identifier(table),sql.Identifier(schema),sql.Literal(role)))


def grant_question_token_count_role(conn, schema, role):
    """기존 질문 실행 역할에 별도 승인한 입력 계수 원장 권한만 추가한다."""
    from psycopg import sql
    target = sql.SQL('{}.{}').format(sql.Identifier(schema),sql.Identifier('server_question_token_counts'))
    conn.execute(sql.SQL('GRANT SELECT, INSERT ON {} TO {}').format(target,sql.Identifier(role)))
    conn.execute(sql.SQL('GRANT UPDATE(status,input_tokens) ON {} TO {}').format(target,sql.Identifier(role)))
