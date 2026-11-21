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


CONNECTION_CHECK_MESSAGES = {
    'ok': '이 Claude가 코드를 발급한 Solo Leveling 웹 계정과 같은 계정으로 연결돼 있음을 확인했어요.',
    'different_account': ('코드는 유효하지만, 이 Claude는 코드를 발급한 웹 계정과 다른 Solo Leveling 계정으로 '
                          '연결돼 있어요. Claude 커넥터에 로그인한 계정으로 웹에 로그인해 코드를 다시 발급하거나, '
                          'Claude 커넥터를 웹과 같은 계정으로 다시 연결해 주세요.'),
    'expired': '코드의 5분 사용 시간이 지났어요. 웹에서 코드를 다시 발급해 주세요.',
    'already_used': '이미 확인에 사용한 코드예요. 웹의 확인 결과를 새로고침하거나 코드를 다시 발급해 주세요.',
    'not_found': ('이 서버에서 발급된 코드를 찾지 못했어요. 코드를 다시 발급한 뒤 그대로 복사했는지, '
                  '같은 환경(스테이징·운영)의 웹에서 발급했는지 확인해 주세요. '
                  '웹에서 코드를 다시 누르면 이전 코드는 무효가 돼요.'),
}


class RemoteProductQueries:
    def __init__(self, pool, *, auto_link_issuer=None):
        self.pool = pool
        self.insights_enabled = False
        self.evidences = PostgresRemoteEvidenceReader(pool)
        self.owners = PostgresRemoteOwnerResolver(pool, auto_link_issuer=auto_link_issuer)
        self.contexts = PostgresRemoteContextResolver(pool)
        self.executions = PostgresRemoteExecutionStore(pool)

    def owner(self):
        token = get_access_token()
        if (not isinstance(token, RemoteIdentityAccessToken) or token.verified_identity() is None
                or type(token.expires_at) is not int or token.expires_at <= time.time()
                or 'papers:read' not in token.scopes):
            return None
        return self.owners.resolve_owner(token.verified_identity())

    def verify_connection(self, code):
        from solo_leveling.interfaces.http.remote_connection_verification import confirm_check

        token = get_access_token()
        if not isinstance(token, RemoteIdentityAccessToken):
            return {'status': 'not_authorized'}
        identity = token.verified_identity()
        if (identity is None or type(token.expires_at) is not int or token.expires_at <= time.time()
                or 'papers:read' not in token.scopes):
            return {'status': 'not_authorized'}
        try:
            # 이 Claude의 첫 요청이 확인 코드여도 다른 도구와 같은 자동 계정 연결을 거친다.
            self.owners.resolve_owner(identity)
            with self.pool.connection() as conn:
                status = confirm_check(conn, identity.issuer, identity.subject, code)['status']
        except Exception:
            return {'status': 'unavailable'}
        message = CONNECTION_CHECK_MESSAGES.get(status)
        return {'status': status} if message is None else {'status': status, 'message': message}

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

    def read_page(self, context_id, pdf_page, cursor=None, include_translation=False):
        from solo_leveling.interfaces.http.remote_paper_pages import read_paper_page, valid_page_arguments

        try:
            context_id, cursor = valid_page_arguments(context_id, pdf_page, cursor)
        except (ValueError, TypeError, AttributeError):
            return {'status': 'invalid_argument'}
        try:
            owner = self.owner()
            binding = self.contexts.resolve(owner, context_id) if owner else None
            if binding is None:
                return {'status': 'not_found'}
            return read_paper_page(self.pool, binding, pdf_page, cursor, include_translation)
        except Exception:
            return {'status': 'unavailable'}

    def open_paper(self, paper_id, version_id, context_key, *, start_new=False):
        try:
            if type(start_new) is not bool or not isinstance(version_id,str) or not version_id.strip() or len(version_id)>512:
                return {'status':'invalid_argument'}
            ids = [str(UUID(paper_id)),version_id,str(UUID(context_key))]
        except (ValueError,TypeError,AttributeError):
            return {'status':'invalid_argument'}
        try:
            owner = self.owner()
            open_context = self.contexts.pin if start_new else self.contexts.resume_or_pin
            binding = open_context(owner,ids[2],ids[0],ids[1]) if owner else None
            if binding is None:
                return {'status':'not_found'}
            message = '추천 페이지와 근거는 이 PDF 판본 기준입니다. 다른 판본은 페이지가 다를 수 있습니다.'
            if binding.publication_policy == 'partial':
                message += ' 부분 번역 게시본이며 색인된 구간에서 학습할 수 있습니다.'
            return {'status':'ok', 'context_id':binding.context_id, 'paper_id':binding.paper_id,
                    'version_id':binding.server_version_id, 'publication_id':binding.publication_id,
                    'publication_policy':binding.publication_policy, 'message':message,
                    'web_path':f'/#/remote-learning/{binding.context_id}'}
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
                insight_rows = []
                insight_status = 'unsupported'
                if row is not None and self.insights_enabled:
                    try:
                        with conn.transaction():
                            insight_rows = conn.execute("""SELECT
                                i.item - 'source_chunk_id' - 'source_translation_revision_id' AS item,
                                a.source->>'stored_at' AS stored_at
                                FROM server_remote_insight_collections a,
                                    jsonb_array_elements(a.items) WITH ORDINALITY AS i(item, n)
                                WHERE a.owner_user_id=%s AND a.context_id=%s
                                ORDER BY a.device_id,a.source_collection_id,i.n LIMIT 41""",
                                (owner, context_id)).fetchall()
                        insight_status = 'ready' if insight_rows else 'empty'
                    except Exception:
                        insight_status = 'unavailable'
            if row is None:
                return {'status': 'not_found'}
            insights = [dict(r['item'], stored_at=r['stored_at']) for r in insight_rows[:40]]
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
                'summary': None,
                'keywords': [item for item in insights if item['kind'] == 'keyword'],
                'important_sentences': [item for item in insights if item['kind'] == 'important_sentence'],
                'insights_status': insight_status,
                'insights_truncated': len(insight_rows) > 40,
                'limitations': ['remote_summary_not_available']
                               + ([] if self.insights_enabled else [
                                   'remote_keywords_not_available', 'remote_important_sentences_not_available'])
                               + ([] if catalog else ['section_catalog_not_available']),
                'reprocessing_preview': {
                    'tool': 'preview_remote_reprocessing',
                    'context_id': context_id,
                    'operations': ['translation_recovery', 'reparse'],
                    'required_scope': 'papers:read', 'submission_available': False},
                'annotation_lookup': {'tool': 'list_remote_annotations',
                                      'arguments': {'context_id': context_id, 'limit': 20},
                                      'required_scope': 'annotations:read'},
                'page_notice': '목차 페이지는 이 고정 PDF 판본 기준입니다. 인쇄 페이지나 다른 판본의 페이지와 다를 수 있습니다.',
                'generation_notice': '저장 요약은 별도 요약 조회 권한으로 확인합니다. 키워드·중요 문장은 이관된 항목만 표시하며 조회 시 자동 생성하지 않습니다.'}}
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


def register_product_queries(server, queries, *, cancellation_enabled=False, reprocessing_enabled=False):
    from pydantic import StrictInt
    from solo_leveling.interfaces.http.remote_job_queries import RemoteJobQueries

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def preview_remote_reprocessing(context_id: str, operation: str) -> dict[str, Any]:
        """고정 게시본의 translation_recovery 또는 reparse 범위·전송 예정 항목을 조회한다. 비용은 미산정이며 접수·동의·AI 호출·색인 변경은 하지 않는다."""
        from solo_leveling.interfaces.http.remote_recovery_preview import RemoteRecoveryPreview
        return RemoteRecoveryPreview(queries).preview(context_id, operation)

    def can_cancel():
        token = get_access_token()
        return (cancellation_enabled and isinstance(token, RemoteIdentityAccessToken)
                and 'jobs:cancel' in token.scopes)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def list_remote_processing_jobs(after: str | None = None, limit: StrictInt = 20) -> dict:
        """내 처리 및 복구 작업을 UUID 순서로 조회한다. 복구 작업의 web_path는 기존 검토 화면을 연다. 반환 커서를 그대로 사용하며 새 접수·재시도·AI 호출은 하지 않는다."""
        return RemoteJobQueries(queries.pool, queries.owner, can_cancel(), reprocessing_enabled=reprocessing_enabled).list(after, limit)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def get_remote_processing_job(job_id: str) -> dict:
        """내 작업의 안전한 상태·게시본을 조회한다. 복구 작업의 web_path는 기존 검토 화면을 연다. 처리 완료는 학습 완료가 아니며 취소는 별도 확인 절차를 거친다."""
        return RemoteJobQueries(queries.pool, queries.owner, can_cancel(), reprocessing_enabled=reprocessing_enabled).get(job_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=False, openWorldHint=False))
    def verify_remote_connection(code: str) -> dict:
        """Solo Leveling 웹에서 발급한 일회용 코드로 현재 원격 연결을 확인한다. 사용자에게 코드 원문을 반복하지 않는다. 결과의 message를 그대로 안내하고 원인을 추측해 덧붙이지 않는다."""
        return queries.verify_connection(code)

    @server.tool()
    def list_remote_papers(offset: int = 0) -> dict:
        """현재 계정의 서버 처리 논문을 20개씩 조회한다. PDF·로컬 파일을 열지 않는다."""
        return queries.list_papers(offset)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def read_remote_paper_page(context_id: str, pdf_page: StrictInt, cursor: str | None = None,
                               include_translation: bool = False) -> dict:
        """고정 PDF 판본의 지정 페이지를 읽는다. next_cursor는 같은 페이지에서 사용한다. 성공한 본문만 읽었다고 설명한다. 이 도구는 읽기 위치를 저장하지 않으며 위치 저장은 사용자의 명시 요청으로 별도 실행한다."""
        return queries.read_page(context_id, pdf_page, cursor, include_translation)

    @server.tool()
    def open_remote_paper(paper_id: str, version_id: str, context_key: str, start_new: bool = False) -> dict:
        """같은 PDF 판본의 기존 학습 맥락을 우선 열고 없을 때 UUID context_key로 만든다.
        사용자가 별도 학습 기록을 명시적으로 원할 때만 start_new=true로 새 맥락을 만든다.
        후속 도구에는 응답의 context_id를 사용한다. 응답 유실 시 같은 키를 사용한다.
        web_url은 이 맥락의 웹 학습 페이지 전체 주소이며 주소를 물으면 그대로 링크로 준다."""
        return queries.open_paper(paper_id,version_id,context_key,start_new=start_new)

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
