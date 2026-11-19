"""인증된 owner의 고정 게시본에서 요약 범위 전체를 읽는다. 저장·외부 호출은 없다."""
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5
import hashlib
import json

from solo_leveling.application.summary.remote_contract import (
    RemoteSummaryError, RemoteSummaryInput, SummarySource,
)
from .remote_retrieval import PostgresRemoteContextResolver


class PostgresSummaryPreparation:
    def __init__(self, pool, *, provider, model, prompt_version, language, budget_policy_version):
        self.pool = pool
        self.settings = dict(provider=provider, model=model, prompt_version=prompt_version,
                             language=language, budget_policy_version=budget_policy_version)

    def prepare(self, owner, context, *, scope='paper', section_id=None):
        """owner는 검증된 인증 계층에서 전달한다. 조회만으로 동의나 실행을 만들지 않는다."""
        if (not isinstance(owner, str) or not owner.strip() or scope not in ('paper', 'section')
                or (scope == 'paper' and section_id is not None)
                or (scope == 'section' and (not isinstance(section_id, str) or not section_id.strip()))):
            raise RemoteSummaryError('REMOTE_SUMMARY_INVALID')
        try:
            context = str(UUID(context))
        except (TypeError, ValueError, AttributeError):
            raise RemoteSummaryError('REMOTE_SUMMARY_INVALID') from None
        with self.pool.connection() as conn:
            # 삭제와 경합해 사라진 원문을 새 요약 입력으로 준비하지 않는다.
            exists = conn.execute('''SELECT p.paper_id FROM server_execution_contexts c
                JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL
                FOR SHARE OF p''', (owner, context)).fetchone()
            if exists is None:
                return None
            scoped = SimpleNamespace(connection=lambda: nullcontext(conn))
            binding = PostgresRemoteContextResolver(scoped).resolve(owner, context)
            if binding is None:
                return None
            rows = conn.execute('''SELECT c.chunk_id,c.pdf_page,c.section_id,c.original_text,
                tc.translated_text,tc.translation_status,count(*) OVER () AS total_scope_chunks
                FROM server_processing_chunks c
                LEFT JOIN server_translation_chunks tc ON tc.owner_user_id=c.owner_user_id
                  AND tc.chunk_id=c.chunk_id AND tc.translation_revision_id=%s
                WHERE c.owner_user_id=%s AND c.paper_id=%s AND c.server_version_id=%s
                  AND c.parse_revision_id=%s AND (%s::text IS NULL OR c.section_id=%s)
                ORDER BY c.ordinal,c.chunk_id LIMIT 1001''',
                (binding.translation_revision_id, owner, binding.paper_id, binding.server_version_id,
                 binding.parse_revision_id, section_id, section_id)).fetchall()
            if not rows:
                raise RemoteSummaryError('REMOTE_SUMMARY_SOURCE_EMPTY')
            if len(rows) > 1000 or rows[0]['total_scope_chunks'] != len(rows):
                raise RemoteSummaryError('REMOTE_SUMMARY_SOURCE_TOO_LARGE')
            sources = []
            for row in rows:
                if row['translation_status'] not in ('translated', 'translation_not_required', 'reference_content', 'original_only'):
                    raise RemoteSummaryError('REMOTE_SUMMARY_SOURCE_INVALID')
                identity = [owner, context, binding.publication_id, binding.parse_revision_id,
                            binding.translation_revision_id, str(row['chunk_id'])]
                digest = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
                evidence_id = str(uuid5(NAMESPACE_URL, 'solo-leveling-summary-evidence-v1:' + digest))
                sources.append(SummarySource(evidence_id, str(row['chunk_id']), row['pdf_page'],
                    row['section_id'], row['original_text'],
                    row['translated_text'] if row['translation_status'] == 'translated' else None))
            return RemoteSummaryInput.create(binding, tuple(sources), scope=scope, section_id=section_id,
                total_scope_chunks=rows[0]['total_scope_chunks'], **self.settings)
