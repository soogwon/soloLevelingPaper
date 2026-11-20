-- Claude에서 작성한 요약의 검토와 저장은 실행·예산 원장을 사용하지 않는다.
CREATE TABLE server_chat_summaries (
    owner_user_id text NOT NULL,
    request_key uuid NOT NULL,
    context_id uuid NOT NULL,
    snapshot jsonb NOT NULL CHECK (
        jsonb_typeof(snapshot) = 'object' AND octet_length(snapshot::text) <= 16384),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    saved_at timestamptz,
    PRIMARY KEY (owner_user_id, request_key),
    FOREIGN KEY (owner_user_id, context_id)
        REFERENCES server_execution_contexts (owner_user_id, context_id) ON DELETE CASCADE
);
CREATE INDEX server_chat_summaries_latest
    ON server_chat_summaries (owner_user_id, context_id, saved_at DESC, request_key DESC)
    WHERE saved_at IS NOT NULL;
