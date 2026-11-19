"""고정 근거와 해당 청크의 메모를 읽기 전용 카드로 연결한다."""
from dataclasses import asdict
import hmac
from pathlib import Path
from typing import Any
from mcp.types import ToolAnnotations

from solo_leveling.application.summary.remote_export import export_summary

CARD_URI = 'ui://remote-evidence/detail.html'
# 상세 응답을 읽는 새 번들은 이전 호스트 캐시와 구분한다.
PAPER_CARD_URI = 'ui://remote-paper/detail-v2.html'
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)


def register_evidence_cards(server, queries, service, call, context, binding, *, latest_summary=None):
    @server.resource(CARD_URI, mime_type='text/html;profile=mcp-app')
    def remote_evidence_detail_card():
        return Path(__file__).with_name('assets').joinpath('remote-evidence-detail.html').read_text(encoding='utf-8')

    def read(owner, context_id, evidence_id, after, limit, expected_binding):
        ctx, eid = context(context_id), context(evidence_id)
        current = binding(owner, ctx)
        if expected_binding is not None and not hmac.compare_digest(current, expected_binding):
            return {'connection_changed': True}
        try:
            evidence = queries.evidences.get(owner, ctx, eid)
        except Exception:
            raise RuntimeError('remote_evidence_unavailable') from None
        if evidence is None:
            return None
        page = service.store.list_annotations(owner, ctx, after=after, limit=limit, evidence=evidence)
        if page is None:
            return None
        return {'context_id': ctx, 'evidence_id': eid, 'binding': current, 'evidence': evidence,
                'annotation_page': {'items': [asdict(item) for item in page['items']],
                                    'next_cursor': page['next_cursor']},
                'after': after,
                'annotation_notice': '같은 고정 청크에 연결된 기록입니다. 인용문과 정확히 겹치는 구간만의 목록은 아닙니다.'}

    @server.tool(annotations=READ_ONLY)
    def list_remote_evidence_annotations(context_id: str, evidence_id: str,
                                         after: str | None = None, limit: int = 20) -> dict[str, Any]:
        """저장 근거의 고정 청크·리비전에 연결된 메모·하이라이트를 조회한다. annotations:read와 papers:read가 필요하며 새로고침은 첫 페이지부터 한다."""
        return call(False, lambda owner: read(owner, context_id, evidence_id, after, limit, None))

    @server.tool(meta={'ui': {'resourceUri': CARD_URI}}, annotations=READ_ONLY)
    def show_remote_evidence_detail(context_id: str, evidence_id: str,
                                    after: str | None = None,
                                    expected_binding: str | None = None) -> dict[str, Any]:
        """저장 인용·판본·페이지와 근거 청크의 메모 첫 페이지를 상세 카드로 연다. 더 보기는 같은 연결과 커서를 사용한다. AI 생성·쓰기 없이 조회한다."""
        return call(False, lambda owner: read(owner, context_id, evidence_id, after, 20, expected_binding))

    @server.resource(PAPER_CARD_URI, mime_type='text/html;profile=mcp-app')
    def remote_paper_detail_card():
        return Path(__file__).with_name('assets').joinpath('remote-paper-detail.html').read_text(encoding='utf-8')

    @server.tool(meta={'ui': {'resourceUri': PAPER_CARD_URI}}, annotations=READ_ONLY)
    def show_remote_paper_detail(context_id: str, section_offset: int = 0,
                                 after: str | None = None,
                                 expected_binding: str | None = None) -> dict[str, Any]:
        """고정 논문 목차와 학습 맥락 전체 메모를 조회한다. 각 목록은 20개씩이며 새로고침은 처음부터 한다. 요약·키워드가 없어도 자동 생성하지 않는다. papers:read와 annotations:read가 필요하다."""
        def operation(owner):
            ctx = context(context_id)
            current = binding(owner, ctx)
            if expected_binding is not None and not hmac.compare_digest(current, expected_binding):
                return {'connection_changed': True}
            detail = queries.detail(ctx, section_offset)
            if detail['status'] == 'not_found':
                return None
            if detail['status'] == 'invalid_argument':
                raise ValueError('remote_detail_input_invalid')
            if detail['status'] != 'ok':
                raise RuntimeError('remote_detail_unavailable')
            page = service.store.list_annotations(owner, ctx, after=after, limit=20)
            if page is None:
                return None
            summary = latest_summary(ctx) if latest_summary is not None else {'status': 'unsupported'}
            markdown = None
            if summary.get('state') == 'completed' and summary.get('result'):
                try:
                    markdown = export_summary(summary)
                except (ValueError, KeyError, TypeError):
                    pass
            return {'context_id': ctx, 'binding': current, 'detail': detail['result'],
                    'section_offset': section_offset, 'after': after,
                    'saved_summary': summary, 'saved_summary_markdown': markdown,
                    'annotation_page': {'items': [asdict(item) for item in page['items']],
                                        'next_cursor': page['next_cursor']}}
        return call(False, operation)
