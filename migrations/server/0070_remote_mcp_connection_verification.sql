CREATE TABLE server_remote_connection_checks (
    owner_user_id text PRIMARY KEY REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    check_id uuid NOT NULL,
    code_hash text NOT NULL UNIQUE CHECK (code_hash ~ '^[0-9a-f]{64}$'),
    expires_at timestamptz NOT NULL,
    verified_at timestamptz
);
