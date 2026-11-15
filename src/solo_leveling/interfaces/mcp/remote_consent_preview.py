"""#396 내부 Guidance의 카드/텍스트 대역. 제품 리소스나 공개 wire 코드는 아니다."""

from dataclasses import dataclass
from html import escape

from .remote_consent_contract import Guidance


_ACTIONS = {
    'review_consent': '인증된 웹에서 동의 확인',
    'review_submission': '제출 내용 검토',
    'refresh_status': '같은 요청 상태 확인',
    'read_result': '저장된 결과 조회',
    'review_limit': '처리 한도 확인',
    'review_request': '요청 정보 확인',
}
_STATES = {
    **{f'{p}_{s}': ('동의 확인', {'review_consent'})
       for p in ('storage', 'ai') for s in ('missing', 'outdated', 'revoked')},
    'budget_limited': ('처리 한도', {'review_limit'}),
    'usage_unknown': ('사용량 확인 중', {'refresh_status'}),
    'review_submission': ('제출 전 확인', {'review_submission'}),
    'not_found': ('조회 불가', {'review_request'}),
    'unavailable': ('상태 확인 필요', {'refresh_status'}),
    **{s: (label, {'read_result', 'refresh_status'}) for s, label in (
        ('running', '처리 중'), ('completed', '처리 완료'),
        ('failed', '처리 실패'), ('unknown', '처리 여부 확인 중'))},
}


@dataclass(frozen=True)
class Preview:
    text: str
    html: str


def render_consent_preview(view: Guidance) -> Preview:
    """읽기 전용 시각화만 만든다. 버튼은 미연결이며 링크/스크립트/제출 기능이 없다."""
    if (not isinstance(view, Guidance) or not isinstance(view.state, str) or view.state not in _STATES
            or not isinstance(view.next_action, str) or view.next_action not in _STATES[view.state][1]
            or type(view.can_read_result) is not bool or view.automatic_paid_retry is not False
            or (view.next_action == 'read_result') != view.can_read_result
            or not isinstance(view.message, str) or not view.message.strip() or len(view.message) > 2000):
        view = Guidance('unavailable', '상태를 확인하지 못했습니다. 같은 요청의 상태를 다시 확인하세요.', 'refresh_status')
    title = _STATES[view.state][0]
    action = _ACTIONS[view.next_action]
    text = f'{title}\n{view.message}\n다음 행동: {action}\n자동 유료 재시도는 하지 않습니다.'
    html = ('<!doctype html><html lang="ko"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>원격 동의 안내 미리보기</title><body><main>'
            '<p>미리보기: 버튼은 아직 연결되지 않았습니다.</p>'
            f'<section aria-labelledby="state-title"><h1 id="state-title">{escape(title)}</h1>'
            f'<p role="status">{escape(view.message)}</p>'
            '<p>자동 유료 재시도는 하지 않습니다.</p>'
            f'<button type="button" disabled>{escape(action)}</button></section></main></body></html>')
    return Preview(text, html)
