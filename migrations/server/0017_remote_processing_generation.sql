-- 처리 결과의 저장을 실행 세대(job lease)에 묶고, 한 작업이 두 번 게시되지 않게 한다.

-- 1. revision·embedding set은 자신을 만든 job과 그때의 lease token을 고정해 가진다.
--    FK는 commit 시점에 검사한다. 논문 삭제의 연쇄 삭제에서 job과 revision이 지워지는 순서에
--    기대지 않기 위해서다. 없는 job을 내세운 쓰기는 아래 트리거가 먼저 거부한다.
ALTER TABLE server_processing_jobs
    ADD CONSTRAINT uq_server_processing_jobs_version_job
    UNIQUE (owner_user_id, paper_id, server_version_id, job_id);

ALTER TABLE server_parse_revisions
    ADD COLUMN source_job_id uuid NULL,
    ADD COLUMN source_lease_token uuid NULL,
    ADD CONSTRAINT fk_server_parse_revisions_source_job
        FOREIGN KEY (owner_user_id, paper_id, server_version_id, source_job_id)
        REFERENCES server_processing_jobs(owner_user_id, paper_id, server_version_id, job_id)
        DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE server_translation_revisions
    ADD COLUMN source_job_id uuid NULL,
    ADD COLUMN source_lease_token uuid NULL,
    ADD CONSTRAINT fk_server_translation_revisions_source_job
        FOREIGN KEY (owner_user_id, paper_id, server_version_id, source_job_id)
        REFERENCES server_processing_jobs(owner_user_id, paper_id, server_version_id, job_id)
        DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE server_embedding_sets
    ADD COLUMN source_job_id uuid NULL,
    ADD COLUMN source_lease_token uuid NULL,
    ADD CONSTRAINT fk_server_embedding_sets_source_job
        FOREIGN KEY (owner_user_id, paper_id, server_version_id, source_job_id)
        REFERENCES server_processing_jobs(owner_user_id, paper_id, server_version_id, job_id)
        DEFERRABLE INITIALLY DEFERRED;

-- 2. 한 job은 publication을 하나만 만든다. 게시 뒤 응답이 유실돼도 같은 결과를 재생한다.
CREATE UNIQUE INDEX uq_server_processing_publications_source_job
    ON server_processing_publications(owner_user_id, source_job_id)
    WHERE source_job_id IS NOT NULL;

-- 3. 현재 실행 세대인지 확인한다. job 행을 FOR SHARE로 읽어 회수(claim)와 엇갈리지 않게 한다.
CREATE FUNCTION server_processing_require_lease(p_owner text, p_job uuid, p_token uuid)
RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF p_job IS NULL OR p_token IS NULL THEN
        RAISE EXCEPTION 'processing_revision_requires_job'
            USING ERRCODE = 'check_violation';
    END IF;
    PERFORM 1
       FROM server_processing_jobs
      WHERE owner_user_id = p_owner
        AND job_id = p_job
        AND status = 'running'
        AND lease_token = p_token
        AND lease_expires_at > CURRENT_TIMESTAMP
        FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'processing_lease_lost'
            USING ERRCODE = 'check_violation';
    END IF;
END;
$$;

-- 4. revision·embedding set: building으로만 만들 수 있고, 만들 때와 building·ready로 저장할 때
--    현재 실행 세대여야 한다. failed·cancelled로 닫는 정리는 lease 없이도 허용한다.
CREATE FUNCTION server_processing_guard_revision_generation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'building' THEN
            RAISE EXCEPTION 'processing_revision_must_start_building'
                USING ERRCODE = 'check_violation';
        END IF;
        PERFORM server_processing_require_lease(
            NEW.owner_user_id, NEW.source_job_id, NEW.source_lease_token);
        RETURN NEW;
    END IF;
    IF OLD.status <> 'building' THEN
        RAISE EXCEPTION 'processing_revision_finalized'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.source_job_id IS DISTINCT FROM OLD.source_job_id
       OR NEW.source_lease_token IS DISTINCT FROM OLD.source_lease_token THEN
        RAISE EXCEPTION 'processing_revision_generation_fixed'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IN ('building', 'ready') THEN
        PERFORM server_processing_require_lease(
            OLD.owner_user_id, OLD.source_job_id, OLD.source_lease_token);
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER trg_server_parse_revisions_finalized ON server_parse_revisions;
DROP TRIGGER trg_server_translation_revisions_finalized ON server_translation_revisions;
DROP TRIGGER trg_server_embedding_sets_finalized ON server_embedding_sets;
DROP FUNCTION server_processing_guard_finalized_revision();

CREATE TRIGGER trg_server_parse_revisions_generation
    BEFORE INSERT OR UPDATE ON server_parse_revisions
    FOR EACH ROW EXECUTE FUNCTION server_processing_guard_revision_generation();

CREATE TRIGGER trg_server_translation_revisions_generation
    BEFORE INSERT OR UPDATE ON server_translation_revisions
    FOR EACH ROW EXECUTE FUNCTION server_processing_guard_revision_generation();

CREATE TRIGGER trg_server_embedding_sets_generation
    BEFORE INSERT OR UPDATE ON server_embedding_sets
    FOR EACH ROW EXECUTE FUNCTION server_processing_guard_revision_generation();

-- 5. chunk·번역 행: 소속 revision이 building이고, 그 revision을 만든 실행 세대가 아직 유효해야 한다.
--    부모가 이미 지워진 경우(논문 삭제의 연쇄 삭제)는 허용한다.
CREATE OR REPLACE FUNCTION server_processing_guard_parse_rows() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target record;
    parent record;
BEGIN
    IF TG_OP = 'DELETE' THEN target := OLD; ELSE target := NEW; END IF;
    SELECT status, source_job_id, source_lease_token INTO parent
      FROM server_parse_revisions
     WHERE owner_user_id = target.owner_user_id
       AND parse_revision_id = target.parse_revision_id
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

CREATE OR REPLACE FUNCTION server_processing_guard_translation_rows() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target record;
    parent record;
BEGIN
    IF TG_OP = 'DELETE' THEN target := OLD; ELSE target := NEW; END IF;
    SELECT status, source_job_id, source_lease_token INTO parent
      FROM server_translation_revisions
     WHERE owner_user_id = target.owner_user_id
       AND translation_revision_id = target.translation_revision_id
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
