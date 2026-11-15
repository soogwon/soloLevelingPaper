ALTER TABLE server_papers
    ADD COLUMN origin text NOT NULL DEFAULT 'local' CHECK (origin IN ('local', 'remote')),
    ALTER COLUMN source_instance_id DROP NOT NULL,
    ALTER COLUMN local_paper_id DROP NOT NULL,
    ADD CONSTRAINT ck_server_papers_origin_ids CHECK (
        (origin='local' AND source_instance_id IS NOT NULL AND local_paper_id IS NOT NULL)
        OR (origin='remote' AND source_instance_id IS NULL AND local_paper_id IS NULL)
    );

CREATE TABLE server_remote_upload_intents (
    intent_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    request_key text NOT NULL,
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    file_name text NOT NULL,
    file_size bigint NOT NULL CHECK (file_size BETWEEN 1 AND 52428800),
    content_type text NOT NULL CHECK (content_type='application/pdf'),
    sha256_hex text NOT NULL CHECK (sha256_hex ~ '^[0-9a-f]{64}$'),
    upload_id uuid NULL REFERENCES server_pdf_uploads(upload_id),
    status text NOT NULL CHECK (status IN ('created', 'uploading', 'verified', 'failed', 'expired')),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_user_id, request_key),
    UNIQUE (owner_user_id, intent_id),
    FOREIGN KEY (paper_id, owner_user_id) REFERENCES server_papers(paper_id, owner_user_id) ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id, paper_id, server_version_id)
        REFERENCES server_processing_versions(owner_user_id, paper_id, server_version_id) ON DELETE CASCADE
);

CREATE INDEX server_remote_upload_intents_expiry_idx
    ON server_remote_upload_intents(status, expires_at) WHERE status IN ('created', 'uploading');
