-- 퀴즈 생성 전용 검토 입력과 목적별 동의를 고정하며 질문 동의를 재사용하지 않는다.
CREATE TABLE server_quiz_generation_reviews (
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    request_key uuid NOT NULL,
    context_id uuid NOT NULL,
    input_hash text NOT NULL,
    input_snapshot jsonb NOT NULL,
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
    CHECK (jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=1048576),
    CHECK (expires_at>created_at)
);
