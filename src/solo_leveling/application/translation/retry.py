"""번역 재시도 정책과 배치별 예산. 실행 중인 호출을 강제 종료하지 않는다."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TranslationRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    jitter_seconds: float = 0.25
    max_delay_seconds: float = 10.0
    chunk_retry_window_seconds: float = 90.0
    batch_retry_window_seconds: float = 600.0
    max_batch_retries: int = 20
    max_batch_wait_seconds: float = 30.0

    def __post_init__(self):
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 3:
            raise ValueError('최초 호출을 포함해 1~3회만 허용합니다.')
        if type(self.max_batch_retries) is not int or self.max_batch_retries < 0:
            raise ValueError('배치 재시도 수는 음이 아닌 정수여야 합니다.')
        for name in ('base_delay_seconds', 'jitter_seconds', 'max_delay_seconds',
                     'chunk_retry_window_seconds', 'batch_retry_window_seconds',
                     'max_batch_wait_seconds'):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('재시도 시간은 유한한 음이 아닌 수여야 합니다.')


@dataclass
class RetryBudget:
    started: float
    retries: int = 0
    waited: float = 0.0
