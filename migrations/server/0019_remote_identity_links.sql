CREATE TABLE IF NOT EXISTS server_remote_identities (
    issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 2048 AND issuer = btrim(issuer)),
    subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 512 AND subject = btrim(subject)),
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    linked_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at timestamptz NULL,
    PRIMARY KEY (issuer, subject)
);

CREATE INDEX IF NOT EXISTS server_remote_identities_active_owner_idx
    ON server_remote_identities(owner_user_id, linked_at DESC)
    WHERE revoked_at IS NULL;
