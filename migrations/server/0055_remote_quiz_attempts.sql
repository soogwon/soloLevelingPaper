-- 검수 퀴즈 판본은 덮어쓰지 않고 시도는 제출 키별로 한 번 저장한다.
CREATE TABLE server_remote_quizzes (
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    quiz_id uuid NOT NULL,
    step text NOT NULL CHECK(step IN ('preview','structure','deep_read','review')),
    snapshot jsonb NOT NULL,
    snapshot_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(owner_user_id,context_id,quiz_id),
    FOREIGN KEY(owner_user_id,context_id) REFERENCES server_execution_contexts(owner_user_id,context_id) ON DELETE CASCADE
);
CREATE TABLE server_remote_quiz_attempts (
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    request_key uuid NOT NULL,
    quiz_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    input_hash text NOT NULL,
    payload jsonb NOT NULL,
    correct_count integer NOT NULL CHECK(correct_count>=0),
    total_count integer NOT NULL CHECK(total_count>0 AND correct_count<=total_count),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(owner_user_id,context_id,request_key),
    UNIQUE(owner_user_id,context_id,attempt_id),
    FOREIGN KEY(owner_user_id,context_id,quiz_id) REFERENCES server_remote_quizzes(owner_user_id,context_id,quiz_id) ON DELETE CASCADE
);
CREATE FUNCTION server_quiz_snapshot_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'quiz_snapshot_immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER server_quiz_snapshot_immutable BEFORE UPDATE ON server_remote_quizzes
    FOR EACH ROW EXECUTE FUNCTION server_quiz_snapshot_immutable();
