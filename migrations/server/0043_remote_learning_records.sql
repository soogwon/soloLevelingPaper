-- 원격 학습은 로컬 기기·동기화 기록을 가장하지 않고 고정 context에 귀속된다.
CREATE TABLE server_remote_learning_records (
    learning_record_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    publication_id uuid NOT NULL,
    parse_revision_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'reading' CHECK (status IN ('reading','completed')),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, context_id),
    FOREIGN KEY(owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id,context_id)
        REFERENCES server_execution_contexts(owner_user_id,paper_id,server_version_id,
                                            publication_id,parse_revision_id,context_id)
        ON DELETE CASCADE
);
CREATE TRIGGER trg_remote_learning_reject_tombstone BEFORE INSERT OR UPDATE ON server_remote_learning_records
FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_paper();

CREATE FUNCTION server_remote_learning_fixed_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.learning_record_id,NEW.owner_user_id,NEW.context_id,NEW.paper_id,
           NEW.server_version_id,NEW.publication_id,NEW.parse_revision_id)
       IS DISTINCT FROM
       ROW(OLD.learning_record_id,OLD.owner_user_id,OLD.context_id,OLD.paper_id,
           OLD.server_version_id,OLD.publication_id,OLD.parse_revision_id) THEN
        RAISE EXCEPTION 'remote_learning_binding_immutable' USING ERRCODE='check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_remote_learning_fixed_binding BEFORE UPDATE ON server_remote_learning_records
FOR EACH ROW EXECUTE FUNCTION server_remote_learning_fixed_binding();
