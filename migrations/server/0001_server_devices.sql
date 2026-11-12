CREATE TABLE IF NOT EXISTS server_devices (
    device_id uuid PRIMARY KEY,
    owner_user_id text NOT NULL,
    display_name varchar(100) NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 100),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at timestamptz NULL
);

CREATE INDEX IF NOT EXISTS server_devices_active_owner_idx
    ON server_devices (owner_user_id, created_at DESC, device_id DESC)
    WHERE revoked_at IS NULL;
