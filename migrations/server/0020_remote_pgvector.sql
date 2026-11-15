CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE server_processing_chunks
    ADD COLUMN section_id text NULL
        CHECK (section_id IS NULL OR (length(section_id) BETWEEN 1 AND 128 AND section_id = btrim(section_id)));

CREATE TABLE server_embedding_vectors (
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    parse_revision_id uuid NOT NULL,
    translation_revision_id uuid NOT NULL,
    embedding_set_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    dimension integer NOT NULL CHECK (dimension BETWEEN 1 AND 4096),
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, embedding_set_id, chunk_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id,
                 translation_revision_id, embedding_set_id)
        REFERENCES server_embedding_sets(owner_user_id, paper_id, server_version_id,
                                          parse_revision_id, translation_revision_id,
                                          embedding_set_id)
        ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id,
                 translation_revision_id, chunk_id)
        REFERENCES server_translation_chunks(owner_user_id, paper_id, server_version_id,
                                              parse_revision_id, translation_revision_id,
                                              chunk_id)
        ON DELETE CASCADE,
    CHECK (vector_dims(embedding) = dimension)
);
