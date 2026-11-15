ALTER TABLE server_pdf_uploads
    ADD COLUMN source_kind text NOT NULL DEFAULT 'local' CHECK (source_kind IN ('local', 'remote')),
    ALTER COLUMN local_version_id DROP NOT NULL,
    ADD CONSTRAINT ck_server_pdf_uploads_source CHECK (
        (source_kind='local' AND local_version_id IS NOT NULL)
        OR (source_kind='remote' AND local_version_id IS NULL)
    );
