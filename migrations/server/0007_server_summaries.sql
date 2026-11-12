CREATE TABLE IF NOT EXISTS server_summaries (
    summary_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    learning_record_id uuid NOT NULL REFERENCES server_learning_records(learning_record_id),
    source_instance_id uuid NOT NULL,
    local_summary_id text NOT NULL,
    scope text NOT NULL,
    author_type text NOT NULL CHECK (author_type IN ('mcp', 'user')),
    items jsonb NOT NULL CHECK (jsonb_typeof(items) = 'array' AND jsonb_array_length(items) > 0),
    original_created_at timestamptz NOT NULL,
    source_updated_at timestamptz NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_user_id, source_instance_id, local_summary_id),
    FOREIGN KEY (paper_id, owner_user_id, source_instance_id)
        REFERENCES server_papers(paper_id, owner_user_id, source_instance_id)
);

CREATE INDEX IF NOT EXISTS idx_server_summaries_detail
    ON server_summaries(owner_user_id, paper_id, learning_record_id, scope, source_updated_at DESC);
