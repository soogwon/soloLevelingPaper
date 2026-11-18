"""원격 고정 맥락과 검수 KG를 연결한다. 저장·외부 모델 호출은 하지 않는다."""
from typing import Any
from uuid import UUID

from mcp.types import ToolAnnotations

from solo_leveling.application.learning_path.views import path_view, prerequisites_view
from solo_leveling.domain.knowledge_graph import learning_path, normalize_name
from solo_leveling.interfaces.http.knowledge_graph_routes import load_server_graph


class RemoteLearningPath:
    def __init__(self, queries):
        self.queries = queries
        self.graph = load_server_graph()

    def _context(self, context_id):
        context_id = str(UUID(context_id))
        owner = self.queries.owner()
        binding = self.queries.contexts.resolve(owner, context_id) if owner else None
        if binding is None:
            return None
        # 현재 활성 게시가 아니라 context가 고정한 작업의 검증된 업로드만 사용한다.
        with self.queries.pool.connection() as conn:
            row = conn.execute('''SELECT i.sha256_hex FROM server_processing_publications p
                JOIN server_processing_job_authorizations a
                  ON a.owner_user_id=p.owner_user_id AND a.job_id=p.source_job_id
                JOIN server_remote_upload_intents i
                  ON i.owner_user_id=a.owner_user_id AND i.intent_id=a.upload_intent_id
                  AND i.paper_id=p.paper_id AND i.server_version_id=p.server_version_id
                WHERE p.owner_user_id=%s AND p.publication_id=%s
                  AND p.paper_id=%s AND p.server_version_id=%s AND i.status='verified' ''',
                (owner, binding.publication_id, binding.paper_id, binding.server_version_id)).fetchone()
        return binding, row['sha256_hex'] if row else None

    def read(self, context_id, *, known=None, mode='compact', concept=None):
        try:
            if mode not in ('compact', 'expanded'):
                raise ValueError
            if known is None:
                known = []
            if not isinstance(known, list) or len(known) > 50:
                raise ValueError
            if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in known):
                raise ValueError
            names = [normalize_name(v) for v in known]
            if len(names) != len(set(names)):
                raise ValueError
            if concept is not None and (not isinstance(concept, str) or not concept.strip() or len(concept) > 200):
                raise ValueError
            str(UUID(context_id))
        except (ValueError, TypeError, AttributeError):
            return {'status': 'invalid_argument', 'error_code': 'INVALID_ARGUMENT'}
        try:
            context = self._context(context_id)
            if context is None:
                return {'status': 'not_found', 'error_code': 'NOT_FOUND'}
            if self.graph is None:
                return {'status': 'unavailable', 'error_code': 'KNOWLEDGE_GRAPH_UNAVAILABLE'}
            binding, digest = context
            paper = self.graph.paper_by_hash(digest) if digest else None
            base = {'context_id': binding.context_id, 'paper_id': binding.paper_id,
                    'version_id': binding.server_version_id, 'publication_id': binding.publication_id,
                    'graph_revision_id': self.graph.revision_id,
                    'graph_status': 'ready' if paper else 'no_graph', 'reviewed_version': None,
                    'notice': '검수 PDF 해시가 일치할 때만 위치를 반환합니다. 원격 판본의 arXiv 식별 검증은 미지원이며 관계를 추측하지 마세요.'}
            if concept is not None:
                if paper is None:
                    view = {'concept': None, 'prerequisites': [], 'required_by': [], 'paper_locations': []}
                else:
                    cid = self.graph.resolve(concept)
                    if cid is None:
                        return {'status': 'not_found', 'error_code': 'NOT_FOUND'}
                    view = prerequisites_view(self.graph, cid, paper)
            else:
                view = path_view(learning_path(self.graph, paper, 'understand', names, mode)) if paper else {
                    'steps': [], 'skipped': [], 'unresolved_concepts': []}
                view.update(goal='understand', mode=mode,
                            personalization_notice='목적은 understand 고정이며 알려진 개념 생략 시 빈 목록입니다. 조회로 학습 완료를 저장하지 않습니다.')
            return {'status': 'ok', 'result': {**base, **view}}
        except Exception:
            return {'status': 'unavailable', 'error_code': 'KNOWLEDGE_GRAPH_UNAVAILABLE'}


def register_remote_learning_path(server, queries):
    reader = RemoteLearningPath(queries)
    hints = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=hints)
    def get_learning_path(context_id: str, known_concepts: list[str] | None = None,
                          mode: str = 'compact') -> dict[str, Any]:
        """소유한 원격 맥락의 검수 KG 경로만 읽는다. 목적 understand, 생략한 known_concepts는 빈 목록이다.
        no_graph이면 관계를 추측하지 않는다. 개념은 최대 50개·각 200자이며 중복은 거부한다. 학습 완료·AI 호출은 없다.
        """
        return reader.read(context_id, known=known_concepts, mode=mode)

    @server.tool(annotations=hints)
    def get_prerequisites(context_id: str, concept: str) -> dict[str, Any]:
        """소유한 원격 맥락의 검수 선수·이유만 읽는다. no_graph에서 관계나 다른 판본의 페이지를 추측하지 않는다."""
        return reader.read(context_id, concept=concept)
