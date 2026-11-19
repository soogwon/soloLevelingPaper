"""승인된 관리자 연결에서 퀴즈 웹 서비스와 실행 역할의 권한을 분리한다."""
import hashlib


def grant_quiz_role(conn, schema, role, *, worker=False):
    from psycopg import sql
    if type(worker) is not bool:
        raise ValueError('quiz_role_invalid')
    profile = conn.execute('''SELECT oid,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls,rolinherit
        FROM pg_roles WHERE rolname=%s''', (role,)).fetchone()
    if (profile is None or any(profile[k] for k in ('rolsuper','rolcreaterole','rolcreatedb','rolbypassrls','rolinherit'))
            or conn.execute('SELECT 1 FROM pg_auth_members WHERE member=%s', (profile['oid'],)).fetchone()):
        raise ValueError('quiz_role_not_isolated')
    suffix = hashlib.sha256(role.encode()).hexdigest()[:12]
    function = 'quiz_scope_' + suffix
    # 같은 역할을 다른 용도로 전환해 권한이 합쳐지는 것을 차단한다.
    other = 'server_quiz_generation_reviews' if worker else 'server_provider_attempts'
    if conn.execute('SELECT has_table_privilege(%s,%s,\'INSERT\') AS yes',
                    (role, f'{schema}.{other}')).fetchone()['yes']:
        raise ValueError('quiz_role_profile_changed')
    reads = {'server_papers','server_execution_contexts','server_remote_executions','server_remote_consents',
             'server_dispatch_controls','server_dispatch_budget_limits','server_question_budget_reservations',
             'server_provider_attempts','server_quiz_generation_inputs','server_remote_quizzes',
             'server_processing_publications','server_embedding_sets','server_processing_evidences',
             'server_processing_chunks','server_translation_chunks'}
    inserts = {'server_provider_attempts','server_remote_quizzes'} if worker else {
        'server_quiz_generation_reviews','server_remote_consents','server_remote_executions',
        'server_quiz_generation_inputs','server_question_budget_reservations','server_remote_quiz_attempts'}
    locks = {'server_papers':'paper_id','server_dispatch_controls':'control_id'}
    updates = {
        'server_remote_executions': ['state','lease_token','lease_expires_at','updated_at'],
        'server_dispatch_budget_limits': ['reserved_input_tokens','reserved_output_tokens','updated_at'],
        'server_question_budget_reservations': ['remaining_input_tokens','remaining_output_tokens'],
    }
    if worker:
        locks['server_remote_consents'] = 'consent_id'
        updates['server_remote_executions'] += ['terminal_reason','finished_at','lease_generation','budget_state','payload_json','payload_sha256']
        updates['server_provider_attempts'] = ['status','actual_input_tokens','actual_output_tokens','settled_at']
        updates['server_dispatch_budget_limits'] += ['consumed_input_tokens','consumed_output_tokens']
    else:
        reads.update({'server_remote_quiz_attempts','server_quiz_generation_reviews','server_processing_publications','server_embedding_sets',
                      'server_processing_chunks','server_translation_chunks'})
        updates['server_quiz_generation_reviews'] = ['storage_consent_id','ai_consent_id']
        updates['server_remote_consents'] = ['revoked_at']
    updates.update({table:[column] for table,column in locks.items()})
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
    for permission,tables in [('SELECT',reads),('INSERT',inserts)]:
        for table in sorted(tables):
            conn.execute(sql.SQL('GRANT {} ON {}.{} TO {}').format(sql.SQL(permission),sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    for table,columns in updates.items():
        conn.execute(sql.SQL('GRANT UPDATE({}) ON {}.{} TO {}').format(sql.SQL(',').join(map(sql.Identifier,columns)),
            sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    # 공유 원장은 퀴즈 작업만 쓰며 웹 역할은 생성 완료를 위조할 수 없다.
    conn.execute(sql.SQL('''CREATE OR REPLACE FUNCTION {}.{}() RETURNS trigger LANGUAGE plpgsql AS $guard$
    BEGIN
        IF current_user<>TG_ARGV[0] THEN RETURN NEW; END IF;
        IF TG_TABLE_NAME='server_remote_consents' THEN
            IF NEW.notice_version NOT IN ('remote-quiz-generation-v1','remote-quiz-attempt-v1')
               OR NEW.purpose NOT IN ('storage','ai_provider')
               OR (NEW.notice_version='remote-quiz-attempt-v1' AND NEW.purpose<>'storage')
               OR (TG_OP='UPDATE' AND (OLD.notice_version NOT IN ('remote-quiz-generation-v1','remote-quiz-attempt-v1')
                  OR NEW.revoked_at IS NULL
                  OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at))) THEN
                RAISE EXCEPTION 'quiz_scope_invalid' USING ERRCODE='42501';
            END IF;
        ELSIF TG_TABLE_NAME='server_remote_executions' THEN
            IF NEW.operation<>'generate_quiz' OR (TG_OP='UPDATE' AND OLD.operation<>'generate_quiz')
               OR (TG_ARGV[1]='web' AND ((TG_OP='INSERT' AND NEW.state<>'queued')
                  OR (TG_OP='UPDATE' AND (NEW.state<>'cancel_requested'
                     OR OLD.state NOT IN ('queued','running')
                     OR NEW.lease_token IS NOT NULL OR NEW.lease_expires_at IS NOT NULL)))) THEN
                RAISE EXCEPTION 'quiz_scope_invalid' USING ERRCODE='42501';
            END IF;
        ELSIF NOT EXISTS (SELECT 1 FROM {}.server_remote_executions e
            WHERE e.owner_user_id=NEW.owner_user_id AND e.execution_id=NEW.execution_id
              AND e.operation='generate_quiz') THEN
            RAISE EXCEPTION 'quiz_scope_invalid' USING ERRCODE='42501';
        END IF;
        RETURN NEW;
    END $guard$''').format(sql.Identifier(schema),sql.Identifier(function),sql.Identifier(schema)))
    scoped = {'server_remote_executions','server_question_budget_reservations'}
    scoped.add('server_provider_attempts' if worker else 'server_remote_consents')
    for table in sorted(set(locks) | scoped):
        trigger = 'quiz_role_' + suffix
        conn.execute(sql.SQL('DROP TRIGGER IF EXISTS {} ON {}.{}').format(sql.Identifier(trigger),sql.Identifier(schema),sql.Identifier(table)))
        target = 'server_question_lock_guard' if table in locks else function
        events = 'UPDATE' if table in locks else 'INSERT OR UPDATE'
        args = [sql.Literal(role)] + ([] if table in locks else [sql.Literal('worker' if worker else 'web')])
        conn.execute(sql.SQL('CREATE TRIGGER {} BEFORE {} ON {}.{} FOR EACH ROW EXECUTE FUNCTION {}.{}({})').format(
            sql.Identifier(trigger),sql.SQL(events),sql.Identifier(schema),sql.Identifier(table),sql.Identifier(schema),
            sql.Identifier(target),sql.SQL(',').join(args)))
