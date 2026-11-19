-- 실행 세대와 보수적 사용량을 접수 원장에 고정한다. 기존 접수는 자동 실행하지 않는다.
ALTER TABLE server_reprocessing_reviews
    DROP CONSTRAINT server_reprocessing_reviews_state_check,
    DROP CONSTRAINT server_reprocessing_reviews_check1,
    ADD COLUMN lease_token uuid,
    ADD COLUMN lease_until timestamptz,
    ADD COLUMN charged boolean NOT NULL DEFAULT false,
    ADD COLUMN attempts jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(attempts)='array'),
    ADD COLUMN staged_result jsonb CHECK(staged_result IS NULL OR jsonb_typeof(staged_result)='object'),
    ADD COLUMN failure_code text,
    ADD CONSTRAINT server_reprocessing_reviews_state_check CHECK(state IN ('review','queued','running','staged','failed','cancelled')),
    ADD CHECK(budget_reserved=(state IN ('queued','running') AND NOT charged)),
    ADD CHECK(state<>'running' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL AND job_id IS NOT NULL));
