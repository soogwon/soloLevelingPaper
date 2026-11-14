CREATE TABLE IF NOT EXISTS server_pdf_uploads (
    upload_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    local_version_id text NOT NULL,
    file_name text NOT NULL,
    file_size bigint NOT NULL CHECK (file_size > 0),
    content_type text NOT NULL CHECK (content_type = 'application/pdf'),
    sha256_hex text NOT NULL CHECK (sha256_hex ~ '^[0-9a-f]{64}$'),
    object_key text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('pending', 'ready', 'failed', 'deleted')),
    failure_reason text NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id, owner_user_id)
        REFERENCES server_papers(paper_id, owner_user_id) ON DELETE CASCADE,
    UNIQUE (owner_user_id, paper_id, server_version_id, sha256_hex)
);

CREATE INDEX IF NOT EXISTS idx_server_pdf_uploads_owner_quota
    ON server_pdf_uploads(owner_user_id, status, file_size);

