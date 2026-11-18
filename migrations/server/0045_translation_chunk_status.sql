ALTER TABLE server_translation_chunks
    ADD COLUMN translation_status text NOT NULL DEFAULT 'translated';

ALTER TABLE server_translation_chunks
    ADD CONSTRAINT server_translation_chunks_status_check
    CHECK (translation_status IN (
        'translated',
        'translation_not_required',
        'reference_content',
        'translation_failed',
        'original_only'
    ));

ALTER TABLE server_processing_result_staging
    DROP CONSTRAINT server_processing_result_staging_payload_version_check;

ALTER TABLE server_processing_result_staging
    ADD CONSTRAINT server_processing_result_staging_payload_version_check
    CHECK (payload_version IN (1, 2));
