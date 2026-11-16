"""#368 C 읽기 전용 안내 대역. 삭제 접수·공개 MCP 계약은 별도다."""

from dataclasses import dataclass
from html import escape
from typing import Protocol

from solo_leveling.domain.deletion_policy import DELETION_NOTICE_KO, DeletionIntent


@dataclass(frozen=True, repr=False)
class DeletionLookup:
    """인증 adapter가 owner를 제공한다. 이 객체 생성은 인증을 대신하지 않는다."""
    owner_id: str
    paper_id: str
    request_key: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in (
                self.owner_id, self.paper_id, self.request_key)):
            raise ValueError('DELETION_LOOKUP_INVALID')


@dataclass(frozen=True, repr=False)
class DeletionSnapshot:
    lookup: DeletionLookup
    request_id: str
    status: str
    access_allowed: bool
    backup_erasure_confirmed: bool = False


class DeletionReader(Protocol):
    def read(self, lookup: DeletionLookup) -> DeletionSnapshot | None:
        """현재 owner 권한으로 기존 요청만 조회한다. 접수·재시도·삭제는 금지한다."""
        ...


@dataclass(frozen=True)
class DeletionGuidance:
    state: str
    title: str
    message: str
    action: str


_VIEWS = {
    'local_only': ('로컬 삭제', '이 기기의 삭제는 서버 삭제를 뜻하지 않습니다.', '삭제 범위 확인'),
    'stop_sync': ('동기화 중지', '새 동기화를 중지해도 서버 자료는 삭제되지 않습니다.', '동기화 범위 확인'),
    'revoke_consent': ('전송 동의 철회', '새 외부 AI 전송을 차단하는 동작이며 서버 자료 삭제와 다릅니다.', '동의 범위 확인'),
    'server_paper': ('서버 논문 삭제 전 확인', DELETION_NOTICE_KO + ' 서버에 저장된 해당 논문과 관련 기록의 삭제 범위를 확인하세요. 복구 가능하다고 보장하지 않습니다.', '삭제 범위와 고지 확인'),
    'account': ('계정 삭제 범위 확인', '계정 삭제는 논문 하나의 삭제와 다릅니다. 계정 전체의 대상·권한·별도 접수 경로를 확인해야 합니다.', '계정 삭제 절차 확인'),
    'blocking': ('접근 차단 · 정리 대기', '삭제 요청이 접수되어 자료 접근과 새 처리가 차단됐습니다. 물리 정리는 아직 완료되지 않았습니다.', '같은 삭제 요청 상태 확인'),
    'cleaning': ('자료 정리 중', '접근은 차단됐으며 자료 정리가 진행 중입니다. 새 삭제 요청을 만들지 마세요.', '같은 삭제 요청 상태 확인'),
    'failed': ('자료 정리 확인 필요', '접근 차단은 유지되며 일부 정리가 끝나지 않았을 수 있습니다. 같은 요청을 조회하세요. 복구 가능 또는 완전 삭제로 표시하지 않습니다.', '같은 삭제 요청 상태 확인'),
    'complete': ('활성 저장소 정리 완료', '활성 저장소의 정리가 완료됐습니다. 백업 완전 삭제 여부는 아직 확인되지 않았습니다. 삭제 표식은 별도 보존됩니다.', '같은 삭제 요청 상태 확인'),
    'erased': ('백업 포함 삭제 확인', '서버가 활성 저장소와 백업의 삭제를 확인했습니다. 재생성 방지를 위한 최소 삭제 표식은 별도 보존됩니다.', '삭제 상태 확인'),
    'not_found': ('삭제 요청 조회 불가', '조회할 수 있는 요청이 없습니다. 새 삭제 요청을 자동으로 만들지 않습니다.', '요청 정보 확인'),
    'unavailable': ('삭제 상태 확인 필요', '상태를 확인하지 못했습니다. 삭제 완료나 취소로 판단하지 말고 같은 요청을 다시 조회하세요.', '같은 삭제 요청 상태 확인'),
}


def _view(state: str) -> DeletionGuidance:
    return DeletionGuidance(state, *_VIEWS[state])


def deletion_confirmation(intent: DeletionIntent) -> DeletionGuidance:
    """조회·미리보기만으로 삭제 동의나 tombstone을 만들지 않는다."""
    return _view(intent.value if isinstance(intent, DeletionIntent) else 'unavailable')


def recover_deletion_guidance(reader: DeletionReader, lookup: DeletionLookup) -> DeletionGuidance:
    if not isinstance(lookup, DeletionLookup):
        return _view('not_found')
    try:
        snapshot = reader.read(lookup)
    except Exception:
        return _view('unavailable')
    if snapshot is None:
        return _view('not_found')
    if not isinstance(snapshot, DeletionSnapshot):
        return _view('unavailable')
    if snapshot.lookup != lookup or snapshot.access_allowed is not True:
        return _view('not_found')
    if (not isinstance(snapshot.request_id, str) or not snapshot.request_id.strip()
            or not isinstance(snapshot.status, str)
            or snapshot.status not in {'blocking', 'cleaning', 'failed', 'complete'}
            or type(snapshot.backup_erasure_confirmed) is not bool
            or (snapshot.backup_erasure_confirmed and snapshot.status != 'complete')):
        return _view('unavailable')
    return _view('erased' if snapshot.backup_erasure_confirmed else snapshot.status)


def render_deletion_preview(view: DeletionGuidance) -> tuple[str, str]:
    """고정 문구만 출력한다. 버튼은 미연결이며 삭제·재접수 기능이 없다."""
    if (not isinstance(view, DeletionGuidance) or not isinstance(view.state, str)
            or view.state not in _VIEWS or view != _view(view.state)):
        view = _view('unavailable')
    text = f'{view.title}\n{view.message}\n다음 행동: {view.action}\n미리보기: 실제 삭제 기능은 연결되지 않았습니다.'
    html = ('<!doctype html><html lang="ko"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>삭제 안내 미리보기</title><main>'
            f'<h1>{escape(view.title)}</h1><p role="status">{escape(view.message)}</p>'
            '<p>미리보기: 실제 삭제 기능은 연결되지 않았습니다.</p>'
            f'<button type="button" disabled>{escape(view.action)}</button></main></html>')
    return text, html
