-- 요청 키 복구와 provider 전송 scope가 같은 영속 실행을 가리킨다.
CREATE TABLE server_remote_executions (
    execution_id text NOT NULL,
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    operation text NOT NULL,
    request_key text NOT NULL,
    input_hash text NOT NULL,
    context_id uuid NOT NULL,
    storage_consent_id text NOT NULL,
    ai_consent_id text NOT NULL,
    notice_version text NOT NULL,
    state text NOT NULL CHECK (state IN (
        'queued', 'running', 'completed', 'failed', 'unknown', 'cancel_requested', 'cancelled'
    )),
    budget_state text NOT NULL CHECK (budget_state IN ('available', 'limited', 'usage_unknown')),
    lease_token text NULL,
    lease_expires_at timestamptz NULL,
    lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    payload_json text NULL,
    payload_sha256 text NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at timestamptz NULL,
    PRIMARY KEY (owner_user_id, execution_id),
    UNIQUE (owner_user_id, operation, request_key),
    FOREIGN KEY (owner_user_id, context_id)
        REFERENCES server_execution_contexts(owner_user_id, context_id),
    FOREIGN KEY (owner_user_id, storage_consent_id)
        REFERENCES server_remote_consents(owner_user_id, consent_id),
    FOREIGN KEY (owner_user_id, ai_consent_id)
        REFERENCES server_remote_consents(owner_user_id, consent_id),
    CHECK ((payload_json IS NULL) = (payload_sha256 IS NULL)),
    CHECK ((state = 'running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK ((state IN ('completed', 'failed', 'cancelled')) = (finished_at IS NOT NULL)),
    CHECK (length(execution_id) BETWEEN 1 AND 100),
    CHECK (length(operation) BETWEEN 1 AND 80),
    CHECK (length(request_key) BETWEEN 1 AND 200)
);

ALTER TABLE server_provider_attempts
    ADD CONSTRAINT fk_server_provider_attempts_execution
    FOREIGN KEY (owner_user_id, execution_id)
    REFERENCES server_remote_executions(owner_user_id, execution_id);

CREATE INDEX server_remote_executions_context_idx
    ON server_remote_executions(owner_user_id, context_id, created_at);

CREATE FUNCTION server_reject_tombstoned_execution() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state NOT IN ('cancel_requested', 'cancelled', 'failed') AND EXISTS (
        SELECT 1 FROM server_execution_contexts c
        JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
        WHERE c.owner_user_id=NEW.owner_user_id AND c.context_id=NEW.context_id
          AND p.deleted_at IS NOT NULL
        FOR SHARE OF p
    ) THEN
        RAISE EXCEPTION 'paper_tombstoned' USING ERRCODE='check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_remote_executions_reject_tombstone
    BEFORE INSERT OR UPDATE ON server_remote_executions
    FOR EACH ROW EXECUTE FUNCTION server_reject_tombstoned_execution();

