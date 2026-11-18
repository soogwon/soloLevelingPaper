"""인증된 원격 요청의 목록·고정 맥락·기존 실행 조회. 생성·provider 포트는 없다."""
import time
from uuid import UUID
from typing import Any
from mcp.types import ToolAnnotations
from solo_leveling.interfaces.http.remote_evidence_read import PostgresRemoteEvidenceReader
from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.remote_identities import PostgresRemoteOwnerResolver
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.application.remote_replay import read_existing_execution
from solo_leveling.interfaces.mcp.remote_recovery import restored_guidance


class RemoteProductQueries:
    def __init__(self, pool):
        self.pool = pool
        self.evidences = PostgresRemoteEvidenceReader(pool)
        self.owners = PostgresRemoteOwnerResolver(pool)
        self.contexts = PostgresRemoteContextResolver(pool)
        self.executions = PostgresRemoteExecutionStore(pool)

    def owner(self):
        token = get_access_token()
        if (not isinstance(token, RemoteIdentityAccessToken) or token.verified_identity() is None
                or type(token.expires_at) is not int or token.expires_at <= time.time()
                or 'papers:read' not in token.scopes):
            return None
        return self.owners.resolve_owner(token.verified_identity())

    def list_papers(self, offset=0):
        if type(offset) is not int or not 0 <= offset <= 100000:
            return {'status':'invalid_argument'}
        try:
            owner = self.owner()
            if owner is None:
                return {'status':'not_found'}
            with self.pool.connection() as conn:
                rows = conn.execute('''SELECT p.paper_id,p.title,v.server_version_id,pub.publication_id
                    FROM server_processing_versions v
                    JOIN server_papers p ON p.owner_user_id=v.owner_user_id AND p.paper_id=v.paper_id
                    JOIN server_processing_publications pub ON pub.owner_user_id=v.owner_user_id
                        AND pub.paper_id=v.paper_id AND pub.server_version_id=v.server_version_id
                        AND pub.publication_id=v.active_publication_id
                    WHERE p.owner_user_id=%s AND p.deleted_at IS NULL
                    ORDER BY p.paper_id,v.server_version_id LIMIT 21 OFFSET %s''',(owner,offset)).fetchall()
            return {'status':'ok','papers':[{k:str(v) if v is not None else None for k,v in row.items()}
                    for row in rows[:20]],'next_offset':offset+20 if len(rows)>20 else None}
        except Exception:
            return {'status':'unavailable'}

    def open_paper(self, paper_id, version_id, context_key):
        try:
            if not isinstance(version_id,str) or not version_id.strip() or len(version_id)>512:
                return {'status':'invalid_argument'}
            ids = [str(UUID(paper_id)),version_id,str(UUID(context_key))]
        except (ValueError,TypeError,AttributeError):
            return {'status':'invalid_argument'}
        try:
            owner = self.owner()
            binding = self.contexts.pin(owner,ids[2],ids[0],ids[1]) if owner else None
            if binding is None:
                return {'status':'not_found'}
            return {'status':'ok', 'context_id':binding.context_id, 'paper_id':binding.paper_id,
                    'version_id':binding.server_version_id, 'publication_id':binding.publication_id,
                    'message':'추천 페이지와 근거는 이 PDF 판본 기준입니다. 다른 판본은 페이지가 다를 수 있습니다.'}
        except ValueError as error:
            return {'status':'conflict' if str(error)=='remote_context_key_conflict' else 'unavailable'}
        except Exception:
            return {'status':'unavailable'}

    def detail(self, context_id, section_offset=0):
        try:
            context_id = str(UUID(context_id))
        except (ValueError, TypeError, AttributeError):
            return {'status': 'invalid_argument'}
        if type(section_offset) is not int or not 0 <= section_offset <= 100000:
            return {'status': 'invalid_argument'}
        try:
            owner = self.owner()
            binding = self.contexts.resolve(owner, context_id) if owner else None
            if binding is None:
                return {'status': 'not_found'}
            with self.pool.connection() as conn:
                row = conn.execute('''SELECT p.title,r.document_metadata
                    FROM server_parse_revisions r
                    JOIN server_papers p ON p.owner_user_id=r.owner_user_id AND p.paper_id=r.paper_id
                    WHERE r.owner_user_id=%s AND r.paper_id=%s AND r.server_version_id=%s
                      AND r.parse_revision_id=%s AND p.deleted_at IS NULL''',
                    (owner, binding.paper_id, binding.server_version_id, binding.parse_revision_id)).fetchone()
            if row is None:
                return {'status': 'not_found'}
            metadata = row['document_metadata']
            sections = metadata.get('sections', [])
            if not isinstance(sections, list):
                raise ValueError('remote_sections_invalid')
            # 다른 리비전이나 잘못된 메타데이터를 정상 목차로 반환하지 않는다.
            fields = ('section_id', 'parse_revision_id', 'title', 'ordinal', 'level',
                      'parent_section_id', 'start_pdf_page', 'end_pdf_page')
            catalog = []
            for section in sections:
                if (not isinstance(section, dict)
                        or section.get('parse_revision_id') != binding.parse_revision_id
                        or not isinstance(section.get('section_id'), str)
                        or not section['section_id'].strip()
                        or not isinstance(section.get('title'), str)
                        or any(type(section.get(k)) is not int for k in
                               ('ordinal', 'level', 'start_pdf_page', 'end_pdf_page'))
                        or section['ordinal'] < 0 or section['level'] < 1
                        or not 1 <= section['start_pdf_page'] <= section['end_pdf_page']):
                    raise ValueError('remote_sections_invalid')
                catalog.append({k: section.get(k) for k in fields})
            ids = [s['section_id'] for s in catalog]
            if len(ids) != len(set(ids)):
                raise ValueError('remote_sections_invalid')
            by_id = {s['section_id']: s for s in catalog}
            for section in catalog:
                parent_id = section['parent_section_id']
                if parent_id is not None:
                    if not isinstance(parent_id, str) or parent_id not in by_id:
                        raise ValueError('remote_sections_invalid')
                    parent = by_id[parent_id]
                    # 부모의 더 낮은 단계와 페이지 포함 관계로 순환도 차단한다.
                    if (parent['level'] >= section['level']
                            or not parent['start_pdf_page'] <= section['start_pdf_page']
                            <= section['end_pdf_page'] <= parent['end_pdf_page']):
                        raise ValueError('remote_sections_invalid')
            catalog.sort(key=lambda s: (s['ordinal'], s['section_id']))
            return {'status': 'ok', 'result': {
                'context_id': context_id, 'paper_id': binding.paper_id,
                'version_id': binding.server_version_id, 'publication_id': binding.publication_id,
                'parse_revision_id': binding.parse_revision_id,
                'translation_revision_id': binding.translation_revision_id,
                'embedding_set_id': binding.embedding_set_id, 'title': row['title'],
                'section_catalog': catalog[section_offset:section_offset + 20],
                'next_section_offset': section_offset + 20 if len(catalog) > section_offset + 20 else None,
                'summary': None, 'keywords': [], 'important_sentences': [],
                'limitations': ['remote_summary_not_available', 'remote_keywords_not_available',
                                'remote_important_sentences_not_available']
                               + ([] if catalog else ['section_catalog_not_available']),
                'annotation_lookup': {'tool': 'list_remote_annotations',
                                      'arguments': {'context_id': context_id, 'limit': 20},
                                      'required_scope': 'annotations:read'},
                'page_notice': '목차 페이지는 이 고정 PDF 판본 기준입니다. 인쇄 페이지나 다른 판본의 페이지와 다를 수 있습니다.',
                'generation_notice': '게시본에 연결된 원격 요약·키워드 저장 계약은 아직 없습니다. 조회 시 자동 생성하지 않습니다.'}}
        except Exception:
            return {'status': 'unavailable'}

    def evidence(self, context_id, evidence_id):
        try:
            context_id, evidence_id = str(UUID(context_id)), str(UUID(evidence_id))
        except (ValueError, TypeError, AttributeError):
            return {'status': 'invalid_argument'}
        try:
            owner = self.owner()
            row = self.evidences.get(owner, context_id, evidence_id) if owner else None
            return {'status': 'not_found'} if row is None else {'status': 'ok', 'result': row}
        except Exception:
            return {'status': 'unavailable'}

    def execution(self, request_key):
        if not isinstance(request_key,str) or not request_key.strip() or len(request_key)>512:
            return {'status':'invalid_argument'}
        try:
            owner = self.owner()
            row = self.executions.lookup(owner,'ask_paper',request_key) if owner else None
            if row is None or not row.access_allowed:
                return {'status':'not_found'}
            # 읽기 포트만 사용한다. 조회 없음·오류·unknown에서도 접수하지 않는다.
            view = read_existing_execution(self.executions,row.lookup)
            if view is None:
                return {'status':'not_found'}
            return {'status':'ok', **restored_guidance(view), 'result':view.payload}
        except Exception:
            return {'status':'unavailable'}


def register_product_queries(server, queries):
    @server.tool()
    def list_remote_papers(offset: int = 0) -> dict:
        """현재 계정의 서버 처리 논문을 20개씩 조회한다. PDF·로컬 파일을 열지 않는다."""
        return queries.list_papers(offset)

    @server.tool()
    def open_remote_paper(paper_id: str, version_id: str, context_key: str) -> dict:
        """UUID context_key로 판본·게시를 고정한다. 응답 유실 시 같은 키를 사용한다. AI 호출은 없다."""
        return queries.open_paper(paper_id,version_id,context_key)

    @server.tool()
    def get_remote_question(request_key: str) -> dict:
        """기존 질문만 조회한다. 조회 실패·미존재는 재제출 이유가 아니며 새 유료 작업을 만들지 않는다."""
        return queries.execution(request_key)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def get_remote_evidence(context_id: str, evidence_id: str) -> dict[str, Any]:
        """완료된 답변의 저장 근거만 고정 판본·게시·인용으로 다시 읽는다. 재검색·AI 호출·새 질문 접수는 하지 않는다."""
        return queries.evidence(context_id, evidence_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def get_remote_paper_detail(context_id: str, section_offset: int = 0) -> dict[str, Any]:
        """고정 게시본의 논문 상세·목차를 20개씩 조회한다. 요약·키워드 미지원은 빈 상태로 알리며 생성하지 않는다. 메모는 annotations:read 권한의 list_remote_annotations로 조회한다."""
        return queries.detail(context_id, section_offset)
