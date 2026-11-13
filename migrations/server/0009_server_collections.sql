CREATE TABLE IF NOT EXISTS server_collections (
    collection_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    name text NOT NULL,
    normalized_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_user_id, normalized_name),
    UNIQUE (collection_id, owner_user_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_server_papers_id_owner
    ON server_papers(paper_id, owner_user_id);

CREATE TABLE IF NOT EXISTS server_collection_papers (
    paper_id uuid PRIMARY KEY,
    collection_id uuid NOT NULL,
    owner_user_id text NOT NULL,
    added_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id, owner_user_id)
        REFERENCES server_collections(collection_id, owner_user_id) ON DELETE CASCADE,
    FOREIGN KEY (paper_id, owner_user_id)
        REFERENCES server_papers(paper_id, owner_user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_server_collection_papers_collection
    ON server_collection_papers(owner_user_id, collection_id, added_at DESC, paper_id DESC);
