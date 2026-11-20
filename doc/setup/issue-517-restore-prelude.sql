-- 승인된 복구 작업 전용. 실제 staging에서 아직 실행하지 않았다.
-- psql -X -1 -v ON_ERROR_STOP=1과 함께 사용한다. 이 파일 단독 실행 금지.
-- 뒤에 검토한 restore-app.sql, restore-acl.sql, verify.sql을 같은 호출로 연결한다.
SELECT set_config('slp.restore_database', :'restore_database', true);
SELECT set_config('slp.restore_admin', :'restore_admin', true);
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';
DO $$
DECLARE actual text[];
BEGIN
 IF current_database() <> current_setting('slp.restore_database') OR
    current_user <> current_setting('slp.restore_admin') THEN
   RAISE EXCEPTION 'restore_target_mismatch';
 END IF;
 IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN
   RAISE EXCEPTION 'restore_requires_reviewed_nonsuperuser';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
    WHERE e.extname='vector' AND n.nspname='slp_staging' AND e.extrelocatable
      AND pg_get_userbyid(e.extowner)=current_user) THEN
   RAISE EXCEPTION 'restore_vector_owner_or_schema_mismatch';
 END IF;
 IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public') THEN
   RAISE EXCEPTION 'restore_public_not_empty';
 END IF;
 SELECT array_agg(tablename::text ORDER BY tablename) INTO actual FROM pg_tables WHERE schemaname='slp_staging';
 IF actual IS DISTINCT FROM ARRAY['server_annotation_consents', 'server_annotation_create_requests', 'server_annotations', 'server_collection_papers', 'server_collections', 'server_context_annotations', 'server_deletion_requests', 'server_device_credentials', 'server_devices', 'server_dispatch_budget_limits', 'server_dispatch_controls', 'server_embedding_fragments', 'server_embedding_sets', 'server_embedding_vectors', 'server_execution_contexts', 'server_idempotency_records', 'server_learning_records', 'server_paper_reading_states', 'server_papers', 'server_parse_revisions', 'server_pdf_uploads', 'server_processing_cancel_requests', 'server_processing_chunks', 'server_processing_evidences', 'server_processing_job_authorizations', 'server_processing_jobs', 'server_processing_provider_attempts', 'server_processing_publications', 'server_processing_result_staging', 'server_processing_versions', 'server_provider_attempts', 'server_question_budget_reservations', 'server_question_reviews', 'server_question_token_counts', 'server_quiz_generation_inputs', 'server_quiz_generation_reviews', 'server_reflection_answers', 'server_reflection_contexts', 'server_reflection_requests', 'server_remote_consents', 'server_remote_executions', 'server_remote_identities', 'server_remote_insight_collections', 'server_remote_insight_requests', 'server_remote_learning_records', 'server_remote_learning_requests', 'server_remote_quiz_attempts', 'server_remote_quizzes', 'server_remote_upload_consent_bindings', 'server_remote_upload_consent_requests', 'server_remote_upload_intents', 'server_reprocessing_reviews', 'server_resume_requests', 'server_schema_migrations', 'server_summaries', 'server_summary_inputs', 'server_summary_reviews', 'server_translation_chunks', 'server_translation_revisions', 'server_users', 'server_verified_web_identities', 'web_login_attempts', 'web_sessions']::text[] THEN
   RAISE EXCEPTION 'restore_table_manifest_mismatch';
 END IF;
 IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname='slp_staging' AND (p.proname || '(' || oidvectortypes(p.proargtypes) || ')') NOT IN ('mcp_read_lock_guard()', 'server_embedding_fragment_identity()', 'server_embedding_fragment_ready()', 'server_guard_execution_policy()', 'server_guard_summary_input()', 'server_guard_quiz_generation_input()', 'server_guard_processing_staging()', 'server_processing_guard_embedding_vectors()', 'server_processing_guard_parse_rows()', 'server_processing_guard_revision_generation()', 'server_processing_guard_translation_rows()', 'server_processing_cancel_guard()', 'server_processing_job_revision()', 'server_processing_require_lease(text, uuid, uuid)', 'server_question_lock_guard()', 'server_quiz_snapshot_immutable()', 'server_learning_consent_role_guard()', 'server_lock_insight_import(text, uuid, uuid, text, text)', 'server_link_verified_mcp_account(text, text)', 'server_release_question_budget()', 'server_reprocessing_release_on_delete()', 'server_reject_tombstoned_execution()', 'server_reject_tombstoned_paper()', 'server_remote_learning_fixed_binding()', 'web_readonly_guard()', 'worker_rehearsal_readonly_guard()')
     AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')) THEN
   RAISE EXCEPTION 'restore_function_manifest_mismatch';
 END IF;
END $$;
-- 새로운 앱 객체 소유자로만 삭제하고 extension 작업은 원래 관리자로 수행한다.
SELECT set_config('role', :'restore_owner', true);
DROP FUNCTION slp_staging.server_lock_insight_import(text, uuid, uuid, text, text);
DROP FUNCTION slp_staging.server_link_verified_mcp_account(text, text);
DROP TABLE
    slp_staging.server_remote_insight_requests,
    slp_staging.server_remote_insight_collections,
    slp_staging.server_reprocessing_reviews,
    slp_staging.server_quiz_generation_reviews,
    slp_staging.server_quiz_generation_inputs,
    slp_staging.server_remote_quiz_attempts,
    slp_staging.server_remote_quizzes,
    slp_staging.server_reflection_requests,
    slp_staging.server_reflection_answers,
    slp_staging.server_reflection_contexts,
    slp_staging.server_annotation_consents,
    slp_staging.server_annotation_create_requests,
    slp_staging.server_annotations,
    slp_staging.server_collection_papers,
    slp_staging.server_collections,
    slp_staging.server_context_annotations,
    slp_staging.server_deletion_requests,
    slp_staging.server_device_credentials,
    slp_staging.server_devices,
    slp_staging.server_dispatch_budget_limits,
    slp_staging.server_dispatch_controls,
    slp_staging.server_embedding_fragments,
    slp_staging.server_embedding_sets,
    slp_staging.server_embedding_vectors,
    slp_staging.server_execution_contexts,
    slp_staging.server_idempotency_records,
    slp_staging.server_learning_records,
    slp_staging.server_paper_reading_states,
    slp_staging.server_papers,
    slp_staging.server_parse_revisions,
    slp_staging.server_pdf_uploads,
    slp_staging.server_processing_cancel_requests,
    slp_staging.server_processing_chunks,
    slp_staging.server_processing_evidences,
    slp_staging.server_processing_job_authorizations,
    slp_staging.server_processing_jobs,
    slp_staging.server_processing_provider_attempts,
    slp_staging.server_processing_publications,
    slp_staging.server_processing_result_staging,
    slp_staging.server_processing_versions,
    slp_staging.server_provider_attempts,
    slp_staging.server_question_budget_reservations,
    slp_staging.server_summary_reviews,slp_staging.server_summary_inputs,
    slp_staging.server_question_reviews,
    slp_staging.server_question_token_counts,
    slp_staging.server_remote_consents,
    slp_staging.server_remote_executions,
    slp_staging.server_remote_identities,
    slp_staging.server_remote_learning_records,
    slp_staging.server_remote_learning_requests,
    slp_staging.server_remote_upload_consent_bindings,
    slp_staging.server_remote_upload_consent_requests,
    slp_staging.server_remote_upload_intents,
    slp_staging.server_resume_requests,
    slp_staging.server_schema_migrations,
    slp_staging.server_summaries,
    slp_staging.server_translation_chunks,
    slp_staging.server_translation_revisions,
    slp_staging.server_verified_web_identities,
    slp_staging.server_users,
    slp_staging.web_login_attempts,
    slp_staging.web_sessions
RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_embedding_fragment_identity() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_embedding_fragment_ready() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.mcp_read_lock_guard() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_guard_summary_input() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_guard_quiz_generation_input() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_guard_execution_policy() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_guard_processing_staging() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_processing_guard_embedding_vectors() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_processing_guard_parse_rows() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_processing_guard_revision_generation() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_processing_guard_translation_rows() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_processing_cancel_guard() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_processing_job_revision() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_processing_require_lease(text,uuid,uuid) RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_question_lock_guard() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_release_question_budget() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_reprocessing_release_on_delete() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_reject_tombstoned_execution() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_reject_tombstoned_paper() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_remote_learning_fixed_binding() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.web_readonly_guard() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.worker_rehearsal_readonly_guard() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_quiz_snapshot_immutable() RESTRICT;
DROP FUNCTION IF EXISTS slp_staging.server_learning_consent_role_guard() RESTRICT;
RESET ROLE;
ALTER EXTENSION vector SET SCHEMA public;
-- 이어지는 restore-app.sql이 public에 원래 앱 객체와 데이터를 복원한다.
