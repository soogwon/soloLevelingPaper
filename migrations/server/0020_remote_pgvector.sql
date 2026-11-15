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

-- 벡터도 chunk/번역 행과 같은 실행 세대에 묶는다. ready 게시 뒤에는 직접 SQL로도
-- 추가·교체·삭제할 수 없고, 논문 삭제의 연쇄 삭제에서 부모가 사라진 경우만 허용한다.
CREATE FUNCTION server_processing_guard_embedding_vectors() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target record;
    parent record;
BEGIN
    IF TG_OP = 'UPDATE' AND
       (NEW.owner_user_id, NEW.paper_id, NEW.server_version_id, NEW.parse_revision_id,
        NEW.translation_revision_id, NEW.embedding_set_id, NEW.chunk_id)
       IS DISTINCT FROM
       (OLD.owner_user_id, OLD.paper_id, OLD.server_version_id, OLD.parse_revision_id,
        OLD.translation_revision_id, OLD.embedding_set_id, OLD.chunk_id) THEN
        RAISE EXCEPTION 'processing_row_identity_fixed'
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN target := OLD; ELSE target := NEW; END IF;
    SELECT status, source_job_id, source_lease_token INTO parent
      FROM server_embedding_sets
     WHERE owner_user_id = target.owner_user_id
       AND embedding_set_id = target.embedding_set_id
       FOR SHARE;
    IF FOUND THEN
        IF parent.status <> 'building' THEN
            RAISE EXCEPTION 'processing_revision_finalized'
                USING ERRCODE = 'check_violation';
        END IF;
        PERFORM server_processing_require_lease(
            target.owner_user_id, parent.source_job_id, parent.source_lease_token);
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_server_embedding_vectors_generation
    BEFORE INSERT OR UPDATE OR DELETE ON server_embedding_vectors
    FOR EACH ROW EXECUTE FUNCTION server_processing_guard_embedding_vectors();
