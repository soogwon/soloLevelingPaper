-- 퀴즈 입력은 질문 입력과 분리하며 같은 실행의 고정 snapshot을 변경하지 않는다.
CREATE TABLE server_quiz_generation_inputs (
    owner_user_id text NOT NULL,
    execution_id text NOT NULL,
    input_snapshot jsonb NOT NULL CHECK (
        jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=1048576),
    PRIMARY KEY(owner_user_id,execution_id),
    FOREIGN KEY(owner_user_id,execution_id)
        REFERENCES server_remote_executions(owner_user_id,execution_id) ON DELETE CASCADE
);
CREATE FUNCTION server_guard_quiz_generation_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e record;
BEGIN
    IF TG_OP='UPDATE' THEN
        RAISE EXCEPTION 'quiz_generation_input_immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO e FROM server_remote_executions
        WHERE owner_user_id=NEW.owner_user_id AND execution_id=NEW.execution_id;
    IF NOT FOUND OR e.operation<>'generate_quiz' OR e.policy_snapshot IS NULL
       OR e.notice_version<>'remote-quiz-generation-v1' OR e.state<>'queued'
       OR NEW.input_snapshot->>'operation' IS DISTINCT FROM e.operation
       OR NEW.input_snapshot->>'notice_version' IS DISTINCT FROM e.notice_version
       OR NEW.input_snapshot->'binding'->>'owner_user_id' IS DISTINCT FROM e.owner_user_id
       OR NEW.input_snapshot->'binding'->>'context_id' IS DISTINCT FROM e.context_id::text THEN
        RAISE EXCEPTION 'quiz_generation_input_scope_invalid' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER server_quiz_generation_input_guard BEFORE INSERT OR UPDATE ON server_quiz_generation_inputs
    FOR EACH ROW EXECUTE FUNCTION server_guard_quiz_generation_input();
