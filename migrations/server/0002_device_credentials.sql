CREATE TABLE IF NOT EXISTS server_device_credentials (
    credential_id uuid PRIMARY KEY,
    family_id uuid NOT NULL,
    device_id uuid NOT NULL REFERENCES server_devices(device_id) ON DELETE CASCADE,
    access_token_hash bytea NOT NULL UNIQUE,
    access_expires_at timestamptz NOT NULL,
    refresh_token_hash bytea NOT NULL UNIQUE,
    refresh_expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at timestamptz NULL,
    CHECK (access_expires_at <= refresh_expires_at)
);

CREATE INDEX IF NOT EXISTS server_device_credentials_active_device_idx
    ON server_device_credentials (device_id, created_at DESC)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS server_device_credentials_family_idx
    ON server_device_credentials (family_id);
