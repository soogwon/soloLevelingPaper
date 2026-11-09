"""복구 실행 결과. 작업 완료와 새 학습 맥락 선택은 별개다."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryResult:
    status: str
    version_id: str
    base_embedding_set_id: str
    job_id: str | None = None
    new_embedding_set_id: str | None = None
    outcome: str | None = None
    reason_code: str | None = None
    target_count: int | None = None
    recovered_count: int | None = None
    remaining_failed_count: int | None = None
    result_available: bool = False


class RecoveryConfigurationError(RuntimeError):
    """기준 번역 설정에 맞는 실행 구성을 제공하지 못했다."""

    def __init__(self):
        super().__init__('RECOVERY_CONFIGURATION_UNAVAILABLE')
