-- 과거 실행은 NULL로 유지한다. 질문 본문은 응답·로그에 자동 노출하지 않는다.
ALTER TABLE server_remote_executions ADD COLUMN question_snapshot jsonb NULL,
    ADD CONSTRAINT ck_execution_question_object CHECK (question_snapshot IS NULL OR
        (jsonb_typeof(question_snapshot)='object' AND policy_snapshot IS NOT NULL
         AND operation='ask_paper' AND octet_length(question_snapshot::text)<=262144));
CREATE OR REPLACE FUNCTION server_guard_execution_policy() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.question_snapshot IS DISTINCT FROM OLD.question_snapshot THEN
        RAISE EXCEPTION 'execution_question_immutable' USING ERRCODE='check_violation';
    END IF;
    IF NEW.policy_snapshot IS NOT NULL AND NEW.state IN ('running','completed') THEN
        PERFORM 1 FROM server_dispatch_controls control
          JOIN server_remote_consents storage ON storage.owner_user_id=NEW.owner_user_id
            AND storage.consent_id=NEW.storage_consent_id AND storage.purpose='storage'
            AND storage.revoked_at IS NULL AND storage.input_hash=NEW.input_hash
            AND storage.notice_version=NEW.notice_version
          JOIN server_remote_consents ai ON ai.owner_user_id=NEW.owner_user_id
            AND ai.consent_id=NEW.ai_consent_id AND ai.purpose='ai_provider'
            AND ai.revoked_at IS NULL AND ai.input_hash=NEW.input_hash
            AND ai.notice_version=NEW.notice_version
          WHERE control.control_id='provider_dispatch' AND control.dispatch_enabled
          FOR SHARE OF control,storage,ai;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'execution_scope_revoked' USING ERRCODE='check_violation';
        END IF;
    END IF;
    IF NEW.policy_snapshot IS DISTINCT FROM OLD.policy_snapshot
       OR NEW.deadline_at IS DISTINCT FROM OLD.deadline_at THEN
        RAISE EXCEPTION 'execution_policy_immutable' USING ERRCODE='check_violation';
    END IF;
    IF NEW.state='completed' AND OLD.state<>'completed'
       AND NEW.deadline_at IS NOT NULL AND NEW.deadline_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'execution_deadline_exceeded' USING ERRCODE='check_violation';
    END IF;
    RETURN NEW;
END $$;
