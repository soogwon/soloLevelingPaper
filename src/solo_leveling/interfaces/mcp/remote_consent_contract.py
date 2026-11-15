"""#396 C 내부 응답 초안. 제품 도구에 등록하지 않으며 동의·작업을 생성하지 않는다."""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ConsentState(Enum):
    MISSING = 'missing'
    CURRENT = 'current'
    OUTDATED = 'outdated'
    REVOKED = 'revoked'


class ExecutionState(Enum):
    ABSENT = 'absent'
    QUEUED = 'queued'
    CANCEL_REQUESTED = 'cancel_requested'
    CANCELLED = 'cancelled'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    UNKNOWN = 'unknown'


class BudgetState(Enum):
    AVAILABLE = 'available'
    LIMITED = 'limited'
    USAGE_UNKNOWN = 'usage_unknown'


@dataclass(frozen=True, repr=False)
class Intent:
    """owner는 인증 adapter, 나머지 값은 검증된 의도에서 전달한다. 객체 생성은 인증이 아니다."""
    owner_id: str
    target_id: str
    operation: str
    input_hash: str
    request_key: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (
            self.owner_id, self.target_id, self.operation, self.input_hash, self.request_key
        )):
            raise ValueError('REMOTE_INTENT_INVALID')


@dataclass(frozen=True, repr=False)
class Snapshot:
    """서버 조회 대역의 응답. current는 서버가 고지 버전·철회·대상 결합을 검증한 상태다."""
    intent: Intent
    storage_consent: ConsentState
    ai_consent: ConsentState
    budget: BudgetState
    execution: ExecutionState
    execution_id: str | None = None
    saved_result_available: bool = False
    access_allowed: bool = False


@dataclass(frozen=True)
class Guidance:
    """내부 표시 모델이며 공개 MCP status/reason 계약이 아니다. 식별자·본문은 넣지 않는다."""
    state: str
    message: str
    next_action: str
    can_read_result: bool = False
    automatic_paid_retry: bool = False


class ConsentStatusReader(Protocol):
    def read(self, intent: Intent) -> Snapshot | None:
        """owner/입력 범위에서 최신 동의·실행을 읽기만 한다. 소비·예약·접수·AI 호출 금지."""
        ...


_UNAVAILABLE = Guidance('unavailable', '상태를 확인하지 못했습니다. 같은 요청의 상태를 다시 확인하세요.', 'refresh_status')
_NOT_FOUND = Guidance('not_found', '조회할 수 있는 요청이 없습니다. 자동으로 다시 제출하지 않습니다.', 'review_request')


def recover_guidance(reader: ConsentStatusReader, intent: Intent) -> Guidance:
    """응답 유실·카드/웹 불일치 시 서버를 재조회한다. 새 키나 제출 동작을 만들지 않는다."""
    if not isinstance(intent, Intent):
        return _NOT_FOUND
    try:
        snapshot = reader.read(intent)
    except Exception:
        # 대역/저장소 예외의 토큰·본문·경로를 외부 안내에 복사하지 않는다.
        return _UNAVAILABLE
    if snapshot is None:
        return _NOT_FOUND
    if not isinstance(snapshot, Snapshot):
        return _UNAVAILABLE
    if snapshot.intent != intent or snapshot.access_allowed is not True:
        return _NOT_FOUND
    if (not isinstance(snapshot.storage_consent, ConsentState)
            or not isinstance(snapshot.ai_consent, ConsentState)
            or not isinstance(snapshot.budget, BudgetState)
            or not isinstance(snapshot.execution, ExecutionState)
            or type(snapshot.saved_result_available) is not bool):
        return _UNAVAILABLE
    existing = snapshot.execution is not ExecutionState.ABSENT
    if ((existing and (not isinstance(snapshot.execution_id, str) or not snapshot.execution_id.strip()))
            or (not existing and (snapshot.execution_id is not None or snapshot.saved_result_available))):
        return _UNAVAILABLE
    readable = snapshot.saved_result_available
    # 기존 실행은 동의/한도가 바뀌어도 재접수하지 않는다. 결과 조회 권한은 별도 서버 판정이다.
    if existing:
        messages = {
            ExecutionState.QUEUED: '기존 작업이 접수되어 대기 중입니다. 다시 제출하지 말고 같은 작업의 상태를 확인하세요.',
            ExecutionState.CANCEL_REQUESTED: '취소를 요청했으며 확정을 기다리는 중입니다. 이미 전송된 요청의 처리·정산은 계속될 수 있습니다.',
            ExecutionState.CANCELLED: '기존 작업의 취소가 확정되었습니다. 이미 전송된 요청의 사용량 정산은 별도이며 자동으로 다시 제출하지 않습니다.',
            ExecutionState.RUNNING: '기존 작업을 처리 중입니다. 같은 작업의 상태를 확인하세요.',
            ExecutionState.COMPLETED: '기존 작업이 완료되었습니다. 저장된 결과를 확인하세요.',
            ExecutionState.FAILED: '기존 작업이 실패했습니다. 상태를 확인하며 자동 재실행하지 않습니다.',
            ExecutionState.UNKNOWN: '기존 작업의 처리 여부를 확인 중입니다. 다시 제출하지 마세요.',
        }
        message = messages[snapshot.execution]
        if snapshot.execution is ExecutionState.COMPLETED and not readable:
            message = '기존 작업은 완료됐지만 조회 가능한 결과를 확인하지 못했습니다. 같은 작업의 상태를 다시 확인하세요.'
        if snapshot.budget is BudgetState.USAGE_UNKNOWN:
            message += ' 사용량 확인이 필요하며 추가 유료 처리를 자동 재시도하지 않습니다.'
        return Guidance(snapshot.execution.value, message,
                        'read_result' if readable else 'refresh_status', readable)
    for purpose, state in [('storage', snapshot.storage_consent), ('ai', snapshot.ai_consent)]:
        if state is not ConsentState.CURRENT:
            label = '서버 PDF 보관' if purpose == 'storage' else '외부 AI 전송'
            text = {
                ConsentState.MISSING: '동의가 필요합니다.',
                ConsentState.OUTDATED: '고지가 변경되어 재동의가 필요합니다.',
                ConsentState.REVOKED: '동의가 철회되었습니다.',
            }[state]
            return Guidance(f'{purpose}_{state.value}', f'{label} {text} 인증된 웹에서 확인하세요.', 'review_consent')
    if snapshot.budget is BudgetState.USAGE_UNKNOWN:
        return Guidance('usage_unknown', '사용량 확인 중입니다. 추가 유료 처리를 자동 재시도하지 않습니다.', 'refresh_status')
    if snapshot.budget is BudgetState.LIMITED:
        return Guidance('budget_limited', '처리 한도가 부족합니다. 저장된 기록 조회는 별도로 이용할 수 있습니다.', 'review_limit')
    return Guidance('review_submission', '동의 상태를 확인했습니다. 명시적으로 제출하면 서버가 동의와 한도를 다시 검사합니다.',
                    'review_submission')
