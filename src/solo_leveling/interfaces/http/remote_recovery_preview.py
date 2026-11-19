"""고정 게시본의 복구 범위만 조회한다. 접수·동의·provider 포트는 없다."""
from uuid import UUID


class RemoteRecoveryPreview:
    def __init__(self, queries):
        self.queries = queries

    def preview(self, context_id, operation):
        if operation not in ('translation_recovery', 'reparse'):
            return {'status': 'invalid_argument'}
        try:
            context_id = str(UUID(context_id))
        except (ValueError, TypeError, AttributeError):
            return {'status': 'invalid_argument'}
        try:
            owner = self.queries.owner()
            binding = self.queries.contexts.resolve(owner, context_id) if owner else None
            if binding is None:
                return {'status': 'not_found'}
            with self.queries.pool.connection() as conn:
                # 소유권·삭제 여부를 다시 확인하고 고정 리비전만 집계한다. 본문은 읽지 않는다.
                row = conn.execute('''SELECT count(c.chunk_id) AS total_chunks,
                    count(c.chunk_id) FILTER (WHERE t.chunk_id IS NULL) AS missing_chunks,
                    count(c.chunk_id) FILTER (WHERE t.translation_status='translation_failed') AS failed_chunks,
                    count(c.chunk_id) FILTER (WHERE t.translation_status='original_only') AS original_only_chunks,
                    count(DISTINCT c.pdf_page) AS source_page_count
                    FROM server_papers p
                    LEFT JOIN server_processing_chunks c ON c.owner_user_id=p.owner_user_id
                      AND c.paper_id=p.paper_id AND c.server_version_id=%s AND c.parse_revision_id=%s
                    LEFT JOIN server_translation_chunks t ON t.owner_user_id=c.owner_user_id
                      AND t.paper_id=c.paper_id AND t.server_version_id=c.server_version_id
                      AND t.parse_revision_id=c.parse_revision_id AND t.chunk_id=c.chunk_id
                      AND t.translation_revision_id=%s
                    WHERE p.owner_user_id=%s AND p.paper_id=%s AND p.deleted_at IS NULL
                    GROUP BY p.paper_id''',
                    (binding.server_version_id, binding.parse_revision_id,
                     binding.translation_revision_id, owner, binding.paper_id)).fetchone()
            if row is None:
                return {'status': 'not_found'}
            recovery_count = row['missing_chunks'] + row['failed_chunks'] + row['original_only_chunks']
            recovery = operation == 'translation_recovery'
            return {'status': 'ok', 'result': {
                'context_id': context_id, 'operation': operation,
                'paper_id': binding.paper_id, 'version_id': binding.server_version_id,
                'publication_id': binding.publication_id,
                'parse_revision_id': binding.parse_revision_id,
                'translation_revision_id': binding.translation_revision_id,
                'embedding_set_id': binding.embedding_set_id,
                'model_fingerprint': binding.model_fingerprint,
                'source_counts': dict(row),
                'target_count': recovery_count if recovery else 1,
                'target_unit': 'chunk' if recovery else 'pdf_version',
                'target_status': 'no_targets' if recovery and recovery_count == 0 else 'requires_review',
                'submission_available': False,
                'estimated_cost': {'status': 'not_estimated', 'amount': None, 'currency': None},
                'transfer_preview': {
                    'status': 'provisional', 'sent_now': False,
                    'data_categories': (['target_original_text', 'translation_instructions'] if recovery
                                        else ['stored_pdf', 'extracted_text_if_translation_selected']),
                    'notice': '실제 목적지·모델·전송 본문·비용은 접수 전 웹 검토에서 확정하고 별도로 동의해야 합니다.'},
                'limitations': ['submission_not_connected', 'consent_not_connected',
                                'budget_not_evaluated', 'source_pdf_availability_not_checked'],
                'notice': '고정된 과거 게시본 기준의 미리보기입니다. 조회는 작업·비용을 만들지 않습니다. '
                          '원문 전용·번역 실패·누락 청크만 번역 복구 후보이며 참조문헌·번역 불필요는 제외합니다. '
                          '재파싱은 PDF 판본 전체가 대상이며 현재 청크 수는 새 파싱 결과 수가 아닙니다. '
                          '새 결과를 생성해도 사용자 선택 전 기존 학습 맥락·과거 근거를 변경하지 않습니다.'}}
        except Exception:
            return {'status': 'unavailable'}
