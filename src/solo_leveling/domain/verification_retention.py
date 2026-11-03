"""검증 이력 보존·정리 정책(#82). 보존 기간은 합의 전이라 기본값을 두지 않고 호출자가 반드시 정한다.

- 정리 대상은 '검증 실행' 단위다. 실행 하나를 지우면 그 주장·근거 연결도 함께 지운다.
- 학습 맥락·답변 근거·청크·논문 파일은 정리 대상이 아니다.
- 로그·보고에는 건수·기간·결과 코드만 쓴다. 주장 문구·사유·청크 내용은 담지 않는다.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

MAX_BATCH_SIZE = 1000
DEFAULT_BATCH_SIZE = 200


class RetentionValidationError(ValueError):
    """보존 설정이 잘못됐다. 메시지에는 값이 아니라 항목 이름만 쓴다."""


class CleanupOutcome(str, Enum):
    COMPLETED = 'completed'            # 대상이 더 남지 않았다.
    STOPPED = 'stopped'                # 배치 한도에서 멈췄다. 다시 실행하면 이어서 지운다.
    LOCKED = 'locked'                  # 다른 쓰기와 겹쳐 멈췄다. 이미 끝난 배치는 유지된다.
    FAILED = 'failed'                  # 저장소 오류. 진행 중이던 배치는 되돌렸다.


class SpaceOutcome(str, Enum):
    COMPLETED = 'completed'
    INSUFFICIENT_DISK = 'insufficient_disk'
    LOCKED = 'locked'
    FAILED = 'failed'


@dataclass(frozen=True)
class RetentionPolicy:
    older_than_days: int
    batch_size: int = DEFAULT_BATCH_SIZE


@dataclass(frozen=True)
class CleanupPlan:
    cutoff: str
    total_runs: int
    eligible_runs: int
    eligible_claims: int
    eligible_refs: int
    oldest_created_at: Optional[str]
    newest_eligible_created_at: Optional[str]


@dataclass(frozen=True)
class CleanupResult:
    outcome: CleanupOutcome
    deleted_runs: int
    deleted_claims: int
    deleted_refs: int
    batches: int


@dataclass(frozen=True)
class SpaceReport:
    file_bytes: int
    free_bytes_in_file: int  # 행을 지워 파일 안에 비어 있지만 운영체제에 돌려주지 않은 공간


@dataclass(frozen=True)
class SpaceResult:
    outcome: SpaceOutcome
    before_bytes: int
    after_bytes: int


def validate_policy(policy: RetentionPolicy) -> None:
    for name, value, low, high in (('older_than_days', policy.older_than_days, 1, 36500),
                                   ('batch_size', policy.batch_size, 1, MAX_BATCH_SIZE)):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise RetentionValidationError(name)
