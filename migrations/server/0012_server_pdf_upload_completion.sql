ALTER TABLE server_pdf_uploads
    ADD COLUMN IF NOT EXISTS completed_at timestamptz NULL;

-- 이전 버전에서 허용한 같은 원문 버전의 중복 예약을 정리한다.
WITH ranked AS (
    SELECT upload_id, row_number() OVER (
        PARTITION BY owner_user_id, paper_id, server_version_id
        ORDER BY (status='ready') DESC, expires_at DESC, upload_id
    ) AS priority
    FROM server_pdf_uploads WHERE status IN ('pending', 'ready')
)
UPDATE server_pdf_uploads u SET status='failed', failure_reason='reservation_superseded'
FROM ranked r WHERE u.upload_id=r.upload_id AND r.priority>1 AND u.status='pending';

CREATE UNIQUE INDEX IF NOT EXISTS uq_server_pdf_uploads_active_version
    ON server_pdf_uploads(owner_user_id, paper_id, server_version_id)
    WHERE status IN ('pending', 'ready');

