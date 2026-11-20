-- 검증된 웹 로그인 기록을 원격 MCP의 최초 계정 연결 증거로 사용한다.
CREATE TABLE server_verified_web_identities (
    issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 2048),
    subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 512)
        REFERENCES server_users(owner_user_id) ON DELETE CASCADE,
    PRIMARY KEY (issuer, subject)
);

-- MCP 역할에 임의 INSERT를 부여하지 않고 검증된 동일 계정만 연결한다.
DO $migration$
DECLARE
    schema_name text := current_schema();
BEGIN
    EXECUTE format($definition$
        CREATE FUNCTION %1$I.server_link_verified_mcp_account(p_issuer text, p_subject text)
        RETURNS text LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $body$
        DECLARE linked_owner text;
        BEGIN
            INSERT INTO %1$I.server_remote_identities(issuer, subject, owner_user_id)
            SELECT issuer, subject, subject FROM %1$I.server_verified_web_identities
            WHERE issuer=p_issuer AND subject=p_subject
            ON CONFLICT (issuer, subject) DO NOTHING;

            SELECT owner_user_id INTO linked_owner FROM %1$I.server_remote_identities
            WHERE issuer=p_issuer AND subject=p_subject
              AND owner_user_id=p_subject AND revoked_at IS NULL
              AND EXISTS (SELECT 1 FROM %1$I.server_verified_web_identities
                          WHERE issuer=p_issuer AND subject=p_subject);
            RETURN linked_owner;
        END
        $body$
    $definition$, schema_name);
    EXECUTE format('REVOKE ALL ON FUNCTION %I.server_link_verified_mcp_account(text,text) FROM PUBLIC', schema_name);
END
$migration$;
