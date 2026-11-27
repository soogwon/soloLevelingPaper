"""복사한 원문 구간을 찾아 확인 카드 없이 바로 저장하는 원격 하이라이트 도구."""
from dataclasses import asdict
from typing import Any
from mcp.types import ToolAnnotations
from pydantic import StrictInt
from solo_leveling.interfaces.http.remote_annotation_recovery_routes import RecoveryInput


def register_annotation_cards(server, service, call, context, binding, queries):
    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def save_remote_highlight_from_quote(context_id: str, request_key: str, quote: str,
                                         pdf_page: StrictInt | None = None,
                                         candidate_id: str | None = None, note: str = '') -> dict[str, Any]:
        """사용자가 복사한 원문을 하이라이트(메모 포함)로 저장해 달라고 하면 확인 카드 없이 바로 저장한다. 가입 때 받은 계정 저장 동의를 쓰므로 따로 동의를 묻지 않는다. request_key는 요청마다 새 UUID이며 응답 유실 시 같은 키로 다시 호출한다. 일치 위치가 여러 곳이면 저장하지 않고 후보를 돌려주므로 사용자가 고른 candidate_id로 다시 호출한다. 저장 뒤 PDF 페이지·섹션·인용·메모를 사용자에게 알린다."""
        from solo_leveling.interfaces.http.remote_highlight_lookup import find_highlight_candidates

        def operation(owner):
            ctx = context(context_id)
            if service.learning.get(owner, ctx) is None:
                return None
            candidates = find_highlight_candidates(queries.pool, owner, ctx, quote, pdf_page)
            selected = (candidates[0] if candidate_id is None and len(candidates) == 1 else
                        next((item for item in candidates if item['candidate_id'] == candidate_id), None))
            if selected is None:
                public = [{k: v for k, v in item.items() if k != 'segments'} for item in candidates]
                return {'context_id': ctx, 'candidates': public,
                        'outcome': 'choose_candidate' if public else 'quote_not_found'}
            value = {'kind': 'highlight', 'body': note,
                     'segments': selected['segments'], 'creation_origin': 'user'}
            saved = service.create(owner, ctx, request_key, RecoveryInput.model_validate(value).domain())
            return {'context_id': ctx, 'outcome': 'saved', **asdict(saved),
                    'quote_location': {'pdf_page': selected['pdf_page'], 'section_title': selected['section_title']}}
        return call(True, operation)
