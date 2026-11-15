CREATE TABLE server_processing_jobs (
    job_id uuid NOT NULL,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    job_kind text NOT NULL CHECK (job_kind IN ('parse', 'translate', 'embed', 'full_processing')),
    request_key text NOT NULL,
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    retryable boolean NOT NULL DEFAULT true,
    lease_owner text NULL,
    lease_token uuid NULL,
    lease_expires_at timestamptz NULL,
    heartbeat_at timestamptz NULL,
    error_code text NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at timestamptz NULL,
    PRIMARY KEY (owner_user_id, job_id),
    UNIQUE (owner_user_id, request_key),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id)
        REFERENCES server_processing_versions(owner_user_id, paper_id, server_version_id)
        ON DELETE CASCADE,
    CHECK (
        (status = 'running' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL
         AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL)
        OR
        (status <> 'running' AND lease_owner IS NULL AND lease_token IS NULL
         AND lease_expires_at IS NULL AND heartbeat_at IS NULL)
    ),
    CHECK ((status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)),
    CHECK (error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,80}$')
);

CREATE INDEX idx_server_processing_jobs_claim
    ON server_processing_jobs(status, lease_expires_at, created_at, job_id);

ALTER TABLE server_processing_publications
    ADD COLUMN source_job_id uuid NULL,
    ADD COLUMN source_lease_token uuid NULL,
    ADD CONSTRAINT fk_server_processing_publications_source_job
        FOREIGN KEY (owner_user_id, source_job_id)
        REFERENCES server_processing_jobs(owner_user_id, job_id),
    ADD CONSTRAINT ck_server_processing_publications_source_lease
        CHECK ((source_job_id IS NULL) = (source_lease_token IS NULL));
