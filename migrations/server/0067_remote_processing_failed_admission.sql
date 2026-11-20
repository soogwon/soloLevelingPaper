-- 원래 접수와 같은 업로드의 명시적 후속 접수를 각각 한 건으로 제한한다.
ALTER TABLE server_processing_job_authorizations
    ADD COLUMN previous_job_id uuid,
    ADD CONSTRAINT server_processing_authorization_upload_job_key
        UNIQUE (owner_user_id, upload_intent_id, job_id),
    ADD CONSTRAINT server_processing_authorization_previous_fk
        FOREIGN KEY (owner_user_id, upload_intent_id, previous_job_id)
        REFERENCES server_processing_job_authorizations(owner_user_id, upload_intent_id, job_id),
    ADD CONSTRAINT server_processing_authorization_not_self
        CHECK (previous_job_id <> job_id),
    DROP CONSTRAINT server_processing_job_authori_owner_user_id_upload_intent_i_key;

CREATE UNIQUE INDEX server_processing_authorization_initial_idx
    ON server_processing_job_authorizations(owner_user_id, upload_intent_id)
    WHERE previous_job_id IS NULL;

CREATE UNIQUE INDEX server_processing_authorization_followup_idx
    ON server_processing_job_authorizations(owner_user_id, upload_intent_id)
    WHERE previous_job_id IS NOT NULL;
