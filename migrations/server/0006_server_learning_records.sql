CREATE UNIQUE INDEX IF NOT EXISTS uq_server_papers_sync_identity
    ON server_papers(paper_id, owner_user_id, source_instance_id);

CREATE TABLE IF NOT EXISTS server_learning_records (
    learning_record_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    source_instance_id uuid NOT NULL,
    local_learning_record_id text NOT NULL,
    selected_version_id text NULL,
    file_name text NULL,
    last_position jsonb NULL,
    last_studied_at timestamptz NULL,
    reading_status text NOT NULL CHECK (reading_status IN ('not_started', 'reading', 'completed')),
    source_updated_at timestamptz NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_user_id, source_instance_id, local_learning_record_id),
    FOREIGN KEY (paper_id, owner_user_id, source_instance_id)
        REFERENCES server_papers(paper_id, owner_user_id, source_instance_id)
);

CREATE INDEX IF NOT EXISTS idx_server_learning_records_latest
    ON server_learning_records(owner_user_id, paper_id, last_studied_at DESC NULLS LAST, learning_record_id);
