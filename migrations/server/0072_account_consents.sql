-- 첫 로그인에서 항목별로 받는 계정 단위 동의. 기능별 동의 기록을 대신 만드는 근거가 된다.
-- storage: PDF와 학습 기록 서버 저장(필수), ai_provider: OpenAI 국외 전송(선택).
CREATE TABLE server_account_consents (
    owner_user_id text NOT NULL REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    purpose text NOT NULL CHECK (purpose IN ('storage', 'ai_provider')),
    revision bigint NOT NULL CHECK (revision >= 1),
    consent_id uuid NOT NULL UNIQUE,
    notice_version text NOT NULL CHECK (length(notice_version) BETWEEN 1 AND 100),
    granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    revoked_at timestamptz NULL,
    PRIMARY KEY (owner_user_id, purpose, revision),
    CHECK (revoked_at IS NULL OR revoked_at >= granted_at)
);

CREATE UNIQUE INDEX uq_account_consent_current
    ON server_account_consents(owner_user_id, purpose)
    WHERE revoked_at IS NULL;

-- 기능별 MCP 역할은 이 표의 권한 없이 현재 동의의 ID만 확인한다(없으면 NULL).
DO $migration$
DECLARE
    schema_name text := current_schema();
BEGIN
    EXECUTE format($definition$
        CREATE FUNCTION %1$I.server_account_consent_current(p_owner text, p_purpose text, p_notice text)
        RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $body$
            SELECT consent_id FROM %1$I.server_account_consents
            WHERE owner_user_id=p_owner AND purpose=p_purpose
              AND notice_version=p_notice AND revoked_at IS NULL
        $body$
    $definition$, schema_name);
END
$migration$;
