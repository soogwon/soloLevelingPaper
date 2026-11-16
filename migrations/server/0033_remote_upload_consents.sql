-- 인증된 웹의 업로드 보관/AI 전송 동의를 원격 업로드 의도에 결합한다.
CREATE TABLE server_remote_upload_consent_bindings (
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    upload_intent_id uuid NOT NULL,
    purpose text NOT NULL CHECK (purpose IN ('storage', 'ai_provider')),
    revision bigint NOT NULL CHECK (revision >= 1),
    consent_id text NOT NULL,
    notice_version text NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at timestamptz NULL,
    PRIMARY KEY (owner_user_id, upload_intent_id, purpose, revision),
    UNIQUE (owner_user_id, consent_id),
    FOREIGN KEY (owner_user_id, upload_intent_id)
        REFERENCES server_remote_upload_intents(owner_user_id, intent_id) ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id, consent_id)
        REFERENCES server_remote_consents(owner_user_id, consent_id),
    CHECK (length(notice_version) BETWEEN 1 AND 100)
);

CREATE TABLE server_remote_upload_consent_requests (
    owner_user_id text NOT NULL,
    purpose text NOT NULL CHECK (purpose IN ('storage', 'ai_provider')),
    request_key text NOT NULL,
    upload_intent_id uuid NOT NULL,
    consent_id text NOT NULL,
    input_hash text NOT NULL,
    notice_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, purpose, request_key),
    FOREIGN KEY (owner_user_id, upload_intent_id)
        REFERENCES server_remote_upload_intents(owner_user_id, intent_id) ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id, consent_id)
        REFERENCES server_remote_upload_consent_bindings(owner_user_id, consent_id),
    CHECK (length(request_key) BETWEEN 1 AND 128),
    CHECK (length(input_hash) BETWEEN 1 AND 200),
    CHECK (length(notice_version) BETWEEN 1 AND 100)
);

CREATE UNIQUE INDEX uq_remote_upload_consent_current
    ON server_remote_upload_consent_bindings(owner_user_id, upload_intent_id, purpose)
    WHERE revoked_at IS NULL;

CREATE INDEX idx_remote_upload_consent_status
    ON server_remote_upload_consent_bindings(owner_user_id, upload_intent_id, purpose, revision DESC);
