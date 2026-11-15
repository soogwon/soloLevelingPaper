-- 0014·0015 검토 보완: 논문 삭제 경로와 확정된 처리 결과의 불변성을 DB에서 보장한다.

-- 1. 논문(처리 버전) 삭제가 게시·context·근거까지 함께 지우도록 삭제 경로를 둔다.
--    revision 쪽 FK는 NO ACTION으로 남겨, 참조 중인 revision만 따로 지우는 것은 계속 거부한다.
ALTER TABLE server_processing_publications
    ADD CONSTRAINT fk_server_processing_publications_version
    FOREIGN KEY (owner_user_id, paper_id, server_version_id)
    REFERENCES server_processing_versions(owner_user_id, paper_id, server_version_id)
    ON DELETE CASCADE;

ALTER TABLE server_execution_contexts
    ADD CONSTRAINT fk_server_execution_contexts_version
    FOREIGN KEY (owner_user_id, paper_id, server_version_id)
    REFERENCES server_processing_versions(owner_user_id, paper_id, server_version_id)
    ON DELETE CASCADE;

ALTER TABLE server_processing_evidences
    ADD CONSTRAINT fk_server_processing_evidences_version
    FOREIGN KEY (owner_user_id, paper_id, server_version_id)
    REFERENCES server_processing_versions(owner_user_id, paper_id, server_version_id)
    ON DELETE CASCADE;

-- 2. building을 벗어난 revision·embedding set은 다시 바꿀 수 없다.
CREATE FUNCTION server_processing_guard_finalized_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'building' THEN
        RAISE EXCEPTION 'processing_revision_finalized'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_server_parse_revisions_finalized
    BEFORE UPDATE ON server_parse_revisions
    FOR EACH ROW EXECUTE FUNCTION server_processing_guard_finalized_revision();

CREATE TRIGGER trg_server_translation_revisions_finalized
    BEFORE UPDATE ON server_translation_revisions
    FOR EACH ROW EXECUTE FUNCTION server_processing_guard_finalized_revision();

CREATE TRIGGER trg_server_embedding_sets_finalized
    BEFORE UPDATE ON server_embedding_sets
    FOR EACH ROW EXECUTE FUNCTION server_processing_guard_finalized_revision();

-- 3. chunk와 번역 행은 소속 revision이 building일 때만 추가·변경·삭제할 수 있다.
--    부모 revision을 FOR SHARE로 읽어 ready 전환과 늦은 쓰기가 엇갈리지 않게 한다.
--    부모가 이미 지워진 경우(논문 삭제의 연쇄 삭제)는 허용한다.
CREATE FUNCTION server_processing_guard_parse_rows() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target record;
    parent_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN target := OLD; ELSE target := NEW; END IF;
    SELECT status INTO parent_status
      FROM server_parse_revisions
     WHERE owner_user_id = target.owner_user_id
       AND parse_revision_id = target.parse_revision_id
       FOR SHARE;
    IF FOUND AND parent_status <> 'building' THEN
        RAISE EXCEPTION 'processing_revision_finalized'
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION server_processing_guard_translation_rows() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target record;
    parent_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN target := OLD; ELSE target := NEW; END IF;
    SELECT status INTO parent_status
      FROM server_translation_revisions
     WHERE owner_user_id = target.owner_user_id
       AND translation_revision_id = target.translation_revision_id
       FOR SHARE;
    IF FOUND AND parent_status <> 'building' THEN
        RAISE EXCEPTION 'processing_revision_finalized'
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_server_processing_chunks_finalized
    BEFORE INSERT OR UPDATE OR DELETE ON server_processing_chunks
    FOR EACH ROW EXECUTE FUNCTION server_processing_guard_parse_rows();

CREATE TRIGGER trg_server_translation_chunks_finalized
    BEFORE INSERT OR UPDATE OR DELETE ON server_translation_chunks
    FOR EACH ROW EXECUTE FUNCTION server_processing_guard_translation_rows();
