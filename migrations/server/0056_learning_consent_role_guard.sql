-- 기능별 웹 역할은 해당 고지의 저장 동의만 기록·철회한다.
CREATE FUNCTION server_learning_consent_role_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_user=TG_ARGV[0] THEN
        IF NEW.purpose<>'storage' OR NEW.notice_version<>TG_ARGV[1]
           OR NEW.input_hash !~ ('^' || TG_ARGV[1] || ':[a-f0-9]{64}$') THEN
            RAISE EXCEPTION 'learning_consent_scope_invalid' USING ERRCODE='42501';
        END IF;
        IF TG_OP='UPDATE' AND (OLD.purpose<>'storage' OR OLD.notice_version<>TG_ARGV[1]
            OR OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL) THEN
            RAISE EXCEPTION 'learning_consent_revoke_only' USING ERRCODE='42501';
        END IF;
    END IF;
    RETURN NEW;
END $$;
