-- 성찰 질문 판본과 현재 답변, 동일 요청 재생을 학습 맥락에 고정한다.
CREATE TABLE server_reflection_contexts (
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    question_revision text NOT NULL,
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot)='object'),
    PRIMARY KEY(owner_user_id,context_id),
    FOREIGN KEY(owner_user_id,context_id) REFERENCES server_execution_contexts(owner_user_id,context_id) ON DELETE CASCADE
);
CREATE TABLE server_reflection_answers (
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    template_id text NOT NULL,
    body text NOT NULL CHECK (length(body)<=5000),
    status text NOT NULL CHECK (status IN ('draft','completed','deleted')),
    revision bigint NOT NULL CHECK (revision>0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(owner_user_id,context_id,template_id),
    FOREIGN KEY(owner_user_id,context_id) REFERENCES server_reflection_contexts(owner_user_id,context_id) ON DELETE CASCADE,
    CHECK (status<>'completed' OR length(btrim(body))>0),
    CHECK (status<>'deleted' OR body='')
);
CREATE TABLE server_reflection_requests (
    owner_user_id text NOT NULL,
    context_id uuid NOT NULL,
    request_key uuid NOT NULL,
    input_hash text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(owner_user_id,context_id,request_key),
    FOREIGN KEY(owner_user_id,context_id) REFERENCES server_reflection_contexts(owner_user_id,context_id) ON DELETE CASCADE
);
