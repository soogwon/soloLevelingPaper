-- 첫 게시 전 부분 번역도 기존 비공개 staging과 같은 수명·불변성으로 보관한다.
ALTER TABLE server_processing_result_staging
    DROP CONSTRAINT server_processing_result_staging_payload_version_check;
ALTER TABLE server_processing_result_staging
    ADD CONSTRAINT server_processing_result_staging_payload_version_check
    CHECK (payload_version IN (1, 2, 3, 4));
