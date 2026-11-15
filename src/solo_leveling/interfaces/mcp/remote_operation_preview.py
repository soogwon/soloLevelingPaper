"""#400 운영 조회의 내부 카드·텍스트 표시. 제어 변경이나 공개 도구 등록은 하지 않는다."""

from html import escape
from typing import Protocol

from solo_leveling.interfaces.http.remote_operation_controls import OperationControls
from .remote_consent_preview import Preview


class OperationControlReader(Protocol):
    def get(self) -> OperationControls | None:
        """현재 제어를 읽기만 한다. 조회값은 접수·전송 승인이 아니다."""
        ...


def render_operation_preview(reader: OperationControlReader, *, mcp_enabled: bool | None) -> Preview:
    """endpoint 값은 서버 설정에서 전달한다. 미확인 상태를 활성으로 추정하지 않는다."""
    title = '운영 상태 확인 필요'
    message = '현재 운영 상태를 확인하지 못했습니다. 새 유료 처리를 자동으로 재시도하지 않습니다.'
    if mcp_enabled is False:
        title = '원격 MCP 중단'
        message = ('원격 MCP 연결과 도구 조회가 중단된 상태입니다. 기존 웹의 이용 가능 여부는 별도로 확인해야 합니다. '
                   '이미 전송된 요청의 취소·환급을 뜻하지 않습니다.')
    elif mcp_enabled is True:
        try:
            controls = reader.get()
        except Exception:
            controls = None
        if (isinstance(controls, OperationControls) and type(controls.generation) is int
                and controls.generation >= 1 and type(controls.intake_enabled) is bool
                and type(controls.dispatch_enabled) is bool):
            title = '원격 처리 운영 상태'
            intake = ('신규 PDF 처리 접수는 운영 제어상 허용 상태입니다.' if controls.intake_enabled
                      else '신규 PDF 처리 접수가 중단되어 있습니다.')
            dispatch = ('진행 작업의 다음 제공자 전송은 운영 제어상 허용 상태입니다.' if controls.dispatch_enabled
                        else '진행 작업의 다음 제공자 전송이 중단되어 있습니다.')
            message = (f'{intake} {dispatch} '
                       '실제 접수·전송 시 동의·예산·권한을 다시 검사합니다. '
                       '기존 결과 조회는 현재 접근·삭제 권한에 따라 별도로 판단합니다. '
                       '이미 전송된 요청과 사용량 확인 중인 기록은 취소·환급된 것으로 보지 않습니다.')
    text = f'{title}\n{message}\n자동 유료 재시도는 하지 않습니다.'
    html = ('<!doctype html><html lang="ko"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>운영 상태 미리보기</title><body><main><p>제품에 연결되지 않은 읽기 전용 미리보기입니다.</p>'
            f'<h1>{escape(title)}</h1><p role="status">{escape(message)}</p>'
            '<p>자동 유료 재시도는 하지 않습니다.</p></main></body></html>')
    return Preview(text, html)
