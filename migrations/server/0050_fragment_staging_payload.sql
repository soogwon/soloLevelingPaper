-- 기존 payload를 그대로 읽고, 토큰 하위 조각 메타데이터를 포함한 버전만 추가한다.
ALTER TABLE server_processing_result_staging
    DROP CONSTRAINT server_processing_result_staging_payload_version_check;
ALTER TABLE server_processing_result_staging
    ADD CONSTRAINT server_processing_result_staging_payload_version_check
    CHECK (payload_version IN (1, 2, 3));
