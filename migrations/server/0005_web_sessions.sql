CREATE TABLE IF NOT EXISTS server_users (
    owner_user_id text PRIMARY KEY,
    display_name varchar(200) NOT NULL,
    email varchar(320) NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS web_login_attempts (
    state_hash bytea PRIMARY KEY,
    code_verifier text NOT NULL,
    return_to text NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS web_sessions (
    session_hash bytea PRIMARY KEY,
    csrf_hash bytea NOT NULL,
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at timestamptz NULL
);
CREATE INDEX IF NOT EXISTS idx_web_sessions_active_user
    ON web_sessions(owner_user_id, expires_at DESC) WHERE revoked_at IS NULL;
