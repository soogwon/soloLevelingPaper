-- 전역 메모·하이라이트 화면이 context 하나에 묶이지 않고 소유자의 살아 있는
-- 원격 기록을 작성 시각순으로 조회한다.
CREATE INDEX idx_server_context_annotations_owner_created
    ON server_context_annotations(owner_user_id, created_at DESC, annotation_id)
    WHERE deleted_at IS NULL;
