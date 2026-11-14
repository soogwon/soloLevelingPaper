ALTER TABLE server_pdf_uploads
    ADD COLUMN IF NOT EXISTS completed_at timestamptz NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_server_pdf_uploads_active_version
    ON server_pdf_uploads(owner_user_id, paper_id, server_version_id)
    WHERE status IN ('pending', 'ready');

