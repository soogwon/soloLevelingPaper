-- 자동 게시 작업은 접수 당시의 예산 정책과 운영 기간 안에서만 실행한다.
ALTER TABLE server_processing_job_authorizations
    ADD COLUMN processing_policy_version text,
    ADD COLUMN processing_window_key text,
    ADD CONSTRAINT server_processing_budget_binding_check CHECK (
        (processing_policy_version IS NULL AND processing_window_key IS NULL)
        OR (processing_policy_version IS NOT NULL AND processing_window_key IS NOT NULL
            AND length(processing_policy_version) BETWEEN 1 AND 100
            AND length(processing_window_key) BETWEEN 1 AND 100)
    );
