-- MCP는 검토 초안만 만든다. 유료 접수는 웹의 목적별 동의 이후에만 허용한다.
CREATE TABLE server_question_reviews (
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    request_key uuid NOT NULL,
    context_id uuid NOT NULL,
    input_hash text NOT NULL,
    question_snapshot jsonb NOT NULL,
    policy_snapshot jsonb NOT NULL,
    window_key text NOT NULL,
    notice_version text NOT NULL,
    storage_consent_id text NULL,
    ai_consent_id text NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY(owner_user_id,request_key),
    FOREIGN KEY(owner_user_id,context_id) REFERENCES server_execution_contexts(owner_user_id,context_id) ON DELETE CASCADE,
    FOREIGN KEY(owner_user_id,storage_consent_id) REFERENCES server_remote_consents(owner_user_id,consent_id),
    FOREIGN KEY(owner_user_id,ai_consent_id) REFERENCES server_remote_consents(owner_user_id,consent_id),
    CHECK (jsonb_typeof(question_snapshot)='object' AND octet_length(question_snapshot::text)<=262144),
    CHECK (expires_at>created_at)
);
CREATE FUNCTION server_question_lock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_user=TG_ARGV[0] THEN
        RAISE EXCEPTION 'question_lock_only' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;
