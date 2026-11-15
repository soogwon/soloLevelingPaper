ALTER TABLE server_deletion_requests
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    ADD COLUMN last_error_code text NULL CHECK (last_error_code IS NULL OR length(last_error_code) BETWEEN 1 AND 80),
    ADD COLUMN completed_at timestamptz NULL;

ALTER TABLE server_deletion_requests ADD CONSTRAINT server_deletion_completion_consistent CHECK (
    (status = 'complete' AND completed_at IS NOT NULL)
    OR (status <> 'complete' AND completed_at IS NULL)
);

CREATE INDEX server_deletion_cleanup_queue
    ON server_deletion_requests(updated_at, request_id)
    WHERE status IN ('blocking', 'failed');
