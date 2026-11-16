-- 원격 메모는 기기 동기화 식별자와 분리하고, 생성 요청 원장과 같은 transaction에서 저장한다.
CREATE TABLE server_context_annotations (
    annotation_id uuid NOT NULL,
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('note', 'highlight')),
    body text NOT NULL DEFAULT '',
    step text NULL,
    segments jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(segments) = 'array'),
    creation_origin text NOT NULL DEFAULT 'unknown'
        CHECK (creation_origin IN ('user', 'ai', 'unknown')),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at timestamptz NULL,
    PRIMARY KEY (owner_user_id, annotation_id),
    FOREIGN KEY (owner_user_id, context_id)
        REFERENCES server_execution_contexts(owner_user_id, context_id)
);

CREATE INDEX idx_server_context_annotations_context
    ON server_context_annotations(owner_user_id, context_id, updated_at DESC, annotation_id);

CREATE TABLE server_annotation_create_requests (
    owner_user_id text NOT NULL,
    service_id text NOT NULL,
    context_id uuid NOT NULL,
    request_key text NOT NULL,
    fingerprint_version smallint NOT NULL CHECK (fingerprint_version >= 1),
    input_sha256 char(64) NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    annotation_id uuid NOT NULL,
    state text NOT NULL DEFAULT 'committed' CHECK (state IN ('committed', 'deleted')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    client_confirmed_at timestamptz NULL,
    PRIMARY KEY (owner_user_id, service_id, context_id, request_key),
    UNIQUE (owner_user_id, service_id, context_id, annotation_id),
    FOREIGN KEY (owner_user_id, context_id)
        REFERENCES server_execution_contexts(owner_user_id, context_id),
    FOREIGN KEY (owner_user_id, annotation_id)
        REFERENCES server_context_annotations(owner_user_id, annotation_id),
    CHECK (length(service_id) BETWEEN 1 AND 128),
    CHECK (length(request_key) BETWEEN 1 AND 128)
);

CREATE INDEX idx_server_annotation_create_recovery
    ON server_annotation_create_requests(
        owner_user_id, service_id, context_id, fingerprint_version, input_sha256
    ) WHERE state = 'committed' AND client_confirmed_at IS NULL;
