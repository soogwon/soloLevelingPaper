CREATE TABLE server_processing_job_authorizations (
    owner_user_id text NOT NULL,
    job_id uuid NOT NULL,
    upload_intent_id uuid NOT NULL,
    storage_consent_id text NOT NULL,
    ai_consent_id text NOT NULL,
    notice_version text NOT NULL,
    input_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, job_id),
    UNIQUE (owner_user_id, upload_intent_id),
    FOREIGN KEY (owner_user_id, job_id)
        REFERENCES server_processing_jobs(owner_user_id, job_id) ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id, upload_intent_id)
        REFERENCES server_remote_upload_intents(owner_user_id, intent_id),
    FOREIGN KEY (owner_user_id, storage_consent_id)
        REFERENCES server_remote_consents(owner_user_id, consent_id),
    FOREIGN KEY (owner_user_id, ai_consent_id)
        REFERENCES server_remote_consents(owner_user_id, consent_id)
);

