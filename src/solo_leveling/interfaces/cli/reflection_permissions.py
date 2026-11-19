"""승인된 관리자 연결에 성찰 웹/MCP 역할을 분리 적용한다. 자동 실행하지 않는다."""
import hashlib


def grant_reflection_role(conn,schema,role,*,web=False):
    from psycopg import sql
    if type(web) is not bool:
        raise ValueError('reflection_role_invalid')
    profile=conn.execute("""SELECT oid,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls,rolinherit
        FROM pg_roles WHERE rolname=%s""",(role,)).fetchone()
    if (profile is None or any(profile[k] for k in ('rolsuper','rolcreaterole','rolcreatedb','rolbypassrls','rolinherit'))
            or conn.execute('SELECT 1 FROM pg_auth_members WHERE member=%s',(profile['oid'],)).fetchone()):
        raise ValueError('reflection_role_not_isolated')
    trigger='reflection_role_'+hashlib.sha256(role.encode()).hexdigest()[:12]
    existing=conn.execute("""SELECT p.proname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE n.nspname=%s AND c.relname='server_remote_consents' AND t.tgname=%s""",(schema,trigger)).fetchone()
    expected='server_learning_consent_role_guard' if web else 'server_question_lock_guard'
    if existing and existing['proname']!=expected:
        raise ValueError('reflection_role_profile_changed')
    reads=('server_papers','server_execution_contexts','server_processing_publications','server_embedding_sets',
           'server_reflection_contexts','server_remote_consents')
    if not web:
        reads+=('server_reflection_answers','server_reflection_requests')
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
    for table in reads:
        conn.execute(sql.SQL('GRANT SELECT ON {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    inserts=('server_remote_consents',) if web else ('server_reflection_contexts','server_reflection_answers','server_reflection_requests')
    for table in inserts:
        conn.execute(sql.SQL('GRANT INSERT ON {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    updates={'server_papers':('paper_id',),'server_remote_consents':('revoked_at',)}
    if not web:
        updates.update(server_reflection_answers=('body','status','revision','updated_at'),server_reflection_requests=('result',))
    for table,columns in updates.items():
        conn.execute(sql.SQL('GRANT UPDATE({}) ON {}.{} TO {}').format(sql.SQL(',').join(map(sql.Identifier,columns)),
            sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    trigger='reflection_role_'+hashlib.sha256(role.encode()).hexdigest()[:12]
    for table in ('server_papers','server_remote_consents'):
        conn.execute(sql.SQL('DROP TRIGGER IF EXISTS {} ON {}.{}').format(sql.Identifier(trigger),sql.Identifier(schema),sql.Identifier(table)))
        function='server_learning_consent_role_guard' if web and table=='server_remote_consents' else 'server_question_lock_guard'
        events='INSERT OR UPDATE' if function=='server_learning_consent_role_guard' else 'UPDATE'
        args=[sql.Literal(role)]+([sql.Literal('remote-reflection-v1')] if function=='server_learning_consent_role_guard' else [])
        conn.execute(sql.SQL('CREATE TRIGGER {} BEFORE {} ON {}.{} FOR EACH ROW EXECUTE FUNCTION {}.{}({})').format(
            sql.Identifier(trigger),sql.SQL(events),sql.Identifier(schema),sql.Identifier(table),sql.Identifier(schema),
            sql.Identifier(function),sql.SQL(',').join(args)))
