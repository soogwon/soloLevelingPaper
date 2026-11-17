-- 승인 후 비특권 MCP 역할을 준비하는 예시. staging에서 아직 실행하지 않았다.
-- psql -X -1 -v ON_ERROR_STOP=1 -v target_database=... -v target_schema=... -v mcp_role=... -v schema_owner=...
-- NOLOGIN으로 생성하며 암호·연결 URL은 별도 비밀 입력 절차로 설정한다.
SELECT set_config('slp.mcp_target_database', :'target_database', true);
SELECT set_config('slp.mcp_target_schema', :'target_schema', true);
SELECT set_config('slp.mcp_target_role', :'mcp_role', true);
SELECT set_config('slp.mcp_schema_owner', :'schema_owner', true);
DO $$
BEGIN
 IF current_database() <> current_setting('slp.mcp_target_database') OR
    current_setting('slp.mcp_target_schema') !~ '^slp_[a-z0-9_]+$' OR
    current_setting('slp.mcp_target_role') !~ '^slp_[a-z0-9_]+$' THEN
   RAISE EXCEPTION 'mcp_role_target_invalid';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
    WHERE n.nspname=current_setting('slp.mcp_target_schema') AND r.rolname=current_setting('slp.mcp_schema_owner')) THEN
   RAISE EXCEPTION 'mcp_schema_owner_mismatch';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=current_setting('slp.mcp_target_role')) THEN
   RAISE EXCEPTION 'mcp_role_already_exists';
 END IF;
END $$;
CREATE ROLE :"mcp_role" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT USAGE ON SCHEMA :"target_schema" TO :"mcp_role";
GRANT SELECT ON
 :"target_schema".server_remote_identities,
 :"target_schema".server_papers,
 :"target_schema".server_processing_versions,
 :"target_schema".server_processing_publications,
 :"target_schema".server_embedding_sets,
 :"target_schema".server_execution_contexts,
 :"target_schema".server_remote_executions,
 :"target_schema".server_provider_attempts TO :"mcp_role";
GRANT INSERT ON :"target_schema".server_execution_contexts TO :"mcp_role";
GRANT UPDATE(paper_id) ON :"target_schema".server_papers TO :"mcp_role";
SET LOCAL ROLE :"schema_owner";
CREATE FUNCTION :"target_schema".mcp_read_lock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF current_user=TG_ARGV[0] THEN
   RAISE EXCEPTION 'mcp_readonly_table' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER mcp_read_lock_guard BEFORE UPDATE ON :"target_schema".server_papers
 FOR EACH ROW EXECUTE FUNCTION :"target_schema".mcp_read_lock_guard(:'mcp_role');
RESET ROLE;
ALTER ROLE :"mcp_role" IN DATABASE :"target_database" SET search_path=:"target_schema";
