-- 논문 아카이브 목록이 논문마다 선택 판본의 마지막 처리 작업을 고른다(처리 상태 열).
CREATE INDEX idx_server_processing_jobs_paper_version_recent
    ON server_processing_jobs(
        owner_user_id, paper_id, server_version_id, created_at DESC, job_id DESC
    );
