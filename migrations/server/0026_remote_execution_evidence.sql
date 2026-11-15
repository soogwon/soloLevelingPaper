ALTER TABLE server_processing_evidences
    ADD COLUMN execution_id text NULL,
    ADD COLUMN quote_ko text NULL,
    ADD CONSTRAINT fk_server_processing_evidences_execution
        FOREIGN KEY (owner_user_id, execution_id)
        REFERENCES server_remote_executions(owner_user_id, execution_id);

CREATE UNIQUE INDEX uq_server_processing_evidences_execution
    ON server_processing_evidences(owner_user_id, execution_id, evidence_id)
    WHERE execution_id IS NOT NULL;

