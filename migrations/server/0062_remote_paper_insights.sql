-- 검증된 원천 묶음과 요청 이력은 대상 맥락과 함께 삭제한다.
CREATE TABLE server_remote_insight_collections (
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    device_id uuid NOT NULL REFERENCES server_devices(device_id),
    source_collection_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    source jsonb NOT NULL CHECK (jsonb_typeof(source) = 'object'),
    items jsonb NOT NULL CHECK (jsonb_typeof(items) = 'array' AND jsonb_array_length(items) BETWEEN 1 AND 20),
    PRIMARY KEY (owner_user_id, context_id, device_id, source_collection_id),
    FOREIGN KEY (owner_user_id, context_id)
        REFERENCES server_execution_contexts(owner_user_id, context_id) ON DELETE CASCADE
);

CREATE TABLE server_remote_insight_requests (
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    device_id uuid NOT NULL,
    request_key uuid NOT NULL,
    source_collection_id text NOT NULL,
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    applied_revision integer NOT NULL CHECK (applied_revision > 0),
    PRIMARY KEY (owner_user_id, context_id, device_id, request_key),
    FOREIGN KEY (owner_user_id, context_id, device_id, source_collection_id)
        REFERENCES server_remote_insight_collections(owner_user_id, context_id, device_id, source_collection_id)
        ON DELETE CASCADE
);

-- 원천의 삭제·폐기와 이관을 직렬화한다. 이관 역할에 원천 UPDATE를 부여하지 않는다.
-- SQL 표준 본문은 생성 시 관계를 해석하므로 호출자의 search_path로 대체할 수 없다.
CREATE FUNCTION server_lock_insight_import(p_owner text, p_device uuid, p_context uuid,
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
      AND u.local_version_id=p_local_version AND u.sha256_hex=p_sha256 AND u.source_kind='local' AND u.status='ready'
    FOR SHARE OF paper,d,u;
END;
REVOKE ALL ON FUNCTION server_lock_insight_import(text, uuid, uuid, text, text) FROM PUBLIC;
