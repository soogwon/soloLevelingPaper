ALTER TABLE server_translation_chunks ADD COLUMN translation_failure_code text NULL;
ALTER TABLE server_translation_chunks ADD CONSTRAINT server_translation_quality_failure_check
CHECK (translation_failure_code IS NULL OR
       (translation_status IN ('translation_failed','original_only') AND
        translation_failure_code IN ('empty_translation','unchanged_translation','target_language_mismatch')));
