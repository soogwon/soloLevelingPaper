-- 위치는 고정 parse의 PDF 페이지이며 논문 인쇄 페이지와 구분한다.
ALTER TABLE server_remote_learning_records
    ADD COLUMN last_pdf_page integer NULL CHECK (last_pdf_page > 0);

CREATE TABLE server_remote_learning_requests (
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    request_key uuid NOT NULL,
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    applied_revision bigint NOT NULL CHECK (applied_revision > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(owner_user_id, context_id, request_key),
    FOREIGN KEY(owner_user_id, context_id)
        REFERENCES server_remote_learning_records(owner_user_id, context_id) ON DELETE CASCADE
);
