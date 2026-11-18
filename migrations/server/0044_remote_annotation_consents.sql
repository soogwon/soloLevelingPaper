-- 학습 맥락별 저장 동의 이력. PDF 보관·AI 전송 동의로 대신하지 않는다.
CREATE TABLE server_annotation_consents (
    consent_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    notice_version text NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at timestamptz,
    UNIQUE(owner_user_id,context_id,revision),
    FOREIGN KEY(owner_user_id,context_id)
        REFERENCES server_remote_learning_records(owner_user_id,context_id) ON DELETE CASCADE
);
