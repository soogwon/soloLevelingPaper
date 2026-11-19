-- 기존 벡터와 과거 context를 유지하고 새 분할 지문의 벡터만 별도로 저장한다.
CREATE TABLE server_embedding_fragments (
    owner_user_id text NOT NULL,
    paper_id uuid NOT NULL,
    server_version_id text NOT NULL,
    parse_revision_id uuid NOT NULL,
    translation_revision_id uuid NOT NULL,
    embedding_set_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    embedding_fragment_id text NOT NULL CHECK (embedding_fragment_id ~ '^[0-9a-f]{64}$'),
    token_start integer NOT NULL CHECK (token_start >= 0),
    token_end integer NOT NULL,
    parent_token_count integer NOT NULL,
    dimension integer NOT NULL CHECK (dimension BETWEEN 1 AND 4096),
    embedding vector NOT NULL,
    PRIMARY KEY (owner_user_id, embedding_set_id, embedding_fragment_id),
    UNIQUE (owner_user_id, embedding_set_id, chunk_id, token_start),
    FOREIGN KEY (owner_user_id, paper_id, server_version_id, parse_revision_id,
                 translation_revision_id, embedding_set_id)
        REFERENCES server_embedding_sets(owner_user_id, paper_id, server_version_id,
            parse_revision_id, translation_revision_id, embedding_set_id) ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id, translation_revision_id, chunk_id)
        REFERENCES server_translation_chunks(owner_user_id, translation_revision_id, chunk_id) ON DELETE CASCADE,
    CHECK (token_start < token_end AND token_end <= parent_token_count),
    CHECK (vector_dims(embedding) = dimension)
);

CREATE TRIGGER trg_server_embedding_fragments_generation
    BEFORE INSERT OR UPDATE OR DELETE ON server_embedding_fragments
    FOR EACH ROW EXECUTE FUNCTION server_processing_guard_embedding_vectors();

CREATE FUNCTION server_embedding_fragment_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.embedding_fragment_id, NEW.token_start, NEW.token_end, NEW.parent_token_count)
       IS DISTINCT FROM
       (OLD.embedding_fragment_id, OLD.token_start, OLD.token_end, OLD.parent_token_count) THEN
        RAISE EXCEPTION 'embedding_fragment_identity_fixed' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_server_embedding_fragments_identity
    BEFORE UPDATE ON server_embedding_fragments
    FOR EACH ROW EXECUTE FUNCTION server_embedding_fragment_identity();

CREATE FUNCTION server_embedding_fragment_ready() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    policy jsonb;
    payload integer;
    overlap integer;
    stride integer;
    parents integer;
BEGIN
    IF NEW.status <> 'ready' OR NOT (NEW.model_fingerprint ? 'split_policy') THEN
        RETURN NEW;
    END IF;
    policy := NEW.model_fingerprint->'split_policy';
    IF jsonb_typeof(policy) IS DISTINCT FROM 'object'
       OR jsonb_typeof(NEW.model_fingerprint->'dimension') IS DISTINCT FROM 'number'
       OR jsonb_typeof(NEW.model_fingerprint->'max_length') IS DISTINCT FROM 'number'
       OR jsonb_typeof(policy->'payload_tokens') IS DISTINCT FROM 'number'
       OR jsonb_typeof(policy->'overlap_tokens') IS DISTINCT FROM 'number'
       OR jsonb_typeof(policy->'stride_tokens') IS DISTINCT FROM 'number' THEN
        RAISE EXCEPTION 'embedding_fragment_policy_invalid' USING ERRCODE = 'check_violation';
    END IF;
    payload := (policy->>'payload_tokens')::integer;
    overlap := (policy->>'overlap_tokens')::integer;
    stride := (policy->>'stride_tokens')::integer;
    IF policy->>'version' IS DISTINCT FROM 'token-window-v1'
       OR payload IS NULL OR payload < 1 OR overlap IS NULL OR overlap < 0 OR overlap >= payload
       OR stride IS NULL OR stride <> payload - overlap
       OR payload >= (NEW.model_fingerprint->>'max_length')::integer
       OR (NEW.model_fingerprint->>'dimension')::integer NOT BETWEEN 1 AND 4096 THEN
        RAISE EXCEPTION 'embedding_fragment_policy_invalid' USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(DISTINCT chunk_id) INTO parents FROM server_embedding_fragments
      WHERE owner_user_id=NEW.owner_user_id AND embedding_set_id=NEW.embedding_set_id;
    IF parents <> NEW.indexed_chunk_count OR parents = 0 OR EXISTS (
        SELECT 1 FROM server_embedding_vectors
         WHERE owner_user_id=NEW.owner_user_id AND embedding_set_id=NEW.embedding_set_id
    ) OR EXISTS (
        SELECT 1 FROM server_embedding_fragments
         WHERE owner_user_id=NEW.owner_user_id AND embedding_set_id=NEW.embedding_set_id
         GROUP BY chunk_id
        HAVING min(token_start) <> 0 OR max(token_end) <> max(parent_token_count)
           OR min(parent_token_count) <> max(parent_token_count)
           OR count(*) <> 1 + (greatest(max(parent_token_count) - payload, 0) + stride - 1) / stride
           OR bool_or(token_start % stride <> 0
                      OR token_end <> least(token_start + payload, parent_token_count)
                      OR dimension <> (NEW.model_fingerprint->>'dimension')::integer)
    ) THEN
        RAISE EXCEPTION 'embedding_fragment_mapping_incomplete' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_server_embedding_fragments_ready
    BEFORE INSERT OR UPDATE ON server_embedding_sets
    FOR EACH ROW EXECUTE FUNCTION server_embedding_fragment_ready();
