-- 논문 상세가 현재 판본의 대표 학습 맥락을 최근 진행 저장순으로 고른다.
CREATE INDEX idx_remote_learning_paper_recent
    ON server_remote_learning_records(
        owner_user_id, paper_id, server_version_id, updated_at DESC, context_id
    );
