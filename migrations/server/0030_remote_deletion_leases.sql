ALTER TABLE server_deletion_requests
    ADD COLUMN lease_owner text NULL,
    ADD COLUMN lease_token uuid NULL,
    ADD COLUMN lease_expires_at timestamptz NULL,
    ADD COLUMN heartbeat_at timestamptz NULL,
    ADD COLUMN derived_deleted_at timestamptz NULL,
    ADD COLUMN source_deleted_at timestamptz NULL;

-- 기존 cleaning 행은 소유권 정보가 없어 다음 claim에서 새 세대로 회수한다.
CREATE INDEX server_deletion_expired_lease
    ON server_deletion_requests(lease_expires_at, request_id) WHERE status='cleaning';
ALTER TABLE server_deletion_requests ADD CONSTRAINT server_deletion_phase_order
    CHECK (source_deleted_at IS NULL OR derived_deleted_at IS NOT NULL);
