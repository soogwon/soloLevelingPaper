-- staging은 검색/게시 테이블과 분리된 불변 결과다. 논문/job 삭제 시 함께 정리한다.
CREATE TABLE server_processing_result_staging (
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    job_id uuid NOT NULL,
    source_lease_token uuid NOT NULL,
    payload_version integer NOT NULL CHECK (payload_version = 1),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_user_id, job_id, source_lease_token),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, job_id)
        REFERENCES server_processing_jobs(owner_user_id, paper_id, server_version_id, job_id)
        ON DELETE CASCADE
);

CREATE FUNCTION server_guard_processing_staging() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'processing_staging_immutable' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM 1 FROM server_papers
      WHERE owner_user_id=NEW.owner_user_id AND paper_id=NEW.paper_id AND deleted_at IS NULL
      FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'processing_staging_not_allowed' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM 1 FROM server_processing_jobs
      WHERE owner_user_id=NEW.owner_user_id AND job_id=NEW.job_id
        AND paper_id=NEW.paper_id AND server_version_id=NEW.server_version_id
        AND status='running' AND lease_token=NEW.source_lease_token
        AND lease_expires_at>clock_timestamp() FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'processing_staging_not_allowed' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_server_processing_result_staging_guard
    BEFORE INSERT OR UPDATE ON server_processing_result_staging
    FOR EACH ROW EXECUTE FUNCTION server_guard_processing_staging();
