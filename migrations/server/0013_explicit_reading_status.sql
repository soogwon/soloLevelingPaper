-- 기존 단계 비율로 추정한 읽기 상태를 명시적 상태로 간주하지 않는다.
ALTER TABLE server_learning_records DROP CONSTRAINT server_learning_records_reading_status_check;
ALTER TABLE server_learning_records ADD CONSTRAINT server_learning_records_reading_status_check
    CHECK (reading_status IN ('unknown', 'not_started', 'reading', 'completed'));
UPDATE server_learning_records SET reading_status='unknown', revision=revision+1;
