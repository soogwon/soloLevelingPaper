-- 제공자 전송 승인은 명시적 동의, 운영 제어, 사용자/서비스 예산을 한 트랜잭션에서 잠근다.
CREATE TABLE server_remote_consents (
    consent_id text NOT NULL,
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    purpose text NOT NULL CHECK (purpose IN ('storage', 'ai_provider')),
    input_hash text NOT NULL,
    notice_version text NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at timestamptz NULL,
    PRIMARY KEY (owner_user_id, consent_id),
    CHECK (length(consent_id) BETWEEN 1 AND 200),
    CHECK (length(input_hash) BETWEEN 1 AND 200),
    CHECK (length(notice_version) BETWEEN 1 AND 100)
);

CREATE TABLE server_dispatch_controls (
    control_id text PRIMARY KEY,
    generation bigint NOT NULL CHECK (generation >= 1),
    dispatch_enabled boolean NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (control_id = 'provider_dispatch')
);

CREATE TABLE server_dispatch_budget_limits (
    scope_type text NOT NULL CHECK (scope_type IN ('service', 'owner')),
    scope_id text NOT NULL,
    policy_version text NOT NULL,
    window_key text NOT NULL,
    max_input_tokens bigint NOT NULL CHECK (max_input_tokens >= 0),
    max_output_tokens bigint NOT NULL CHECK (max_output_tokens >= 0),
    reserved_input_tokens bigint NOT NULL DEFAULT 0 CHECK (reserved_input_tokens >= 0),
    reserved_output_tokens bigint NOT NULL DEFAULT 0 CHECK (reserved_output_tokens >= 0),
    consumed_input_tokens bigint NOT NULL DEFAULT 0 CHECK (consumed_input_tokens >= 0),
    consumed_output_tokens bigint NOT NULL DEFAULT 0 CHECK (consumed_output_tokens >= 0),
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scope_type, scope_id, policy_version, window_key),
    CHECK ((scope_type = 'service' AND scope_id = 'service') OR scope_type = 'owner')
);

CREATE TABLE server_provider_attempts (
    attempt_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    execution_id text NOT NULL,
    lease_token text NOT NULL,
    input_hash text NOT NULL,
    storage_consent_id text NOT NULL,
    ai_consent_id text NOT NULL,
    notice_version text NOT NULL,
    dispatch_key text NOT NULL,
    stage text NOT NULL CHECK (stage IN ('generation', 'repair', 'coverage', 'semantic_verification', 'translation')),
    policy_version text NOT NULL,
    window_key text NOT NULL,
    control_generation bigint NOT NULL,
    reserved_input_tokens bigint NOT NULL CHECK (reserved_input_tokens >= 1),
    reserved_output_tokens bigint NOT NULL CHECK (reserved_output_tokens >= 1),
    status text NOT NULL CHECK (status IN ('reserved', 'observed', 'unknown')),
    actual_input_tokens bigint NULL CHECK (actual_input_tokens >= 0),
    actual_output_tokens bigint NULL CHECK (actual_output_tokens >= 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settled_at timestamptz NULL,
    UNIQUE (owner_user_id, execution_id, dispatch_key),
    FOREIGN KEY (owner_user_id, storage_consent_id)
        REFERENCES server_remote_consents(owner_user_id, consent_id),
    FOREIGN KEY (owner_user_id, ai_consent_id)
        REFERENCES server_remote_consents(owner_user_id, consent_id),
    CHECK ((status = 'observed') = (actual_input_tokens IS NOT NULL AND actual_output_tokens IS NOT NULL)),
    CHECK ((status = 'reserved') = (settled_at IS NULL))
);

CREATE INDEX server_provider_attempts_execution_idx
    ON server_provider_attempts(owner_user_id, execution_id, created_at);

