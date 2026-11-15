-- tombstone 뒤 파생 데이터 정리는 active publication pointer를 먼저 비워야 한다.
-- 해당 pointer 제거 외의 tombstone 대상 쓰기는 계속 거부한다.
CREATE OR REPLACE FUNCTION server_reject_tombstoned_paper() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    tombstoned_at timestamptz;
BEGIN
    SELECT p.deleted_at INTO tombstoned_at FROM server_papers p
      WHERE p.owner_user_id=NEW.owner_user_id AND p.paper_id=NEW.paper_id FOR SHARE;
    IF tombstoned_at IS NOT NULL THEN
        IF TG_TABLE_NAME = 'server_processing_versions' AND TG_OP = 'UPDATE' THEN
          IF OLD.active_publication_id IS NOT NULL AND NEW.active_publication_id IS NULL
           AND NEW.owner_user_id = OLD.owner_user_id
           AND NEW.paper_id = OLD.paper_id
           AND NEW.server_version_id = OLD.server_version_id
           AND NEW.created_at = OLD.created_at THEN
            RETURN NEW;
          END IF;
        END IF;
        RAISE EXCEPTION 'paper_tombstoned' USING ERRCODE='check_violation';
    END IF;
    RETURN NEW;
END $$;
