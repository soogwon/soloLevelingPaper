-- 검증된 웹 업로드도 동일 소유자·고정 판본·파일 해시에서 출처로 인정한다.
-- 기존 함수의 소유자와 호출 권한을 유지한다.
CREATE OR REPLACE FUNCTION server_lock_insight_import(p_owner text, p_device uuid, p_context uuid,
                                          p_local_version text, p_sha256 text)
RETURNS TABLE(paper_id uuid, server_version_id text, publication_id uuid, parse_revision_id uuid,
              translation_revision_id uuid, embedding_set_id uuid)
LANGUAGE SQL SECURITY DEFINER SET search_path = pg_catalog, pg_temp
BEGIN ATOMIC
    SELECT c.paper_id,c.server_version_id,c.publication_id,c.parse_revision_id,
           p.translation_revision_id,p.embedding_set_id
    FROM server_execution_contexts c
    JOIN server_papers paper USING(owner_user_id,paper_id)
    JOIN server_processing_publications p USING(owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id)
    JOIN server_devices d ON d.owner_user_id=c.owner_user_id AND d.device_id=p_device
    JOIN server_pdf_uploads u ON u.owner_user_id=c.owner_user_id AND u.paper_id=c.paper_id
        AND u.server_version_id=c.server_version_id
    WHERE c.owner_user_id=p_owner AND c.context_id=p_context AND paper.deleted_at IS NULL AND d.revoked_at IS NULL
      AND u.sha256_hex=p_sha256 AND u.status='ready'
      AND (
          (u.source_kind='local' AND u.local_version_id=p_local_version)
          OR (u.source_kind='remote' AND EXISTS (
              SELECT 1 FROM server_remote_upload_intents i
              WHERE i.owner_user_id=u.owner_user_id AND i.paper_id=u.paper_id
                AND i.server_version_id=u.server_version_id AND i.upload_id=u.upload_id
                AND i.sha256_hex=u.sha256_hex AND i.status='verified'
          ))
      )
    FOR SHARE OF paper,d,u;
END;
