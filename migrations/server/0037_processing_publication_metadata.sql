-- 원자 게시 시 staging의 문서·번역 설정과 출처 hash를 정규 revision에 보존한다.
ALTER TABLE server_parse_revisions
    ADD COLUMN document_metadata jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(document_metadata)='object');
ALTER TABLE server_processing_chunks
    ADD COLUMN printed_page_label text NULL;
ALTER TABLE server_translation_revisions
    ADD COLUMN translation_settings jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(translation_settings)='object');
ALTER TABLE server_processing_publications
    ADD COLUMN staging_payload_sha256 text NULL
        CHECK (staging_payload_sha256 IS NULL OR staging_payload_sha256 ~ '^[0-9a-f]{64}$');
