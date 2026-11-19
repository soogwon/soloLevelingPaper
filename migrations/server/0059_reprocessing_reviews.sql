-- 복구·재파싱은 기존 full_processing worker가 소비하지 않는 별도 접수 원장이다.
CREATE TABLE server_reprocessing_reviews (
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    request_key uuid NOT NULL,
    context_id uuid NOT NULL,
    operation text NOT NULL CHECK(operation IN ('translation_recovery','reparse')),
    input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
    snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
    contract jsonb NOT NULL CHECK(jsonb_typeof(contract)='object'),
    storage_consent_id text NULL,
    ai_consent_id text NULL,
    job_id uuid UNIQUE,
    state text NOT NULL DEFAULT 'review' CHECK(state IN ('review','queued','cancelled')),
    budget_reserved boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY(owner_user_id,request_key),
    FOREIGN KEY(owner_user_id,context_id) REFERENCES server_execution_contexts(owner_user_id,context_id) ON DELETE CASCADE,
    FOREIGN KEY(owner_user_id,storage_consent_id) REFERENCES server_remote_consents(owner_user_id,consent_id),
    FOREIGN KEY(owner_user_id,ai_consent_id) REFERENCES server_remote_consents(owner_user_id,consent_id),
    CHECK(expires_at>created_at),
    CHECK((state='queued')=budget_reserved),
    CHECK(state<>'queued' OR job_id IS NOT NULL)
);

-- 맥락 삭제로 접수 원장이 연쇄 정리되어도 미사용 예약을 잃지 않는다.
CREATE FUNCTION server_reprocessing_release_on_delete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE touched integer;
BEGIN
    IF OLD.budget_reserved THEN
        PERFORM 1 FROM server_dispatch_budget_limits
        WHERE policy_version=OLD.contract->>'policy_version' AND window_key=OLD.contract->>'window_key'
          AND ((scope_type='owner' AND scope_id=OLD.owner_user_id) OR (scope_type='service' AND scope_id='service'))
        ORDER BY scope_type,scope_id FOR UPDATE;
        UPDATE server_dispatch_budget_limits
        SET reserved_input_tokens=reserved_input_tokens-(OLD.contract->>'max_input_tokens')::bigint,
            reserved_output_tokens=reserved_output_tokens-(OLD.contract->>'max_output_tokens')::bigint
        WHERE policy_version=OLD.contract->>'policy_version' AND window_key=OLD.contract->>'window_key'
          AND ((scope_type='owner' AND scope_id=OLD.owner_user_id) OR (scope_type='service' AND scope_id='service'));
        GET DIAGNOSTICS touched=ROW_COUNT;
        IF touched<>2 THEN RAISE EXCEPTION 'reprocessing_budget_missing'; END IF;
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER server_reprocessing_delete_budget BEFORE DELETE ON server_reprocessing_reviews
FOR EACH ROW EXECUTE FUNCTION server_reprocessing_release_on_delete();
