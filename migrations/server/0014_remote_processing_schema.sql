-- 원격 처리 결과는 owner와 원문 버전을 모든 참조에 포함해 교차 사용자 연결을 막는다.
CREATE TABLE server_processing_versions (
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    active_publication_id uuid NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, paper_id, server_version_id),
    FOREIGN KEY (paper_id, owner_user_id)
        REFERENCES server_papers(paper_id, owner_user_id) ON DELETE CASCADE
);

CREATE TABLE server_parse_revisions (
    parse_revision_id uuid NOT NULL,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('building', 'ready', 'failed', 'cancelled')),
    chunk_count integer NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamptz NULL,
    PRIMARY KEY (owner_user_id, parse_revision_id),
    UNIQUE (owner_user_id, paper_id, server_version_id, parse_revision_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id)
        REFERENCES server_processing_versions(owner_user_id, paper_id, server_version_id)
        ON DELETE CASCADE,
    CHECK ((status = 'ready') = (completed_at IS NOT NULL))
);

CREATE TABLE server_processing_chunks (
    chunk_id uuid NOT NULL,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    parse_revision_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    pdf_page integer NOT NULL CHECK (pdf_page >= 1),
    original_text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, chunk_id),
    UNIQUE (owner_user_id, parse_revision_id, ordinal),
    UNIQUE (owner_user_id, paper_id, server_version_id, parse_revision_id, chunk_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id)
        REFERENCES server_parse_revisions(owner_user_id, paper_id, server_version_id, parse_revision_id)
        ON DELETE CASCADE
);

CREATE TABLE server_translation_revisions (
    translation_revision_id uuid NOT NULL,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    parse_revision_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('building', 'ready', 'failed', 'cancelled')),
    source_chunk_count integer NOT NULL CHECK (source_chunk_count >= 0),
    translated_chunk_count integer NOT NULL DEFAULT 0 CHECK (translated_chunk_count >= 0),
    omissions jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(omissions) = 'array'),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamptz NULL,
    PRIMARY KEY (owner_user_id, translation_revision_id),
    UNIQUE (owner_user_id, paper_id, server_version_id, parse_revision_id, translation_revision_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id)
        REFERENCES server_parse_revisions(owner_user_id, paper_id, server_version_id, parse_revision_id)
        ON DELETE CASCADE,
    CHECK (translated_chunk_count <= source_chunk_count),
    CHECK ((status = 'ready') = (completed_at IS NOT NULL))
);

CREATE TABLE server_translation_chunks (
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    parse_revision_id uuid NOT NULL,
    translation_revision_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    translated_text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, translation_revision_id, chunk_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id,
                 translation_revision_id)
        REFERENCES server_translation_revisions(owner_user_id, paper_id, server_version_id,
                                                 parse_revision_id, translation_revision_id)
        ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id, chunk_id)
        REFERENCES server_processing_chunks(owner_user_id, paper_id, server_version_id,
                                             parse_revision_id, chunk_id)
        ON DELETE CASCADE
);

CREATE TABLE server_embedding_sets (
    embedding_set_id uuid NOT NULL,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    parse_revision_id uuid NOT NULL,
    translation_revision_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('building', 'ready', 'failed', 'cancelled')),
    indexed_chunk_count integer NOT NULL DEFAULT 0 CHECK (indexed_chunk_count >= 0),
    model_fingerprint jsonb NOT NULL CHECK (jsonb_typeof(model_fingerprint) = 'object'),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamptz NULL,
    PRIMARY KEY (owner_user_id, embedding_set_id),
    UNIQUE (owner_user_id, paper_id, server_version_id, parse_revision_id,
            translation_revision_id, embedding_set_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id,
                 translation_revision_id)
        REFERENCES server_translation_revisions(owner_user_id, paper_id, server_version_id,
                                                 parse_revision_id, translation_revision_id)
        ON DELETE CASCADE,
    CHECK ((status = 'ready') = (completed_at IS NOT NULL))
);

CREATE TABLE server_processing_publications (
    publication_id uuid NOT NULL,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    parse_revision_id uuid NOT NULL,
    translation_revision_id uuid NOT NULL,
    embedding_set_id uuid NOT NULL,
    publication_policy text NOT NULL CHECK (publication_policy IN ('complete', 'partial')),
    manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
    published_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, publication_id),
    UNIQUE (owner_user_id, paper_id, server_version_id, publication_id),
    UNIQUE (owner_user_id, paper_id, server_version_id, publication_id, parse_revision_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id)
        REFERENCES server_parse_revisions(owner_user_id, paper_id, server_version_id,
                                           parse_revision_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id,
                 translation_revision_id)
        REFERENCES server_translation_revisions(owner_user_id, paper_id, server_version_id,
                                                 parse_revision_id, translation_revision_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id,
                 translation_revision_id, embedding_set_id)
        REFERENCES server_embedding_sets(owner_user_id, paper_id, server_version_id,
                                          parse_revision_id, translation_revision_id,
                                          embedding_set_id)
);

ALTER TABLE server_processing_versions
    ADD CONSTRAINT fk_server_processing_versions_active_publication
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, active_publication_id)
    REFERENCES server_processing_publications(owner_user_id, paper_id, server_version_id,
                                               publication_id);

CREATE TABLE server_execution_contexts (
    context_id uuid NOT NULL,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    publication_id uuid NOT NULL,
    parse_revision_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, context_id),
    UNIQUE (owner_user_id, paper_id, server_version_id, publication_id,
            parse_revision_id, context_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, publication_id,
                 parse_revision_id)
        REFERENCES server_processing_publications(owner_user_id, paper_id, server_version_id,
                                                  publication_id, parse_revision_id)
);

CREATE TABLE server_processing_evidences (
    evidence_id uuid NOT NULL,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    publication_id uuid NOT NULL,
    parse_revision_id uuid NOT NULL,
    context_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    quoted_text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, evidence_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, publication_id,
                 parse_revision_id, context_id)
        REFERENCES server_execution_contexts(owner_user_id, paper_id, server_version_id,
                                             publication_id, parse_revision_id, context_id),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id, chunk_id)
        REFERENCES server_processing_chunks(owner_user_id, paper_id, server_version_id,
                                             parse_revision_id, chunk_id)
);

CREATE INDEX idx_server_processing_chunks_revision
    ON server_processing_chunks(owner_user_id, parse_revision_id, ordinal);
CREATE INDEX idx_server_processing_publications_active
    ON server_processing_publications(owner_user_id, paper_id, server_version_id, published_at DESC);
CREATE INDEX idx_server_processing_evidences_context
    ON server_processing_evidences(owner_user_id, context_id, created_at);
