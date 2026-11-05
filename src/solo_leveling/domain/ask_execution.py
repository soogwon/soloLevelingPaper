"""ask 실행 기록의 계약 값과 결과 형식(#119). 합의된 초기값: 하트비트 10초, 임대 60초, 결과 7일, 요청 키 기록 30일.

- 같은 요청 키의 실행은 소유자 토큰을 가진 하나만 진행한다. 토큰은 시작한 호출자에게만 돌려주고 조회에는 담지 않는다.
- 하트비트(10초마다 갱신)가 임대(60초) 안에 오지 않으면 그 실행은 만료다. 만료된 뒤의 갱신·결과 저장·실패 기록은 거부한다.
- 같은 키로 중단된 실행을 자동으로 다시 시작하지 않는다. 새 실행은 호출자가 새 키로 명시적으로 요청한다.
- 키 조회는 읽기 전용이다. 조회가 실행을 시작·재시작하거나 상태를 바꾸지 않는다.
- 결과 7일이 지나면 EXPIRED, 키 기록 30일이 지나도 정리 전까지는 EXPIRED다. 삭제는 명시적인 정리 호출로만 한다.
- EXPIRED와 NOT_FOUND는 자동 재실행의 근거가 아니다.
- 질문·답변·키는 로그에 남기지 않는다. 결과·실패 코드 같은 고정 값과 건수만 쓴다.
"""
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

HEARTBEAT_SECONDS = 10   # 호출자가 갱신하는 권장 간격(저장소는 간격을 강제하지 않는다)
LEASE_SECONDS = 60       # 마지막 하트비트 이후 이 시간이 지나면 만료
RESULT_RETENTION_DAYS = 7
KEY_RETENTION_DAYS = 30
MAX_RESULT_BYTES = 262144
MAX_TEXT_LENGTH = 200
LEASE_EXPIRED = 'LEASE_EXPIRED'
_CODE = re.compile(r'[A-Z][A-Z0-9_]{2,63}')


class ExecutionValidationError(ValueError):
    """입력 형식이 맞지 않는다. 키·질문·결과 본문은 메시지에 넣지 않는다."""


class ExecutionOutcome(str, Enum):
    STARTED = 'started'                        # 새 실행 시작. 소유자 토큰을 돌려준다.
    IN_PROGRESS = 'in_progress'                # 같은 키의 실행이 임대 안에서 진행 중. 토큰은 주지 않는다.
    COMPLETED = 'completed'                    # 같은 키의 결과가 있다(만료 전).
    FAILED = 'failed'                          # 같은 키의 실행이 실패했거나 임대가 만료됐다. 자동 재시작하지 않는다.
    EXPIRED = 'expired'                        # 결과 또는 키 기록의 보존 기간이 지났다. 자동 재실행의 근거가 아니다.
    STATUS_UNKNOWN = 'status_unknown'          # 실패 기록도 쓸 수 없었다. 같은 키로 조회한다.
    CONTEXT_NOT_FOUND = 'context_not_found'
    RENEWED = 'renewed'                        # 하트비트 갱신됨
    SAVED = 'saved'                            # 결과/실패 기록됨
    LEASE_LOST = 'lease_lost'                  # 토큰 불일치, 임대 만료, 이미 끝난 실행 — 아무것도 바꾸지 않았다.
    NOT_FOUND = 'not_found'


class ExecutionState(str, Enum):
    """키 조회로 보이는 상태. 접수와 실행 중은 하나(running)로 본다."""
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    EXPIRED = 'expired'
    NOT_FOUND = 'not_found'


@dataclass(frozen=True)
class ExecutionView:
    state: ExecutionState
    context_id: Optional[str] = None
    failure_code: Optional[str] = None      # failed일 때. 임대 만료는 LEASE_EXPIRED
    result_json: Optional[str] = None       # completed(만료 전)일 때만
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    lease_expires_at: Optional[str] = None  # running일 때
    result_expires_at: Optional[str] = None


@dataclass(frozen=True)
class ExecutionResult:
    outcome: ExecutionOutcome
    owner_token: Optional[str] = None       # STARTED일 때만
    view: Optional[ExecutionView] = None


@dataclass(frozen=True)
class PurgePlan:
    results_to_clear: int   # 결과 보존(7일)이 지나 본문을 비울 행
    rows_to_delete: int     # 키 기록 보존(30일)이 지나 지울 행(유효한 임대의 실행은 제외)


@dataclass(frozen=True)
class PurgeResult:
    results_cleared: int
    rows_deleted: int
    batches: int
    complete: bool          # False면 배치 한도에서 멈췄다. 다시 실행하면 이어서 한다.


def check_text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_LENGTH:
        raise ExecutionValidationError(name)
    return value


def check_failure_code(code) -> str:
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        raise ExecutionValidationError('failure_code')
    return code
