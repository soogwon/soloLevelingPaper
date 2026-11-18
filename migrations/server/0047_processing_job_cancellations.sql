-- heartbeat는 취소 판단을 무효화하지 않고, 상태·실행 세대 변경만 revision을 증가시킨다.
ALTER TABLE server_processing_jobs
    ADD COLUMN revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0);

CREATE FUNCTION server_processing_job_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.status, NEW.attempt, NEW.lease_token) IS DISTINCT FROM
       (OLD.status, OLD.attempt, OLD.lease_token) THEN
        NEW.revision := OLD.revision + 1;
    ELSE
        NEW.revision := OLD.revision;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_processing_job_revision BEFORE UPDATE ON server_processing_jobs
FOR EACH ROW EXECUTE FUNCTION server_processing_job_revision();

CREATE TABLE server_processing_cancel_requests (
    owner_user_id text NOT NULL,
    request_key uuid NOT NULL,
    job_id uuid NOT NULL,
    expected_revision bigint NOT NULL CHECK (expected_revision > 0),
    applied_revision bigint NOT NULL CHECK (applied_revision > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, request_key),
    FOREIGN KEY (owner_user_id, job_id)
        REFERENCES server_processing_jobs(owner_user_id, job_id) ON DELETE CASCADE
);
