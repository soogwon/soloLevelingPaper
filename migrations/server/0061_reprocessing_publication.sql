-- 후보 게시와 사용자의 명시적 선택은 별개다. 기존 context를 갱신하지 않는다.
ALTER TABLE server_reprocessing_reviews
    DROP CONSTRAINT server_reprocessing_reviews_state_check,
    ADD CONSTRAINT server_reprocessing_reviews_state_check
        CHECK(state IN ('review','queued','running','staged','published','failed','cancelled')),
    ADD COLUMN publication_id uuid,
    ADD COLUMN selected_context_id uuid,
    ADD COLUMN publication_payload_sha256 text CHECK(publication_payload_sha256 ~ '^[0-9a-f]{64}$'),
    ADD FOREIGN KEY(owner_user_id,publication_id)
        REFERENCES server_processing_publications(owner_user_id,publication_id),
    ADD FOREIGN KEY(owner_user_id,selected_context_id)
        REFERENCES server_execution_contexts(owner_user_id,context_id) ON DELETE SET NULL (selected_context_id),
    ADD CHECK((state='published')=(publication_id IS NOT NULL));
