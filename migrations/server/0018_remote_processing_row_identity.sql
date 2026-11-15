-- chunk·번역 행의 소속과 식별자는 만든 뒤 바꿀 수 없다.
-- 0016·0017의 행 트리거는 UPDATE에서 NEW의 부모 revision만 검사했다. 그래서 확정된 revision의
-- 행을 building revision으로 옮기면 통과했고, 게시된 집합에서 행이 빠질 수 있었다.
-- 소속 변경 자체를 금지하면 OLD와 NEW의 부모가 항상 같아 한 번의 검사로 양쪽을 모두 보호한다.
-- 부모 잠금과 연쇄 삭제 허용은 그대로다.

CREATE OR REPLACE FUNCTION server_processing_guard_parse_rows() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target record;
    parent record;
BEGIN
    IF TG_OP = 'UPDATE' AND
       (NEW.owner_user_id, NEW.paper_id, NEW.server_version_id, NEW.parse_revision_id, NEW.chunk_id)
       IS DISTINCT FROM
       (OLD.owner_user_id, OLD.paper_id, OLD.server_version_id, OLD.parse_revision_id, OLD.chunk_id) THEN
        RAISE EXCEPTION 'processing_row_identity_fixed'
            USING ERRCODE = 'check_violation';
    END IF;
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
    IF TG_OP = 'UPDATE' AND
       (NEW.owner_user_id, NEW.paper_id, NEW.server_version_id, NEW.parse_revision_id, NEW.translation_revision_id, NEW.chunk_id)
       IS DISTINCT FROM
       (OLD.owner_user_id, OLD.paper_id, OLD.server_version_id, OLD.parse_revision_id, OLD.translation_revision_id, OLD.chunk_id) THEN
        RAISE EXCEPTION 'processing_row_identity_fixed'
            USING ERRCODE = 'check_violation';
    END IF;
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
