"""승인된 관리자 연결에서만 원격 학습·메모 역할의 최소 권한을 구성한다."""
import hashlib


def grant_annotation_role(conn, schema, role):
    """애플리케이션 owner 검증과 별개로 자격·예산·원문 변경 권한을 차단한다."""
    from psycopg import sql
    reads = ('server_papers', 'server_execution_contexts', 'server_processing_publications',
             'server_processing_chunks', 'server_translation_chunks',
             'server_remote_learning_records', 'server_context_annotations',
             'server_annotation_create_requests', 'server_annotation_consents')
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
    for table in reads:
        conn.execute(sql.SQL('GRANT SELECT ON {}.{} TO {}').format(
            sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
    for table in ('server_remote_learning_records', 'server_context_annotations', 'server_annotation_create_requests'):
        conn.execute(sql.SQL('GRANT INSERT ON {}.{} TO {}').format(
            sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
    updates = {
        'server_remote_learning_records': ('status', 'revision', 'updated_at'),
        'server_context_annotations': ('body', 'step', 'segments', 'revision', 'updated_at', 'deleted_at'),
        'server_annotation_create_requests': ('state', 'client_confirmed_at'),
        'server_papers': ('paper_id',),
        'server_execution_contexts': ('context_id',),
        'server_processing_publications': ('publication_id',),
    }
    for table, columns in updates.items():
        conn.execute(sql.SQL('GRANT UPDATE({}) ON {}.{} TO {}').format(
            sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(schema),
            sql.Identifier(table), sql.Identifier(role)))
    # 행 잠금용 UPDATE 권한은 실제 데이터 수정 권한으로 사용할 수 없다.
    trigger = 'annotation_lock_' + hashlib.sha256(role.encode()).hexdigest()[:12]
    for table in ('server_papers', 'server_execution_contexts', 'server_processing_publications'):
        conn.execute(sql.SQL('DROP TRIGGER IF EXISTS {} ON {}.{}').format(
            sql.Identifier(trigger), sql.Identifier(schema), sql.Identifier(table)))
        conn.execute(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON {}.{} FOR EACH ROW '
                            'EXECUTE FUNCTION {}.server_question_lock_guard({})').format(
            sql.Identifier(trigger), sql.Identifier(schema), sql.Identifier(table),
            sql.Identifier(schema), sql.Literal(role)))


def grant_annotation_web_role(conn, schema, role):
    """웹 세션의 명시적 동의·철회만 추가 허용한다. MCP 역할에는 적용하지 않는다."""
    from psycopg import sql
    grant_annotation_role(conn, schema, role)
    conn.execute(sql.SQL('GRANT INSERT ON {}.server_annotation_consents TO {}').format(
        sql.Identifier(schema), sql.Identifier(role)))
    conn.execute(sql.SQL('GRANT UPDATE(revoked_at) ON {}.server_annotation_consents TO {}').format(
        sql.Identifier(schema), sql.Identifier(role)))
