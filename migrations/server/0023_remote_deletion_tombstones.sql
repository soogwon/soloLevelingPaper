ALTER TABLE server_papers ADD COLUMN deleted_at timestamptz NULL;

CREATE TABLE server_deletion_requests (
    request_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    request_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('blocking', 'cleaning', 'complete', 'failed')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_user_id, request_key),
    UNIQUE (owner_user_id, paper_id),
    FOREIGN KEY (paper_id, owner_user_id) REFERENCES server_papers(paper_id, owner_user_id)
);

CREATE FUNCTION server_reject_tombstoned_paper() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    tombstoned_at timestamptz;
BEGIN
    -- 삭제와 후발 쓰기를 같은 논문 행 잠금으로 직렬화한다.
    SELECT p.deleted_at INTO tombstoned_at FROM server_papers p
      WHERE p.owner_user_id=NEW.owner_user_id AND p.paper_id=NEW.paper_id FOR SHARE;
    IF tombstoned_at IS NOT NULL THEN
        RAISE EXCEPTION 'paper_tombstoned' USING ERRCODE='check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_jobs_reject_tombstone BEFORE INSERT OR UPDATE ON server_processing_jobs
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_versions_reject_tombstone BEFORE INSERT OR UPDATE ON server_processing_versions
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_parse_reject_tombstone BEFORE INSERT OR UPDATE ON server_parse_revisions
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_chunks_reject_tombstone BEFORE INSERT OR UPDATE ON server_processing_chunks
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_translation_reject_tombstone BEFORE INSERT OR UPDATE ON server_translation_revisions
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_translation_chunks_reject_tombstone BEFORE INSERT OR UPDATE ON server_translation_chunks
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_embedding_reject_tombstone BEFORE INSERT OR UPDATE ON server_embedding_sets
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_vectors_reject_tombstone BEFORE INSERT OR UPDATE ON server_embedding_vectors
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_publication_reject_tombstone BEFORE INSERT OR UPDATE ON server_processing_publications
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_context_reject_tombstone BEFORE INSERT OR UPDATE ON server_execution_contexts
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_evidence_reject_tombstone BEFORE INSERT OR UPDATE ON server_processing_evidences
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_upload_reject_tombstone BEFORE INSERT OR UPDATE ON server_pdf_uploads
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
CREATE TRIGGER trg_upload_intent_reject_tombstone BEFORE INSERT OR UPDATE ON server_remote_upload_intents
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();
