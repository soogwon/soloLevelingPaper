-- 입력 계수도 외부 전송이다. 실행마다 한 번 예약하고 불명확한 전송을 재시도하지 않는다.
CREATE TABLE server_question_token_counts (
    owner_user_id text NOT NULL,
    execution_id text NOT NULL,
    lease_token text NOT NULL,
    profile text NOT NULL,
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('reserved','observed','unknown')),
    input_tokens bigint NULL CHECK (input_tokens > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(owner_user_id,execution_id),
    FOREIGN KEY(owner_user_id,execution_id) REFERENCES server_remote_executions(owner_user_id,execution_id) ON DELETE CASCADE,
    CHECK ((status='observed') = (input_tokens IS NOT NULL))
);
