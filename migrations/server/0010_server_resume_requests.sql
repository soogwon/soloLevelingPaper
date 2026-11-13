CREATE TABLE IF NOT EXISTS server_resume_requests (
    resume_request_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    idempotency_key text NOT NULL,
    payload_hash bytea NOT NULL,
    code text NOT NULL UNIQUE,
    paper_id uuid NOT NULL,
    version_id text NOT NULL,
    learning_record_id uuid NOT NULL
        REFERENCES server_learning_records(learning_record_id) ON DELETE CASCADE,
    annotation_id uuid
        REFERENCES server_annotations(annotation_id) ON DELETE SET NULL,
    expires_at timestamptz NOT NULL,
    prompt_text text NOT NULL,
    status text NOT NULL,
    reason text,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_user_id, idempotency_key),
    FOREIGN KEY (paper_id, owner_user_id)
        REFERENCES server_papers(paper_id, owner_user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_server_resume_requests_owner_code
    ON server_resume_requests(owner_user_id, code, expires_at);
