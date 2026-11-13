CREATE TABLE IF NOT EXISTS server_annotations (
    annotation_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    learning_record_id uuid NOT NULL REFERENCES server_learning_records(learning_record_id),
    source_instance_id uuid NOT NULL,
    local_annotation_id text NOT NULL,
    selected_version_id text,
    kind text NOT NULL CHECK (kind IN ('note', 'highlight')),
    body text NOT NULL DEFAULT '',
    step text,
    segments jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(segments) = 'array'),
    original_created_at timestamptz NOT NULL,
    source_updated_at timestamptz NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_user_id, source_instance_id, local_annotation_id),
    FOREIGN KEY (paper_id, owner_user_id, source_instance_id)
        REFERENCES server_papers(paper_id, owner_user_id, source_instance_id)
);

CREATE INDEX IF NOT EXISTS idx_server_annotations_learning
    ON server_annotations(owner_user_id, paper_id, learning_record_id, source_updated_at DESC);
