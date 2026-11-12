CREATE TABLE IF NOT EXISTS server_papers (
    paper_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    source_instance_id uuid NOT NULL,
    local_paper_id text NOT NULL,
    title text NULL,
    authors jsonb NULL,
    publication_year integer NULL,
    original_created_at timestamptz NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_user_id, source_instance_id, local_paper_id)
);

CREATE TABLE IF NOT EXISTS server_idempotency_records (
    owner_user_id text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    payload_hash bytea NOT NULL,
    response_status integer NULL,
    response_body jsonb NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, operation, idempotency_key),
    CHECK ((response_status IS NULL) = (response_body IS NULL))
);
