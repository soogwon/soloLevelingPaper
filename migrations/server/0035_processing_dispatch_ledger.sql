-- 질문 실행의 기존 FK는 유지하고, PDF 처리 job의 예약·정산을 별도 원장에 둔다.
-- LIKE는 외래키를 복제하지 않으므로 아래에서 처리 job과 동의의 owner 참조를 명시한다.
CREATE TABLE server_processing_provider_attempts (
    LIKE server_provider_attempts INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES
);
ALTER TABLE server_processing_provider_attempts
    ADD COLUMN processing_job_id uuid GENERATED ALWAYS AS (execution_id::uuid) STORED,
    ADD FOREIGN KEY (owner_user_id) REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    ADD FOREIGN KEY (owner_user_id, processing_job_id)
        REFERENCES server_processing_jobs(owner_user_id, job_id),
    ADD FOREIGN KEY (owner_user_id, storage_consent_id)
        REFERENCES server_remote_consents(owner_user_id, consent_id),
    ADD FOREIGN KEY (owner_user_id, ai_consent_id)
        REFERENCES server_remote_consents(owner_user_id, consent_id);
