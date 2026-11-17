-- 질문 접수 시 전체 상한을 확보하고 물리 전송 예약은 이 안에서 나눈다.
CREATE TABLE server_question_budget_reservations (
    owner_user_id text NOT NULL,
    execution_id text NOT NULL,
    policy_version text NOT NULL,
    window_key text NOT NULL,
    remaining_input_tokens bigint NOT NULL CHECK (remaining_input_tokens>=0),
    remaining_output_tokens bigint NOT NULL CHECK (remaining_output_tokens>=0),
    PRIMARY KEY(owner_user_id,execution_id),
    FOREIGN KEY(owner_user_id,execution_id) REFERENCES server_remote_executions(owner_user_id,execution_id) ON DELETE CASCADE
);
CREATE FUNCTION server_release_question_budget() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
    IF TG_OP='DELETE' OR NEW.state IN ('completed','failed','cancelled','cancel_requested') THEN
        SELECT * INTO r FROM server_question_budget_reservations
            WHERE owner_user_id=OLD.owner_user_id AND execution_id=OLD.execution_id FOR UPDATE;
        IF FOUND THEN
            -- 이미 전송된 attempt 예약은 여기서 반환하지 않는다.
            UPDATE server_dispatch_budget_limits SET
                reserved_input_tokens=reserved_input_tokens-r.remaining_input_tokens,
                reserved_output_tokens=reserved_output_tokens-r.remaining_output_tokens,
                updated_at=clock_timestamp()
            WHERE policy_version=r.policy_version AND window_key=r.window_key AND
                ((scope_type='service' AND scope_id='service') OR (scope_type='owner' AND scope_id=r.owner_user_id));
            UPDATE server_question_budget_reservations SET remaining_input_tokens=0,remaining_output_tokens=0
                WHERE owner_user_id=r.owner_user_id AND execution_id=r.execution_id;
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER server_release_question_budget_before_change
    BEFORE UPDATE OR DELETE ON server_remote_executions FOR EACH ROW EXECUTE FUNCTION server_release_question_budget();
