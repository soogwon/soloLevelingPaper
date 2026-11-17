-- 기존 실행은 정책 미설정 상태로 유지한다. 새 runner는 명시적 정책 실행만 사용한다.
ALTER TABLE server_remote_executions
    ADD COLUMN policy_snapshot jsonb NULL,
    ADD COLUMN deadline_at timestamptz NULL,
    ADD COLUMN terminal_reason text NULL,
    ADD CONSTRAINT ck_execution_policy_pair CHECK (
        (policy_snapshot IS NULL) = (deadline_at IS NULL)),
    ADD CONSTRAINT ck_execution_policy_object CHECK (
        policy_snapshot IS NULL OR jsonb_typeof(policy_snapshot)='object'),
    ADD CONSTRAINT ck_execution_terminal_reason CHECK (terminal_reason IS NULL OR terminal_reason IN (
        'budget_exhausted','deadline_exceeded','provider_failed','cancelled','scope_revoked','internal_failed'));

CREATE FUNCTION server_guard_execution_policy() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
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
CREATE TRIGGER trg_execution_policy BEFORE UPDATE ON server_remote_executions
FOR EACH ROW EXECUTE FUNCTION server_guard_execution_policy();
